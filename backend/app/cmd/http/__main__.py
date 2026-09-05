"""HTTP 入口命令行：``python -m app.cmd.http [--reload]``。"""

import os
from typing import Annotated

import typer
import uvicorn

app = typer.Typer()


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="监听地址")] = "0.0.0.0",
    port: Annotated[int, typer.Option(help="监听端口")] = 8000,
    reload: Annotated[bool, typer.Option("--reload", help="开启热重载")] = False,
    config_dir: Annotated[
        str | None, typer.Option("--config-dir", help="配置目录，默认包内 app/configs/")
    ] = None,
    env_file: Annotated[
        str | None, typer.Option("--env-file", help=".env 文件路径，默认从工作目录查找")
    ] = None,
) -> None:
    """启动 agentic server。"""
    # CLI 参数直接写入环境变量（优先级高于已有值）：reload 子进程与 uvicorn
    # 二次导入的应用模块均能继承
    if config_dir:
        os.environ["AGENTIC_CONFIG_DIR"] = config_dir
    if env_file:
        os.environ["AGENTIC_ENV_FILE"] = env_file
    # reload 模式要求 import string，不能传 app 对象
    uvicorn.run("app.cmd.http.main:server", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
