"""入口层：每个子包一个可独立启动的应用（进程入口）。

http —— FastAPI 应用（uvicorn 导入串 app.cmd.http.main:server）；
task_executor —— Celery 任务执行器（celery -A app.cmd.task_executor.main worker）。

共用装配在 app.core.container，入口包只保留各自应用特有的接线。
"""
