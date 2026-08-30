"""HTTP 入口边缘策略配置：CORS 白名单与请求体大小上限。

与 ``app/configs/http.yaml`` 一一对应；数值可经环境变量（``.env`` / 进程环境）
覆盖。CORS origins 以逗号分隔字符串整体覆盖（YAML 内联默认值同为逗号分隔），
由校验器拆分为列表——环境变量插值只产出字符串，列表化收敛在模型层。

限流不在本节：fastapi-limiter 依赖按端点挂载，Redis 共享计数，
见 ``app/api/rate_limit.py``（额度为代码常量）。
"""

from pydantic import BaseModel, Field, field_validator

_HTTP_SCHEMES = ("http://", "https://")


class CorsConfig(BaseModel):
    """CORS 白名单：源精确匹配（scheme + host + port），不支持通配符。"""

    origins: list[str] = Field(
        description="允许的浏览器源列表；逗号分隔字符串自动拆分，条目必须形如 http(s)://host[:port] 且不带尾斜杠。",
    )
    allow_credentials: bool = Field(
        default=True,
        description="是否允许携带凭证（Cookie 等）；白名单精确匹配下开启是安全的。",
    )

    @field_validator("origins", mode="before")
    @classmethod
    def _parse_origins(cls, value):
        # 环境变量覆盖（CORS_ORIGINS=a,b）与 YAML 列表两种形态统一收敛
        items = value.split(",") if isinstance(value, str) else [str(item) for item in value]
        origins = [item.strip() for item in items if item.strip()]
        bad = [o for o in origins if not o.startswith(_HTTP_SCHEMES) or o.endswith("/")]
        if bad:
            raise ValueError(f"CORS origin 必须形如 http(s)://host[:port] 且不带尾斜杠: {bad}")
        if not origins:
            raise ValueError("CORS origins 白名单不能为空")
        return origins


class MaxBodyConfig(BaseModel):
    """按端点作用域的请求体字节上限。"""

    run: int = Field(gt=0, description="run JSON 请求体上限（字节）。")
    upload: int = Field(
        gt=0,
        description="上传 multipart 请求体上限（字节）；需大于服务层单文件上限（support.MAX_UPLOAD_BYTES）并留出表单开销。",
    )


class HttpConfig(BaseModel):
    """HTTP 边缘策略分节（http.yaml → HttpConfig）。"""

    cors: CorsConfig = Field(description="CORS 白名单与凭证策略。")
    max_body_bytes: MaxBodyConfig = Field(description="请求体大小上限。")
