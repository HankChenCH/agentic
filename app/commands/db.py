"""数据库迁移命令域：Alembic 命令的薄封装。

``python -m app.cmd.admin db <命令>``；目标库 URL 与表元数据由
migrations/env.py 从应用配置链解析（AppConfig → DatabaseFactory），
本模块只负责把 Alembic 挂进 admin 入口并锚定 alembic.ini（其
``%(here)s`` 定位不依赖 CWD）。顶层 ``--config-dir`` / ``--env-file``
由 admin 入口 callback 先行桥接，env.py 内的 AppConfig 解析照常生效。
"""

from pathlib import Path
from typing import Annotated

import typer
from alembic import command
from alembic.config import Config

# server/ 根：app/commands/db.py → 上溯 2 级
_SERVER_ROOT = Path(__file__).resolve().parents[2]
_INI_PATH = _SERVER_ROOT / "alembic.ini"

app = typer.Typer(no_args_is_help=True, help="数据库迁移（Alembic）")


def _config() -> Config:
    return Config(str(_INI_PATH))


@app.command("upgrade")
def upgrade_cmd(
    revision: Annotated[str, typer.Argument(help="目标版本（默认 head）")] = "head",
) -> None:
    """把数据库迁移到目标版本（默认最新）。"""
    command.upgrade(_config(), revision)


@app.command("downgrade")
def downgrade_cmd(
    revision: Annotated[str, typer.Argument(help="回退目标版本（如 -1 或具体 revision id）")],
) -> None:
    """回退到目标版本（需显式给出，避免误回退）。"""
    command.downgrade(_config(), revision)


@app.command("revision")
def revision_cmd(
    message: Annotated[str, typer.Option("--message", "-m", help="迁移说明")] = "",
    autogenerate: Annotated[
        bool, typer.Option("--autogenerate", help="对比 SQLModel.metadata 与当前库自动生成")
    ] = False,
) -> None:
    """新建一个迁移脚本（versions/ 目录）。"""
    command.revision(_config(), message=message, autogenerate=autogenerate)


@app.command("current")
def current_cmd(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="输出详细信息")] = False,
) -> None:
    """查看数据库当前所在版本。"""
    command.current(_config(), verbose=verbose)


@app.command("history")
def history_cmd(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="输出详细信息")] = False,
) -> None:
    """查看迁移历史。"""
    command.history(_config(), verbose=verbose)


@app.command("stamp")
def stamp_cmd(
    revision: Annotated[str, typer.Argument(help="标记目标版本（默认 head）")] = "head",
) -> None:
    """不执行 DDL，仅把 alembic_version 标记到目标版本。

    用于把「create_all 时代」的既有库纳入迁移管理（一次性操作）。
    """
    command.stamp(_config(), revision)
