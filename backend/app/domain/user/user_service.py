"""用户域领域服务：注册/登录/用户查询/资料与密码的唯一门面。

密码哈希（PBKDF2）与 JWT 签发都在本域内完成；格式校验是领域规则
（端点模型上的长度约束只服务 422 明细），其余入口同样受约束。
"""

import re
from dataclasses import dataclass
from uuid import UUID

from wireup import injectable

from app.domain.ports import RepositoryConflictError
from app.core.config import AppConfig
from app.core.logging import LoggerFactory
from app.exceptions import (
    InvalidCredentialsError,
    UserInvalidParamError,
    UserNotFoundError,
    UsernameDuplicatedError,
)
from app.models.domain.user import User
from app.domain.user.ports import UserRepositoryPort

from .passwords import hash_password, verify_password
from .ports import UserNodeSyncPort
from .token import TokenBundle, encode_access_token, encode_refresh_token

# 用户名：字母/数字/下划线/中文；密码：可见 ASCII + 常用中文输入均放行，仅限长度
_USERNAME_PATTERN = re.compile(r"^\w+([\u4e00-\u9fff]+\w*)*$", re.UNICODE)
_USERNAME_MIN, _USERNAME_MAX = 3, 32
_PASSWORD_MIN, _PASSWORD_MAX = 8, 64
_NICKNAME_MAX = 32


@dataclass(frozen=True)
class AuthSession:
    """注册/登录的成功产物：用户 + 新签发的访问/刷新令牌对。"""

    user: User
    token: TokenBundle
    refresh_token: TokenBundle


@injectable
@dataclass
class UserService:
    user_repo: UserRepositoryPort
    memory_user_node: UserNodeSyncPort
    app_config: AppConfig
    logger_factory: LoggerFactory

    def __post_init__(self):
        self.logger = self.logger_factory.get_logger(__name__)

    # ---- 写路径：注册/登录 ----

    def register(self, username: str, password: str) -> AuthSession:
        self._validate_username(username)
        self._validate_password(password)
        if self.user_repo.get_by_username(username) is not None:
            raise UsernameDuplicatedError(f"用户名已被注册: {username}")
        try:
            user = self.user_repo.create(username=username, password_hash=hash_password(password), nickname="")
        except RepositoryConflictError:
            # 查重与插入之间的并发注册竞态：唯一约束兜底
            raise UsernameDuplicatedError(f"用户名已被注册: {username}") from None
        self.logger.info("user registered: %s (%s)", username, user.id)
        # 预建记忆「用户」节点并写入账号信息；失败不阻断注册（节点会在
        # 后续轮次收尾时自愈补建）
        try:
            self.memory_user_node.sync_user_node(user.id, user.username, user.nickname)
        except Exception:
            self.logger.exception("sync memory user node failed on register: %s", user.id)
        access, refresh = self._issue_token_pair(user)
        return AuthSession(user=user, token=access, refresh_token=refresh)

    def login(self, username: str, password: str) -> AuthSession:
        user = self.user_repo.get_by_username(username)
        # 用户名不存在与密码错误同口径 401，不泄露用户存在性；空哈希（默认用户）恒失败
        if user is None or not verify_password(password, user.password_hash):
            raise InvalidCredentialsError("用户名或密码错误")
        access, refresh = self._issue_token_pair(user)
        return AuthSession(user=user, token=access, refresh_token=refresh)

    # ---- 读路径：/auth/me ----

    def get_user(self, user_id: UUID) -> User:
        user = self.user_repo.get_by_id(user_id)
        if user is None:
            raise UserNotFoundError(f"user not found: {user_id}")
        return user

    # ---- 写路径：资料与密码 ----

    def update_profile(self, user_id: UUID, nickname: str) -> User:
        if len(nickname) > _NICKNAME_MAX:
            raise UserInvalidParamError(f"昵称长度不能超过 {_NICKNAME_MAX} 字符")
        user = self.user_repo.update_profile(user_id, nickname)
        if user is None:
            raise UserNotFoundError(f"user not found: {user_id}")
        self.logger.info("user profile updated: %s", user.id)
        # 账号信息变更同步进记忆「用户」节点；失败不阻断（轮次收尾自愈补齐）
        try:
            self.memory_user_node.sync_user_node(user.id, user.username, user.nickname)
        except Exception:
            self.logger.exception("sync memory user node failed on profile update: %s", user.id)
        return user

    def change_password(self, user_id: UUID, old_password: str, new_password: str) -> User:
        user = self.user_repo.get_by_id(user_id)
        if user is None:
            raise UserNotFoundError(f"user not found: {user_id}")
        if not verify_password(old_password, user.password_hash):
            raise InvalidCredentialsError("原密码错误")
        self._validate_password(new_password)
        if new_password == old_password:
            raise UserInvalidParamError("新密码不能与原密码相同")
        updated = self.user_repo.update_password(user_id, hash_password(new_password))
        if updated is None:  # 与上面 get_by_id 之间用户被删除的极端竞态
            raise UserNotFoundError(f"user not found: {user_id}")
        self.logger.info("user password changed: %s", user.id)
        return updated

    # ---- 内部 ----

    def _issue_token_pair(self, user: User) -> tuple[TokenBundle, TokenBundle]:
        auth = self.app_config.auth
        kwargs = dict(
            user_id=user.id,
            username=user.username,
            secret=auth.jwt_secret,
            algorithm=auth.jwt_algorithm,
        )
        return (
            encode_access_token(
                expires_minutes=auth.access_token_expire_minutes, **kwargs
            ),
            encode_refresh_token(
                expires_minutes=auth.refresh_token_expire_minutes, **kwargs
            ),
        )

    @staticmethod
    def _validate_username(username: str) -> None:
        if not (_USERNAME_MIN <= len(username) <= _USERNAME_MAX):
            raise UserInvalidParamError(f"用户名长度须在 {_USERNAME_MIN}-{_USERNAME_MAX} 字符之间")
        if not _USERNAME_PATTERN.match(username):
            raise UserInvalidParamError("用户名只能包含中文、字母、数字或下划线")

    @staticmethod
    def _validate_password(password: str) -> None:
        if not (_PASSWORD_MIN <= len(password) <= _PASSWORD_MAX):
            raise UserInvalidParamError(f"密码长度须在 {_PASSWORD_MIN}-{_PASSWORD_MAX} 字符之间")


def public_user(user: User) -> dict:
    """用户公开信息（信封 payload 用）：剔除 password_hash 等敏感字段。"""
    return {
        "id": str(user.id),
        "username": user.username,
        "nickname": user.nickname or user.username,
        "created_at": user.created_at.isoformat(),
    }
