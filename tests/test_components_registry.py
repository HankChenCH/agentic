"""组件注册表与能力装配：登记校验、能力清单导出、toolbox 装配与筛选。"""

import pytest
from pydantic import BaseModel

from app.agents.toolbox import AgentToolbox
from app.components.base import (
    COMPONENT_REGISTRY,
    ComponentSpec,
    ToolSpec,
    describe_capabilities,
    register_component,
)
from app.components.demo import DemoComponent, DemoWeatherService
from app.components.knowledge import KnowledgeComponent
from app.components.memory import MemoryComponent


class _Args(BaseModel):
    query: str = ""


def _tool_spec(name: str = "tool_a", description: str = "测试工具") -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        args_model=_Args,
        build=lambda *_args, **_kwargs: None,
    )


def _spec(name: str = "comp_x", tools: tuple[ToolSpec, ...] = (_tool_spec(),)) -> ComponentSpec:
    return ComponentSpec(name=name, title="测试组件", description="用于测试的组件", tools=tools)


def test_builtin_components_registered():
    # 三组件经 manifest import 期注册（app.components 包副作用导入）
    assert {"memory", "knowledge", "demo"} <= set(COMPONENT_REGISTRY)
    memory = COMPONENT_REGISTRY["memory"]
    knowledge = COMPONENT_REGISTRY["knowledge"]
    demo = COMPONENT_REGISTRY["demo"]
    assert {t.name for t in memory.tools} == {"timeline", "expand", "state_at"}
    assert {t.name for t in knowledge.tools} == {
        "knowledge_list", "knowledge_search", "knowledge_context",
        "knowledge_document_read", "knowledge_document_list",
    }
    assert {t.name for t in demo.tools} == {"get_weather"}
    for spec in (memory, knowledge, demo):
        assert spec.title and spec.description
        for tool in spec.tools:
            assert tool.description  # LLM-facing 描述是必备元数据
            schema = tool.args_model.model_json_schema()
            assert "properties" in schema


def test_register_component_rejects_duplicate_name():
    with pytest.raises(ValueError, match="already registered"):
        register_component(_spec(name="memory"))


def test_register_component_rejects_duplicate_tool_name():
    with pytest.raises(ValueError, match="duplicate tool name"):
        register_component(_spec(tools=(_tool_spec(name="dup"), _tool_spec(name="dup"))))


def test_register_component_rejects_blank_description():
    with pytest.raises(ValueError, match="description required"):
        register_component(_spec(tools=(_tool_spec(name="t", description=""),)))


def test_describe_capabilities_exports_schema():
    caps = {c["component"]: c for c in describe_capabilities()}
    knowledge = caps["knowledge"]
    assert knowledge["title"] == "知识库检索"
    search = next(t for t in knowledge["tools"] if t["name"] == "knowledge_search")
    assert "query" in search["parameters"]["properties"]
    assert search["description"]


def _fake_component(name: str, tool_names: list[str]):
    """鸭子类型替身：与真实装配器同接口（.spec / .tools(agentic_id)）。"""

    class _Fake:
        spec = ComponentSpec(
            name=name,
            title=name,
            description="fake",
            tools=tuple(_tool_spec(n) for n in tool_names),
        )

        def tools(self, agentic_id: str):
            return [f"{name}:{tool_name}@{agentic_id}" for tool_name in tool_names]

    return _Fake()


def test_toolbox_assembles_all_and_filters_by_component():
    toolbox = AgentToolbox(
        memory=_fake_component("memory", ["timeline"]),
        knowledge=_fake_component("knowledge", ["knowledge_list"]),
        demo=_fake_component("demo", ["get_weather"]),
    )
    assert [s.name for s in toolbox.specs] == ["memory", "knowledge", "demo"]
    assert toolbox.tools("builtin:demo") == [
        "memory:timeline@builtin:demo",
        "knowledge:knowledge_list@builtin:demo",
        "demo:get_weather@builtin:demo",
    ]
    assert toolbox.tools("builtin:demo", only={"knowledge"}) == ["knowledge:knowledge_list@builtin:demo"]
    assert toolbox.spec("memory").name == "memory"
    with pytest.raises(KeyError, match="not in toolbox"):
        toolbox.spec("nope")


def test_toolbox_rejects_cross_component_tool_collision():
    with pytest.raises(ValueError, match="tool name collision"):
        AgentToolbox(
            memory=_fake_component("memory", ["same"]),
            knowledge=_fake_component("knowledge", ["same"]),
            demo=_fake_component("demo", ["get_weather"]),
        )


def test_real_components_no_tool_collision_and_assemble():
    # 真实三组件的装配冒烟：spec 校验不依赖门面服务（服务仅 build 时触达），
    # 工具名互不冲突且数量与声明一致
    toolbox = AgentToolbox(
        memory=MemoryComponent(recall=None),
        knowledge=KnowledgeComponent(retrieval=None, navigation=None),
        demo=DemoComponent(weather=None),
    )
    names = [t.name for s in toolbox.specs for t in s.tools]
    assert len(names) == len(set(names)) == 9


def test_demo_weather_tool_returns_canned_report():
    # demo 组件冒烟：spec.build 直接产出工具（不依赖 DI），调用返回固定文案
    spec_tool = COMPONENT_REGISTRY["demo"].tools[0]
    tool = spec_tool.build(DemoWeatherService())
    assert tool.name == "get_weather"
    assert tool.invoke({"city": "中山", "date": "2026-01-01"}) == "中山 2026-01-01 天气晴朗，气温33度，湿度60%"
