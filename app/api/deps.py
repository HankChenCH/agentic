"""认证依赖：JWT Bearer 无状态验签。

不回库查用户（user_id 即身份），删除用户后的残留令牌由各端点的归属查询
（查无此行 → 404）自然兜住。失败一律抛 InvalidCredentialsError → 全局
异常处理器出 401 信封，与 RateLimit 手写信封的边缘层口径区分开。

选型说明（为何不用中间件）：现有 api/middleware.py 是纯 ASGI 且位于全局
异常处理器之外，鉴权放那层 401 需手写信封形成第二份口径；路径白名单与
路由定义分离易漂移；BaseHTTPMiddleware 对 SSE 流式有缓冲风险；身份经
request.state 传递非类型化。FastAPI 依赖是官方认证惯例（OAuth2/HTTPBearer
体系即基于 Depends），按 router 声明式挂载、principal 类型化注入、
dependency_overrides 可测。
"""

from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

import jwt
from fastapi import Request

from app.core.config import AuthConfig, load_section
from app.exceptions import InvalidCredentialsError
from app.services.domain.user.token import decode_access_token

_BEARER_PREFIX = "Bearer "


@dataclass(frozen=True)
class UserPrincipal:
    """已认证身份（JWT claims），不含数据库状态。"""

    user_id: UUID
    username: str


@lru_cache(maxsize=1)
def _auth_config() -> AuthConfig:
    # 与 AppConfig.auth 同源同文件（read_config 进程内缓存）；AppConfig 在
    # lifespan 急切解析保证启动期 fail-fast，此处惰性仅为依赖函数无容器可注入
    return load_section("auth.yaml", AuthConfig)


def require_user(request: Request) -> UserPrincipal:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith(_BEARER_PREFIX) or not auth_header[len(_BEARER_PREFIX):].strip():
        raise InvalidCredentialsError("缺少访问令牌")
    token = auth_header[len(_BEARER_PREFIX):].strip()
    auth = _auth_config()
    try:
        payload = decode_access_token(token, secret=auth.jwt_secret, algorithm=auth.jwt_algorithm)
    except jwt.InvalidTokenError:
        # 过期/签名非法/结构不符同口径：不给攻击者区分信息
        raise InvalidCredentialsError("访问令牌无效或已过期") from None
    return UserPrincipal(user_id=payload.user_id, username=payload.username)
