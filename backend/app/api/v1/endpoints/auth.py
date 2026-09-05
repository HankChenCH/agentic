"""认证端点：注册（注册即登录）/ 登录 / 当前用户资料 / 修改密码。

/auth 路由不受 require_user 管辖（register/login 本身是取票入口，其余
端点自行声明依赖）；注册/登录成功同构返回 token + 用户公开信息。
"""

from fastapi import APIRouter, Depends
from wireup import Injected

from app.api.deps import UserPrincipal, require_user
from app.models.schema.request.user import (
    ChangePasswordRequest,
    LoginRequest,
    RegisterRequest,
    UpdateProfileRequest,
)
from app.models.schema.response.biz_response import Response
from app.domain.user import UserService
from app.domain.user.user_service import public_user

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/register")
def register(
    user_service: Injected[UserService],
    request: RegisterRequest,
):
    session = user_service.register(request.username, request.password)
    return Response.success({
        "token": session.token.token,
        "expires_at": session.token.expires_at.isoformat(),
        "user": public_user(session.user),
    }).to_dict()


@router.post("/login")
def login(
    user_service: Injected[UserService],
    request: LoginRequest,
):
    session = user_service.login(request.username, request.password)
    return Response.success({
        "token": session.token.token,
        "expires_at": session.token.expires_at.isoformat(),
        "user": public_user(session.user),
    }).to_dict()


@router.get("/me")
def me(
    user_service: Injected[UserService],
    principal: UserPrincipal = Depends(require_user),
):
    user = user_service.get_user(principal.user_id)
    return Response.success(public_user(user)).to_dict()


@router.patch("/me")
def update_me(
    user_service: Injected[UserService],
    request: UpdateProfileRequest,
    principal: UserPrincipal = Depends(require_user),
):
    user = user_service.update_profile(principal.user_id, request.nickname)
    return Response.success(public_user(user)).to_dict()


@router.post("/change-password")
def change_password(
    user_service: Injected[UserService],
    request: ChangePasswordRequest,
    principal: UserPrincipal = Depends(require_user),
):
    user = user_service.change_password(
        principal.user_id, request.old_password, request.new_password
    )
    return Response.success(public_user(user)).to_dict()
