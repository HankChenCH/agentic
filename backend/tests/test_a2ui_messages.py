"""A2UI v0.9 消息构造库（app/packages/a2ui）的单元测试：结构契约。

载荷形态（消息数组 + 信封字段 + 组件树引用完整性）对齐官方规范
（server_to_client_list.json + basic catalog，生成载荷已离线通过官方
schema 校验，本套件锁定结构不变量防回归）。
"""


from app.packages.a2ui import (
    A2UI_CUSTOM_EVENT_NAME,
    A2UI_SPEC_VERSION,
    BASIC_CATALOG_ID,
    ROOT_COMPONENT_ID,
    card,
    column,
    divider,
    image,
    render_surface,
    row,
    text,
    update_components,
)


def test_render_surface_pairs_create_and_update_with_spec_constants():
    surface_id = "weather-abc"
    messages = render_surface(surface_id, [
        card(ROOT_COMPONENT_ID, "body"),
        column("body", ["hello"]),
        text("hello", "你好"),
    ])

    assert [m["version"] for m in messages] == [A2UI_SPEC_VERSION, A2UI_SPEC_VERSION] == ["v0.9", "v0.9"]
    assert messages[0]["createSurface"] == {"surfaceId": surface_id, "catalogId": BASIC_CATALOG_ID}
    assert messages[1]["updateComponents"]["surfaceId"] == surface_id
    # 组件树根 id 固定为 root（updateComponents 的规范不变量）
    assert ROOT_COMPONENT_ID == "root"
    assert messages[1]["updateComponents"]["components"][0]["id"] == ROOT_COMPONENT_ID


def test_component_builders_shape():
    assert text("t1", "晴", variant="h2") == {
        "id": "t1", "component": "Text", "text": "晴", "variant": "h2",
    }
    assert text("t2", "x") == {"id": "t2", "component": "Text", "text": "x"}
    assert row("r1", ["t1", "t2"], align="center") == {
        "id": "r1", "component": "Row", "children": ["t1", "t2"], "align": "center",
    }
    assert column("c1", ["r1"]) == {"id": "c1", "component": "Column", "children": ["r1"]}
    assert card("root", "c1") == {"id": "root", "component": "Card", "child": "c1"}
    assert divider("d1") == {"id": "d1", "component": "Divider", "axis": "horizontal"}
    assert image("i1", "https://x/y.png", description="雷达图") == {
        "id": "i1", "component": "Image", "url": "https://x/y.png", "description": "雷达图",
    }
    # update_components 单独可用（增量下发场景）
    assert update_components("s1", [text("t", "x")])["updateComponents"]["components"] == [
        {"id": "t", "component": "Text", "text": "x"}
    ]


def test_all_children_references_resolve_within_surface():
    """布局组件引用的每个 id 都必须在组件清单中定义（悬挂引用是渲染期事故）。"""
    components = [
        card("root", "body"),
        column("body", ["title", "sep", "main"]),
        row("title", ["city", "date"]),
        text("city", "中山"),
        text("date", "2026-09-05"),
        divider("sep"),
        row("main", ["temperature", "condition"]),
        text("temperature", "33°", variant="h2"),
        text("condition", "晴朗"),
    ]
    defined = {c["id"] for c in components}
    for component in components:
        for child in component.get("children", []) + ([component["child"]] if "child" in component else []):
            assert child in defined, f"组件 {component['id']} 引用了未定义的子组件 {child}"


def test_custom_event_name_is_stable_contract():
    # ag-ui CUSTOM 事件名是前后端对齐的传输契约（两侧 AGENTS.md 记录），
    # 变更即断链，本用例防止无意识改名
    assert A2UI_CUSTOM_EVENT_NAME == "a2ui"


def test_builders_take_caller_supplied_ids():
    """组件 id 由调用方给定——构造器不做生成，避免 surface 内 id 冲突的隐形来源。"""
    import inspect

    for builder in (text, row, column, card, divider, image):
        assert list(inspect.signature(builder).parameters)[0] == "component_id"
