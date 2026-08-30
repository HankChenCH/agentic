"""认证端点：注册（注册即登录）/ 登录 / 当前用户。

/auth 路由不受 require_user 管辖（register/login 本身是取票入口，me 在
端点内自行声明依赖）；注册/登录成功同构返回 token + 用户公开信息。
"""

from fastapi import APIRouter, Depends
from wireup import Injected

from app.api.deps import UserPrincipal, require_user
from app.models.schema.request.user import LoginRequest, RegisterRequest
from app.models.schema.response.biz_response import Response
from app.services import UserService
from app.services.domain.user.user_service import public_user

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
