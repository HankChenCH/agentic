"""用户域领域服务：注册/登录/用户查询的唯一门面。

密码哈希（PBKDF2）与 JWT 签发都在本域内完成；格式校验是领域规则
（端点模型上的长度约束只服务 422 明细），其余入口同样受约束。
"""

import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from wireup import injectable

from app.core.config import AppConfig
from app.core.logging import LoggerFactory
from app.exceptions import (
    InvalidCredentialsError,
    UserInvalidParamError,
    UserNotFoundError,
    UsernameDuplicatedError,
)
from app.models.domain.user import User
from app.repositories.user_repository import UserRepository

from .passwords import hash_password, verify_password
from .ports import UserNodeSyncPort
from .token import TokenBundle, encode_access_token

# 用户名：字母/数字/下划线/中文；密码：可见 ASCII + 常用中文输入均放行，仅限长度
_USERNAME_PATTERN = re.compile(r"^\w+([\u4e00-\u9fff]+\w*)*$", re.UNICODE)
_USERNAME_MIN, _USERNAME_MAX = 3, 32
_PASSWORD_MIN, _PASSWORD_MAX = 8, 64


@dataclass(frozen=True)
class AuthSession:
    """注册/登录的成功产物：用户 + 新签发的访问令牌。"""

    user: User
    token: TokenBundle


@injectable
@dataclass
class UserService:
    user_repo: UserRepository
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
        except IntegrityError:
            # 查重与插入之间的并发注册竞态：唯一约束兜底
            raise UsernameDuplicatedError(f"用户名已被注册: {username}") from None
        self.logger.info("user registered: %s (%s)", username, user.id)
        # 预建记忆「用户」节点并写入账号信息；失败不阻断注册（节点会在
        # 后续轮次收尾时自愈补建）
        try:
            self.memory_user_node.sync_user_node(user.id, user.username, user.nickname)
        except Exception:
            self.logger.exception("sync memory user node failed on register: %s", user.id)
        return AuthSession(user=user, token=self._issue_token(user))

    def login(self, username: str, password: str) -> AuthSession:
        user = self.user_repo.get_by_username(username)
        # 用户名不存在与密码错误同口径 401，不泄露用户存在性；空哈希（默认用户）恒失败
        if user is None or not verify_password(password, user.password_hash):
            raise InvalidCredentialsError("用户名或密码错误")
        return AuthSession(user=user, token=self._issue_token(user))

    # ---- 读路径：/auth/me ----

    def get_user(self, user_id: UUID) -> User:
        user = self.user_repo.get_by_id(user_id)
        if user is None:
            raise UserNotFoundError(f"user not found: {user_id}")
        return user

    # ---- 内部 ----

    def _issue_token(self, user: User) -> TokenBundle:
        auth = self.app_config.auth
        return encode_access_token(
            user_id=user.id,
            username=user.username,
            secret=auth.jwt_secret,
            algorithm=auth.jwt_algorithm,
            expires_minutes=auth.token_expire_minutes,
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
