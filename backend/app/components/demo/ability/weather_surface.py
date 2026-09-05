"""天气卡片 → A2UI 载荷组装：demo 组件的 UI 呈现面（A2UI 能力第一块基石）。

职责边界：``weather`` 门面产出结构化数据（LLM 与卡片共用的展示契约），
本模块把它组装成 A2UI v0.9 消息数组（静态卡片子集，见
``app/packages/a2ui``）；工具层经 artifact 携带、translator 经 ag-ui CUSTOM
事件下发，LLM 不感知 UI。surfaceId 用 uuid4 保证全局唯一——surface 生命周期
由每次工具调用独立拥有，不做增量更新。
"""

import uuid
from typing import Any

from app.packages.a2ui import (
    ROOT_COMPONENT_ID,
    card,
    column,
    divider,
    render_surface,
    row,
    text,
)


def build_weather_surface(weather: dict) -> list[dict[str, Any]]:
    """结构化天气数据 → A2UI 消息数组（createSurface + updateComponents）。

    卡片结构（标准基础目录组件）：Card 包 Column——标题行（城市 + 日期）、
    分隔线、主行（气温大字 + 天气现象）、辅行（湿度）。字段缺失时整体跳过
    对应块，mock 数据恒齐全。
    """
    surface_id = f"weather-{uuid.uuid4().hex}"
    components = [
        card(ROOT_COMPONENT_ID, "body"),
        column("body", ["title", "sep", "main", "extra"]),
        row("title", ["city", "date"]),
        text("city", weather.get("city", ""), variant="h4"),
        text("date", weather.get("date", ""), variant="caption"),
        divider("sep"),
        row("main", ["temperature", "condition"], align="center"),
        text("temperature", f"{weather.get('temperature', '')}°", variant="h2"),
        text("condition", weather.get("condition", ""), variant="body"),
        row("extra", ["humidity"], align="center"),
        text("humidity", f"湿度 {weather.get('humidity', '')}%", variant="caption"),
    ]
    return render_surface(surface_id, components)
