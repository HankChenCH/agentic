from pydantic import BaseModel, Field

from app.core.exceptions.framework import ConfigError

# auth.yaml 的 dev 兜底密钥（${AUTH_JWT_SECRET:...} 内联默认值）：
# prod 环境装配期检出即拒绝启动（校验接线在 AppConfig，见 core/config/__init__.py）
DEV_JWT_SECRET = "dev-only-secret-0123456789abcdef-change-me"

# HS256 密钥最小字节数：弱于签名输出长度时 PyJWT 告警，prod 直接收紧为启动门槛
JWT_SECRET_MIN_BYTES = 32


def jwt_secret_problems(secret: str) -> list[str]:
    """JWT 密钥体检：返回不满足生产要求的问题描述清单，空清单 = 通过。

    口径：未设置（空串 / 仍是 dev 兜底密钥）或字节数不足
    :data:`JWT_SECRET_MIN_BYTES`。
    """
    if not secret or secret == DEV_JWT_SECRET:
        return ["未设置或仍是 dev 兜底密钥（应经 AUTH_JWT_SECRET 注入强随机值）"]
    actual = len(secret.encode("utf-8"))
    if actual < JWT_SECRET_MIN_BYTES:
        return [f"长度 {actual} 字节，不足 {JWT_SECRET_MIN_BYTES} 字节（HS256 要求 ≥32）"]
    return []


def assert_jwt_secret_ok_for(environment: str, jwt_secret: str) -> None:
    """prod 环境校验 JWT 密钥安全口径，不满足即抛 :class:`ConfigError`。

    其余环境不设限（dev 沿用 auth.yaml 兜底密钥）。fail-fast 收口在
    AppConfig 装配期——http/worker/migrate 全部入口启动即拦截，不带弱密钥
    进入服务。
    """
    if environment != "prod":
        return
    problems = jwt_secret_problems(jwt_secret)
    if problems:
        raise ConfigError(
            "AUTH_JWT_SECRET 不满足生产要求：" + "；".join(problems)
            + "。生成强随机密钥："
            + 'python3 -c "import secrets; print(secrets.token_urlsafe(48))"'
        )


class AuthConfig(BaseModel):
    """认证配置（auth.yaml）：JWT 签发/验签参数。"""

    jwt_secret: str = Field(
        description="JWT 签名密钥（HS256），生产环境必须通过 AUTH_JWT_SECRET 注入（dev 兜底密钥/过短将在 prod 装配期拒绝启动）",
    )

    jwt_algorithm: str = Field(
        description="JWT 签名算法",
        default="HS256",
    )

    token_expire_minutes: int = Field(
        description="访问令牌有效期（分钟）",
        default=60 * 24 * 7,
        ge=1,
    )
