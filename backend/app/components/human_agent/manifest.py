"""human_agent 组件清单：能力声明（spec）+ 工具构造 + LLM 契约模型。

能力导出 = 坐席列表查询（human_agent_list）。本组件只供数据、不产卡片：
卡片由客服智能体经 a2ui 组件的 a2ui_compose 工具自主生成（Generative UI
通道），布局与文案由 LLM 决定——本组件的工具 description 只引导「先查
坐席、再生成卡片」的调用顺序，不承担渲染职责。

坐席数据经 HumanAgentRepositoryPort 查库（全局资源，无用户可见性过滤；
写入口在 app/commands/human_agent.py 管理命令）。
"""

import json
from dataclasses import dataclass

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from wireup import injectable

from app.components.base import ComponentSpec, ToolSpec, register_component
from app.components.human_agent.ability.directory import HumanAgentDirectoryService
from app.models.domain.human_agent import HumanAgentStatus


class HumanAgentListArgs(BaseModel):
    """human_agent_list 无参数。"""


class HumanAgentProfile(BaseModel):
    """单条坐席契约：LLM 组织转接话术与生成卡片的数据形状（id 供卡片按钮
    关联坐席，status 供判断可接待性）。"""

    id: str = Field(description="坐席 id")
    name: str = Field(description="坐席姓名")
    title: str = Field(description="职务")
    specialty: str = Field(default="", description="擅长问题类型")
    intro: str = Field(default="", description="坐席简介")
    status: HumanAgentStatus = Field(description="接待状态: online, busy, offline")


def _build_human_agent_list_tool(directory: HumanAgentDirectoryService) -> StructuredTool:
    def human_agent_list() -> str:
        agents = directory.list_agents()
        if not agents:
            return "当前没有可转接的人工客服坐席"
        profiles = [
            HumanAgentProfile(
                id=str(agent.id),
                name=agent.name,
                title=agent.title,
                specialty=agent.specialty,
                intro=agent.intro,
                status=agent.status,
            ).model_dump(mode="json")
            for agent in agents
        ]
        return json.dumps({"agents": profiles}, ensure_ascii=False)

    return StructuredTool.from_function(
        name="human_agent_list",
        description=(
            "获取人工客服坐席列表（id/姓名/职务/擅长问题/在线状态），在线坐席排在前面。"
            "需要转人工时先调用本工具了解可用坐席，查到坐席后必须紧接着调用 a2ui_compose "
            "生成转接卡片展示给用户（禁止只文字回复）；无坐席或全部不可接待时如实告知用户。"
        ),
        args_schema=HumanAgentListArgs,
        func=human_agent_list,
        infer_schema=False,
    )


_SPEC = register_component(ComponentSpec(
    name="human_agent",
    title="人工客服坐席",
    description=(
        "人工客服坐席目录（查库）：列出坐席的姓名/职务/擅长问题/接待状态，"
        "供转人工场景选择与展示坐席。数据由管理命令维护。"
    ),
    tools=(
        ToolSpec(
            name="human_agent_list",
            title="查看人工客服坐席",
            description="列出人工客服坐席（在线优先），转人工场景的数据入口。",
            args_model=HumanAgentListArgs,
            build=_build_human_agent_list_tool,
        ),
    ),
))


@injectable
@dataclass
class HumanAgentComponent:
    """human_agent 组件装配器：持坐席目录门面，把 spec 声明实例化为可运行工具。

    ``agentic_id`` 形参是与 ``MemoryComponent`` 等的统一装配接口；坐席是
    全局资源，与 agent 身份无关，此处不使用。
    """

    directory: HumanAgentDirectoryService

    @property
    def spec(self) -> ComponentSpec:
        return _SPEC

    def tools(self, agentic_id: str) -> list[StructuredTool]:
        return [tool.build(self.directory) for tool in _SPEC.tools]
