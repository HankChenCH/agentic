"""认证用例：注册（注册即登录）/ 登录 / 当前用户 / 资料与密码维护 + 验签通道。

api 层唯一消费面（依赖箭头表禁止 api 直触 domain）；JWT 机制细节
（签发/验签/PBKDF2）归 domain/user。``verify_access_token`` 为 api 依赖
（require_user 无容器可注入）提供的模块级验签通道——api 只 import
application，jwt 异常面原样透出由依赖翻译为 401。
"""

from dataclasses import dataclass
from uuid import UUID

from wireup import injectable

from app.domain.user import UserService
from app.domain.user.token import TokenPayload, decode_access_token
from app.domain.user.user_service import public_user


@dataclass(frozen=True)
class AuthSessionPayload:
    """注册/登录的同构输出：令牌 + 用户公开信息。"""

    token: str
    expires_at: str
    user: dict


def verify_access_token(token: str, *, secret: str, algorithm: str) -> TokenPayload:
    """JWT 验签通道：验签失败抛 ``jwt.InvalidTokenError``（调用方翻译 401）。"""
    return decode_access_token(token, secret=secret, algorithm=algorithm)


@injectable
@dataclass
class AuthAppService:
    """认证/账号用例门面。"""

    user_service: UserService

    def register(self, username: str, password: str) -> AuthSessionPayload:
        session = self.user_service.register(username, password)
        return AuthSessionPayload(
            token=session.token.token,
            expires_at=session.token.expires_at.isoformat(),
            user=public_user(session.user),
        )

    def login(self, username: str, password: str) -> AuthSessionPayload:
        session = self.user_service.login(username, password)
        return AuthSessionPayload(
            token=session.token.token,
            expires_at=session.token.expires_at.isoformat(),
            user=public_user(session.user),
        )

    def me(self, user_id: UUID) -> dict:
        return public_user(self.user_service.get_user(user_id))

    def update_profile(self, user_id: UUID, nickname: str) -> dict:
        return public_user(self.user_service.update_profile(user_id, nickname))

    def change_password(self, user_id: UUID, old_password: str, new_password: str) -> dict:
        return public_user(
            self.user_service.change_password(user_id, old_password, new_password)
        )
