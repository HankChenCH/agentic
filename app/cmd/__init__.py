"""入口层：每个子包一个可独立启动的应用（进程入口）。

http —— FastAPI 应用（uvicorn 导入串 app.cmd.http.main:server）；
task_executor —— Celery 任务执行器（celery -A app.cmd.task_executor.main worker）；
admin —— 维护管理命令行（python -m app.cmd.admin <域> <命令>）。

共用装配在 app.core.container，入口包只保留各自应用特有的接线。
命令体不在本层：按产物类型分居 api/（HTTP 端点体）、tasks/（Celery 任务体）、
commands/（CLI 命令体）——`cmd` 与 `commands` 只是前缀相像，前者是进程入口，后者是
admin 入口挂载的 Typer 命令实体。
"""
