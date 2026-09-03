"""知识库文档聚合数据访问：文档 + 分段（分段与文档事务耦合，同归本仓库）。

``knowledge_base.doc_num`` 是跨聚合的冗余计数：与文档行增删同会话维护，
避免计数漂移——本仓库因此会在同一事务里触碰 KnowledgeBase 表（有意的
例外，勿推广到其它跨聚合写）。
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Tuple
from uuid import UUID

from sqlalchemy import Engine, delete, func, update
from sqlmodel import Session, col, select
from wireup import injectable

from app.models.domain.knowledge import (
    DocumentSegment,
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeStatus,
)


@injectable
@dataclass
class KnowledgeDocumentRepository:
    engine: Engine

    def create_document(self, doc: KnowledgeDocument) -> KnowledgeDocument:
        # doc_num 与文档行同事务维护，避免冗余计数漂移
        with Session(self.engine, expire_on_commit=False) as session:
            kb = session.get(KnowledgeBase, doc.kb_id)
            if kb is not None:
                kb.doc_num += 1
            session.add(doc)
            session.commit()
            session.refresh(doc)
        return doc

    def get_document(self, kb_id: UUID, doc_id: UUID) -> KnowledgeDocument | None:
        with Session(self.engine, expire_on_commit=False) as session:
            doc = session.exec(
                select(KnowledgeDocument)
                .where(col(KnowledgeDocument.id) == doc_id)
                .where(col(KnowledgeDocument.kb_id) == kb_id)
            ).first()
            session.commit()
        return doc

    def update_document(self, doc: KnowledgeDocument) -> KnowledgeDocument:
        with Session(self.engine, expire_on_commit=False) as session:
            session.add(doc)
            session.commit()
            session.refresh(doc)
        return doc

    def claim_document(
        self, kb_id: UUID, doc_id: UUID, *, stale_before: datetime | None
    ) -> KnowledgeDocument | None:
        """幂等 claim 门闸：把文档从可处理状态原子地占为 processing，返回占到的行。

        重投/重复消息/看门狗补发的并发安全全部收口于此：
        - pending/failed 直接可抢（failed 由重试语义进入）；
        - processing 仅在 ``updated_at < stale_before`` 时可抢（存活任务最长跑
          time_limit，调用方传入 now - stale_processing_seconds；None = 不接管）；
        - ready/enabled/disabled/deleting 一律拒绝——游离消息不得重刷已完成文档。

        实现为「读后乐观锁条件更新」：WHERE 带上读到的旧 status + 旧 updated_at
        做等值比较（同一表示跨 SQLite/PG 时区口径可移植），rowcount=1 即抢占成功，
        并发方落空返回 None（调用方按幂等跳过处理）。
        """
        with Session(self.engine, expire_on_commit=False) as session:
            doc = session.exec(
                select(KnowledgeDocument)
                .where(col(KnowledgeDocument.id) == doc_id)
                .where(col(KnowledgeDocument.kb_id) == kb_id)
            ).first()
            if doc is None:
                session.commit()
                return None
            if doc.status == KnowledgeStatus.PROCESSING:
                if stale_before is None or _as_utc(doc.updated_at) >= _as_utc(stale_before):
                    session.commit()
                    return None
            elif doc.status not in (KnowledgeStatus.PENDING, KnowledgeStatus.FAILED):
                session.commit()
                return None
            result = session.exec(
                update(KnowledgeDocument)
                .where(col(KnowledgeDocument.id) == doc_id)
                .where(col(KnowledgeDocument.kb_id) == kb_id)
                .where(col(KnowledgeDocument.status) == doc.status)
                .where(col(KnowledgeDocument.updated_at) == doc.updated_at)
                .values(status=KnowledgeStatus.PROCESSING, error_message=None)
            )
            claimed = int(result.rowcount) == 1
            session.commit()
            if not claimed:
                return None
            return session.exec(
                select(KnowledgeDocument)
                .where(col(KnowledgeDocument.id) == doc_id)
                .where(col(KnowledgeDocument.kb_id) == kb_id)
            ).first()

    def list_reap_candidates(self) -> List[KnowledgeDocument]:
        """看门狗对账候选：全部 processing/pending 文档行。

        卡死即异常，此类行数极小，全量捞回后由调用方在 Python 端做陈旧阈值
        过滤——避免跨方言（SQLite naive / PG timestamptz）的 SQL 时间比较口径问题。
        """
        with Session(self.engine, expire_on_commit=False) as session:
            rows = session.exec(
                select(KnowledgeDocument).where(
                    col(KnowledgeDocument.status).in_(
                        [KnowledgeStatus.PROCESSING, KnowledgeStatus.PENDING]
                    )
                )
            ).all()
            session.commit()
        return list(rows)

    def mark_reaped(self, doc_id: UUID, *, max_attempts: int) -> bool:
        """看门狗重投计数 +1；达上限置 failed + 原因（毒丸保护）。

        返回 True 表示应继续重投，False 表示已终结为 failed（走人工 retry）。
        成功收尾的归零在 :meth:`complete_document`。
        """
        with Session(self.engine, expire_on_commit=False) as session:
            doc = session.get(KnowledgeDocument, doc_id)
            if doc is None:
                session.commit()
                return False
            count = doc.reap_count + 1
            if count > max_attempts:
                doc.status = KnowledgeStatus.FAILED
                doc.error_message = (
                    f"task reaped {max_attempts} times without completing; manual retry required"
                )[:_ERROR_MESSAGE_MAX]
            else:
                doc.reap_count = count
            session.add(doc)
            session.commit()
            return count <= max_attempts

    def delete_document(self, kb_id: UUID, doc_id: UUID) -> bool:
        with Session(self.engine, expire_on_commit=False) as session:
            doc = session.exec(
                select(KnowledgeDocument)
                .where(col(KnowledgeDocument.id) == doc_id)
                .where(col(KnowledgeDocument.kb_id) == kb_id)
            ).first()
            if doc is None:
                session.commit()
                return False
            kb = session.get(KnowledgeBase, kb_id)
            if kb is not None:
                kb.doc_num = max(kb.doc_num - 1, 0)
            session.delete(doc)  # segments 由 delete-orphan 级联删除
            session.commit()
        return True

    def list_documents(self, kb_id: UUID, page: int, page_size: int) -> Tuple[List[KnowledgeDocument], int]:
        # deleting 文档视同已删除：列表与计数都不返回（详情接口仍可查到）
        offset = (page - 1) * page_size
        with Session(self.engine, expire_on_commit=False) as session:
            total = session.exec(
                select(func.count())
                .select_from(KnowledgeDocument)
                .where(col(KnowledgeDocument.kb_id) == kb_id)
                .where(col(KnowledgeDocument.status) != KnowledgeStatus.DELETING)
            ).one()
            result = session.exec(
                select(KnowledgeDocument)
                .where(col(KnowledgeDocument.kb_id) == kb_id)
                .where(col(KnowledgeDocument.status) != KnowledgeStatus.DELETING)
                .order_by(col(KnowledgeDocument.weight).desc(), col(KnowledgeDocument.created_at).desc())
                .offset(offset)
                .limit(page_size)
            ).all()
            session.commit()
        return list(result), int(total)

    # ---------- 文档分段 ----------

    def list_enabled_doc_ids(self, kb_ids: List[UUID]) -> List[UUID]:
        # 检索召回前的可用文档集：仅 enabled 文档的分段可被召回
        if not kb_ids:
            return []
        with Session(self.engine, expire_on_commit=False) as session:
            ids = session.exec(
                select(col(KnowledgeDocument.id))
                .where(col(KnowledgeDocument.kb_id).in_(kb_ids))
                .where(col(KnowledgeDocument.status) == KnowledgeStatus.ENABLED)
            ).all()
            session.commit()
        return list(ids)

    def get_document_by_id(self, doc_id: UUID) -> KnowledgeDocument | None:
        # 定位读取工具只拿到 doc_id：先按 id 取回文档行（kb_id/名称/状态/seg_num），
        # 可见性与启用守卫由上层服务圈定
        with Session(self.engine, expire_on_commit=False) as session:
            doc = session.exec(
                select(KnowledgeDocument).where(col(KnowledgeDocument.id) == doc_id)
            ).first()
            session.commit()
        return doc

    def list_documents_by_ids(self, doc_ids: List[UUID]) -> List[KnowledgeDocument]:
        # 检索命中后的溯源信息组装（文档名等），去重后的 id 批量取回
        if not doc_ids:
            return []
        with Session(self.engine, expire_on_commit=False) as session:
            rows = session.exec(
                select(KnowledgeDocument).where(col(KnowledgeDocument.id).in_(doc_ids))
            ).all()
            session.commit()
        return list(rows)

    def replace_segments(self, doc_id: UUID, segments: List[DocumentSegment]) -> List[DocumentSegment]:
        """整体替换文档分段：删旧段 + 批量插入 + seg_num 同事务维护。

        重试/重解析的幂等入口——调用方已先行清理旧向量（segment.id 即向量
        库对象 UUID），本方法保证行与计数一致。segments 需携带完整字段
       （id/position/content/word_count/meta/状态）。
        """
        with Session(self.engine, expire_on_commit=False) as session:
            session.exec(delete(DocumentSegment).where(col(DocumentSegment.doc_id) == doc_id))
            for segment in segments:
                session.add(segment)
            doc = session.get(KnowledgeDocument, doc_id)
            if doc is not None:
                doc.seg_num = len(segments)
            session.commit()
        return segments

    def complete_document(self, doc_id: UUID) -> bool:
        """入库收尾：doc 与全部分段置 ready（同一事务，避免中间态可见）。

        同时把库从 pending 提升为 ready（状态机的启用闸门要求 ready）：
        与 doc_num 维护同属本仓库有意的跨聚合例外；仅从 pending 提升，
        enabled/disabled/deleting 状态不受影响。
        """
        with Session(self.engine, expire_on_commit=False) as session:
            doc = session.get(KnowledgeDocument, doc_id)
            if doc is None:
                session.commit()
                return False
            doc.status = KnowledgeStatus.READY
            doc.reap_count = 0  # 成功收尾归零重投计数
            session.exec(
                update(DocumentSegment)
                .where(col(DocumentSegment.doc_id) == doc_id)
                .values(status=KnowledgeStatus.READY)
            )
            kb = session.get(KnowledgeBase, doc.kb_id)
            if kb is not None and kb.status == KnowledgeStatus.PENDING:
                kb.status = KnowledgeStatus.READY
            session.commit()
        return True

    def list_segments_by_doc(
        self, doc_id: UUID, start: int | None = None, end: int | None = None
    ) -> List[DocumentSegment]:
        """按 position 有序取回文档分段（含 content/meta），支持闭区间 [start, end] 过滤。

        定位读取工具（邻域/范围读文）的数据通道；区间语义由调用方钳制后传入，
        本方法不做越界修正（不存在的 position 自然查不到行）。
        """
        with Session(self.engine, expire_on_commit=False) as session:
            statement = (
                select(DocumentSegment)
                .where(col(DocumentSegment.doc_id) == doc_id)
                .order_by(col(DocumentSegment.position))
            )
            if start is not None:
                statement = statement.where(col(DocumentSegment.position) >= start)
            if end is not None:
                statement = statement.where(col(DocumentSegment.position) <= end)
            rows = session.exec(statement).all()
            session.commit()
        return list(rows)

    def list_segment_ids_by_doc(self, doc_id: UUID) -> List[UUID]:
        # 删除文档/重解析前收集分段 id，供上层清理向量库对象
        with Session(self.engine, expire_on_commit=False) as session:
            ids = session.exec(
                select(col(DocumentSegment.id)).where(col(DocumentSegment.doc_id) == doc_id)
            ).all()
            session.commit()
        return list(ids)

    # ---------- 文档分段（管理侧） ----------

    def list_segments(
        self, doc_id: UUID, page: int, page_size: int, keyword: str | None = None
    ) -> Tuple[List[DocumentSegment], int]:
        """管理侧分段分页列表：按 position 有序，keyword 非空时按内容模糊过滤。"""
        offset = (page - 1) * page_size
        with Session(self.engine, expire_on_commit=False) as session:
            statement = select(DocumentSegment).where(col(DocumentSegment.doc_id) == doc_id)
            if keyword:
                statement = statement.where(col(DocumentSegment.content).ilike(f"%{keyword}%"))
            total = session.exec(select(func.count()).select_from(statement.subquery())).one()
            rows = session.exec(
                statement.order_by(col(DocumentSegment.position)).offset(offset).limit(page_size)
            ).all()
            session.commit()
        return list(rows), int(total)

    def get_segment(self, doc_id: UUID, segment_id: UUID) -> DocumentSegment | None:
        with Session(self.engine, expire_on_commit=False) as session:
            segment = session.exec(
                select(DocumentSegment)
                .where(col(DocumentSegment.id) == segment_id)
                .where(col(DocumentSegment.doc_id) == doc_id)
            ).first()
            session.commit()
        return segment

    def next_segment_position(self, doc_id: UUID) -> int:
        """追加分段的下一个 position（现有最大值 +1；空文档从 0 起）。"""
        with Session(self.engine, expire_on_commit=False) as session:
            current = session.exec(
                select(func.max(col(DocumentSegment.position))).where(
                    col(DocumentSegment.doc_id) == doc_id
                )
            ).one()
            session.commit()
        return (current or -1) + 1

    def append_segment(self, doc_id: UUID, segment: DocumentSegment) -> DocumentSegment:
        """追加分段：seg_num 与分段行同事务维护（与 doc_num 同一口径）。

        (doc_id, position) 唯一约束冲突（并发追加）时提交失败整体回滚，
        IntegrityError 由服务层映射为业务冲突。
        """
        with Session(self.engine, expire_on_commit=False) as session:
            doc = session.get(KnowledgeDocument, doc_id)
            if doc is not None:
                doc.seg_num += 1
            session.add(segment)
            session.commit()
            session.refresh(segment)
        return segment

    def update_segment_content(
        self, doc_id: UUID, segment_id: UUID, *, content: str, word_count: int
    ) -> DocumentSegment | None:
        with Session(self.engine, expire_on_commit=False) as session:
            segment = self._get_segment(session, doc_id, segment_id)
            if segment is None:
                session.commit()
                return None
            segment.content = content
            segment.word_count = word_count
            session.add(segment)
            session.commit()
            session.refresh(segment)
        return segment

    def set_segment_status(
        self, doc_id: UUID, segment_id: UUID, status: KnowledgeStatus
    ) -> DocumentSegment | None:
        with Session(self.engine, expire_on_commit=False) as session:
            segment = self._get_segment(session, doc_id, segment_id)
            if segment is None:
                session.commit()
                return None
            segment.status = status
            session.add(segment)
            session.commit()
            session.refresh(segment)
        return segment

    def delete_segment(self, doc_id: UUID, segment_id: UUID) -> bool:
        """删除单段：seg_num 与分段行同事务递减；向量清理由调用方负责。"""
        with Session(self.engine, expire_on_commit=False) as session:
            segment = self._get_segment(session, doc_id, segment_id)
            if segment is None:
                session.commit()
                return False
            doc = session.get(KnowledgeDocument, doc_id)
            if doc is not None:
                doc.seg_num = max(doc.seg_num - 1, 0)
            session.delete(segment)
            session.commit()
        return True

    def reset_document_for_rechunk(self, kb_id: UUID, doc_id: UUID) -> KnowledgeDocument | None:
        """重分段受理：稳定态（ready/enabled/disabled）文档原子置回 pending 并清错误信息。

        其余状态（pending/processing/deleting/failed）拒绝——pending/processing
        期间流水线正在重写分段集合，failed 应走 retry，deleting 即将消失。
        返回 None 表示状态不允许，由服务层映射为业务错误。
        """
        with Session(self.engine, expire_on_commit=False) as session:
            doc = session.exec(
                select(KnowledgeDocument)
                .where(col(KnowledgeDocument.id) == doc_id)
                .where(col(KnowledgeDocument.kb_id) == kb_id)
            ).first()
            if doc is None or doc.status not in (
                KnowledgeStatus.READY,
                KnowledgeStatus.ENABLED,
                KnowledgeStatus.DISABLED,
            ):
                session.commit()
                return None
            doc.status = KnowledgeStatus.PENDING
            doc.error_message = None
            session.add(doc)
            session.commit()
            session.refresh(doc)
        return doc

    @staticmethod
    def _get_segment(session: Session, doc_id: UUID, segment_id: UUID) -> DocumentSegment | None:
        return session.exec(
            select(DocumentSegment)
            .where(col(DocumentSegment.id) == segment_id)
            .where(col(DocumentSegment.doc_id) == doc_id)
        ).first()


# 落库的看门狗终结原因截断长度（与 domain support.ERROR_MESSAGE_MAX 同口径；
# repositories 禁反向依赖 services，故本地声明）
_ERROR_MESSAGE_MAX = 2000


def _as_utc(value: datetime) -> datetime:
    """把读回的时间归一为 aware UTC：SQLite 丢 tzinfo（naive 即 UTC 墙钟），PG timestamptz 保留 aware。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
