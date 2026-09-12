"""用户域领域服务：注册/登录/refresh 旋转与复用检测/登出/用户查询/资料与密码的唯一门面。

密码哈希（PBKDF2）与 JWT 签发都在本域内完成；格式校验是领域规则
（端点模型上的长度约束只服务 422 明细），其余入口同样受约束。

refresh 有状态（登记表 + 旋转/复用检测/族吊销），access 保持无状态
（require_user 纯验签，登出/改密后旧 access 到期前仍有效）。
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
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
from app.models.domain.user.refresh_token import STATUS_REVOKED, STATUS_ROTATED
from app.domain.user.ports import RefreshTokenStorePort, UserRepositoryPort

from .passwords import hash_password, verify_password
from .ports import UserNodeSyncPort
from .token import TokenBundle, decode_refresh_token, encode_access_token, encode_refresh_token

# 用户名：字母/数字/下划线/中文；密码：可见 ASCII + 常用中文输入均放行，仅限长度
_USERNAME_PATTERN = re.compile(r"^\w+([\u4e00-\u9fff]+\w*)*$", re.UNICODE)
_USERNAME_MIN, _USERNAME_MAX = 3, 32
_PASSWORD_MIN, _PASSWORD_MAX = 8, 64
_NICKNAME_MAX = 32


@dataclass(frozen=True)
class AuthSession:
    """注册/登录/refresh 的成功产物：用户 + 新签发的访问/刷新令牌对。"""

    user: User
    token: TokenBundle
    refresh_token: TokenBundle


@injectable
@dataclass
class UserService:
    """用户域门面：注册/登录/refresh 旋转与复用检测/登出/资料与密码。"""

    user_repo: UserRepositoryPort
    refresh_token_store: RefreshTokenStorePort
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
        return self._register_family_root(user, access, refresh)

    def login(self, username: str, password: str) -> AuthSession:
        user = self.user_repo.get_by_username(username)
        # 用户名不存在与密码错误同口径 401，不泄露用户存在性；空哈希（默认用户）恒失败
        if user is None or not verify_password(password, user.password_hash):
            raise InvalidCredentialsError("用户名或密码错误")
        access, refresh = self._issue_token_pair(user)
        return self._register_family_root(user, access, refresh)

    # ---- 读路径：/auth/me ----

    def get_user(self, user_id: UUID) -> User:
        user = self.user_repo.get_by_id(user_id)
        if user is None:
            raise UserNotFoundError(f"user not found: {user_id}")
        return user

    # ---- 写路径：refresh 旋转 / 登出 ----

    def refresh(self, presented_token: str) -> AuthSession:
        """刷新令牌旋转换新：服务端登记校验 + 复用检测（族连坐）。

        服务端事实源是 refresh_token 登记表（jti 主键）；过期由 JWT exp
        兜住，行上状态只回答「这张票是否仍被承认」。全部失败路径同口径
        401（不给窃取者区分信息），复用连坐事件只落服务端日志。宽限期内
        重放已旋转票仅拒绝不连坐——多标签页并发刷新的良性竞态。
        """
        auth = self.app_config.auth
        try:
            claims = decode_refresh_token(
                presented_token,
                secret=auth.jwt_secret,
                algorithm=auth.jwt_algorithm,
            )
        except jwt.InvalidTokenError:
            raise InvalidCredentialsError("刷新令牌无效或已过期") from None
        if claims.jti is None:  # 历史无 jti 的旧票：无从查登记，一律拒绝
            raise InvalidCredentialsError("刷新令牌无效或已过期") from None
        record = self.refresh_token_store.get(claims.jti)
        if record is None or record.status == STATUS_REVOKED:
            raise InvalidCredentialsError("刷新令牌无效或已过期") from None
        if record.status == STATUS_ROTATED:
            stale_before = datetime.now(timezone.utc) - timedelta(
                seconds=auth.refresh_reuse_grace_seconds
            )
            if self.refresh_token_store.revoke_on_reuse(
                record.jti, record.family_id, stale_before=stale_before
            ):
                self.logger.warning(
                    "refresh token reuse detected, family revoked: user=%s family=%s jti=%s",
                    record.user_id,
                    record.family_id,
                    record.jti,
                )
            raise InvalidCredentialsError("刷新令牌无效或已过期") from None
        # active：条件认领旋转。认领失败 = 并发另一请求刚旋转了同一张票
        # （多标签页竞态的输家），按宽限期口径只拒绝不连坐
        if not self.refresh_token_store.claim_active(
            record.jti, rotated_at=datetime.now(timezone.utc)
        ):
            raise InvalidCredentialsError("刷新令牌无效或已过期") from None
        user = self.user_repo.get_by_id(claims.user_id)
        if user is None:
            raise UserNotFoundError(f"user not found: {claims.user_id}")
        access, refresh = self._issue_token_pair(user)
        self._register_refresh(refresh, user, family_id=record.family_id)
        self._purge_expired()
        self.logger.info("refresh token rotated: user=%s family=%s", user.id, record.family_id)
        return AuthSession(user=user, token=access, refresh_token=refresh)

    def logout_family(self, presented_token: str | None) -> None:
        """登出：吊销该 refresh 票所在族（本设备会话世系），幂等。

        票无效/查无登记一律静默返回（无可吊销，客户端本就要清本地态）；
        其他设备的族不受影响——族按登录划分、各设备独立存活。
        """
        if not presented_token:
            return
        auth = self.app_config.auth
        try:
            claims = decode_refresh_token(
                presented_token,
                secret=auth.jwt_secret,
                algorithm=auth.jwt_algorithm,
            )
        except jwt.InvalidTokenError:
            return
        if claims.jti is None:
            return
        record = self.refresh_token_store.get(claims.jti)
        if record is None:
            return
        self.refresh_token_store.revoke_family(record.family_id)
        self.logger.info(
            "session family revoked on logout: user=%s family=%s", record.user_id, record.family_id
        )

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
        # 改密吊销该用户全部 refresh 族（含当前会话——全部设备需重新登录）；
        # access 无状态不撤销，旧票到期前仍有效（≤30 分钟，语义如此）
        revoked = self.refresh_token_store.revoke_all_for_user(user_id)
        if revoked:
            self.logger.info(
                "all refresh families revoked after password change: user=%s count=%s",
                user_id,
                revoked,
            )
        return updated

    # ---- 内部 ----

    def _register_family_root(
        self, user: User, access: TokenBundle, refresh: TokenBundle
    ) -> AuthSession:
        # 一次登录一个族：族根 = 本次 refresh 的 jti。各登录互不隶属
        # （多设备独立存活），旋转继承族，复用检测按族连坐
        self._register_refresh(refresh, user, family_id=refresh.jti)
        self._purge_expired()
        return AuthSession(user=user, token=access, refresh_token=refresh)

    def _register_refresh(self, bundle: TokenBundle, user: User, *, family_id: str) -> None:
        self.refresh_token_store.create(
            jti=bundle.jti,
            user_id=user.id,
            family_id=family_id,
            expires_at=bundle.expires_at,
        )

    def _purge_expired(self) -> None:
        self.refresh_token_store.purge_expired(now=datetime.now(timezone.utc))

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
