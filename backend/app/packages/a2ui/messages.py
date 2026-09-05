"""A2UI v0.9 服务端消息构造库（声明式 UI 的组装侧基石）。

A2UI（Agent to UI，https://a2ui.org）是 agent 向客户端「说 UI」的开放标准：
服务端发送声明式 JSON 消息（surface 生命周期 + 组件树），客户端用原生渲染器
呈现——不向客户端投递任何可执行代码。契约与后端内聚一包（同 ``signal/`` 范式：
领域无关、禁向上依赖），供各组件/工具把结构化数据组装成 A2UI 载荷。

规范锚点（v0.9 stable）：
- 信封 schema：https://a2ui.org/specification/v0_9/json/server_to_client.json
  —— 消息 oneOf ``createSurface`` / ``updateComponents`` / ``updateDataModel`` /
  ``deleteSurface``，每条携带版本常量 ``"v0.9"``；载荷形态是**消息数组**
  （``server_to_client_list.json``），客户端按序处理。
- 标准基础组件目录：https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json
  —— ``createSurface.catalogId`` 即该 URL；组件为扁平对象
  （``{"id", "component": "Text", ...props}``），布局组件的 children 以**组件 id
  数组**引用，组件树根的 id 固定为 ``"root"``。

本模块覆盖「静态卡片 + 按钮回传」子集：``createSurface`` + ``updateComponents``
+ 字面量属性的 Text/Row/Column/Card/Divider/Image 组件，以及 Button（第一批
交互组件：``action.event`` 点击回传，context 仅支持字面量值）。刻意留白的
扩展面：数据绑定（``updateDataModel`` + ``{"path": ...}`` 动态值）与
``functionCall`` 型 action——出现真实需求时再引入，避免为用不到的协议面
预付复杂度。
"""

from typing import Any

A2UI_SPEC_VERSION = "v0.9"

# 标准基础组件目录的 catalogId（官方规定为目录 schema 的 URL）
BASIC_CATALOG_ID = "https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json"

# 组件树根的约定 id（updateComponents 中必须存在一个 id="root" 的组件）
ROOT_COMPONENT_ID = "root"

# ag-ui 传输绑定：A2UI 消息数组经 ag-ui 的 CUSTOM 事件下发，事件名约定为 "a2ui"
# （ag-ui 的 Custom 即 {name, value} 扩展口，两端以此名对齐；A2UI 官方
# 未钉死 AG-UI 绑定的事件名，本仓库自约定并在两侧 AGENTS.md 记录）。
A2UI_CUSTOM_EVENT_NAME = "a2ui"


def create_surface(surface_id: str) -> dict[str, Any]:
    """创建 surface 并开始渲染（v0.9 里 createSurface 自带 begin 语义）。"""
    return {
        "version": A2UI_SPEC_VERSION,
        "createSurface": {"surfaceId": surface_id, "catalogId": BASIC_CATALOG_ID},
    }


def update_components(surface_id: str, components: list[dict[str, Any]]) -> dict[str, Any]:
    """为 surface 下发完整组件树（可多次增量下发，本库一次成树）。"""
    return {
        "version": A2UI_SPEC_VERSION,
        "updateComponents": {"surfaceId": surface_id, "components": components},
    }


def render_surface(surface_id: str, components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """最小可渲染消息序：createSurface → updateComponents（最常用组合）。"""
    return [create_surface(surface_id), update_components(surface_id, components)]


# ----------------------------------------------------------------------
# 标准基础目录组件构造器（字面量属性子集；id 由调用方保证 surface 内唯一）
# ----------------------------------------------------------------------
def text(component_id: str, value: str, *, variant: str | None = None) -> dict[str, Any]:
    """文本组件；variant ∈ h1–h5/caption/body。"""
    component: dict[str, Any] = {"id": component_id, "component": "Text", "text": value}
    if variant is not None:
        component["variant"] = variant
    return component


def row(
    component_id: str,
    children: list[str],
    *,
    align: str | None = None,
    justify: str | None = None,
) -> dict[str, Any]:
    """横向布局；children 是子组件 id 数组。

    align ∈ start/center/end/stretch（交叉轴），justify ∈ start/center/end/
    spaceBetween/spaceAround/spaceEvenly/stretch（主轴）。
    """
    component: dict[str, Any] = {"id": component_id, "component": "Row", "children": children}
    if align is not None:
        component["align"] = align
    if justify is not None:
        component["justify"] = justify
    return component


def column(
    component_id: str,
    children: list[str],
    *,
    align: str | None = None,
    justify: str | None = None,
) -> dict[str, Any]:
    """纵向布局；children 是子组件 id 数组（枚举同 row）。"""
    component: dict[str, Any] = {"id": component_id, "component": "Column", "children": children}
    if align is not None:
        component["align"] = align
    if justify is not None:
        component["justify"] = justify
    return component


def card(component_id: str, child: str) -> dict[str, Any]:
    """卡片容器；child 是唯一子组件的 id。"""
    return {"id": component_id, "component": "Card", "child": child}


def divider(component_id: str, *, axis: str = "horizontal") -> dict[str, Any]:
    """分隔线；axis ∈ horizontal/vertical。"""
    return {"id": component_id, "component": "Divider", "axis": axis}


def icon(component_id: str, svg_path: str) -> dict[str, Any]:
    """单色矢量图标：svg_path 为 24x24 视口的单条 path 数据（fill 型，
    颜色由客户端主题的 --a2ui-icon-color 控制）。注意协议渲染端把 viewBox
    硬编码为 "0 0 24 24"——960 坐标系的图标库（如 Material Symbols）须先
    做坐标变换，示例见 weather_surface 的天气图标表。"""
    return {
        "id": component_id,
        "component": "Icon",
        "name": {"svgPath": svg_path},
    }


def image(component_id: str, url: str, *, description: str | None = None) -> dict[str, Any]:
    """图片组件；url 为图片地址（A2UI 不会代拉需要鉴权的资源）。"""
    component: dict[str, Any] = {"id": component_id, "component": "Image", "url": url}
    if description is not None:
        component["description"] = description
    return component


# 点击回传的事件名约定：前端 actionHandler 收到该事件时把 context.text 作为
# 用户新消息追加进会话（chat-page 侧 a2ui-data.tsx 实现）——按钮即「替用户
# 说一句话」，不引入独立的动作执行通道
CHAT_SEND_ACTION = "chat.send"


def button(
    component_id: str,
    child: str,
    *,
    action_name: str,
    context: dict[str, Any] | None = None,
    variant: str | None = None,
) -> dict[str, Any]:
    """按钮组件（交互回传）：child 是 label 子组件的 id（Button.child 为组件
    id 引用，与 Card.child 同口径）；action.event 为点击回传事件（name +
    字面量 context），客户端经 MessageProcessor 的 actionHandler 接收。
    variant ∈ default/primary/borderless。"""
    event: dict[str, Any] = {"name": action_name}
    if context:
        event["context"] = context
    component: dict[str, Any] = {
        "id": component_id,
        "component": "Button",
        "child": child,
        "action": {"event": event},
    }
    if variant is not None:
        component["variant"] = variant
    return component
