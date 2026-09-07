"""按文件类型路由的解析器：后缀 → 解析器链（回退链）的分发装饰层。

domain 契约不变——本类仍实现 ``DocumentParser``，``DocumentIngestionService``
注入的默认实例即它（见 ``document_parser_factory.create_default_document_parser``）：
``parse`` 按文件名后缀查路由表得到有序解析器链，按序尝试——前序解析器
``DocumentParseError``（永久性失败，如本地库读不了损坏/怪异文件）或**产出
为空**（无可索引内容，如纯图表 sheet）时升级下一个（如本地失败兜底 MinerU
云端）；命中即返回。

回退边界是刻意的：``InfrastructureError`` 等瞬态故障不触发回退、如实上抛
——Celery autoretry 会重跑全链，静默降级只会掩盖机制故障。全链穷尽时：
最后一次尝试是异常则原样上抛，否则抛 ``DocumentParseError`` 注明整链无产出。
路由未命中回退 default entry（等价于历史上「全交给默认解析器」的行为）。
解析器实例按 entry key 缓存——各实现均为无状态轻封装，缓存只为省去每次重建。
"""

import posixpath
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.adapters.document_parser.document_parser_provider import DocumentParser
from app.core.logging import LoggerFactory
from app.domain.ports import DocumentParseError, ParsedDocument

if TYPE_CHECKING:
    # 仅类型注解引用：document_parser_factory 反向导入本类，运行时互导会成环
    from app.adapters.document_parser.document_parser_factory import DocumentParserFactory


@dataclass
class FileTypeRoutingParser(DocumentParser):
    """文件后缀路由解析器：以文件类型为 key 在解析器链间分发与回退。"""

    factory: "DocumentParserFactory"
    routing: dict[str, list[str]]
    logger_factory: LoggerFactory
    _cache: dict = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self.logger = self.logger_factory.get_logger(__name__)
        # 容忍 str 值（配置校验器之外的直接构造路径）：统一归一为链，parse 无需再分支
        self.routing = {
            suffix: ([value] if isinstance(value, str) else list(value))
            for suffix, value in self.routing.items()
        }

    def parse(self, data: bytes, filename: str) -> ParsedDocument:
        suffix = posixpath.splitext(filename)[1].lower()
        chain = self.routing.get(suffix)
        if chain is None:
            self.logger.warning(
                "no parser routing for suffix %r (file %r), falling back to default parser", suffix, filename
            )
            return self._parser_for(None).parse(data, filename)
        last_error: DocumentParseError | None = None
        for key in chain:
            try:
                parsed = self._parser_for(key).parse(data, filename)
            except DocumentParseError as exc:
                last_error = exc
                self.logger.warning(
                    "parser %r failed for %r (%s), falling back to next in chain", key, filename, exc
                )
                continue
            if not parsed.blocks:
                last_error = DocumentParseError(f"parser '{key}' produced no indexable content for {filename!r}")
                self.logger.warning("%s, falling back to next in chain", last_error)
                continue
            return parsed
        if last_error is not None:
            raise last_error
        raise DocumentParseError(
            f"no parser in the routing chain for suffix {suffix!r} produced indexable content: {chain}"
        )

    def _parser_for(self, key: str | None) -> DocumentParser:
        cache_key = key if key is not None else "__default__"
        parser = self._cache.get(cache_key)
        if parser is None:
            parser = self.factory.create() if key is None else self.factory.create(name=key)
            self._cache[cache_key] = parser
        return parser
