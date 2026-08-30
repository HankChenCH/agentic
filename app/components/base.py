"""组件范式核心：声明式 ComponentSpec + 注册表 + 能力清单导出 + 工具运行时
注入辅助。

能力范式（v1 能力集合仅 tools）：组件经 ``register_component`` 向注册表
登记静态 spec——spec 是纯数据（可枚举、可校验、可序列化），不依赖任何
DI；装配是 DI 行为，由各组件 manifest 里的装配器（@injectable，持门面
服务）在运行期把 ``ToolSpec.build`` 实例化为 StructuredTool。声明与构造
同住 manifest.py，注册表（静态）与装配（DI）共用同一份声明，不会双源
漂移。spec/装配分离让「组件导出什么能力」无需实例化服务即可回答
（测试、调试与未来的能力清单端点直接读注册表）。

运行时身份注入：agent 实例按 agentic_id 跨会话缓存，工具不能闭包捕获
用户/会话身份，也不能用 ContextVar（sync 生成器跨 context 副本 + 工具跑
在 langgraph 线程池，set/reset 跨不过边界）——统一走 langchain 的 config
注入：工具函数声明 ``config: RunnableConfig`` 形参（StructuredTool 按显式
args_schema 构造，该参数不进 LLM 工具 schema、运行时注入），langgraph 把
``BaseAgent._config`` 的 ``configurable.thread_id / user_id`` 透传进来。
``configurable_*`` 系列是各组件工具读取该注入的公共入口（缺失返回 None，
如单测直调）。

组件解剖学（结构范式，总纲见包 ``__init__`` docstring）::

    <component>/
    ├── __init__.py      # 必有：公共面再导出，不写逻辑
    ├── manifest.py      # 必有：register_component(spec) + 工具构造 + 导出契约模型
    ├── ability/         # 必有：能力模块，按能力命名，持 @injectable 门面服务
    ├── internal/        # 可选：跨能力共享机件；app 层禁入（tests 与 app/commands 白盒豁免）
    ├── admin.py         # 可选：管理面端口实现（wireup as_type 回填领域端口）
    └── repositories/    # 可选：组件自有存储策略（ABC + 实现）

约束由 ``tests/test_component_structure.py`` 机器强制；本注册表的唯一
显式消费点是 ``app.agents.toolbox.AgentToolbox``（跨组件工具名冲突在
装配期 fail-fast）。
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Callable
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel


def configurable_uuid(config: RunnableConfig | None, key: str) -> UUID | None:
    """从注入的运行配置 configurable 取 UUID 身份；缺失（如单测直调）返回 None。"""
    raw = (config or {}).get("configurable", {}).get(key)
    if raw is None:
        return None
    try:
        return raw if isinstance(raw, UUID) else UUID(str(raw))
    except ValueError:
        return None


def configurable_user(config: RunnableConfig | None) -> UUID | None:
    """当前用户（用户级作用域：记忆、知识库可见性）。"""
    return configurable_uuid(config, "user_id")


def configurable_thread(config: RunnableConfig | None) -> UUID | None:
    """当前会话 thread（会话内去重与投喂登记）。"""
    return configurable_uuid(config, "thread_id")


@dataclass(frozen=True)
class ToolSpec:
    """声明式工具描述符：能力导出面的最小单元（v1 唯一能力种类）。

    ``name`` / ``description`` / ``args_model`` 面向 LLM 与能力清单导出；
    ``build`` 是构造工厂，签名由各组件自约（``(门面服务…, agentic_id?)``），
    装配器在 DI 语境下调它产出绑定服务后的 StructuredTool。
    """

    name: str
    description: str
    args_model: type[BaseModel]
    build: Callable[..., Any]


@dataclass(frozen=True)
class ComponentSpec:
    """组件能力声明：注册表的登记单元。

    ``name`` 全局唯一（如 "memory"）；``title``/``description`` 供能力清单
    消费方阅读；``tools`` 为该组件向 agent 导出的全部工具。
    """

    name: str
    title: str
    description: str
    tools: tuple[ToolSpec, ...] = ()


COMPONENT_REGISTRY: dict[str, ComponentSpec] = {}


def register_component(spec: ComponentSpec) -> ComponentSpec:
    """登记组件 spec：组件重名、组内工具重名、空描述一律 fail-fast。

    风格对齐 ``app.agents.base.register_agent``（import 期副作用注册），
    但重复登记视为错误而非覆盖——组件身份必须唯一。
    """
    if spec.name in COMPONENT_REGISTRY:
        raise ValueError(f"component already registered: {spec.name}")
    seen: set[str] = set()
    for tool in spec.tools:
        if not tool.name or not tool.description:
            raise ValueError(f"component {spec.name}: tool name/description required, got {tool.name!r}")
        if tool.name in seen:
            raise ValueError(f"component {spec.name}: duplicate tool name {tool.name!r}")
        seen.add(tool.name)
    COMPONENT_REGISTRY[spec.name] = spec
    return spec


def describe_capabilities(specs: Iterable[ComponentSpec] | None = None) -> list[dict[str, Any]]:
    """能力清单导出（后端结构化）：组件 → 工具（名/描述/参数 JSON schema）。

    ``specs`` 缺省取全量注册表。v1 只服务测试与调试；未来对前端开放时，
    HTTP 能力清单端点只需把本函数的返回序列化出去。
    """
    return [
        {
            "component": spec.name,
            "title": spec.title,
            "description": spec.description,
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.args_model.model_json_schema(),
                }
                for tool in spec.tools
            ],
        }
        for spec in (specs if specs is not None else COMPONENT_REGISTRY.values())
    ]
