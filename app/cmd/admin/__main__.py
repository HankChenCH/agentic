"""维护管理命令行入口：``python -m app.cmd.admin <域> <命令> [...]``。"""

import os
from typing import Annotated

import typer

from app.cmd.admin import memory

app = typer.Typer(
    no_args_is_help=True,
    help="agentic 维护管理命令行（按域分组的一次性维护/修复命令）",
)


@app.callback()
def main(
    config_dir: Annotated[
        str | None, typer.Option("--config-dir", help="配置目录，默认包内 app/configs/")
    ] = None,
    env_file: Annotated[
        str | None, typer.Option("--env-file", help=".env 文件路径，默认从工作目录查找")
    ] = None,
) -> None:
    # 与 cmd/http 同款桥接：写环境变量（优先级高于已有值），命令体内建容器时生效
    if config_dir:
        os.environ["AGENTIC_CONFIG_DIR"] = config_dir
    if env_file:
        os.environ["AGENTIC_ENV_FILE"] = env_file


app.add_typer(memory.app, name="memory", help="记忆域维护")


if __name__ == "__main__":
    app()
