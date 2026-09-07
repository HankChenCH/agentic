"""本地 Office 文档解析供应商（xlsx / docx）：LangChain loader 范式组装。

组装链路：文件字节 → LangChain ``BaseLoader`` 产出元素级 ``Document``
（category/标题层级/表体 HTML 等结构化元数据）→ 元素映射为 domain 的
``ParsedBlock`` → ``ParsedDocument``。读取原语用 openpyxl / python-docx
（纯 Python、进程内执行，无外部服务与密钥）；供应商注册与构建沿用
``document_parser_provider`` 的 builder 注册表，文件类型 → 本 provider 的
选择由 ``routing_parser`` 按配置路由表完成。

切分不在此展开：标题感知分段由 domain 的 ``document_chunker``（其本身即
splitter）统一承担，本文件只对超大表格做行窗口控制（``rows_per_block``），
保证单个 TABLE 块体量可嵌入——行级窗口保 HTML 标记完整，不能用字符级
splitter 破坏表格结构。

错误语义：文件损坏 / 字节与后缀不符属永久性失败，抛 ``DocumentParseError``
（业务错误，Celery 不 autoretry）；其余异常如实上抛。
"""

import html
import io
import re
from collections.abc import Iterable, Iterator

from docx import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import Table as DocxTable
from docx.table import _Cell
from docx.text.paragraph import Paragraph
from langchain_core.document_loaders.base import BaseLoader
from langchain_core.documents import Document
from openpyxl import load_workbook

from app.adapters.document_parser.document_parser_provider import (
    DocumentParser,
    DocumentParserBuilder,
    DocumentParserProvider,
    register,
)
from app.core.config import LocalDocxEntry, LocalXlsxEntry
from app.domain.ports import DocumentParseError, ParsedBlock, ParsedBlockType, ParsedDocument

# docx/xlsx/markdown 本地解析器共用的元素 category 契约（loader 产出、
# blocks_from_elements 消费的中间表示）
CATEGORY_TITLE = "Title"
CATEGORY_LIST_ITEM = "ListItem"
CATEGORY_TABLE = "Table"
CATEGORY_NARRATIVE = "NarrativeText"
CATEGORY_CODE = "Code"
CATEGORY_IMAGE = "Image"

# 内建标题样式的稳定标识（display name 随文档语言变化，style_id 不变）
_HEADING_STYLE_RE = re.compile(r"^Heading(\d+)$", re.IGNORECASE)


# ---------- xlsx：openpyxl read_only 流式读取，逐 sheet 产标题 + 行窗口表格 ----------


class _XlsxElementLoader(BaseLoader):
    """xlsx → 元素 Document 流：每个非空工作表一个标题块 + N 个表格块。"""

    def __init__(self, data: bytes, *, rows_per_block: int, max_rows: int, max_cols: int) -> None:
        self._data = data
        self._rows_per_block = rows_per_block
        self._max_rows = max_rows
        self._max_cols = max_cols

    def lazy_load(self) -> Iterator[Document]:
        try:
            workbook = load_workbook(io.BytesIO(self._data), read_only=True, data_only=True)
        except Exception as exc:
            raise DocumentParseError(f"invalid xlsx file: {exc}") from exc
        try:
            for sheet_index, sheet_name in enumerate(workbook.sheetnames):
                yield from self._sheet_documents(workbook[sheet_name], sheet_index, sheet_name)
        finally:
            workbook.close()

    def _sheet_documents(self, worksheet, sheet_index: int, sheet_name: str) -> list[Document]:
        raw_rows: list[list] = [list(row) for row in worksheet.iter_rows(values_only=True)]
        # 行列尾部裁剪：read_only 模式的 dimension 可能虚胖（脏文件），按实读收敛
        raw_width = max((len(row) for row in raw_rows), default=0)
        width = min(raw_width, self._max_cols)
        rows = [row[:width] for row in raw_rows]
        while rows and not _row_has_content(rows[-1]):
            rows.pop()
        while rows and not _row_has_content(rows[0]):
            rows.pop(0)
        if not rows:
            return []  # 空表（或仅图表）不产块：避免裸标题混进邻段面包屑

        total_data = len(rows) - 1  # 首行视为表头，行数口径与 caption 一致
        notes = []
        if total_data > self._max_rows:
            notes.append(f"已截断：仅读取前 {self._max_rows} 行（共 {total_data} 行）")
        if raw_width > self._max_cols:
            notes.append(f"已截断：仅读取前 {self._max_cols} 列")

        sheet_title = Document(
            page_content=sheet_name,
            metadata={"category": CATEGORY_TITLE, "depth": 1, "page_idx": sheet_index},
        )
        header = rows[0]
        body_rows = rows[1 : self._max_rows + 1]
        # 仅表头的 sheet 也产出整表（thead）块，不静默丢内容
        windows = [body_rows[i : i + self._rows_per_block] for i in range(0, len(body_rows), self._rows_per_block)]
        if not windows:
            windows = [[]]
        documents = [sheet_title]
        for offset, window in enumerate(windows):
            start_row = offset * self._rows_per_block + 1  # 数据行号（1 起，不含表头）
            if window:
                caption = (
                    f"工作表「{sheet_name}」第 {start_row}–{start_row + len(window) - 1} 行"
                    f"（共 {total_data} 行数据）"
                )
            else:
                caption = f"工作表「{sheet_name}」仅表头，无数据行"
            if notes:
                caption += "；" + "；".join(notes)
            documents.append(
                Document(
                    page_content="",
                    metadata={
                        "category": CATEGORY_TABLE,
                        "html": html_table_from_rows(header, window, width),
                        "caption": caption,
                        "page_idx": sheet_index,
                    },
                )
            )
        return documents


def _row_has_content(row: list) -> bool:
    return any(str(cell).strip() for cell in row if cell is not None)


def _cell_text(value) -> str:
    """单元格值 → 展示文本；None 归空串，整数化浮点去掉 .0 尾巴。"""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def html_table_from_rows(header: list, body_rows: list[list], width: int) -> str:
    """行矩阵 → 完整 HTML 表格（xlsx sheet 窗口与 markdown 管道表共用）。"""

    def row_html(cells: list, tag: str) -> str:
        padded = list(cells[:width]) + [""] * (width - len(cells))
        inner = "".join(f"<{tag}>{html.escape(_cell_text(cell))}</{tag}>" for cell in padded)
        return f"<tr>{inner}</tr>"

    thead = row_html(header, "th")
    tbody = "".join(row_html(row, "td") for row in body_rows)
    return f"<table><thead>{thead}</thead><tbody>{tbody}</tbody></table>"


# ---------- docx：python-docx 按阅读序遍历 body，标题/正文/列表/表格 ----------


class _DocxElementLoader(BaseLoader):
    """docx → 元素 Document 流：w:p 与 w:tbl 交错按文档顺序产出。"""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def lazy_load(self) -> Iterator[Document]:
        try:
            document = DocxDocument(io.BytesIO(self._data))
        except Exception as exc:
            raise DocumentParseError(f"invalid docx file: {exc}") from exc
        for child in document.element.body.iterchildren():
            if child.tag == qn("w:p"):
                yield from self._paragraph_documents(Paragraph(child, document))
            elif child.tag == qn("w:tbl"):
                table_html = _docx_table_html(DocxTable(child, document))
                if table_html:
                    yield Document(page_content="", metadata={"category": CATEGORY_TABLE, "html": table_html, "page_idx": 0})
            # 其余 body 子元素（sectPr/bookmark 等）不产块

    def _paragraph_documents(self, paragraph: Paragraph) -> list[Document]:
        text = paragraph.text.strip()
        if not text:
            return []
        category, depth = self._classify(paragraph)
        metadata: dict = {"category": category, "page_idx": 0}
        if depth is not None:
            metadata["depth"] = depth
        return [Document(page_content=text, metadata=metadata)]

    @staticmethod
    def _classify(paragraph: Paragraph) -> tuple[str, int | None]:
        """按样式稳定标识（style_id）归类：display name 随语言漂移不可靠。"""
        try:
            style_id = paragraph.style.style_id or ""
        except Exception:  # 样式表引用缺失等：按正文处理，不炸解析
            style_id = ""
        heading = _HEADING_STYLE_RE.match(style_id)
        if heading:
            return CATEGORY_TITLE, int(heading.group(1))
        if style_id == "Title":
            return CATEGORY_TITLE, 1
        if style_id.startswith("List") or (
            paragraph._p.pPr is not None and paragraph._p.pPr.numPr is not None
        ):
            return CATEGORY_LIST_ITEM, None
        return CATEGORY_NARRATIVE, None


def _docx_table_html(table: DocxTable) -> str:
    """docx 表格 → HTML：gridSpan 映射 colspan（精确）；vMerge 拍平——
    锚点单元格持值，续接格渲染为空 td（保矩形结构，损失可接受）。"""

    def cell_html(cell: _Cell, span: int) -> str:
        text = html.escape(cell.text.strip())
        text = text.replace("\n", "<br/>")
        colspan = f' colspan="{span}"' if span > 1 else ""
        return f"<td{colspan}>{text}</td>"

    rows: list[str] = []
    for row in table.rows:
        cells: list[str] = []
        for tc in row._tr.tc_lst:
            span = max(int(getattr(tc, "grid_span", 1) or 1), 1)
            if tc.vMerge == "continue":
                cells.append("<td></td>" * span)
            else:
                cells.append(cell_html(_Cell(tc, table), span))
        if cells:
            rows.append(f"<tr>{''.join(cells)}</tr>")
    if not rows:
        return ""
    body = "".join(rows)
    return f"<table><tbody>{body}</tbody></table>"


# ---------- 元素 → ParsedBlock 映射与 md 合成（office/markdown 本地解析器共用） ----------


def blocks_from_elements(elements: Iterable[Document]) -> list[ParsedBlock]:
    blocks: list[ParsedBlock] = []
    for element in elements:
        category = element.metadata.get("category", CATEGORY_NARRATIVE)
        page_idx = int(element.metadata.get("page_idx") or 0)
        if category == CATEGORY_TABLE:
            blocks.append(
                ParsedBlock(
                    type=ParsedBlockType.TABLE,
                    html=element.metadata.get("html"),
                    captions=[element.metadata["caption"]] if element.metadata.get("caption") else [],
                    page_idx=page_idx,
                )
            )
        elif category == CATEGORY_TITLE:
            blocks.append(
                ParsedBlock(
                    type=ParsedBlockType.TEXT,
                    text=element.page_content,
                    text_level=max(int(element.metadata.get("depth") or 1), 1),
                    page_idx=page_idx,
                )
            )
        elif category == CATEGORY_LIST_ITEM:
            blocks.append(ParsedBlock(type=ParsedBlockType.LIST, text=element.page_content, page_idx=page_idx))
        elif category == CATEGORY_CODE:
            blocks.append(
                ParsedBlock(
                    type=ParsedBlockType.CODE,
                    text=element.page_content,
                    captions=[element.metadata["caption"]] if element.metadata.get("caption") else [],
                    sub_type=element.metadata.get("sub_type"),
                    page_idx=page_idx,
                )
            )
        elif category == CATEGORY_IMAGE:
            # markdown 的独立图片行：仅 alt 文本可嵌入，无字节资产（外链不转存）
            blocks.append(
                ParsedBlock(
                    type=ParsedBlockType.IMAGE,
                    text="",
                    captions=[element.metadata["caption"]] if element.metadata.get("caption") else [],
                    page_idx=page_idx,
                )
            )
        else:
            blocks.append(ParsedBlock(type=ParsedBlockType.TEXT, text=element.page_content, page_idx=page_idx))
    return blocks


def _markdown(blocks: list[ParsedBlock]) -> str | None:
    """由块合成全文 Markdown（预览用，best-effort；表格保留 HTML 形态）。"""
    parts: list[str] = []
    for block in blocks:
        if block.type is ParsedBlockType.TEXT:
            prefix = f"{'#' * block.text_level} " if block.text_level >= 1 else ""
            parts.append(prefix + block.text.strip())
        elif block.type is ParsedBlockType.LIST:
            parts.append("- " + block.text.strip())
        elif block.type is ParsedBlockType.TABLE and block.html:
            parts.append("\n\n".join(p for p in ("\n".join(block.captions), block.html) if p))
    markdown = "\n\n".join(p for p in parts if p.strip())
    return markdown or None


class LocalXlsxParser(DocumentParser):
    """xlsx 本地解析：LangChain loader 产元素，映射为归一化块。"""

    def __init__(self, entry: LocalXlsxEntry) -> None:
        self._entry = entry

    def parse(self, data: bytes, filename: str) -> ParsedDocument:
        loader = _XlsxElementLoader(
            data,
            rows_per_block=self._entry.rows_per_block,
            max_rows=self._entry.max_rows_per_sheet,
            max_cols=self._entry.max_cols,
        )
        try:
            elements = list(loader.lazy_load())
        except DocumentParseError:
            raise
        except Exception as exc:  # 迭代途中的脏值（怪异单元格类型等）同归永久性失败
            raise DocumentParseError(f"failed to parse xlsx file {filename!r}: {exc}") from exc
        blocks = blocks_from_elements(elements)
        return ParsedDocument(blocks=blocks, md_content=_markdown(blocks))


class LocalDocxParser(DocumentParser):
    """docx 本地解析：LangChain loader 产元素，映射为归一化块。"""

    def __init__(self, entry: LocalDocxEntry) -> None:
        del entry  # 无可调参数，entry 仅为构建器契约占位

    def parse(self, data: bytes, filename: str) -> ParsedDocument:
        try:
            elements = list(_DocxElementLoader(data).lazy_load())
        except DocumentParseError:
            raise
        except Exception as exc:
            raise DocumentParseError(f"failed to parse docx file {filename!r}: {exc}") from exc
        blocks = blocks_from_elements(elements)
        return ParsedDocument(blocks=blocks, md_content=_markdown(blocks))


@register
class LocalXlsxBuilder(DocumentParserBuilder):
    provider = DocumentParserProvider.LOCAL_XLSX

    def build(self, entry: LocalXlsxEntry) -> DocumentParser:
        return LocalXlsxParser(entry)


@register
class LocalDocxBuilder(DocumentParserBuilder):
    provider = DocumentParserProvider.LOCAL_DOCX

    def build(self, entry: LocalDocxEntry) -> DocumentParser:
        return LocalDocxParser(entry)
