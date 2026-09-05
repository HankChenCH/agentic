"""会话附件端点：上传走后端（校验收口），展示地址走预签名 JSON 端点，
下载走预签名 302（浏览器直拉对象存储，绕过后端字节中转——签名 URL 即时
签发，不落库不进日志）。业务在 application 会话用例（ConversationAppService）。

分域口径见 domain/conversation/attachments.py。GET 在存储后端不具备签名
能力（本地磁盘，presign 返回 None）时降级为流式回源，本地开发与单测环境
无需 rustfs。``/url`` 端点为前端 ``<img>`` 展示专用——图片标签带不上
Authorization 头，由前端先换预签名地址再渲染。
"""

import mimetypes
from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import RedirectResponse, Response as RawResponse
from wireup import Injected

from app.api.deps import UserPrincipal, require_user
from app.application import ATTACHMENT_URL_PREFIX, ConversationAppService
from app.models.schema.response.biz_response import Response

# 顶级领域前缀：与消息 content 里的稳定引用前缀一致（ATTACHMENT_URL_PREFIX）
router = APIRouter(prefix="/agentic/attachments", tags=["Attachments"])


@router.post("")
def upload_attachment(
    app_service: Injected[ConversationAppService],
    principal: Annotated[UserPrincipal, Depends(require_user)],
    file: UploadFile,
):
    """上传会话附件（multipart），返回稳定引用（客户端入库进消息 content）。

    校验（类型/大小）在附件 store 内收口；文件流经 UploadFile 底层透传，
    边计数边转存，不整读入内存。
    """
    stored = app_service.save_attachment(
        principal.user_id,
        filename=file.filename or "",
        content_type=file.content_type,
        stream=file.file,
    )
    return Response.success({
        "id": str(stored.attachment_id),
        "filename": stored.filename,
        "mime_type": stored.mime_type,
        "size_bytes": stored.size_bytes,
        "url": stored.url,
    }).to_dict()


@router.get("/url")
def display_attachment_url(
    app_service: Injected[ConversationAppService],
    principal: Annotated[UserPrincipal, Depends(require_user)],
    ref: str,
):
    """解析附件引用为浏览器可直拉的展示地址（JSON，前端 ``<img>`` 消费）。

    ``<img>``/新标签页请求带不上 Authorization 头，直接渲染稳定引用会
    401；302 预签名又被 rustfs 的无 CORS 响应挡住（前端 fetch 跟随跨域
    重定向读 blob 需要逐跳 CORS）。故由本端点鉴权 + 属主校验后把预签名
    URL 以 JSON 返回——签名地址进 ``<img src>`` 不受 CORS 约束。签名不可
    用（本地磁盘后端）返回 ``url=null``，调用方降级为鉴权回源（GET 本域
    字节流转 blob，同 API 源无跨域问题）。

    ``ref`` 接受稳定引用的任意形态（裸相对路径或「API 根 + 相对路径」
    绝对 URL，host 任意），与 ``resolve_own_key`` 口径一致。路由须注册
    在 catch-all 之前（``/url`` 恰好不是合法的附件路径——引用恒为
    ``{id}/{filename}`` 两段，不存在遮蔽）。
    """
    return Response.success({"url": app_service.display_url(principal.user_id, ref)}).to_dict()


@router.get("/{attachment_path:path}")
def download_attachment(
    app_service: Injected[ConversationAppService],
    principal: Annotated[UserPrincipal, Depends(require_user)],
    attachment_path: str,
):
    """附件读取入口：属主校验通过后 302 重定向到预签名 URL。

    ``<img>`` 等浏览器原生加载不受跨域限制，直接消费重定向；签名有效期
    只需覆盖一次渲染（过期由下次加载重新签发），响应禁缓存防把过期签名
    存进浏览器。签名不可用（本地磁盘后端）降级为后端流式回源。
    """
    # 路径参数已被 ASGI 层百分号解码；再 unquote 兜底双重编码的文件名
    target = app_service.resolve_download(
        principal.user_id, f"{ATTACHMENT_URL_PREFIX}{unquote(attachment_path)}"
    )
    if target.presigned:
        return RedirectResponse(target.presigned, status_code=302, headers={"Cache-Control": "no-store"})

    # 降级回源（本地磁盘后端）：字节流直出，Content-Type 按扩展名推断
    data = app_service.read_attachment(target.key)
    media_type = mimetypes.guess_type(target.key.rsplit("/", 1)[-1])[0] or "application/octet-stream"
    return RawResponse(content=data, media_type=media_type, headers={"Cache-Control": "private, max-age=60"})
