"""A2UI 组件树 → 扁平组件数组：Generative UI 的机械翻译层。

LLM 以嵌套树声明卡片（A2uiNode），本模块负责协议翻译——树扁平化为 A2UI
v0.9 的扁平组件数组（children 以组件 id 引用）、自动分配 id、根组件固定
"root"、Button 展开为「label Text + 按钮本体」两组件。只做翻译与限额校验，
不做任何布局决策——布局完全由 LLM 的树参数决定（Generative UI 的"生成"
侧），本模块是纯通道。
"""

import itertools
from typing import Literal

from pydantic import BaseModel, Field

from app.packages.a2ui import (
    CHAT_SEND_ACTION,
    ROOT_COMPONENT_ID,
    button,
    card,
    column,
    divider,
    row,
    text,
)

# 限额（防滥用：单卡组件数/树深/单文本长度），超限拒绝生成
_MAX_NODES = 60
_MAX_DEPTH = 6
_MAX_TEXT_LENGTH = 500

# id 分配器在 compose_components 内按次创建（c1, c2, ...；surface 内唯一
# 由计数器保证，根组件单独命名）


class A2uiNode(BaseModel):
    """LLM 生成的卡片组件树节点（嵌套声明，扁平化在工具执行侧完成）。"""

    type: Literal["text", "row", "column", "card", "divider", "button"] = Field(
        description="组件类型",
    )
    text: str | None = Field(
        default=None,
        description="text/button 的文案；其余类型忽略",
    )
    variant: Literal["h1", "h2", "h3", "h4", "h5", "caption", "body"] | None = Field(
        default=None,
        description="text 的字号档位（标题用 h3/h4，辅助说明用 caption，正文用 body）；其余类型忽略",
    )
    align: Literal["start", "center", "end", "stretch"] | None = Field(
        default=None,
        description="row/column 的对齐方式；其余类型忽略",
    )
    action_text: str | None = Field(
        default=None,
        description="button 专属：点击后作为用户新消息发送的完整文案（按钮即「替用户说一句话」）",
    )
    button_variant: Literal["default", "primary", "borderless"] | None = Field(
        default=None,
        description="button 专属：视觉档位——primary 为强调色实心按钮（每张卡的主操作用"
            "它，如「转接」），default 为描边次级按钮，borderless 为文字链；其余类型忽略",
    )
    children: list["A2uiNode"] | None = Field(
        default=None,
        description="row/column 的子组件（横向/纵向排列）；card 的唯一子组件；其余类型忽略",
    )


class ComposeError(ValueError):
    """组件树非法（超限/结构错误）——工具层捕获后转引导话术返回 LLM。"""


def compose_components(nodes: list[A2uiNode]) -> list[dict]:
    """组件树 → A2UI 扁平组件数组（根组件 id 固定 "root"）。

    顶层允许多节点：多于一个时自动包一层 Column 作为根。非法结构
    （深度/数量/文案超限、缺文案、children 误挂）抛 ``ComposeError``。
    """
    counter = itertools.count(1)
    flat: list[dict] = []

    def emit(node: A2uiNode, depth: int) -> str:
        if depth > _MAX_DEPTH:
            raise ComposeError(f"组件树深度超过上限（{_MAX_DEPTH} 层）")
        node_id = f"c{next(counter)}"
        ntype = node.type

        if ntype == "text":
            if not node.text:
                raise ComposeError("text 组件缺少文案（text 字段）")
            flat.append(text(node_id, _clip(node.text, "text"), variant=node.variant))
        elif ntype == "divider":
            flat.append(divider(node_id))
        elif ntype == "button":
            if not node.text:
                raise ComposeError("button 组件缺少按钮文案（text 字段）")
            if not node.action_text:
                raise ComposeError(
                    "button 组件缺少 action_text（点击后作为用户新消息发送的完整文案）"
                )
            label_id = f"{node_id}-label"
            flat.append(text(label_id, _clip(node.text, "button 文案"), variant="body"))
            flat.append(button(
                node_id,
                label_id,
                action_name=CHAT_SEND_ACTION,
                context={"text": _clip(node.action_text, "action_text")},
                variant=node.button_variant,
            ))
        elif ntype in ("row", "column"):
            children = node.children or []
            child_ids = [emit(child, depth + 1) for child in children]
            builder = row if ntype == "row" else column
            component = builder(node_id, child_ids, align=node.align)
            flat.append(component)
        elif ntype == "card":
            children = node.children or []
            if len(children) != 1:
                raise ComposeError("card 组件必须有且只有一个子组件（children）")
            child_id = emit(children[0], depth + 1)
            flat.append(card(node_id, child_id))
        else:  # pragma: no cover - Literal 已约束
            raise ComposeError(f"未知组件类型：{ntype}")
        return node_id

    if len(nodes) == 1:
        root_id = emit(nodes[0], 1)
    else:
        child_ids = [emit(node, 1) for node in nodes]
        flat.append(column(ROOT_COMPONENT_ID, child_ids))
        root_id = ROOT_COMPONENT_ID
    # 根组件必须是 id="root"：单节点场景按 emit 返回的 id 重命名（根 id 此前
    # 未被任何组件引用，重命名不影响引用闭合）
    if root_id != ROOT_COMPONENT_ID:
        for component in flat:
            if component["id"] == root_id:
                component["id"] = ROOT_COMPONENT_ID
                break
    if len(flat) > _MAX_NODES:
        raise ComposeError(f"组件数超过上限（{_MAX_NODES} 个）")
    return flat


def _clip(value: str, field: str) -> str:
    if len(value) > _MAX_TEXT_LENGTH:
        raise ComposeError(f"{field} 超过长度上限（{_MAX_TEXT_LENGTH} 字符）")
    return value
