"""文件类型路由：后缀分发、未命中回退、实例缓存与 routing 配置契约。"""

import pytest
from pydantic import ValidationError

from app.adapters.document_parser.document_parser_factory import DocumentParserFactory
from app.adapters.document_parser.local_office_provider import LocalDocxParser, LocalXlsxParser
from app.adapters.document_parser.routing_parser import FileTypeRoutingParser
from app.core.config import AppConfig, DocumentParserConfig, MineruCloudEntry
from app.core.logging import LoggerFactory
from app.domain.knowledge.support import ALLOWED_UPLOAD_SUFFIXES
from app.domain.ports import DocumentParser, ParsedDocument


class RecordingParser(DocumentParser):
    """解析桩：记录 parse 入参，返回可辨认的结果。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[tuple[bytes, str]] = []

    def parse(self, data: bytes, filename: str) -> ParsedDocument:
        self.calls.append((data, filename))
        return ParsedDocument(blocks=[], md_content=f"parsed-by-{self.name}")


class StubFactory:
    """工厂桩：记录 create 调用（FileTypeRoutingParser 只依赖 create 签名）。"""

    def __init__(self) -> None:
        self.created: list[str | None] = []
        self._parsers: dict[str, RecordingParser] = {}

    def create(self, *, name: str | None = None) -> DocumentParser:
        self.created.append(name)
        key = name or "__default__"
        if key not in self._parsers:
            self._parsers[key] = RecordingParser(key)
        return self._parsers[key]


def make_router(routing: dict[str, str]) -> tuple[FileTypeRoutingParser, StubFactory]:
    factory = StubFactory()
    router = FileTypeRoutingParser(factory=factory, routing=routing, logger_factory=LoggerFactory())
    return router, factory


def test_parse_dispatches_by_suffix():
    router, factory = make_router({".pdf": "pdf-parser", ".xlsx": "xlsx-parser"})
    assert router.parse(b"x", "报告.PDF").md_content == "parsed-by-pdf-parser"  # 后缀大小写归一
    assert router.parse(b"y", "表格.xlsx").md_content == "parsed-by-xlsx-parser"
    assert factory.created == ["pdf-parser", "xlsx-parser"]


def test_parse_falls_back_to_default_when_suffix_unrouted():
    router, factory = make_router({".pdf": "pdf-parser"})
    assert router.parse(b"x", "未知.abc").md_content == "parsed-by-__default__"
    assert factory.created == [None]


def test_parser_instances_cached_per_key():
    router, factory = make_router({".pdf": "pdf-parser"})
    router.parse(b"1", "a.pdf")
    router.parse(b"2", "b.pdf")
    router.parse(b"3", "c.abc")  # default 实例同样缓存
    assert factory.created == ["pdf-parser", None]


# ---------- routing 配置契约 ----------


def make_config(routing: dict[str, str], providers: dict | None = None) -> DocumentParserConfig:
    return DocumentParserConfig(
        default="p1",
        providers=providers or {"p1": MineruCloudEntry()},
        routing=routing,
    )


def test_config_routing_normalizes_suffix_keys():
    config = make_config({"PDF": "p1", " xlsx ": "p1"})
    assert config.routing == {".pdf": "p1", ".xlsx": "p1"}


def test_config_routing_rejects_unknown_provider():
    with pytest.raises(ValidationError, match="unknown provider"):
        make_config({".pdf": "nope"})


def test_config_routing_rejects_duplicate_after_normalization():
    with pytest.raises(ValidationError, match="duplicate"):
        make_config({".pdf": "p1", "pdf": "p1"})


def test_default_app_config_routing_covers_upload_allowlist():
    """一致性守卫：出厂配置的 routing 必须覆盖上传白名单——白名单放开的
    格式若没有路由，解析会静默回退 default（MinerU）并在解析期失败。"""
    routing = AppConfig().document_parser.routing
    assert set(routing) >= ALLOWED_UPLOAD_SUFFIXES


def test_factory_builds_local_parsers_from_config():
    """出厂配置的 local-xlsx / local-docx entry 经工厂产出对应解析器。"""
    factory = DocumentParserFactory(app_config=AppConfig())
    assert isinstance(factory.create(name="local-xlsx"), LocalXlsxParser)
    assert isinstance(factory.create(name="local-docx"), LocalDocxParser)
