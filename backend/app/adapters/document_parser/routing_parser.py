"""按文件类型路由的解析器：后缀 → provider entry key 的分发装饰层。

domain 契约不变——本类仍实现 ``DocumentParser``，``DocumentIngestionService``
注入的默认实例即它（见 ``document_parser_factory.create_default_document_parser``）：
``parse`` 按文件名后缀查路由表选中具体 entry 的解析器再委派，路由未命中回退
default entry（等价于历史上「全交给默认解析器」的行为，旧部署无 routing 段
不受影响）。解析器实例按 entry key 缓存——各实现均为无状态轻封装，缓存只为
省去每次重建。
"""

import posixpath
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.adapters.document_parser.document_parser_provider import DocumentParser
from app.core.logging import LoggerFactory
from app.domain.ports import ParsedDocument

if TYPE_CHECKING:
    # 仅类型注解引用：document_parser_factory 反向导入本类，运行时互导会成环
    from app.adapters.document_parser.document_parser_factory import DocumentParserFactory


@dataclass
class FileTypeRoutingParser(DocumentParser):
    """文件后缀路由解析器：以文件类型为 key 在多个 provider 间分发。"""

    factory: "DocumentParserFactory"
    routing: dict[str, str]
    logger_factory: LoggerFactory
    _cache: dict = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self.logger = self.logger_factory.get_logger(__name__)

    def parse(self, data: bytes, filename: str) -> ParsedDocument:
        suffix = posixpath.splitext(filename)[1].lower()
        key = self.routing.get(suffix)
        if key is None:
            self.logger.warning(
                "no parser routing for suffix %r (file %r), falling back to default parser", suffix, filename
            )
            return self._parser_for(None).parse(data, filename)
        return self._parser_for(key).parse(data, filename)

    def _parser_for(self, key: str | None) -> DocumentParser:
        cache_key = key if key is not None else "__default__"
        parser = self._cache.get(cache_key)
        if parser is None:
            parser = self.factory.create() if key is None else self.factory.create(name=key)
            self._cache[cache_key] = parser
        return parser
