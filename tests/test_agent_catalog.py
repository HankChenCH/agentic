"""智能体目录服务：注册表展示元数据 → 前端选择 UI 契约。

用替身工厂（鸭子类型对齐 AgentFactory 的目录面）避免真实模型/组件装配；
真实注册表的元数据完整性由 register_agent 的 fail-fast 校验兜底。
"""

from app.services.orchestration.agent_catalog import AgentCatalogService

from conftest import StubLoggerFactory


class _FakeAgent:
    def __init__(self, supports_vision):
        self.supports_vision = supports_vision


class FakeFactory:
    """目录所需的最小工厂面：default/registered_ids/agent_class/create。"""

    def __init__(self, agents, error_ids=()):
        # agents: {id: (display_name, description, supports_vision)}
        self._agents = agents
        self.default_agentic_id = "builtin:demo"
        self._error_ids = set(error_ids)

    def registered_ids(self):
        return list(self._agents)

    def agent_class(self, agentic_id):
        if agentic_id not in self._agents:
            return None
        name, description, _ = self._agents[agentic_id]
        return type("FakeAgentCls", (), {"display_name": name, "description": description})()

    def create(self, agentic_id):
        if agentic_id in self._error_ids:
            raise RuntimeError("agent build failed")
        return _FakeAgent(self._agents[agentic_id][2])


def _catalog(factory):
    return AgentCatalogService(agent_factory=factory, logger_factory=StubLoggerFactory())


def test_describe_lists_agents_with_default_first():
    factory = FakeFactory({
        "builtin:demo": ("演示助手", "通用助手", True),
        "builtin:rag": ("知识库问答", "检索问答", False),
    })
    result = _catalog(factory).describe()

    assert result["defaultAgentId"] == "builtin:demo"
    assert [a["id"] for a in result["agents"]] == ["builtin:demo", "builtin:rag"]
    demo = result["agents"][0]
    assert demo["name"] == "演示助手"
    assert demo["supportsVision"] is True
    assert result["agents"][1]["supportsVision"] is False


def test_vision_probe_failure_degrades_to_false():
    factory = FakeFactory(
        {"builtin:demo": ("演示助手", "通用助手", True), "builtin:rag": ("知识库问答", "检索问答", True)},
        error_ids=("builtin:rag",),
    )
    result = _catalog(factory).describe()

    by_id = {a["id"]: a for a in result["agents"]}
    assert by_id["builtin:rag"]["supportsVision"] is False
    assert by_id["builtin:demo"]["supportsVision"] is True
