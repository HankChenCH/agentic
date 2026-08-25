"""任务层：``app/tasks`` 下每个模块自动注册为 Celery 任务。

新增任务 = 在本包下新建 ``.py`` 文件（不以 ``_`` 开头），用
``@celery_app.task`` + ``@wireup.integration.celery.inject`` 定义即可，
无需改动 task_executor——本 ``__init__`` 自动导入所有同级模块触发注册。

迁移真实业务任务（如对话后置处理）时注意：
- 任务载荷只传可序列化的 ID / 原始数据，worker 侧重新加载领域对象；
- 任务需保证幂等（重试会重复执行）；
- SQLite Engine 在 prefork 下的连接池共享问题需一并加固
  （NullPool 或 worker_process_init 后再初始化容器）。
"""

from importlib import import_module
from pathlib import Path

for _file in sorted(Path(__file__).parent.glob("*.py")):
    if not _file.name.startswith("_"):
        import_module(f"{__name__}.{_file.stem}")
