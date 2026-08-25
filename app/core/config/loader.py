"""YAML 配置加载器。

配置文件位于 ``app/configs/``（可用环境变量 ``AGENTIC_CONFIG_DIR`` 或启动参数
``python -m app.cmd.http --config-dir`` 覆盖），与
:class:`~app.core.config.AppConfig` 的分节一一对应：``app.yaml`` 提供顶层元
信息，``llm.yaml`` / ``db.yaml`` / ``memory.yaml`` 提供各分节数据，由 Pydantic
模型完成校验与类型转换。

环境变量插值沿用 docker-compose.yaml 的写法，在 YAML 解析后统一作用于所有
字符串值（无需标签）：
- ``${VAR}`` — 引用必需变量，未设置则整个加载失败；
- ``${VAR:default}`` / ``${VAR:-default}`` — 未设置时使用内联默认值；
- ``$$`` — 转义为字面量 ``$``。

值优先级：进程环境变量 > ``.env`` 文件 > 内联默认值 —— 结构进 YAML、密钥等
敏感值留在环境（工作目录的 ``.env`` 或 ``AGENTIC_ENV_FILE`` 指定路径），避免
机密被提交进仓库；``.env`` 在首次读取配置前加载，且不覆盖已存在的环境变量。

文件缺失、解析失败、校验失败、插值变量缺失均立即抛 :class:`ConfigError`，
不带残缺配置启动。
"""

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import TypeVar

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

from app.core.exceptions.framework import ConfigError

TModel = TypeVar("TModel", bound=BaseModel)


# $$ 转义与 ${VAR} / ${VAR:default} / ${VAR:-default} 引用；$$ 在最前优先匹配
_ENV_TOKEN_PATTERN = re.compile(r"\$\$|\$\{([^}:]+)(?::(-?)([^}]*))?\}")


def _config_dir() -> Path:
    """配置目录：``AGENTIC_CONFIG_DIR`` 优先，默认为包内 ``app/configs/``。

    每次调用时解析而非 import 时固化，使启动参数桥接的环境变量在首次
    读取配置前设置即可生效。
    """
    override = os.environ.get("AGENTIC_CONFIG_DIR")
    if override:
        return Path(override)
    # 基于 __file__ 定位包内默认目录，与运行目录无关
    return Path(__file__).resolve().parents[2] / "configs"


@lru_cache(maxsize=1)
def _load_env_once() -> None:
    """加载 ``.env``：优先 ``AGENTIC_ENV_FILE`` 指定路径，否则从工作目录查找。

    ``load_dotenv`` 默认不覆盖已存在的环境变量，保证「进程环境 > .env」。
    """
    load_dotenv(os.environ.get("AGENTIC_ENV_FILE") or None)


def _interpolate(value, missing: list[str]):
    if isinstance(value, dict):
        return {key: _interpolate(item, missing) for key, item in value.items()}
    if isinstance(value, list):
        return [_interpolate(item, missing) for item in value]
    if isinstance(value, str):
        return _interpolate_str(value, missing)
    return value


def _interpolate_str(value: str, missing: list[str]) -> str:
    def replace(match: re.Match) -> str:
        if match.group(0) == "$$":
            return "$"
        var, default = match.group(1), match.group(3)
        if var in os.environ:
            return os.environ[var]
        if match.group(2) is not None:  # 带 : 或 :- 分隔，即有内联默认值
            return default
        missing.append(var)
        return match.group(0)

    return _ENV_TOKEN_PATTERN.sub(replace, value)


@lru_cache(maxsize=None)
def read_config(filename: str) -> dict:
    """读取并完成环境变量插值，返回顶层 mapping；同一文件进程内只读一次。"""
    _load_env_once()
    path = _config_dir() / filename
    if not path.is_file():
        raise ConfigError(f"配置文件不存在: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"配置文件解析失败: {path}\n{e}") from e
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件顶层必须是 mapping: {path}（实际为 {type(data).__name__}）")
    missing: list[str] = []
    data = _interpolate(data, missing)
    if missing:
        names = list(dict.fromkeys(missing))
        raise ConfigError(f"{path}: 未设置的环境变量: {names}（${{VAR}} 引用，请通过 .env 或环境提供）")
    return data


def load_section(filename: str, model_cls: type[TModel]) -> TModel:
    """读取配置文件并校验为指定的分节模型，失败时定位到具体文件。"""
    data = read_config(filename)
    try:
        return model_cls(**data)
    except ValidationError as e:
        raise ConfigError(f"配置文件校验失败: {_config_dir() / filename}\n{e}") from e
