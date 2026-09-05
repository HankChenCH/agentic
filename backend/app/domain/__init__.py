"""领域层：按聚合组织的领域服务与端口，管理侧端点与后台任务的直接消费面。

每聚合一个包：领域服务 + ``ports.py``（本聚合的仓储/向量索引/网关协议；
跨聚合基础契约在 ``domain/ports`` 包）。依赖方向：只允许向下依赖
models/domain（共享数据形状）——零 adapters 依赖，外部机制一律经端口由
adapters 回填；严禁 import application / components / agents / api。
依赖箭头表见 backend/README.md 与 tests/test_layer_boundaries.py。
"""
