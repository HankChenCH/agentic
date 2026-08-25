"""知识库检索编排：绑定解析 → 状态/模型守卫 → 向量扇出融合 → 命中组装。

消费方是 agent 工具（build_knowledge_tools）：LLM 先经 knowledge_list
了解可用知识库，再按需以 kb_ids 自选库检索（缺省检索全部可用库）。
状态收敛规则：知识库须为 enabled、文档须为 enabled 才可被召回——管理
侧的启停开关即检索开关。
"""

from dataclasses import dataclass
from typing import List, Tuple
from uuid import UUID

from wireup import injectable

from app.components.knowledge.vector_index import DEFAULT_TOP_K, KnowledgeVectorIndex, VectorHit
from app.core.config import AppConfig
from app.core.logging import LoggerFactory
from app.models.domain.knowledge import KnowledgeBase, KnowledgeStatus
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.repositories.knowledge_binding_repository import KnowledgeBindingRepository
from app.repositories.knowledge_document_repository import KnowledgeDocumentRepository


@dataclass
class RetrievalHit:
    """面向引用展示的检索命中：文档名 + 溯源 meta（页码/标题路径/资产 key）。"""

    content: str
    score: float
    kb_id: str
    doc_id: str
    doc_name: str
    position: int
    meta: dict


@injectable
@dataclass
class KnowledgeRetrievalService:
    binding_repo: KnowledgeBindingRepository
    kb_repo: KnowledgeBaseRepository
    document_repo: KnowledgeDocumentRepository
    vector_index: KnowledgeVectorIndex
    app_config: AppConfig
    logger_factory: LoggerFactory

    def __post_init__(self):
        self.logger = self.logger_factory.get_logger(__name__)

    def list_knowledge_for_agent(self, agentic_id: str) -> List[KnowledgeBase]:
        """agent 可用（已绑定且未在删除中）的知识库，供 knowledge_list 展示。"""
        bindings = self.binding_repo.list_by_agent(agentic_id)
        kbs = (self.kb_repo.get_kb(binding.kb_id) for binding in bindings)
        return [kb for kb in kbs if kb is not None and kb.status != KnowledgeStatus.DELETING]

    def search_for_agent(
        self,
        agentic_id: str,
        query: str,
        kb_ids: List[UUID] | None = None,
        top_k: int = DEFAULT_TOP_K,
    ) -> Tuple[List[RetrievalHit], List[str]]:
        """agent 域检索：返回（命中列表, 跳过说明——工具会把说明附在结果尾部）。

        编排：绑定解析 → LLM 自选库子集过滤 → 库级守卫（enabled + 嵌入
        模型一致）→ 多库扇出融合 → 文档级 enabled 后滤 → 溯源组装。
        """
        kbs = self.list_knowledge_for_agent(agentic_id)
        notes: List[str] = []

        selected = kbs
        if kb_ids:
            wanted = set(kb_ids)
            selected = [kb for kb in kbs if kb.id in wanted]
            unknown = wanted - {kb.id for kb in kbs}
            if unknown:
                notes.append(
                    "未绑定或不存在的知识库已忽略: " + ", ".join(sorted(str(u) for u in unknown))
                )

        usable: List[KnowledgeBase] = []
        for kb in selected:
            if kb.status != KnowledgeStatus.ENABLED:
                notes.append(f"知识库「{kb.name}」未启用（{kb.status.value}），已跳过")
                continue
            if kb.embedding_model != self.app_config.vector_db.embedding:
                # 跨向量空间检索只会得到噪声：建库固化的模型与全局配置不一致必须跳过
                notes.append(
                    f"知识库「{kb.name}」嵌入模型与当前配置不一致（{kb.embedding_model}），已跳过"
                )
                self.logger.warning(
                    "kb %s embedding model mismatch: kb=%s config=%s",
                    kb.id,
                    kb.embedding_model,
                    self.app_config.vector_db.embedding,
                )
                continue
            usable.append(kb)

        if not usable:
            return [], notes
        hits = self.search([kb.id for kb in usable if kb.id], query, top_k=top_k)
        return hits, notes

    def search(self, kb_ids: List[UUID], query: str, top_k: int = DEFAULT_TOP_K) -> List[RetrievalHit]:
        """直连多库检索（管理/调试与未来预检索节点复用）。

        过采样 + 文档 enabled 后滤：向量库不存文档状态，Python 侧过滤后
        截断 top_k（当前规模下后滤代价可忽略）。
        """
        # hit.doc_id 是向量 metadata 里的字符串 id，统一为 str 比较
        allowed_doc_ids = {str(doc_id) for doc_id in self.document_repo.list_enabled_doc_ids(kb_ids)}
        vector_hits: List[VectorHit] = self.vector_index.search_many(kb_ids, query, top_k=top_k * 2)
        filtered = [hit for hit in vector_hits if hit.doc_id in allowed_doc_ids][:top_k]
        if not filtered:
            return []
        docs = {
            doc.id: doc
            for doc in self.document_repo.list_documents_by_ids(
                list({UUID(hit.doc_id) for hit in filtered})
            )
        }
        return [
            RetrievalHit(
                content=hit.content,
                score=hit.score,
                kb_id=hit.kb_id,
                doc_id=hit.doc_id,
                doc_name=(docs.get(UUID(hit.doc_id)).name if UUID(hit.doc_id) in docs else ""),
                position=hit.position,
                meta=hit.meta,
            )
            for hit in filtered
        ]
