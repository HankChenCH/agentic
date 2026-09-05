"""demo 组件清单：能力声明（spec）+ 工具构造 + 装配器。

能力导出 = 演示用工具（当前仅天气查询）。与 memory/knowledge 组件的
差异：本组件面向 builtin:demo 的演示场景，工具数据源是 mock（收敛在
ability 门面内部），无 agentic_id 绑定、无运行时身份注入需求。

get_weather 是 A2UI 能力的试点工具：content（LLM 可见）= 结构化天气 JSON，
artifact（UI 通道，LLM 不可见）= A2UI v0.9 消息数组——经 LangChain 的
``content_and_artifact`` 返回形态分离两侧，ToolsTransformer/Translator 据此
下发 ag-ui CUSTOM 事件（契约见两侧 AGENTS.md 的 A2UI 小节）。
"""

import json
from dataclasses import dataclass

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from wireup import injectable

from app.components.base import ComponentSpec, ToolSpec, register_component
from app.components.demo.ability.weather import DemoWeatherService
from app.components.demo.ability.weather_surface import build_weather_surface


class WeatherArgs(BaseModel):
    """get_weather 工具参数。"""

    city: str = Field(description="城市名称, eg: 中山")
    date: str = Field(description="日期，eg: 2026-01-01")


def _build_weather_tool(weather_service: DemoWeatherService) -> StructuredTool:
    def get_weather(city: str, date: str) -> tuple[str, dict]:
        """content_and_artifact 返回形态：content 给 LLM，artifact 给 UI 通道。"""
        weather = weather_service.get_weather(city=city, date=date)
        return (
            json.dumps(weather, ensure_ascii=False),
            {"a2ui": build_weather_surface(weather)},
        )

    return StructuredTool.from_function(
        name="get_weather",
        description="查询对应城市的天气情况。返回结构化天气数据，客户端会自动渲染天气卡片。",
        args_schema=WeatherArgs,
        func=get_weather,
        infer_schema=False,
        response_format="content_and_artifact",
    )


_SPEC = register_component(ComponentSpec(
    name="demo",
    title="演示工具",
    description=(
        "内置演示智能体的演示工具集（数据源为 mock，收敛在 ability 门面"
        "内部）：当前导出天气查询。"
    ),
    tools=(
        ToolSpec(
            name="get_weather",
            title="查询天气",
            description="查询对应城市的天气情况。返回结构化天气数据，客户端会自动渲染天气卡片。",
            args_model=WeatherArgs,
            build=_build_weather_tool,
        ),
    ),
))


@injectable
@dataclass
class DemoComponent:
    """demo 组件装配器：持能力门面服务，把 spec 声明实例化为可运行工具。

    ``agentic_id`` 形参是与 ``MemoryComponent`` / ``KnowledgeComponent``
    的统一装配接口；演示工具不绑定 agent 身份。
    """

    weather: DemoWeatherService

    @property
    def spec(self) -> ComponentSpec:
        return _SPEC

    def tools(self, agentic_id: str) -> list[StructuredTool]:
        return [tool.build(self.weather) for tool in _SPEC.tools]
