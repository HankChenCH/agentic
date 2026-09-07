from dataclasses import dataclass

from wireup import injectable

from app.core.config import AppConfig, DocumentParserConfig, DocumentParserProviderEntry
from app.adapters.document_parser.document_parser_provider import (
    DOCUMENT_PARSER_BUILDERS,
    DocumentParser,
    DocumentParserBuilder,
    DocumentParserProvider,
)
from app.adapters.document_parser.routing_parser import FileTypeRoutingParser
from app.core.logging import LoggerFactory

import app.adapters.document_parser.mineru_cloud_provider  # noqa: F401  触发 @register 供应商注册
import app.adapters.document_parser.local_office_provider  # noqa: F401  触发 @register 供应商注册


@injectable
@dataclass
class DocumentParserFactory:
    """文档解析工厂：按 provider entry key 创建 DocumentParser 契约实例。

    契约类型为 :class:`~app.adapters.document_parser.document_parser_provider.DocumentParser`
    （``parse(bytes, filename) -> ParsedDocument``），消费方（知识库入库
    流水线）只依赖该抽象，不感知云端 / 自部署的差异。

    解析器为按次调用的无状态 HTTP 封装，不做客户端级缓存。默认宽松、
    显式严格：未指定 name 时用 ``DocumentParserConfig.default``；显式指定
    但 providers 里不存在的 key，或 entry 的供应商 type 未注册构建器，
    则直接抛 ValueError。lazy：构造不发网络请求。
    """

    app_config: AppConfig

    def create(self, *, name: str | None = None) -> DocumentParser:
        """按 provider entry key 创建文档解析实例。"""
        key = name if name is not None else self._config.default
        entry = self._config.providers.get(key)
        if entry is None:
            raise ValueError(
                f"unknown document parser provider: {key}, configured: {list(self._config.providers)}"
            )
        builder = self._builder_for(key, entry)
        return builder.build(entry)

    @property
    def _config(self) -> DocumentParserConfig:
        return self.app_config.document_parser

    def _builder_for(self, key: str, entry: DocumentParserProviderEntry) -> DocumentParserBuilder:
        try:
            provider = DocumentParserProvider(entry.type)
        except ValueError:
            supported = [p.value for p in DocumentParserProvider]
            raise ValueError(
                f"unsupported document parser provider type: {entry.type} (entry: {key}), supported: {supported}"
            )
        return DOCUMENT_PARSER_BUILDERS[provider]()


@injectable(lifetime="singleton")
def create_default_document_parser(
    factory: DocumentParserFactory, logger_factory: LoggerFactory
) -> DocumentParser:
    """默认文档解析实例（``DocumentParserConfig.default``）的注入入口。

    返回按文件类型路由的解析器：``routing`` 段把文件后缀映射到 provider
    entry（如 ``.pdf → mineru-cloud``），未命中回退 default entry——消费方
    （知识库入库流水线）仍只注入 ``DocumentParser`` 契约，不感知路由与
    云端 / 本地的差异。需要指定其他 entry 时注入 ``DocumentParserFactory``
    自行 ``create(name=...)``。
    """
    routing = factory.app_config.document_parser.routing
    return FileTypeRoutingParser(factory=factory, routing=dict(routing), logger_factory=logger_factory)
