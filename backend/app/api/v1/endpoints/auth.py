"""认证端点：注册（注册即登录）/ 登录 / 当前用户资料 / 修改密码。

/auth 路由不受 require_user 管辖（register/login 本身是取票入口，其余
端点自行声明依赖）；注册/登录成功同构返回 token + 用户公开信息。业务
在 application 认证用例（AuthAppService），端点只做 HTTP wiring。
"""

from fastapi import APIRouter, Depends
from wireup import Injected

from app.api.deps import UserPrincipal, require_user
from app.application import AuthAppService
from app.models.schema.request.user import (
    ChangePasswordRequest,
    LoginRequest,
    RegisterRequest,
    UpdateProfileRequest,
)
from app.models.schema.response.biz_response import Response

router = APIRouter(prefix="/auth", tags=["Auth"])


def _session_response(payload) -> dict:
    return Response.success({
        "token": payload.token,
        "expires_at": payload.expires_at,
        "user": payload.user,
    }).to_dict()


@router.post("/register")
def register(
    auth: Injected[AuthAppService],
    request: RegisterRequest,
):
    return _session_response(auth.register(request.username, request.password))


@router.post("/login")
def login(
    auth: Injected[AuthAppService],
    request: LoginRequest,
):
    return _session_response(auth.login(request.username, request.password))


@router.get("/me")
def me(
    auth: Injected[AuthAppService],
    principal: UserPrincipal = Depends(require_user),
):
    return Response.success(auth.me(principal.user_id)).to_dict()


@router.patch("/me")
def update_me(
    auth: Injected[AuthAppService],
    request: UpdateProfileRequest,
    principal: UserPrincipal = Depends(require_user),
):
    return Response.success(auth.update_profile(principal.user_id, request.nickname)).to_dict()


@router.post("/change-password")
def change_password(
    auth: Injected[AuthAppService],
    request: ChangePasswordRequest,
    principal: UserPrincipal = Depends(require_user),
):
    return Response.success(
        auth.change_password(principal.user_id, request.old_password, request.new_password)
    ).to_dict()
