"""本地 Markdown 解析供应商：行级块解析，LangChain loader 范式组装。

与 office 本地解析同一套组装链：文件字节 → ``BaseLoader`` 产元素级
``Document``（category 契约与元素→块映射复用 ``local_office_provider`` 的
共享机件）→ ``ParsedDocument``。解析为纯 Python 行级扫描（零第三方依赖、
零新机制），只做块级粒度——标题层级/列表/围栏代码/管道表/独立图片行，
行内语法（加粗/链接等）原样保留在文本里（分块嵌入时 markdown 结构标记
本身就是有效的检索信号）。

MinerU 云端不支持 markdown，故 ``.md`` 的路由链只有本地一个元素（纯配置
语义，见 document_parser.yaml）。``md_content`` 直接透传原文——markdown
本身即预览形态。字节按 UTF-8 解码（errors="replace"），本解析器不会因
内容抛错；无可索引内容的空文件由入库流水线统一拒绝。
"""

import re
from collections.abc import Iterator

from langchain_core.document_loaders.base import BaseLoader
from langchain_core.documents import Document

from app.adapters.document_parser.document_parser_provider import (
    DocumentParser,
    DocumentParserBuilder,
    DocumentParserProvider,
    register,
)
from app.adapters.document_parser.local_office_provider import (
    CATEGORY_CODE,
    CATEGORY_IMAGE,
    CATEGORY_LIST_ITEM,
    CATEGORY_NARRATIVE,
    CATEGORY_TABLE,
    CATEGORY_TITLE,
    blocks_from_elements,
    html_table_from_rows,
)
from app.core.config import LocalMarkdownEntry
from app.domain.ports import ParsedDocument

# ATX 标题：#{1..6} + 空格 + 文本
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
# 列表项：- / * / + / 1. / 1)（嵌套缩进拍平，仅保留内容）
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
# 独立图片行：![alt](src)（外链不转存，仅 alt 文本可嵌入）
_IMAGE_RE = re.compile(r"^\s*!\[([^\]]*)\]\(([^)]*)\)\s*$")
# 水平线：--- / *** / ___（不产块）
_HR_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
# 管道表分隔行：| --- | :---: | ...
_TABLE_DELIMITER_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
# 围栏代码块：``` 或 ~~~ 开头，info 串为语言提示
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})\s*(\S*)\s*$")


class _MarkdownElementLoader(BaseLoader):
    """markdown 文本 → 元素 Document 流：按行扫描，块级粒度。

    各 ``_emit_*`` 收集器返回 ``(该块产出的 Document 列表, 消费到的行号)``。
    """

    def __init__(self, text: str) -> None:
        self._lines = text.splitlines()

    def lazy_load(self) -> Iterator[Document]:
        lines = self._lines
        index = 0
        while index < len(lines):
            line = lines[index]
            heading = _HEADING_RE.match(line)
            if heading:
                yield Document(
                    page_content=heading.group(2).strip(),
                    metadata={"category": CATEGORY_TITLE, "depth": len(heading.group(1)), "page_idx": 0},
                )
                index += 1
                continue
            fence = _FENCE_RE.match(line)
            if fence:
                documents, index = self._collect_code_fence(lines, index, fence.group(1), fence.group(2))
                yield from documents
                continue
            if self._is_table_start(lines, index):
                document, index = self._collect_table(lines, index)
                yield document
                continue
            image = _IMAGE_RE.match(line)
            if image:
                yield Document(
                    page_content="",
                    metadata={"category": CATEGORY_IMAGE, "caption": image.group(1).strip(), "page_idx": 0},
                )
                index += 1
                continue
            if _HR_RE.match(line):
                index += 1
                continue
            if _LIST_ITEM_RE.match(line):
                document, index = self._collect_list_run(lines, index)
                yield document
                continue
            if line.strip():
                document, index = self._collect_paragraph(lines, index)
                if document.page_content:
                    yield document
                continue
            index += 1  # 空行

    # ---------- 各类块收集 ----------

    def _collect_code_fence(
        self, lines: list[str], start: int, marker: str, info: str
    ) -> tuple[list[Document], int]:
        body: list[str] = []
        index = start + 1
        while index < len(lines) and not lines[index].lstrip().startswith(marker[:3]):
            body.append(lines[index])
            index += 1
        index += 1  # 跳过闭合围栏；未闭合块按到文件尾计（此时越界即退出）
        language = info.strip()
        if not body and not language:
            return [], index  # 空围栏不产块
        metadata: dict = {"category": CATEGORY_CODE, "page_idx": 0}
        if language:
            # 语言进题注参与嵌入；sub_type 透传保留原始 info 串
            metadata["caption"] = f"代码块（{language}）"
            metadata["sub_type"] = language
        document = Document(page_content="\n".join(body).rstrip(), metadata=metadata)
        return [document], index

    def _collect_table(self, lines: list[str], start: int) -> tuple[Document, int]:
        header = self._split_table_row(lines[start])
        index = start + 2  # 跳过表头与分隔行
        body_rows: list[list[str]] = []
        while index < len(lines) and lines[index].strip() and "|" in lines[index]:
            body_rows.append(self._split_table_row(lines[index]))
            index += 1
        width = max(len(header), max((len(row) for row in body_rows), default=0))
        document = Document(
            page_content="",
            metadata={
                "category": CATEGORY_TABLE,
                "html": html_table_from_rows(header, body_rows, width),
                "page_idx": 0,
            },
        )
        return document, index

    def _collect_list_run(self, lines: list[str], start: int) -> tuple[Document, int]:
        items: list[str] = []
        index = start
        while index < len(lines) and (match := _LIST_ITEM_RE.match(lines[index])):
            items.append(match.group(1).strip())
            index += 1
        document = Document(
            page_content="\n".join(items),
            metadata={"category": CATEGORY_LIST_ITEM, "page_idx": 0},
        )
        return document, index

    def _collect_paragraph(self, lines: list[str], start: int) -> tuple[Document, int]:
        # 段落/引用：聚合到空行或下一个结构性块为止；引用行剥掉 "> " 标记
        parts: list[str] = []
        index = start
        while index < len(lines):
            line = lines[index]
            if not line.strip() or self._is_structural(lines, index):
                break
            parts.append(_strip_blockquote(line))
            index += 1
        document = Document(
            page_content="\n".join(parts).strip(),
            metadata={"category": CATEGORY_NARRATIVE, "page_idx": 0},
        )
        return document, index

    # ---------- 判定 ----------

    def _is_table_start(self, lines: list[str], index: int) -> bool:
        if index + 1 >= len(lines):
            return False
        return "|" in lines[index] and "-" in lines[index + 1] and bool(_TABLE_DELIMITER_RE.match(lines[index + 1]))

    def _is_structural(self, lines: list[str], index: int) -> bool:
        line = lines[index]
        return bool(
            _HEADING_RE.match(line)
            or _FENCE_RE.match(line)
            or _IMAGE_RE.match(line)
            or _HR_RE.match(line)
            or _LIST_ITEM_RE.match(line)
            or self._is_table_start(lines, index)
        )

    @staticmethod
    def _split_table_row(line: str) -> list[str]:
        # 行内转义竖线先占位再还原；边界管道产生的首尾空格剔除
        escaped = line.strip().replace("\\|", "\x00")
        return [cell.strip().replace("\x00", "|") for cell in escaped.strip("|").split("|")]


def _strip_blockquote(line: str) -> str:
    return re.sub(r"^\s*>\s?", "", line)


class LocalMarkdownParser(DocumentParser):
    """markdown 本地解析：行级 loader 产元素，映射为归一化块。"""

    def __init__(self, entry: LocalMarkdownEntry) -> None:
        del entry  # 无可调参数，entry 仅为构建器契约占位

    def parse(self, data: bytes, filename: str) -> ParsedDocument:
        # errors="replace"：任意字节都可解析，本供应商不产永久性失败
        text = data.decode("utf-8", errors="replace")
        blocks = blocks_from_elements(_MarkdownElementLoader(text).lazy_load())
        return ParsedDocument(blocks=blocks, md_content=text or None)


@register
class LocalMarkdownBuilder(DocumentParserBuilder):
    provider = DocumentParserProvider.LOCAL_MARKDOWN

    def build(self, entry: LocalMarkdownEntry) -> DocumentParser:
        return LocalMarkdownParser(entry)
