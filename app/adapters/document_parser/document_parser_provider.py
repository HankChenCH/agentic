from abc import ABC, abstractmethod
from enum import Enum
from typing import ClassVar

from app.core.config import DocumentParserProviderEntry
from app.infrastructures.document_parser.models import ParsedDocument


class DocumentParserProvider(str, Enum):
    """文档解析供应商标识，与 ``DocumentParserProviderEntry.type`` 对应。"""

    MINERU_CLOUD = "mineru_cloud"


class DocumentParser(ABC):
    """统一文档解析契约：原始文件字节 → 归一化结构（blocks + assets + md）。

    接口为同步阻塞——调用方是 Celery 后台任务（见 ``app/tasks/knowledge.py``），
    解析耗时（云端排队/轮询）天然属于任务时长。实现负责把供应商原始输出
    归一化为 :class:`~app.infrastructures.document_parser.models.ParsedDocument`，
    格式差异（content_list 版本、bbox 坐标系）不出 provider 边界。
    """

    @abstractmethod
    def parse(self, data: bytes, filename: str) -> ParsedDocument:
        """解析文档字节，filename 用于供应商侧的格式识别与结果文件定位。"""


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
