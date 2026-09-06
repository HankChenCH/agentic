"""认证用例：注册（注册即登录）/ 登录 / 当前用户 / 资料与密码维护 + 验签通道。

api 层唯一消费面（依赖箭头表禁止 api 直触 domain）；JWT 机制细节
（签发/验签/PBKDF2）归 domain/user。``verify_access_token`` 为 api 依赖
（require_user 无容器可注入）提供的模块级验签通道——api 只 import
application，jwt 异常面原样透出由依赖翻译为 401。
"""

from dataclasses import dataclass
from uuid import UUID

import jwt
from wireup import injectable

from app.exceptions import InvalidCredentialsError
from app.domain.user import UserService
from app.domain.user.token import (
    TokenPayload,
    decode_access_token,
    decode_refresh_token,
    encode_access_token,
    encode_refresh_token,
)
from app.domain.user.user_service import public_user


@dataclass(frozen=True)
class AuthSessionPayload:
    """注册/登录/刷新的同构输出：访问/刷新令牌 + 用户公开信息。"""

    token: str
    expires_at: str
    refresh_token: str
    refresh_expires_at: str
    user: dict


def verify_access_token(token: str, *, secret: str, algorithm: str) -> TokenPayload:
    """JWT 验签通道：验签失败抛 ``jwt.InvalidTokenError``（调用方翻译 401）。"""
    return decode_access_token(token, secret=secret, algorithm=algorithm)


@injectable
@dataclass
class AuthAppService:
    """认证/账号用例门面。"""

    user_service: UserService

    def _session_payload(self, session) -> AuthSessionPayload:
        return AuthSessionPayload(
            token=session.token.token,
            expires_at=session.token.expires_at.isoformat(),
            refresh_token=session.refresh_token.token,
            refresh_expires_at=session.refresh_token.expires_at.isoformat(),
            user=public_user(session.user),
        )

    def register(self, username: str, password: str) -> AuthSessionPayload:
        return self._session_payload(self.user_service.register(username, password))

    def login(self, username: str, password: str) -> AuthSessionPayload:
        return self._session_payload(self.user_service.login(username, password))

    def refresh(self, refresh_token: str) -> AuthSessionPayload:
        """刷新令牌换新一对令牌（旋转 refresh）；无效/过期一律 401 同口径。"""
        auth_config = self.user_service.app_config.auth
        try:
            payload = decode_refresh_token(
                refresh_token,
                secret=auth_config.jwt_secret,
                algorithm=auth_config.jwt_algorithm,
            )
        except jwt.InvalidTokenError:
            raise InvalidCredentialsError("刷新令牌无效或已过期") from None
        user = self.user_service.get_user(payload.user_id)
        access = encode_access_token(
            user_id=user.id,
            username=user.username,
            secret=auth_config.jwt_secret,
            algorithm=auth_config.jwt_algorithm,
            expires_minutes=auth_config.access_token_expire_minutes,
        )
        new_refresh = encode_refresh_token(
            user_id=user.id,
            username=user.username,
            secret=auth_config.jwt_secret,
            algorithm=auth_config.jwt_algorithm,
            expires_minutes=auth_config.refresh_token_expire_minutes,
        )
        return AuthSessionPayload(
            token=access.token,
            expires_at=access.expires_at.isoformat(),
            refresh_token=new_refresh.token,
            refresh_expires_at=new_refresh.expires_at.isoformat(),
            user=public_user(user),
        )

    def me(self, user_id: UUID) -> dict:
        return public_user(self.user_service.get_user(user_id))

    def update_profile(self, user_id: UUID, nickname: str) -> dict:
        return public_user(self.user_service.update_profile(user_id, nickname))

    def change_password(self, user_id: UUID, old_password: str, new_password: str) -> dict:
        return public_user(
            self.user_service.change_password(user_id, old_password, new_password)
        )
