"""human_agent + a2ui 组件的单元测试：真实仓储（临时 SQLite）+ 纯函数翻译层。

覆盖：仓储 CRUD（姓名唯一冲突收口）、directory 服务在线优先排序、
human_agent_list 工具查库契约、a2ui_compose 树校验/扁平化（根组件命名、
button action 形状、限额拒绝）、packages/a2ui.button 构造函数、
human-agent 管理命令（typer CliRunner）。
"""

import json
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from app.adapters.persistence.human_agent_repository import HumanAgentRepository
from app.application import HumanAgentAppService
from app.commands.human_agent import app as human_agent_cli
from app.components.a2ui.ability.compose import A2uiNode, ComposeError, compose_components
from app.components.human_agent.ability.directory import HumanAgentDirectoryService
from app.components.human_agent.manifest import _SPEC as _HUMAN_AGENT_SPEC
from app.components.a2ui.manifest import _SPEC as _A2UI_SPEC, A2uiComposeArgs
from app.models.domain.human_agent import HumanAgent, HumanAgentStatus
from app.packages.a2ui import (
    CHAT_SEND_ACTION,
    ROOT_COMPONENT_ID,
    button,
)


@pytest.fixture()
def repo(engine):
    return HumanAgentRepository(engine=engine)


def _agent(name="张三", status=HumanAgentStatus.ONLINE, **kwargs) -> HumanAgent:
    kwargs.setdefault("title", "高级客服")
    return HumanAgent(name=name, status=status, **kwargs)


# ---------------------------------------------------------------- 仓储
def test_repository_crud_roundtrip(repo):
    created = repo.create_agent(_agent(specialty="退换货"))
    assert created.id is not None

    fetched = repo.get_agent(created.id)
    assert fetched.name == "张三" and fetched.specialty == "退换货"

    fetched.title = "资深客服"
    assert repo.update_agent(fetched).title == "资深客服"

    assert repo.get_agent_by_name("张三").id == created.id
    assert repo.delete_agent(created.id)
    assert repo.get_agent(created.id) is None
    assert not repo.delete_agent(created.id)


def test_repository_rejects_duplicate_name(repo):
    repo.create_agent(_agent(name="李四"))
    from app.domain.ports import RepositoryConflictError

    with pytest.raises(RepositoryConflictError):
        repo.create_agent(_agent(name="李四"))


def test_repository_list_all(repo):
    repo.create_agent(_agent(name="a"))
    repo.create_agent(_agent(name="b"))
    assert {a.name for a in repo.list_agents()} == {"a", "b"}


# ---------------------------------------------------------------- 目录门面
def test_directory_sorts_online_first(repo):
    repo.create_agent(_agent(name="离线", status=HumanAgentStatus.OFFLINE))
    repo.create_agent(_agent(name="在线", status=HumanAgentStatus.ONLINE))
    repo.create_agent(_agent(name="忙碌", status=HumanAgentStatus.BUSY))

    ordered = [a.name for a in HumanAgentDirectoryService(repo=repo).list_agents()]
    assert ordered[0] == "在线"


# ---------------------------------------------------------------- 工具契约
def test_human_agent_list_tool_returns_profiles(repo):
    repo.create_agent(_agent(name="王五", title="售后专员", specialty="退款"))
    tool = _HUMAN_AGENT_SPEC.tools[0].build(HumanAgentDirectoryService(repo=repo))
    assert tool.name == "human_agent_list"

    payload = json.loads(tool.invoke({}))
    assert payload["agents"][0]["name"] == "王五"
    assert payload["agents"][0]["title"] == "售后专员"
    assert payload["agents"][0]["status"] == "online"


def test_human_agent_list_tool_empty(repo):
    tool = _HUMAN_AGENT_SPEC.tools[0].build(HumanAgentDirectoryService(repo=repo))
    assert tool.invoke({}) == "当前没有可转接的人工客服坐席"


# ---------------------------------------------------------------- 生成通道
def test_compose_flattens_tree_and_names_root():
    tree = [A2uiNode(type="card", children=[
        A2uiNode(type="column", children=[
            A2uiNode(type="text", text="人工客服", variant="h4"),
            A2uiNode(type="row", children=[
                A2uiNode(type="text", text="张三（在线）"),
                A2uiNode(type="button", text="转接", action_text="我要转接人工客服：张三"),
            ]),
        ]),
    ])]
    flat = compose_components(tree)

    # 根组件（Card）名为 root；emit 是后序遍历，根在数组尾部附近
    root = next(c for c in flat if c["component"] == "Card")
    assert root["id"] == ROOT_COMPONENT_ID
    # 引用闭合：所有 children/child 引用的 id 都在扁平数组中
    ids = {c["id"] for c in flat}
    for component in flat:
        for ref in [component.get("child"), *(component.get("children") or [])]:
            if ref:
                assert ref in ids
    # button 展开为 label Text + Button 两组件，action 形状符合回传契约
    btn = next(c for c in flat if c["component"] == "Button")
    assert btn["child"] in ids
    assert btn["action"]["event"]["name"] == CHAT_SEND_ACTION
    assert btn["action"]["event"]["context"]["text"] == "我要转接人工客服：张三"


def test_compose_wraps_multiple_top_level_nodes_in_column():
    flat = compose_components([
        A2uiNode(type="text", text="第一行"),
        A2uiNode(type="text", text="第二行"),
    ])
    assert flat[-1]["id"] == ROOT_COMPONENT_ID and flat[-1]["component"] == "Column"
    assert len(flat[-1]["children"]) == 2


def test_compose_passes_button_variant_through():
    flat = compose_components([
        A2uiNode(type="button", text="转接", action_text="转接张三", button_variant="primary"),
        A2uiNode(type="button", text="次级", action_text="次级操作"),
    ])
    variants = [c.get("variant") for c in flat if c["component"] == "Button"]
    assert variants == ["primary", None]


def test_compose_rejects_invalid_trees():
    with pytest.raises(ComposeError, match="text 组件缺少文案"):
        compose_components([A2uiNode(type="text")])
    with pytest.raises(ComposeError, match="action_text"):
        compose_components([A2uiNode(type="button", text="转接")])
    with pytest.raises(ComposeError, match="有且只有一个子组件"):
        compose_components([A2uiNode(type="card", children=[])])
    with pytest.raises(ComposeError, match="深度超过上限"):
        deep = A2uiNode(type="text", text="底")
        for _ in range(8):
            deep = A2uiNode(type="column", children=[deep])
        compose_components([deep])
    with pytest.raises(ComposeError, match="组件数超过上限"):
        compose_components([A2uiNode(type="row", children=[
            A2uiNode(type="text", text=f"t{i}") for i in range(70)
        ])])


def test_a2ui_compose_tool_returns_content_and_artifact():
    tool = _A2UI_SPEC.tools[0].build()
    assert tool.name == "a2ui_compose"

    # func 直调取原始 (content, artifact) 二元组（invoke 会包成 ToolMessage）；
    # 入参先过 schema 校验（真实链路由 langchain 按 args_schema 完成）
    nodes = A2uiComposeArgs.model_validate({"components": [
        {"type": "card", "children": [{"type": "text", "text": "卡片"}]},
    ]}).components
    content, artifact = tool.func(components=nodes)
    assert "卡片已生成" in content
    messages = artifact["a2ui"]
    assert messages[0]["createSurface"]["surfaceId"].startswith("composed-")
    assert any(c["id"] == ROOT_COMPONENT_ID for c in messages[1]["updateComponents"]["components"])


def test_a2ui_compose_tool_rejects_invalid_tree():
    tool = _A2UI_SPEC.tools[0].build()
    nodes = A2uiComposeArgs.model_validate({"components": [{"type": "text"}]}).components
    content, artifact = tool.func(components=nodes)
    assert "卡片生成失败" in content
    assert artifact is None


# ---------------------------------------------------------------- a2ui 库
def test_messages_button_constructor():
    component = button("b1", "b1-label", action_name=CHAT_SEND_ACTION, context={"text": "hi"}, variant="primary")
    assert component == {
        "id": "b1", "component": "Button", "child": "b1-label",
        "action": {"event": {"name": CHAT_SEND_ACTION, "context": {"text": "hi"}}},
        "variant": "primary",
    }
    # context 缺省 / variant 缺省时不产生空键
    minimal = button("b2", "b2-label", action_name="x.y")
    assert "variant" not in minimal and minimal["action"]["event"] == {"name": "x.y"}


# ---------------------------------------------------------------- 管理命令
def test_admin_cli_add_and_list(engine, monkeypatch):
    monkeypatch.setattr(
        "app.commands.human_agent._service",
        lambda: HumanAgentAppService(repo=HumanAgentRepository(engine=engine)),
    )
    runner = CliRunner()
    result = runner.invoke(human_agent_cli, [
        "add", "--name", "赵六", "--title", "高级客服", "--specialty", "账单", "--status", "online",
    ])
    assert result.exit_code == 0 and "赵六" in result.output

    listing = runner.invoke(human_agent_cli, ["list"])
    assert listing.exit_code == 0 and "赵六" in listing.output and "高级客服" in listing.output


def test_admin_cli_update_and_remove(engine, monkeypatch):
    monkeypatch.setattr(
        "app.commands.human_agent._service",
        lambda: HumanAgentAppService(repo=HumanAgentRepository(engine=engine)),
    )
    repo = HumanAgentRepository(engine=engine)
    created = repo.create_agent(_agent(name="钱七"))
    runner = CliRunner()

    updated = runner.invoke(human_agent_cli, [
        "update", str(created.id), "--status", "busy", "--title", "售后专员",
    ])
    assert updated.exit_code == 0 and "售后专员" in updated.output
    assert repo.get_agent(created.id).status == HumanAgentStatus.BUSY

    removed = runner.invoke(human_agent_cli, ["remove", str(uuid4())])
    assert removed.exit_code == 1 and "不存在" in removed.output

    removed = runner.invoke(human_agent_cli, ["remove", str(created.id)])
    assert removed.exit_code == 0
    assert repo.get_agent(created.id) is None
