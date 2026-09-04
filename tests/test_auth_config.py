"""AuthConfig 生产密钥 fail-fast：dev 兜底密钥/长度不足在 prod 拒绝启动。

校验口径单测（jwt_secret_problems / assert_jwt_secret_ok_for）+ 装配级
验证（APP_ENV=prod 下实例化 AppConfig 即拦截，dev 不设限）。
"""

import os

os.environ.setdefault("DEEPSEEK_API_KEY", "ci-dummy")

import pytest

from app.core.config import AppConfig, ConfigError, get_environment
from app.core.config.auth import (
    DEV_JWT_SECRET,
    assert_jwt_secret_ok_for,
    jwt_secret_problems,
)
from app.core.config.loader import read_config

GOOD_SECRET = "s" * 48


def test_jwt_secret_problems_categorize():
    assert jwt_secret_problems("")  # 未设置
    assert jwt_secret_problems(DEV_JWT_SECRET)  # dev 兜底密钥
    assert jwt_secret_problems("short")  # 长度不足
    assert jwt_secret_problems(GOOD_SECRET) == []  # 达标


def test_prod_rejects_weak_secret_dev_unrestricted():
    with pytest.raises(ConfigError, match="dev 兜底密钥"):
        assert_jwt_secret_ok_for("prod", DEV_JWT_SECRET)
    with pytest.raises(ConfigError, match="不足"):
        assert_jwt_secret_ok_for("prod", "too-short")
    # 非 prod 环境不设限（dev 沿用 auth.yaml 兜底密钥）
    assert_jwt_secret_ok_for("dev", DEV_JWT_SECRET)
    assert_jwt_secret_ok_for("test", "short")


def _fresh_config_caches():
    """清空配置缓存，让下一个 AppConfig() 按当前环境变量重新解析。"""
    get_environment.cache_clear()
    read_config.cache_clear()


def test_app_config_prod_assembly_fail_fast(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("AUTH_JWT_SECRET", DEV_JWT_SECRET)
    _fresh_config_caches()
    try:
        with pytest.raises(ConfigError, match="AUTH_JWT_SECRET"):
            AppConfig()
    finally:
        _fresh_config_caches()  # 不把按测试环境解析的缓存带给后续用例


def test_app_config_prod_with_good_secret_passes(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("AUTH_JWT_SECRET", GOOD_SECRET)
    _fresh_config_caches()
    try:
        config = AppConfig()
        assert config.environment == "prod"
    finally:
        _fresh_config_caches()
