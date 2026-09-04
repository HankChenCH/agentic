"""会话附件对象存储：key 布局、上传校验与读路径的领域归属。

分域口径（对象存储访问三通道）：
- **上传**永远过后端：类型（仅图片）/大小校验收口在本 store，流式转存
  边计数边落盘，超限即断流并清理半截对象；
- **浏览器展示**走预签名 302（``presign``，浏览器直拉对象存储绕过后端
  字节中转）；签名 URL 不落库不进日志——消息 content 里存的是稳定相对
  引用（``ATTACHMENT_URL_PREFIX``），永不过期；
- **LLM 输入**走 ``read()`` 内部读（模型云端拉不到内网对象存储）。

key 布局 ``{PREFIX}/{user_id}/{attachment_id}/{filename}``：属主维度在
首段——``resolve_own_key`` 以消息属主身份重建 key，跨用户引用天然失配
（按不存在处理），无需查库。``filename`` 保留原始名（下载时按扩展名
推断 Content-Type），经 URL 引用时百分号编码。
"""

import io
import mimetypes
from dataclasses import dataclass
from typing import BinaryIO, Callable
from urllib.parse import quote, unquote, urlsplit
from uuid import UUID, uuid4

from wireup import injectable

from app.core.logging import LoggerFactory
from app.exceptions import AttachmentNotFoundError, AttachmentTooLargeError, UnsupportedAttachmentTypeError
from app.domain.ports import Filesystem

# key 首段（对象存储里的领域命名空间）
_KEY_PREFIX = "conversation-attachments"
# 浏览器/LLM 引用附件的稳定 URL 前缀（API 相对路径，与
# api/v1/endpoints/attachments.py 的 GET 路由约定一致）
ATTACHMENT_URL_PREFIX = "/agentic/attachments/"

# 本期仅图片；与 llm.yaml vision 模型可接受的图型对齐
ALLOWED_CONTENT_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
# 单附件字节上限（图片走预签名直拉，无需给后端留大文件带宽，上限收得比知识库紧）
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
# 预签名 URL 有效期（秒）：覆盖一次页面渲染即可，过期由下次渲染重新签发
PRESIGN_TTL_SECONDS = 600

_READ_CHUNK = 256 * 1024


@dataclass(frozen=True)
class StoredAttachment:
    """上传结果：``url`` 为稳定相对引用（客户端拼 API 根后使用/入库）。"""

    attachment_id: UUID
    filename: str
    mime_type: str
    size_bytes: int
    url: str


@injectable
@dataclass
class ConversationAttachmentStore:
    """会话附件存储门面：key 布局唯一归属 + 上传校验收口。"""

    filesystem: Filesystem
    logger_factory: LoggerFactory

    def __post_init__(self):
        self.logger = self.logger_factory.get_logger(__name__)

    # ---------- 上传 ----------

    def save(self, user_id: UUID, filename: str, content_type: str | None, stream: BinaryIO) -> StoredAttachment:
        """校验并流式转存一个附件，返回稳定引用。

        校验口径：mime 取显式 ``content_type``，缺失时按文件名扩展推断，
        两者都不可判定为图片则拒绝；大小在转存途中计数，超限断流并清理
        半截对象（对齐知识库上传的 read-through 计数模式）。
        """
        clean_name = _sanitize_filename(filename)
        mime = (content_type or "").split(";")[0].strip().lower() or (mimetypes.guess_type(clean_name)[0] or "")
        if mime not in ALLOWED_CONTENT_TYPES:
            raise UnsupportedAttachmentTypeError(
                f"unsupported attachment type: {mime or 'unknown'} (allowed: {sorted(ALLOWED_CONTENT_TYPES)})"
            )

        attachment_id = uuid4()
        key = self.attachment_key(user_id, attachment_id, clean_name)
        counting = _CountingLimitedReader(stream, MAX_ATTACHMENT_BYTES)
        try:
            self.filesystem.put(key, counting)
        except _LimitExceeded:
            self._delete_best_effort(key)
            raise AttachmentTooLargeError(f"attachment exceeds size limit ({MAX_ATTACHMENT_BYTES} bytes)")
        except Exception:
            # 转存中断（网络/存储故障）同样可能留下半截对象，best-effort 清理
            self._delete_best_effort(key)
            raise
        return StoredAttachment(
            attachment_id=attachment_id,
            filename=clean_name,
            mime_type=mime,
            size_bytes=counting.count,
            url=self.attachment_url(attachment_id, clean_name),
        )

    # ---------- key / url 布局 ----------

    @staticmethod
    def attachment_key(user_id: UUID, attachment_id: UUID, filename: str) -> str:
        return f"{_KEY_PREFIX}/{user_id}/{attachment_id}/{filename}"

    @staticmethod
    def attachment_url(attachment_id: UUID, filename: str) -> str:
        # safe="" 把路径分隔符也编码，保证 url 路径恰好两段（id + filename）
        return f"{ATTACHMENT_URL_PREFIX}{attachment_id}/{quote(filename, safe='')}"

    def resolve_own_key(self, url_or_path: str, user_id: UUID) -> str | None:
        """把本域附件引用解析为对象 key；非本域引用返回 None。

        ``url_or_path`` 接受完整 URL 或路径（前端引用为「API 根 + 相对
        路径」，host 部分任意）。属主以 ``user_id``（消息属主）为准构造
        key——引用他人附件会得到不存在的 key，按不存在处理。
        """
        path = urlsplit(url_or_path).path
        if not path.startswith(ATTACHMENT_URL_PREFIX):
            return None
        rest = unquote(path[len(ATTACHMENT_URL_PREFIX):])
        segments = rest.split("/", 1)
        if len(segments) != 2 or not segments[1]:
            return None
        try:
            attachment_id = UUID(segments[0])
        except ValueError:
            return None
        return self.attachment_key(user_id, attachment_id, segments[1])

    # ---------- 读路径 ----------

    def read(self, key: str) -> bytes:
        """内部读（LLM 输入转 base64 用）；不存在抛 AttachmentNotFoundError。"""
        try:
            return self.filesystem.read(key)
        except FileNotFoundError:
            raise AttachmentNotFoundError("attachment not found")

    def presign(self, key: str, expires_in: int = PRESIGN_TTL_SECONDS) -> str | None:
        """预签名 GET URL；存储后端不支持签名（本地磁盘）返回 None，
        调用方（下载端点）降级为流式回源。"""
        return self.filesystem.presign_get(key, expires_in)

    def own_url_resolver(self, user_id: UUID) -> Callable[[str], bytes | None]:
        """构造「引用 → 字节」解析器（多模态消息构造用）。

        非本域引用、对象缺失、读取失败一律返回 None（调用方按降级丢弃），
        读取异常只记日志——单张坏图不阻断整轮流。
        """

        def resolve(url: str) -> bytes | None:
            key = self.resolve_own_key(url, user_id)
            if key is None:
                return None
            try:
                return self.filesystem.read(key)
            except Exception:
                self.logger.warning("failed to read attachment for url %s", url, exc_info=True)
                return None

        return resolve

    def _delete_best_effort(self, key: str) -> None:
        try:
            self.filesystem.delete(key)
        except Exception:
            self.logger.warning("failed to clean up partial object %s", key, exc_info=True)


def _sanitize_filename(filename: str | None) -> str:
    # 只保留 basename 与常见文件名字符，杜绝路径逃逸与控制字符
    name = (filename or "attachment").replace("\\", "/").rsplit("/", 1)[-1].strip()
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '\r\n"')
    return name or "attachment"


class _CountingLimitedReader:
    """读穿透计数限长包装：边读边计数，超限即抛（断流转存）。

    ``seek``/``tell`` 委托底层流——obstore ``put`` 的输入校验要求 file-like
    具备这两个方法（探测流长以决定是否 multipart，只实现 ``read`` 会被拒：
    Unexpected input for PutInput），探长后必先 seek 回起点再单遍顺序读取，
    计数不受影响（口径同知识库的 _CountingDigestReader）。
    """

    def __init__(self, stream: BinaryIO, limit: int):
        self._stream = stream
        self._limit = limit
        self.count = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self._stream.read(_READ_CHUNK if size is None or size < 0 else min(size, _READ_CHUNK))
        if chunk:
            self.count += len(chunk)
            if self.count > self._limit:
                raise _LimitExceeded()
        return chunk

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        return self._stream.seek(offset, whence)

    def tell(self) -> int:
        return self._stream.tell()


class _LimitExceeded(Exception):
    """内部信号：转存途中超过大小上限（save 捕获后翻译为业务异常）。"""
