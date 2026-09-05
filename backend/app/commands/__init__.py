"""admin 命令行的领域命令层：一个域一个模块，各自暴露 ``typer.Typer`` 实例。

与 ``app/tasks`` 之于 ``app/cmd/task_executor`` 同构——``app/cmd/admin`` 只留
复合入口（装配 + 全局选项桥接），命令实现按域放在本包，由入口显式导入并
``add_typer`` 挂载（命令数量少且需要命名，无需 tasks 那样的自动注册）。
新增域 = 新建模块定义 ``app = typer.Typer(...)``，再到 admin 入口挂载。
"""
