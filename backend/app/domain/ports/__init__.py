"""共享基础设施契约（端口）：协议住 domain，实现住 adapters。

这里的抽象是纯契约——零供应商依赖、零机制细节；adapters 侧导入本包实现
（如 ``adapters/filesystem`` 的 local/s3 实现 ``Filesystem``）。domain 与
components 只依赖本包，不感知实现差异。聚合私有端口（仓储/向量索引/目录
服务等）住在各聚合的 ``ports.py``，仅跨聚合共用的基础契约进本包。
"""

from .filesystem import Filesystem
from .parsing import (
    DocumentParseError,
    DocumentParser,
    ParsedBlock,
    ParsedBlockType,
    ParsedDocument,
)

class RepositoryConflictError(Exception):
    """仓储契约级冲突信号：唯一约束等并发窗口的驱动无关翻译。

    领域服务捕获本错误并翻译为对应业务异常；驱动的原生异常
    （如 sqlalchemy 的 IntegrityError）由 adapters/persistence 在仓储
    方法内收口翻译，不出适配器边界。
    """


__all__ = [
    "Filesystem",
    "RepositoryConflictError",
    "DocumentParseError",
    "DocumentParser",
    "ParsedBlock",
    "ParsedBlockType",
    "ParsedDocument",
]
