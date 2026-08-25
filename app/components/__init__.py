"""组件层：自内聚的能力组件。

每个组件自带业务门面（service）、对外工具（tools）与自有 repositories，
配合共享基础设施层（app/infrastructures）自行管理存储与获取策略；
表模型统一放在 app/models/domain。

新增组件（如 retriever）时在包下新建子包，并在 app.core.container 的
wireup injectables 中注册本包即可被扫描装配。
"""
