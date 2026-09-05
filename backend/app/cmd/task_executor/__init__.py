"""任务执行器入口包：``python -m app.cmd.task_executor`` 或 ``celery -A app.cmd.task_executor.main worker``。

任务本体在 app/tasks 包（其 __init__ 自动导入全部模块完成注册）。
"""
