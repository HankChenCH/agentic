"""可复用能力库层：契约 + 后端内聚一包，领域无关。

与 ``adapters/``（共享驱动/连接，vendor SDK 适配）的分工：本层把
驱动组装成领域能力库（如信号 = Redis 驱动 + Event 行为协议），契约与
后端同包内聚，消费方（application/domain/components/tasks 等）向下只注入契约。
依赖纪律：本层禁止 import app.domain / app.application / app.components /
app.agents / app.api / app.tasks / app.models（AST 扫描强制，
见 tests/test_layer_boundaries.py），底层连接一律来自 adapters。

新增居民遵循 ``signal/`` 的解剖：契约模块 + 各后端模块 + ``__init__``
公共面再导出；包内模块间完整子模块路径互导，禁经 ``__init__`` 取属性。
"""
