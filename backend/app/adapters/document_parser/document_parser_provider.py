"""文档解析供应商机制：builder 注册表 + 供应商枚举。

``DocumentParser`` 契约与 ``Parsed*`` 模型住 ``app/domain/ports``——端口
反转：domain（分块器/入库流水线）只依赖契约，本包（MinerU 云端实现）导入
契约来实现。本模块保留实现侧机件：``DocumentParserBuilder`` 抽象与
``@register`` 注册表，由 ``document_parser_factory`` 消费。
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import ClassVar

from app.core.config import DocumentParserProviderEntry
from app.domain.ports import DocumentParser

__all__ = [
    "DOCUMENT_PARSER_BUILDERS",
    "DocumentParser",
    "DocumentParserBuilder",
    "DocumentParserProvider",
    "register",
]


class DocumentParserProvider(str, Enum):
    """文档解析供应商标识，与 ``DocumentParserProviderEntry.type`` 对应。"""

    MINERU_CLOUD = "mineru_cloud"


class DocumentParserBuilder(ABC):
    """文档解析构建器：每个供应商一个子类，把 provider entry 翻译成契约实例。

    与 filesystem/vector 的两层构建不同，解析器无跨 collection 的重资源
    客户端可复用（HTTP 调用按次无状态），单层 ``build`` 即可。
    """

    provider: ClassVar[DocumentParserProvider]

    @abstractmethod
    def build(self, entry: DocumentParserProviderEntry) -> DocumentParser:
        """按 entry 构建 DocumentParser 契约实例。"""


# 供应商注册表：@register 自动登记，新增供应商不改工厂
DOCUMENT_PARSER_BUILDERS: dict[DocumentParserProvider, type[DocumentParserBuilder]] = {}


def register(builder_cls: type[DocumentParserBuilder]) -> type[DocumentParserBuilder]:
    DOCUMENT_PARSER_BUILDERS[builder_cls.provider] = builder_cls
    return builder_cls
