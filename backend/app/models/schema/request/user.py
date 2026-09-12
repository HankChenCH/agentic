from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    username: str = Field(
        ...,
        description="用户名：3-32 字符，中文/字母/数字/下划线",
        min_length=3,
        max_length=32,
    )
    password: str = Field(
        ...,
        description="密码：8-64 字符",
        min_length=8,
        max_length=64,
    )


class LoginRequest(BaseModel):
    username: str = Field(..., description="用户名", min_length=1, max_length=32)
    password: str = Field(..., description="密码", min_length=1, max_length=64)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., description="刷新令牌", min_length=1)


class LogoutRequest(BaseModel):
    refresh_token: str | None = Field(
        None,
        description="待吊销的刷新令牌；缺省/无效时幂等成功（吊销语义在服务端按族执行）",
        min_length=1,
    )


class UpdateProfileRequest(BaseModel):
    nickname: str = Field(
        ...,
        description="昵称：0-32 字符，留空则展示回退为用户名",
        max_length=32,
    )


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., description="原密码", min_length=1, max_length=64)
    new_password: str = Field(
        ...,
        description="新密码：8-64 字符",
        min_length=8,
        max_length=64,
    )
