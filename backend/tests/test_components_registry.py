"""组件注册表与能力装配：登记校验、能力清单导出、toolbox 装配与筛选。"""

import pytest
from pydantic import BaseModel
from types import SimpleNamespace

from app.agents.toolbox import AgentToolbox
from app.components.base import (
    COMPONENT_REGISTRY,
    ComponentSpec,
    ToolSpec,
    describe_capabilities,
    register_component,
)
from app.components.a2ui import A2uiComponent
from app.components.demo import DemoComponent, DemoWeatherService
from app.components.human_agent import HumanAgentComponent
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
        "knowledge_list", "knowledge_search", "knowledge_context", "knowledge_document_list",
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
    # 工具级展示标题（纯 UI 元数据）随清单导出；已声明 title 的工具须正确透传
    assert search["title"] == "知识库检索"
    weather = next(t for t in caps["demo"]["tools"] if t["name"] == "get_weather")
    assert weather["title"] == "查询天气"


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
            # 带 .name 属性的轻对象（StructuredTool 的鸭子类型面：name 是
            # 纯工具名，toolbox 的 tool_names 过滤读它）；origin 供断言区分来源
            return [
                SimpleNamespace(name=tool_name, origin=f"{name}:{tool_name}@{agentic_id}")
                for tool_name in tool_names
            ]

    return _Fake()


def test_toolbox_assembles_all_and_filters_by_component():
    toolbox = AgentToolbox(
        memory=_fake_component("memory", ["timeline"]),
        knowledge=_fake_component("knowledge", ["knowledge_list"]),
        demo=_fake_component("demo", ["get_weather"]),
        human_agent=_fake_component("human_agent", ["human_agent_list"]),
        a2ui=_fake_component("a2ui", ["a2ui_compose"]),
    )
    assert [s.name for s in toolbox.specs] == ["memory", "knowledge", "demo", "human_agent", "a2ui"]
    assert [t.origin for t in toolbox.tools("builtin:demo")] == [
        "memory:timeline@builtin:demo",
        "knowledge:knowledge_list@builtin:demo",
        "demo:get_weather@builtin:demo",
        "human_agent:human_agent_list@builtin:demo",
        "a2ui:a2ui_compose@builtin:demo",
    ]
    assert [t.origin for t in toolbox.tools("builtin:demo", only={"knowledge"})] == ["knowledge:knowledge_list@builtin:demo"]
    assert toolbox.spec("memory").name == "memory"
    with pytest.raises(KeyError, match="not in toolbox"):
        toolbox.spec("nope")


def test_toolbox_rejects_cross_component_tool_collision():
    with pytest.raises(ValueError, match="tool name collision"):
        AgentToolbox(
            memory=_fake_component("memory", ["same"]),
            knowledge=_fake_component("knowledge", ["same"]),
            demo=_fake_component("demo", ["get_weather"]),
            human_agent=_fake_component("human_agent", ["human_agent_list"]),
            a2ui=_fake_component("a2ui", ["a2ui_compose"]),
        )


def test_toolbox_filters_by_tool_names():
    toolbox = AgentToolbox(
        memory=_fake_component("memory", ["timeline", "expand"]),
        knowledge=_fake_component("knowledge", ["knowledge_list"]),
        demo=_fake_component("demo", ["get_weather"]),
        human_agent=_fake_component("human_agent", ["human_agent_list"]),
        a2ui=_fake_component("a2ui", ["a2ui_compose"]),
    )
    # ``tool_names`` 过滤后按 origin 断言
    selected = toolbox.tools(
        "builtin:support", only={"memory", "knowledge"}, tool_names={"knowledge_list", "timeline", "expand"}
    )
    assert [t.origin for t in selected] == [
        "memory:timeline@builtin:support",
        "memory:expand@builtin:support",
        "knowledge:knowledge_list@builtin:support",
    ]


def test_real_components_no_tool_collision_and_assemble():
    # 真实五组件的装配冒烟：spec 校验不依赖门面服务（服务仅 build 时触达），
    # 工具名互不冲突且数量与声明一致
    toolbox = AgentToolbox(
        memory=MemoryComponent(recall=None),
        knowledge=KnowledgeComponent(retrieval=None, navigation=None),
        demo=DemoComponent(weather=None),
        human_agent=HumanAgentComponent(directory=None),
        a2ui=A2uiComponent(),
    )
    names = [t.name for s in toolbox.specs for t in s.tools]
    assert len(names) == len(set(names)) == 10


def test_demo_weather_tool_returns_canned_report():
    # demo 组件冒烟：spec.build 直接产出工具（不依赖 DI），invoke 返回结构化
    # 天气 JSON（LLM 可见的 content；A2UI 卡片 artifact 经 func 直调验证）
    spec_tool = COMPONENT_REGISTRY["demo"].tools[0]
    tool = spec_tool.build(DemoWeatherService())
    assert tool.name == "get_weather"
    import json as _json

    payload = _json.loads(tool.invoke({"city": "中山", "date": "2026-01-01"}))
    assert payload == {
        "city": "中山", "date": "2026-01-01",
        "condition": "晴朗", "temperature": 33, "humidity": 60,
    }
    # content_and_artifact 形态：直调 func 拿 (content, artifact)，
    # artifact 是 {"a2ui": [消息数组]}，信封与组件树形状正确
    content, artifact = tool.func(city="中山", date="2026-01-01")
    assert _json.loads(content) == payload
    assert set(artifact.keys()) == {"a2ui"}
    messages = artifact["a2ui"]
    assert [m["version"] for m in messages] == ["v0.9", "v0.9"]
    assert set(messages[0]["createSurface"].keys()) == {"surfaceId", "catalogId"}
    components = messages[1]["updateComponents"]["components"]
    assert components[0] == {"id": "root", "component": "Card", "child": "body"}
    assert any(c["id"] == "city" and c["text"] == "中山" for c in components)
