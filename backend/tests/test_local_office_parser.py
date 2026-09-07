"""本地 Office 解析器（xlsx / docx）：元素映射、行窗口、截断守卫与错误语义。

样例文件在测试内用 openpyxl / python-docx 构造（与解析同源的库），不落盘。
"""

import io

import docx
import pytest
from openpyxl import Workbook

from app.adapters.document_parser.local_office_provider import LocalDocxParser, LocalXlsxParser
from app.core.config import LocalDocxEntry, LocalXlsxEntry
from app.domain.ports import DocumentParseError, ParsedBlockType


def make_xlsx_bytes(sheets: dict[str, list[list]], lead_blank: bool = False) -> bytes:
    workbook = Workbook()
    first_name = next(iter(sheets))
    worksheet = workbook.active
    worksheet.title = first_name
    for name, rows in sheets.items():
        target = worksheet if name == first_name else workbook.create_sheet(name)
        if lead_blank:
            target.append([])  # 行 1 留空：逼出行首裁剪路径
        for row in rows:
            target.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def make_docx_bytes() -> bytes:
    document = docx.Document()
    document.add_heading("第一章 概述", level=1)
    document.add_heading("背景", level=2)
    document.add_paragraph("这是正文段落。")
    document.add_paragraph("")  # 空段落：不产块
    document.add_paragraph("列表项一", style="List Bullet")
    document.add_paragraph("列表项二", style="List Number")
    table = document.add_table(rows=2, cols=2)
    table.style = "Table Grid"
    table.cell(0, 0).text = "指标"
    table.cell(0, 1).text = "值"
    table.cell(1, 0).text = "Q1"
    table.cell(1, 1).text = "100"
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# ---------- xlsx ----------


def test_xlsx_sheet_title_and_table_blocks():
    data = make_xlsx_bytes({"季度数据": [["月份", "销售额"], ["1月", 100], ["2月", 200]]})
    parsed = LocalXlsxParser(LocalXlsxEntry()).parse(data, "报表.xlsx")

    assert [b.type for b in parsed.blocks] == [
        ParsedBlockType.TEXT,
        ParsedBlockType.TABLE,
    ]
    title, table = parsed.blocks
    assert title.text == "季度数据" and title.text_level == 1
    # 表头进 thead（th），数据行进 tbody（td），值经 HTML 转义
    assert "<th>月份</th><th>销售额</th>" in table.html
    assert "<td>1月</td><td>100</td>" in table.html
    assert table.captions == ["工作表「季度数据」第 1–2 行（共 2 行数据）"]
    assert table.page_idx == 0  # xlsx 的 page_idx = sheet 序号


def test_xlsx_row_window_repeats_header():
    rows = [["指标", "值"]] + [[f"k{i}", i] for i in range(12)]
    data = make_xlsx_bytes({"数据": rows})
    entry = LocalXlsxEntry(rows_per_block=5)
    parsed = LocalXlsxParser(entry).parse(data, "a.xlsx")

    tables = [b for b in parsed.blocks if b.type is ParsedBlockType.TABLE]
    assert len(tables) == 3  # 12 行数据按 5 行窗口 → 3 块
    assert all("<th>指标</th>" in b.html for b in tables)  # 表头每块重复
    assert "第 1–5 行" in tables[0].captions[0]
    assert "第 11–12 行" in tables[2].captions[0]


def test_xlsx_truncation_notes():
    rows = [["指标", "值"]] + [[f"k{i}", i] for i in range(30)] + [[""] * 10]
    rows = [row + [f"extra{ i }"] if i < 3 else row for i, row in enumerate(rows)]  # 第 4 列溢出
    data = make_xlsx_bytes({"数据": rows})
    entry = LocalXlsxEntry(rows_per_block=50, max_rows_per_sheet=10, max_cols=3)
    parsed = LocalXlsxParser(entry).parse(data, "a.xlsx")

    table = next(b for b in parsed.blocks if b.type is ParsedBlockType.TABLE)
    assert "仅读取前 10 行（共 30 行）" in table.captions[0]
    assert "仅读取前 3 列" in table.captions[0]


def test_xlsx_blank_rows_and_sheets_skipped():
    data = make_xlsx_bytes({"数据": [["a", "b"], [1, 2]], "空白表": []})
    parsed = LocalXlsxParser(LocalXlsxEntry()).parse(data, "a.xlsx")

    assert [b.text for b in parsed.blocks if b.type is ParsedBlockType.TEXT] == ["数据"]


def test_xlsx_leading_blank_row_trimmed():
    data = make_xlsx_bytes({"数据": [["列头"], ["值一"], ["值二"]]}, lead_blank=True)
    parsed = LocalXlsxParser(LocalXlsxEntry()).parse(data, "a.xlsx")

    table = next(b for b in parsed.blocks if b.type is ParsedBlockType.TABLE)
    assert "共 2 行数据" in table.captions[0]
    assert "<td>值一</td>" in table.html


def test_xlsx_header_only_sheet_keeps_table():
    data = make_xlsx_bytes({"只有表头": [["列一", "列二"]]})
    parsed = LocalXlsxParser(LocalXlsxEntry()).parse(data, "a.xlsx")

    table = next(b for b in parsed.blocks if b.type is ParsedBlockType.TABLE)
    assert "仅表头" in table.captions[0]
    assert "<th>列一</th>" in table.html


def test_xlsx_second_sheet_page_idx():
    data = make_xlsx_bytes({"一": [["a"]], "二": [["b"], ["c"]]})
    parsed = LocalXlsxParser(LocalXlsxEntry()).parse(data, "a.xlsx")

    sheet_two_table = [b for b in parsed.blocks if b.type is ParsedBlockType.TABLE][-1]
    assert sheet_two_table.page_idx == 1
    assert "工作表「二」" in sheet_two_table.captions[0]


def test_xlsx_corrupt_bytes_raise_parse_error():
    with pytest.raises(DocumentParseError, match="invalid xlsx"):
        LocalXlsxParser(LocalXlsxEntry()).parse(b"not an xlsx", "a.xlsx")


def test_xlsx_md_content_synthesized():
    data = make_xlsx_bytes({"表": [["列"], ["值"]]})
    parsed = LocalXlsxParser(LocalXlsxEntry()).parse(data, "a.xlsx")

    assert parsed.md_content is not None
    assert "# 表" in parsed.md_content and "<table>" in parsed.md_content


# ---------- docx ----------


def test_docx_element_mapping_in_reading_order():
    parsed = LocalDocxParser(LocalDocxEntry()).parse(make_docx_bytes(), "报告.docx")

    assert [(b.type, b.text_level, b.text) for b in parsed.blocks] == [
        (ParsedBlockType.TEXT, 1, "第一章 概述"),
        (ParsedBlockType.TEXT, 2, "背景"),
        (ParsedBlockType.TEXT, 0, "这是正文段落。"),
        (ParsedBlockType.LIST, 0, "列表项一"),
        (ParsedBlockType.LIST, 0, "列表项二"),
    ] + [(ParsedBlockType.TABLE, 0, "")]  # 表格块 text 为空，内容在 html
    table = parsed.blocks[-1]
    assert "<td>指标</td><td>值</td>" in table.html
    assert table.page_idx == 0  # docx 无页码概念，恒为 0


def test_docx_table_colspan_from_grid_span():
    document = docx.Document()
    table = document.add_table(rows=1, cols=3)
    merged = table.cell(0, 0).merge(table.cell(0, 1))  # 横向合并两格
    merged.text = "宽表头"
    table.cell(0, 2).text = "末列"
    buffer = io.BytesIO()
    document.save(buffer)
    parsed = LocalDocxParser(LocalDocxEntry()).parse(buffer.getvalue(), "a.docx")

    table_html = parsed.blocks[-1].html
    assert 'colspan="2"' in table_html
    assert "宽表头" in table_html


def test_docx_corrupt_bytes_raise_parse_error():
    with pytest.raises(DocumentParseError, match="invalid docx"):
        LocalDocxParser(LocalDocxEntry()).parse(b"not a docx", "a.docx")


def test_docx_md_content_synthesized():
    parsed = LocalDocxParser(LocalDocxEntry()).parse(make_docx_bytes(), "报告.docx")

    assert parsed.md_content is not None
    assert "# 第一章 概述" in parsed.md_content
    assert "- 列表项一" in parsed.md_content
