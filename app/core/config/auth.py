from pydantic import BaseModel, Field


class AuthConfig(BaseModel):
    """认证配置（auth.yaml）：JWT 签发/验签参数。"""

    jwt_secret: str = Field(
        description="JWT 签名密钥（HS256），生产环境必须通过 AUTH_JWT_SECRET 注入",
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
