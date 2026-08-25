"""任务执行器命令行：``python -m app.cmd.task_executor [worker 参数]``。

未识别的参数原样透传给 celery worker（如 --pool=solo）。
"""

import argparse
import os

parser = argparse.ArgumentParser(description="启动 agentic task executor")
parser.add_argument("--config-dir", help="配置目录，默认包内 app/configs/")
parser.add_argument("--env-file", help=".env 文件路径，默认从工作目录查找")
args, worker_args = parser.parse_known_args()

# CLI 参数直接写入环境变量（优先级高于已有值）：prefork 子进程均能继承
if args.config_dir:
    os.environ["AGENTIC_CONFIG_DIR"] = args.config_dir
if args.env_file:
    os.environ["AGENTIC_ENV_FILE"] = args.env_file

# env 桥接完成后再导入 Celery 应用（导入期即装配容器并做配置 fail-fast）
from app.cmd.task_executor.main import celery_app  # noqa: E402

# click 的 main(args=...) 不含程序名，首元素即 worker 子命令
celery_app.worker_main(argv=["worker", *worker_args])
