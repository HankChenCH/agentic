from typing import Any, BinaryIO, ClassVar, Iterator

from obstore.exceptions import NotFoundError
from obstore.store import S3Store

from app.core.config import S3FilesystemEntry
from app.infrastructures.filesystem.filesystem_provider import (
    Filesystem,
    FilesystemBuilder,
    FilesystemProvider,
    register,
)


class S3Filesystem(Filesystem):
    """S3 / 兼容对象存储实现：封装 obstore ``S3Store``（Rust object_store 内核）。

    obstore 0.11 的 ``head`` 缺失时抛内建 ``FileNotFoundError`` 而非其
    ``NotFoundError``，``exists`` 同时捕获两者以兼容版本差异；
    ``list`` 返回的分块流（ListStream）逐块展平为 key。
    """

    def __init__(self, store: S3Store):
        self._store = store

    def read(self, key: str) -> bytes:
        return bytes(self._store.get(key).bytes())

    def put(self, key: str, data: bytes | BinaryIO) -> None:
        self._store.put(key, data)

    def delete(self, key: str) -> None:
        self._store.delete(key)

    def exists(self, key: str) -> bool:
        try:
            self._store.head(key)
            return True
        except (FileNotFoundError, NotFoundError):
            return False

    def list(self, prefix: str = "") -> Iterator[str]:
        for chunk in self._store.list(prefix or None):
            for meta in chunk:
                yield meta["path"]


@register
class S3FilesystemBuilder(FilesystemBuilder):
    """S3 供应商构建器：entry 直连参数透传给 obstore ``S3Store``。

    ``S3Store`` 持有 HTTP 连接池等重资源（由工厂按 entry key 缓存复用）；
    构造本身不发起网络请求，真正的连接发生在首次读写——与 vector 的
    lazy 语义一致。可选字段仅在有值时透传，留空走 obstore 的默认寻址
    与环境凭证。
    """

    provider: ClassVar[FilesystemProvider] = FilesystemProvider.S3

    def build_client(self, entry: S3FilesystemEntry) -> S3Store:
        kwargs: dict[str, Any] = {}
        if entry.endpoint:
            kwargs["endpoint"] = entry.endpoint
        if entry.access_key_id:
            kwargs["access_key_id"] = entry.access_key_id
        if entry.secret_access_key:
            kwargs["secret_access_key"] = entry.secret_access_key.get_secret_value()
        if entry.region:
            kwargs["region"] = entry.region
        if entry.virtual_hosted_style_request is not None:
            kwargs["virtual_hosted_style_request"] = entry.virtual_hosted_style_request
        if entry.allow_http:
            kwargs["client_options"] = {"allow_http": True}
        return S3Store(entry.bucket, **kwargs)

    def build(self, client: Any) -> Filesystem:
        return S3Filesystem(store=client)
