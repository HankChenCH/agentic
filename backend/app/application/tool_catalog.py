"""工具能力目录：把静态注册表的展示元数据以结构化形式暴露给前端。

为什么在编排层包一层：能力清单的唯一事实源是 ``app.components`` 的
``COMPONENT_REGISTRY``（``describe_capabilities()``），而依赖箭头表
（AST 强制）禁止 api 直触 components——api 只消费 orchestration/domain。
目录与用户无关、纯读静态 spec，因此无 DI 依赖、无状态。

``title`` 的定位：纯展示元数据（前端 UI 标识化的中文短标签），不进 LLM
工具 schema、不改变协议事件与落库的机器名（``tool_call_name``）。
"""

from dataclasses import dataclass
from typing import Any

from wireup import injectable

from app.components.base import describe_capabilities


@injectable
@dataclass
class ToolCatalogService:
    """工具能力目录服务：``describe()`` 返回全量注册表的能力清单。"""

    def describe(self) -> dict[str, Any]:
        return {"components": describe_capabilities()}
