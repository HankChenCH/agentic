"""HTTP 入口命令行：``python -m app.cmd.http [--reload]``。"""

import argparse
import os

import uvicorn

parser = argparse.ArgumentParser(description="启动 agentic server")
parser.add_argument("--config-dir", help="配置目录，默认包内 app/configs/")
parser.add_argument("--env-file", help=".env 文件路径，默认从工作目录查找")
parser.add_argument("--reload", action="store_true", help="开启热重载")
args = parser.parse_args()

# CLI 参数直接写入环境变量（优先级高于已有值）：reload 子进程与 uvicorn
# 二次导入的应用模块均能继承
if args.config_dir:
    os.environ["AGENTIC_CONFIG_DIR"] = args.config_dir
if args.env_file:
    os.environ["AGENTIC_ENV_FILE"] = args.env_file

# reload 模式要求 import string，不能传 app 对象
uvicorn.run("app.cmd.http.main:server", host="0.0.0.0", port=8000, reload=args.reload)
