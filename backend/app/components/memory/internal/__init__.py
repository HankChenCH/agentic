"""memory 组件的跨能力共享机件：纯函数 / 数据结构，不持服务、不对外导出。

app 层（orchestration/agents/api/tasks/domain）禁止 import 本包；白盒
消费方仅限 tests/ 与维护 CLI（app/commands）。规则由
``tests/test_component_structure.py`` 强制。
"""
