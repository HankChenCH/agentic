"""知识库对象存储适配器：统一 key 布局与 best-effort 清理语义。

``knowledge/{kb_id}/{doc_id}/...`` 布局是知识库域私有约定（doc_path 落库
后不可变，后台流水线以此 key 读回原始文件），集中在此避免字符串拼接
散落各服务；best-effort 删除/转存的告警日志也随之收敛到一处。
"""

import posixpath
from dataclasses import dataclass
from typing import BinaryIO
from uuid import UUID

from wireup import injectable

from app.core.logging import LoggerFactory
from app.domain.ports import Filesystem

# 预签名 GET 的 TTL：与 conversation 附件域同口径（600s）
PRESIGN_TTL_SECONDS = 600


@injectable
@dataclass
class KnowledgeObjectStore:
    filesystem: Filesystem
    logger_factory: LoggerFactory

    def __post_init__(self):
        self.logger = self.logger_factory.get_logger(__name__)

    # ---------- key 布局 ----------

    @staticmethod
    def kb_prefix(kb_id: UUID) -> str:
        return f"knowledge/{kb_id}/"

    @staticmethod
    def doc_prefix(kb_id: UUID, doc_id: UUID) -> str:
        return f"knowledge/{kb_id}/{doc_id}/"

    @staticmethod
    def document_key(kb_id: UUID, doc_id: UUID, filename: str) -> str:
        return f"knowledge/{kb_id}/{doc_id}/{filename}"

    @staticmethod
    def asset_key(kb_id: UUID, doc_id: UUID, name: str) -> str:
        # 解析产物资产名（zip 相对路径）取 basename 防路径逃逸
        return f"knowledge/{kb_id}/{doc_id}/assets/{posixpath.basename(name)}"

    @staticmethod
    def derived_markdown_key(kb_id: UUID, doc_id: UUID) -> str:
        return f"knowledge/{kb_id}/{doc_id}/derived/content.md"

    # ---------- 对象操作 ----------

    def presign(self, key: str, expires_in: int = PRESIGN_TTL_SECONDS) -> str | None:
        """预签名 GET（浏览器凭签名直拉对象存储，绕过后端字节中转）。

        存储后端不具备签名能力（本地磁盘）返回 ``None``，调用方降级
        ``read()`` 由后端回源——与会话附件域同一降级口径。
        """
        return self.filesystem.presign_get(key, expires_in)

    def read(self, key: str) -> bytes:
        return self.filesystem.read(key)

    def put(self, key: str, data: bytes | BinaryIO) -> None:
        self.filesystem.put(key, data)

    def put_best_effort(self, key: str, data: bytes | BinaryIO) -> None:
        # 转存 best-effort：失败不阻断主流程，缺产物只影响展示不影响检索
        try:
            self.filesystem.put(key, data)
        except Exception:
            self.logger.warning("failed to store object %s", key, exc_info=True)

    def delete_best_effort(self, key: str) -> None:
        # 对象清理 best-effort：失败只告警不回滚（孤儿对象可后续对账清理）
        try:
            self.filesystem.delete(key)
        except Exception:
            self.logger.warning("failed to delete object %s", key, exc_info=True)

    def delete_prefix_best_effort(self, prefix: str) -> None:
        # 前缀清理 best-effort：列出后逐个删除，列取失败只告警
        try:
            keys = list(self.filesystem.list(prefix))
        except Exception:
            self.logger.warning("failed to list objects under prefix %s", prefix, exc_info=True)
            return
        for key in keys:
            self.delete_best_effort(key)
