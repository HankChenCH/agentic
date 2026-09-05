"""a2ui 组件：Generative UI 生成通道（LLM 声明组件树 → A2UI 卡片）。"""

from app.components.a2ui.ability.compose import A2uiNode, ComposeError
from app.components.a2ui.manifest import A2uiComponent

__all__ = ["A2uiComponent", "A2uiNode", "ComposeError"]
