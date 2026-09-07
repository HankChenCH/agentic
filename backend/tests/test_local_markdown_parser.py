"""本地 Markdown 解析器：块级映射（标题/列表/代码/表格/图片行）与原文透传。"""

from app.adapters.document_parser.local_markdown_provider import LocalMarkdownParser
from app.core.config import LocalMarkdownEntry
from app.domain.ports import ParsedBlockType


def parse_markdown(text: str):
    return LocalMarkdownParser(LocalMarkdownEntry()).parse(text.encode("utf-8"), "notes.md")


def test_headings_map_to_text_levels():
    parsed = parse_markdown("# 一级\n\n## 二级\n\n### 三级\n\n#### 四级\n\n##### 五级\n\n###### 六级")
    assert [(b.type, b.text_level, b.text) for b in parsed.blocks] == [
        (ParsedBlockType.TEXT, 1, "一级"),
        (ParsedBlockType.TEXT, 2, "二级"),
        (ParsedBlockType.TEXT, 3, "三级"),
        (ParsedBlockType.TEXT, 4, "四级"),
        (ParsedBlockType.TEXT, 5, "五级"),
        (ParsedBlockType.TEXT, 6, "六级"),
    ]


def test_paragraphs_split_on_blank_lines():
    parsed = parse_markdown("第一段第一行\n第一段第二行\n\n第二段")
    assert [b.text for b in parsed.blocks] == ["第一段第一行\n第一段第二行", "第二段"]
    assert all(b.text_level == 0 for b in parsed.blocks)


def test_list_run_joins_items_into_one_block():
    parsed = parse_markdown("- 条目一\n* 条目二\n+ 条目三\n\n1. 有序一\n2) 有序二")
    assert [b.type for b in parsed.blocks] == [ParsedBlockType.LIST, ParsedBlockType.LIST]
    assert parsed.blocks[0].text == "条目一\n条目二\n条目三"
    assert parsed.blocks[1].text == "有序一\n有序二"


def test_fenced_code_block_with_language():
    parsed = parse_markdown("```python\nprint(1)\nprint(2)\n```")
    (code,) = parsed.blocks
    assert code.type is ParsedBlockType.CODE
    assert code.text == "print(1)\nprint(2)"
    assert code.captions == ["代码块（python）"]
    assert code.sub_type == "python"


def test_unterminated_code_fence_runs_to_eof():
    parsed = parse_markdown("```\nnever closed")
    (code,) = parsed.blocks
    assert code.text == "never closed"


def test_pipe_table_becomes_html_table_block():
    text = "| 指标 | 值 |\n| --- | ---: |\n| Q1 | 100 |\n| 含\\|竖线 | x |"
    parsed = parse_markdown(text)
    (table,) = parsed.blocks
    assert table.type is ParsedBlockType.TABLE
    assert "<th>指标</th><th>值</th>" in table.html
    assert "<td>Q1</td><td>100</td>" in table.html
    assert "<td>含|竖线</td>" in table.html  # 转义竖线还原为字面量
    assert "thead" in table.html and "tbody" in table.html


def test_standalone_image_line_yields_alt_caption():
    parsed = parse_markdown("前文段落\n\n![架构图](https://example.com/a.png)")
    assert [b.type for b in parsed.blocks] == [ParsedBlockType.TEXT, ParsedBlockType.IMAGE]
    assert parsed.blocks[1].captions == ["架构图"]
    assert parsed.blocks[1].asset_name is None  # 外链不产字节资产


def test_horizontal_rules_are_dropped():
    parsed = parse_markdown("上段\n\n---\n\n***\n\n下段")
    assert [b.text for b in parsed.blocks] == ["上段", "下段"]


def test_blockquote_markers_stripped_into_paragraph():
    parsed = parse_markdown("> 引用一行\n> 续行\n\n正文")
    assert parsed.blocks[0].text == "引用一行\n续行"


def test_inline_markdown_kept_verbatim_in_text():
    parsed = parse_markdown("**加粗** 与 [链接](https://a.b) 保留原样")
    assert parsed.blocks[0].text == "**加粗** 与 [链接](https://a.b) 保留原样"


def test_md_content_passes_through_original_text():
    text = "# 标题\n\n正文"
    parsed = parse_markdown(text)
    assert parsed.md_content == text  # markdown 本身即预览形态


def test_empty_file_yields_no_blocks():
    parsed = parse_markdown("")
    assert parsed.blocks == []
    assert parsed.md_content is None


def test_arbitrary_bytes_never_raise():
    # 非 UTF-8 乱码按 replace 解码，不抛错（本供应商无永久性失败路径）
    parsed = LocalMarkdownParser(LocalMarkdownEntry()).parse(b"\xff\xfe binary \x00 junk", "x.md")
    assert parsed.md_content is not None
