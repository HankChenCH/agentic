"""被驱动适配器层：实现 domain 端口、持有基础设施机制，可整体替换。

- ``persistence/``：关系型仓储（实现 domain 各聚合的仓储端口）；
- ``tasking/``：Celery 应用实例与任务队列 conf（tasks/ 层与发送方共用）；
- ``llm/`` ``db/`` ``redis/`` ``vector/`` ``filesystem/`` ``document_parser/``：
  供应商驱动与工厂（vector/ 另含知识/记忆两个向量索引适配器）。

依赖方向：本层 ──► {domain 端口, models, core/config}；严禁被 domain 反向
依赖（领域只认端口，端口声明住 domain，本层经 wireup ``as_type`` 回填）。
"""
