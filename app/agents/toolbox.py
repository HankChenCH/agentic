"""智能体可装配的能力集：从组件注册表装配（全系统唯一显式组件清单点）。

组件以「装配器」为单位列在这里（各组件 manifest 的 @injectable，持门面
服务）；新增组件 = 新建组件包（manifest 声明 spec）+ 本类加一个字段，
AgentFactory 与各 agent 的 build_tools 不必再变。构造期做组件名与跨组件
工具名冲突校验（fail-fast）；``tools()`` 把 spec 声明实例化为绑定服务与
agent 身份后的 StructuredTool。
"""

from collections.abc import Collection

from dataclasses import dataclass

from langchain_core.tools import StructuredTool
from wireup import injectable

from app.components.base import ComponentSpec
from app.components.demo import DemoComponent
from app.components.knowledge import KnowledgeComponent
from app.components.memory import MemoryComponent


@injectable
@dataclass
class AgentToolbox:
    """组件能力装配点：specs 供枚举导出，tools 供 agent build_tools 取用。"""

    memory: MemoryComponent
    knowledge: KnowledgeComponent
    demo: DemoComponent

    def __post_init__(self):
        specs = self.specs
        names = [spec.name for spec in specs]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate component names in toolbox: {names}")
        tool_names = [tool.name for spec in specs for tool in spec.tools]
        dupes = sorted({name for name in tool_names if tool_names.count(name) > 1})
        if dupes:
            raise ValueError(f"tool name collision across components: {dupes}")

    @property
    def specs(self) -> list[ComponentSpec]:
        return [component.spec for component in self._components()]

    def spec(self, name: str) -> ComponentSpec:
        for component in self._components():
            if component.spec.name == name:
                return component.spec
        raise KeyError(f"component not in toolbox: {name}")

    def tools(self, agentic_id: str, only: Collection[str] | None = None) -> list[StructuredTool]:
        """实例化全部（或 ``only`` 指定组件名的）能力工具。

        与各组件的工具名冲突校验在构造期已完成，此处不再重复。
        """
        selected = self._components() if only is None else [c for c in self._components() if c.spec.name in only]
        return [tool for component in selected for tool in component.tools(agentic_id)]

    def _components(self) -> tuple[MemoryComponent | KnowledgeComponent | DemoComponent, ...]:
        return (self.memory, self.knowledge, self.demo)
