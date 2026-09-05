from datetime import timedelta
from typing import Any, BinaryIO, ClassVar, Iterator, NamedTuple

import obstore
from obstore.exceptions import NotFoundError
from obstore.store import S3Store

from app.core.config import S3FilesystemEntry
from app.adapters.filesystem.filesystem_provider import (
    Filesystem,
    FilesystemBuilder,
    FilesystemProvider,
    register,
)


class S3Clients(NamedTuple):
    """S3 供应商原生资源对（由工厂按 entry key 缓存复用）。

    ``store`` 为读写用主 store；``signing_store`` 仅在配置了
    ``public_endpoint``（反代部署形态）时存在，专用于预签名——SigV4 把
    host/path 签进签名，浏览器可达地址与内网 endpoint 不一致时必须以
    对外地址构建签名 store。两者均 lazy，构造不发起网络请求。
    """

    store: S3Store
    signing_store: S3Store | None = None


class S3Filesystem(Filesystem):
    """S3 / 兼容对象存储实现：封装 obstore ``S3Store``（Rust object_store 内核）。

    obstore 0.11 的 ``head`` 缺失时抛内建 ``FileNotFoundError`` 而非其
    ``NotFoundError``，``exists`` 同时捕获两者以兼容版本差异；
    ``list`` 返回的分块流（ListStream）逐块展平为 key。
    预签名优先用签名专用 store（public_endpoint 部署形态），未配置时与
    读写共用主 store（直连形态）。
    """

    def __init__(self, store: S3Store, signing_store: S3Store | None = None):
        self._store = store
        self._signing_store = signing_store

    def read(self, key: str) -> bytes:
        try:
            return bytes(self._store.get(key).bytes())
        except NotFoundError as exc:
            # 契约口径：read 对缺失 key 抛内建 FileNotFoundError（obstore 用自家异常类型）
            raise FileNotFoundError(key) from exc

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

    def presign_get(self, key: str, expires_in: int) -> str | None:
        signer = self._signing_store or self._store
        return obstore.sign(signer, "GET", key, timedelta(seconds=expires_in))


@register
class S3FilesystemBuilder(FilesystemBuilder):
    """S3 供应商构建器：entry 直连参数透传给 obstore ``S3Store``。

    ``S3Store`` 持有 HTTP 连接池等重资源（由工厂按 entry key 缓存复用）；
    构造本身不发起网络请求，真正的连接发生在首次读写——与 vector 的
    lazy 语义一致。可选字段仅在有值时透传，留空走 obstore 的默认寻址
    与环境凭证。配置 ``public_endpoint`` 时额外构建一个签名专用 store
    （仅换 endpoint，其余参数一致）。
    """

    provider: ClassVar[FilesystemProvider] = FilesystemProvider.S3

    @staticmethod
    def _store_kwargs(entry: S3FilesystemEntry, endpoint: str | None) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if endpoint:
            kwargs["endpoint"] = endpoint
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
        return kwargs

    def build_client(self, entry: S3FilesystemEntry) -> S3Clients:
        store = S3Store(entry.bucket, **self._store_kwargs(entry, entry.endpoint))
        signing = None
        if entry.public_endpoint:
            signing = S3Store(entry.bucket, **self._store_kwargs(entry, entry.public_endpoint))
        return S3Clients(store=store, signing_store=signing)

    def build(self, client: Any) -> Filesystem:
        assert isinstance(client, S3Clients)
        return S3Filesystem(store=client.store, signing_store=client.signing_store)
