"""a2ui 组件清单：Generative UI 生成通道（能力声明 + 工具构造 + 装配器）。

能力导出 = a2ui_compose：LLM 以 A2uiNode 嵌套树声明卡片，工具执行侧做
协议翻译（ability/compose.py 的 compose_components）并以 content_and_artifact
形态返回——content 给 LLM 一句确认话术（UI 细节无需回流），artifact =
A2UI 消息数组走 UI 通道（ToolsTransformer 的 ui 透传 → ag-ui CUSTOM 事件）。

这是「智能体生成 UI」的通道工具：组件白名单与限额由参数 schema 和
compose 限额保证，布局/文案/结构完全由 LLM 决定。消费约定（前后端
自契约）：button 点击回传 ``chat.send`` 事件，前端把 context.text 作为
用户新消息追加进会话。
"""

from dataclasses import dataclass
from uuid import uuid4

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from wireup import injectable

from app.components.a2ui.ability.compose import A2uiNode, ComposeError, compose_components
from app.components.base import ComponentSpec, ToolSpec, register_component
from app.packages.a2ui import render_surface


class A2uiComposeArgs(BaseModel):
    """a2ui_compose 工具参数：顶层组件列表（通常一个 Card，多个时自动纵向排列）。"""

    components: list[A2uiNode] = Field(
        min_length=1,
        max_length=12,
        description="卡片顶层组件列表；每个元素是一个 A2UI 组件节点（text/row/column/card/divider/button）",
    )


def _build_a2ui_compose_tool() -> StructuredTool:
    def a2ui_compose(components: list[A2uiNode]) -> tuple[str, dict | None]:
        # content_and_artifact 形态必须始终返回二元组：失败时 artifact 置 None
        # （ToolsTransformer 对非 dict artifact 不带 ui 键，走纯文本透传）
        try:
            flat = compose_components(components)
        except ComposeError as error:
            return (f"卡片生成失败：{error}。请修正组件树后重试。", None)
        surface_id = f"composed-{uuid4().hex[:12]}"
        return (
            f"卡片已生成并展示给用户（surface: {surface_id}，共 {len(flat)} 个组件）。",
            {"a2ui": render_surface(surface_id, flat)},
        )

    return StructuredTool.from_function(
        name="a2ui_compose",
        description=(
            "生成一张声明式 UI 卡片并展示给用户（Generative UI）：用嵌套组件树描述卡片，"
            "类型为 text（文案）、row/column（横/纵布局，children 为子组件数组）、card（卡片容器，"
            "children 必须恰好一个）、divider（分隔线）、button（按钮，text 为按钮文案，"
            "action_text 为点击后作为用户新消息发送的完整文案）。树深最多 6 层、单卡组件总数有限额。"
            "组件树示例（转接卡片：标题 + 每位坐席一行姓名与转接按钮）："
            '[{"type":"card","children":[{"type":"column","children":['
            '{"type":"text","text":"人工客服","variant":"h4"},'
            '{"type":"text","text":"张三（高级客服·在线）"},'
            '{"type":"button","text":"转接 张三","action_text":"我要转接人工客服：张三（高级客服），我的问题是……"}'
            ']}]}]。'
            "需要向用户展示结构化信息（如坐席列表、选项菜单）且纯文本不够直观时必须使用本工具，"
            "不要只用文字罗列；button 用于引导用户一键发起后续请求。"
        ),
        args_schema=A2uiComposeArgs,
        func=a2ui_compose,
        infer_schema=False,
        response_format="content_and_artifact",
    )


_SPEC = register_component(ComponentSpec(
    name="a2ui",
    title="声明式卡片",
    description=(
        "Generative UI 生成通道：把 LLM 声明的组件树翻译为 A2UI 卡片展示给用户，"
        "支持文本/布局/卡片/按钮（按钮点击回传为用户消息）。"
    ),
    tools=(
        ToolSpec(
            name="a2ui_compose",
            title="生成 UI 卡片",
            description="以组件树声明一张卡片，校验后展示给用户；按钮点击回传为用户消息。",
            args_model=A2uiComposeArgs,
            build=_build_a2ui_compose_tool,
        ),
    ),
))


@injectable
@dataclass
class A2uiComponent:
    """a2ui 组件装配器：生成通道无服务依赖（翻译层是纯函数）。

    ``agentic_id`` 形参是与其它组件的统一装配接口，此处不使用。
    """

    @property
    def spec(self) -> ComponentSpec:
        return _SPEC

    def tools(self, agentic_id: str) -> list[StructuredTool]:
        return [tool.build() for tool in _SPEC.tools]
