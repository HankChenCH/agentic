"""维护管理命令行（cmd 复合入口包，``python -m app.cmd.admin <域> <命令>``）。

本包只留入口装配（Typer app + 全局选项桥接）；各域命令实现在 ``app/commands/``。
"""
