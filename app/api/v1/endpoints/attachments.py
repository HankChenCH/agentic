"""会话附件端点：上传走后端（校验收口），下载走预签名 302（浏览器直拉
对象存储，绕过后端字节中转——签名 URL 即时签发，不落库不进日志）。

分域口径见 services/domain/conversation/attachments.py。GET 在存储后端
不具备签名能力（本地磁盘，presign 返回 None）时降级为流式回源，本地
开发与单测环境无需 rustfs。
"""

import logging
import mimetypes
from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import RedirectResponse, Response as RawResponse
from wireup import Injected

from app.api.deps import UserPrincipal, require_user
from app.exceptions import AttachmentNotFoundError
from app.models.schema.response.biz_response import Response
from app.services.domain.conversation.attachments import (
    ATTACHMENT_URL_PREFIX,
    ConversationAttachmentStore,
)

# 顶级领域前缀：与消息 content 里的稳定引用前缀一致（ATTACHMENT_URL_PREFIX）
router = APIRouter(prefix="/agentic/attachments", tags=["Attachments"])

# 模块级非 DI 代码走 stdlib logger（app 域由 InterceptHandler 桥接进统一 sinks）
logger = logging.getLogger(__name__)


@router.post("")
def upload_attachment(
    store: Injected[ConversationAttachmentStore],
    principal: Annotated[UserPrincipal, Depends(require_user)],
    file: UploadFile,
):
    """上传会话附件（multipart），返回稳定引用（客户端入库进消息 content）。

    校验（类型/大小）在附件 store 内收口；文件流经 UploadFile 底层透传，
    边计数边转存，不整读入内存。
    """
    stored = store.save(
        user_id=principal.user_id,
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


@router.get("/{attachment_path:path}")
def download_attachment(
    store: Injected[ConversationAttachmentStore],
    principal: Annotated[UserPrincipal, Depends(require_user)],
    attachment_path: str,
):
    """附件读取入口：属主校验通过后 302 重定向到预签名 URL。

    ``<img>`` 等浏览器原生加载不受跨域限制，直接消费重定向；签名有效期
    只需覆盖一次渲染（过期由下次加载重新签发），响应禁缓存防把过期签名
    存进浏览器。签名不可用（本地磁盘后端）降级为后端流式回源。
    """
    # 路径参数已被 ASGI 层百分号解码；再 unquote 兜底双重编码的文件名
    key = store.resolve_own_key(f"{ATTACHMENT_URL_PREFIX}{unquote(attachment_path)}", principal.user_id)
    if key is None:
        raise AttachmentNotFoundError("attachment not found")

    presigned = store.presign(key)
    if presigned:
        return RedirectResponse(presigned, status_code=302, headers={"Cache-Control": "no-store"})

    # 降级回源（本地磁盘后端）：字节流直出，Content-Type 按扩展名推断
    try:
        data = store.read(key)
    except AttachmentNotFoundError:
        raise
    media_type = mimetypes.guess_type(key.rsplit("/", 1)[-1])[0] or "application/octet-stream"
    return RawResponse(content=data, media_type=media_type, headers={"Cache-Control": "private, max-age=60"})
