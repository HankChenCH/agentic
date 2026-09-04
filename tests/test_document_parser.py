"""MinerU 归一化：bbox 保留与坐标基准归一（0-1000 / 0-1 / 脏数据降级）。"""

from app.adapters.document_parser.mineru_cloud_provider import (
    _normalize_blocks,
    _to_bbox,
)
from app.domain.ports import ParsedBlockType


def test_to_bbox_normalizes_0_1000_scale():
    # 官方 content_list 基准：0-1000 归一化（左上原点）
    assert _to_bbox([70, 120, 560, 160]) == [0.07, 0.12, 0.56, 0.16]


def test_to_bbox_keeps_0_1_scale():
    # 部分新版输出 0-1 百分比：max<=1.5 视为已归一化
    assert _to_bbox([0.07, 0.12, 0.56, 0.16]) == [0.07, 0.12, 0.56, 0.16]


def test_to_bbox_clamps_overflow():
    # 边缘块略超 1000：归一后截断到 1.0
    assert _to_bbox([0, 0, 1005, 1000]) == [0.0, 0.0, 1.0, 1.0]


def test_to_bbox_rejects_dirty_values():
    assert _to_bbox(None) is None
    assert _to_bbox([1, 2, 3]) is None  # 长度≠4
    assert _to_bbox([1, 2, 3, 4, 5]) is None
    assert _to_bbox(["a", 2, 3, 4]) is None  # 非数值
    assert _to_bbox([0, 0, float("nan"), 1]) is None  # 非有限数
    assert _to_bbox([0.5, 0.5, 0.4, 0.6]) is None  # 非法几何（x1<=x0）


def test_normalize_blocks_passes_bbox_through():
    raw_blocks = [
        {"type": "text", "text": "标题前的正文", "page_idx": 0, "bbox": [70, 120, 560, 160]},
        {
            "type": "table",
            "table_body": "<table></table>",
            "page_idx": 1,
            "bbox": [0.07, 0.12, 0.56, 0.16],
        },
        {"type": "header", "text": "页眉", "page_idx": 0, "bbox": [0, 0, 100, 10]},  # 噪音块过滤
        {"type": "text", "text": "无坐标块", "page_idx": 2},  # bbox 缺失 → None
    ]
    blocks = _normalize_blocks(raw_blocks, {})

    assert [b.type for b in blocks] == [ParsedBlockType.TEXT, ParsedBlockType.TABLE, ParsedBlockType.TEXT]
    # 0-1000 制换算为 0-1；已是 0-1 的保持；缺失为 None
    assert blocks[0].bbox == [0.07, 0.12, 0.56, 0.16]
    assert blocks[1].bbox == [0.07, 0.12, 0.56, 0.16]
    assert blocks[2].bbox is None
