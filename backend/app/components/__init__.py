"""组件层：自内聚的能力组件，以统一范式声明与导出能力。

组件解剖学（结构范式，机器强制见 tests/test_component_structure.py）::

    <component>/
    ├── __init__.py      # 必有：公共面再导出，不写逻辑
    ├── manifest.py      # 必有：register_component(spec) + 工具构造 + 导出契约模型
    ├── ability/         # 必有：能力模块，按能力命名，持 @injectable 门面服务
    ├── internal/        # 可选：跨能力共享机件；app 层禁入（tests 与 app/commands 白盒豁免）
    ├── admin.py         # 可选：管理面端口实现（wireup as_type 回填领域端口）
    └── repositories/    # 可选：组件自有存储策略（ABC + 实现）

能力范式（v1 能力集合仅 tools）：能力经 manifest 的
``register_component(ComponentSpec(...))`` 静态登记进
``app.components.base.COMPONENT_REGISTRY``（组件重名/工具重名/空描述
fail-fast）；装配器（manifest 里的 @injectable，持门面服务）在 DI 语境
下把 ``ToolSpec.build`` 实例化为 StructuredTool。spec 与装配分离——
「导出什么能力」无需实例化服务即可枚举（``describe_capabilities()``）。

新增组件三步：① 新建子包按上述解剖学落位；② manifest.py 声明 spec 并
实现装配器（@injectable）；③ 在 ``app/agents/toolbox.py`` 加一个装配器
字段（全系统唯一显式组件清单点）——AgentFactory 与各 agent 的
build_tools 不必再变。包内模块间一律用完整子模块路径互相导入，禁止经
包 ``__init__`` 取属性（避免初始化环）。

每个组件自带业务门面（ability/）、对外工具（manifest 声明）与可选的
自有 repositories；表模型统一放在 app/models/domain；wireup 经
app.core.container 扫描本包完成依赖装配。
"""

# 触发各组件 manifest 的 import 期注册（风格同 app.agents.factory 触发内置智能体注册）
import app.components.a2ui.manifest  # noqa: F401,E402
import app.components.demo.manifest  # noqa: F401,E402
import app.components.human_agent.manifest  # noqa: F401,E402
import app.components.knowledge.manifest  # noqa: F401,E402
import app.components.memory.manifest  # noqa: F401,E402
