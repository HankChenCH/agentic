"""任务执行器命令行：``python -m app.cmd.task_executor [worker 参数]``。

未识别的参数原样透传给 celery worker（如 --pool=solo）。
"""

import os
from typing import Annotated

import typer

app = typer.Typer()


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def worker(
    ctx: typer.Context,
    config_dir: Annotated[
        str | None, typer.Option("--config-dir", help="配置目录，默认包内 app/configs/")
    ] = None,
    env_file: Annotated[
        str | None, typer.Option("--env-file", help=".env 文件路径，默认从工作目录查找")
    ] = None,
) -> None:
    """启动 agentic task executor（celery worker）。"""
    # CLI 参数直接写入环境变量（优先级高于已有值）：prefork 子进程均能继承
    if config_dir:
        os.environ["AGENTIC_CONFIG_DIR"] = config_dir
    if env_file:
        os.environ["AGENTIC_ENV_FILE"] = env_file

    # env 桥接完成后再导入 Celery 应用（导入期即装配容器并做配置 fail-fast）
    from app.cmd.task_executor.main import celery_app  # noqa: E402

    # click 的 main(args=...) 不含程序名，首元素即 worker 子命令
    celery_app.worker_main(argv=["worker", *ctx.args])


if __name__ == "__main__":
    app()
