"""HTTP 边缘策略配置校验（CORS 白名单）与日志全局级别推导。

纯单测：pydantic 校验器与纯函数直测，不触 yaml 装配（shipped yaml 装载
口径见 test_task_config.py 同款先例）。
"""

import pytest
from pydantic import ValidationError

from app.core.config import CorsConfig, LoggingConfig
from app.core.exceptions.framework import ConfigError
from app.core.logging.setup import _resolve_global_level


# ---------- CORS 白名单 ----------


def test_origins_comma_string_split_and_stripped():
    cors = CorsConfig(origins="http://localhost:5173, http://127.0.0.1:5173")
    assert cors.origins == ["http://localhost:5173", "http://127.0.0.1:5173"]


def test_origins_accepts_list_form():
    assert CorsConfig(origins=["https://app.example.com"]).origins == ["https://app.example.com"]


@pytest.mark.parametrize("bad", [
    "https://host.example.com/",   # 尾斜杠
    "ftp://host.example.com",      # 非 http(s) scheme
    "host.example.com",            # 缺 scheme
])
def test_origins_rejects_malformed_entries(bad):
    with pytest.raises(ValidationError, match="必须形如"):
        CorsConfig(origins=f"http://ok.example.com,{bad}")


def test_origins_blank_string_rejected():
    with pytest.raises(ValidationError, match="不能为空"):
        CorsConfig(origins="  ,  ")


def test_allow_credentials_defaults_on():
    assert CorsConfig(origins="http://localhost:5173").allow_credentials is True


# ---------- 日志全局级别推导 ----------


def _config(level=None) -> LoggingConfig:
    return LoggingConfig(level=level, sinks=[])


def test_explicit_level_wins(monkeypatch):
    monkeypatch.setattr("app.core.logging.setup.get_environment", lambda: "prod")
    assert _resolve_global_level(_config("warning")) == "WARNING"


def test_dev_and_test_default_to_debug(monkeypatch):
    for env in ("dev", "test"):
        monkeypatch.setattr("app.core.logging.setup.get_environment", lambda env=env: env)
        assert _resolve_global_level(_config()) == "DEBUG"


def test_prod_defaults_to_info(monkeypatch):
    monkeypatch.setattr("app.core.logging.setup.get_environment", lambda: "prod")
    assert _resolve_global_level(_config()) == "INFO"


def test_invalid_level_fails_fast():
    # 校验在装配期属性访问（normalized_level）触发，不在模型构造期
    with pytest.raises(ConfigError, match="日志级别非法"):
        _resolve_global_level(_config("LOUD"))
