"""S3Filesystem 预签名与构建器：SigV4 本地签名、public_endpoint 签名专用
store、工厂缓存与错误路径。

纯单测：obstore 的 ``sign`` 是纯本地 SigV4 计算（无网络 I/O），``S3Store``
构造 lazy（不发起连接），因此哑凭据即可全量断言——无需真实 rustfs。签名
URL 的形态契约（host/path/过期参数）是浏览器直拉链路的部署事实，这里锁定。
"""

from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import SecretStr

from app.core.config import FilesystemConfig, S3FilesystemEntry
from app.adapters.filesystem.filesystem_factory import FilesystemFactory
from app.adapters.filesystem.s3_provider import S3Clients, S3Filesystem, S3FilesystemBuilder


@dataclass
class _StubAppConfig:
    """最小 AppConfig 替身：工厂只消费 filesystem 分节（口径同 test_db_factory）。"""

    filesystem: FilesystemConfig


def _entry(**overrides) -> S3FilesystemEntry:
    base = dict(
        bucket="agentic",
        endpoint="http://127.0.0.1:9000",
        access_key_id="agentic",
        secret_access_key=SecretStr("agentic-secret"),
        region="us-east-1",
        allow_http=True,
        virtual_hosted_style_request=False,
    )
    base.update(overrides)
    return S3FilesystemEntry(**base)


def _presign_parts(url: str) -> tuple:
    """拆签名 URL：(scheme://host/path, query dict)。"""
    parsed = urlparse(url)
    query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}", query


def test_build_client_without_public_endpoint_shares_store():
    """直连形态：不配 public_endpoint，签名与读写共用主 store。"""
    clients = S3FilesystemBuilder().build_client(_entry())
    assert isinstance(clients, S3Clients)
    assert clients.signing_store is None


def test_build_client_with_public_endpoint_builds_signing_store():
    """反代形态：public_endpoint 时另建签名专用 store（仅换 endpoint）。"""
    clients = S3FilesystemBuilder().build_client(_entry(public_endpoint="http://files.example.com/s3"))
    assert clients.signing_store is not None


def test_presign_get_signs_internal_endpoint_when_no_public_endpoint():
    fs = S3Filesystem(store=S3FilesystemBuilder().build_client(_entry()).store)
    url = fs.presign_get("knowledge/abc/doc.pdf", 600)

    base, query = _presign_parts(url)
    # path-style 寻址：bucket 在 path 首段，对象 key 紧随
    assert base == "http://127.0.0.1:9000/agentic/knowledge/abc/doc.pdf"
    assert query["X-Amz-Expires"] == "600"
    assert query["X-Amz-Signature"]
    assert "agentic/" in query["X-Amz-Credential"]  # access key 签进凭证
    assert query["X-Amz-Algorithm"] == "AWS4-HMAC-SHA256"


def test_presign_get_uses_signing_store_endpoint_when_configured():
    """反代形态：签名 URL 指向对外地址（浏览器可达），而非内网 endpoint。"""
    clients = S3FilesystemBuilder().build_client(_entry(public_endpoint="http://files.example.com/s3"))
    fs = S3Filesystem(store=clients.store, signing_store=clients.signing_store)

    url = fs.presign_get("conversation/u1/img.png", 60)

    base, query = _presign_parts(url)
    assert base.startswith("http://files.example.com/s3/agentic/conversation/u1/img.png")
    assert "127.0.0.1:9000" not in url  # 内网地址不外泄
    assert query["X-Amz-Expires"] == "60"


def test_presign_expiry_grows_with_ttl():
    fs = S3Filesystem(store=S3FilesystemBuilder().build_client(_entry()).store)
    _, short = _presign_parts(fs.presign_get("k", 60))
    _, long = _presign_parts(fs.presign_get("k", 3600))
    assert int(long["X-Amz-Expires"]) > int(short["X-Amz-Expires"])


def test_factory_caches_clients_per_entry_key(tmp_path):
    """重资源客户端按 entry key 缓存复用（Filesystem 封装每次新建）。"""
    config = FilesystemConfig(
        default="rustfs",
        providers={"rustfs": _entry()},
    )
    factory = FilesystemFactory(app_config=_StubAppConfig(filesystem=config))

    first = factory.create()
    second = factory.create()

    assert isinstance(first, S3Filesystem)
    assert first is not second  # 封装无状态，每次新建
    assert len(factory._clients) == 1  # 底层 S3Store 复用


def test_factory_unknown_key_fails_fast():
    config = FilesystemConfig(default="rustfs", providers={"rustfs": _entry()})
    factory = FilesystemFactory(app_config=_StubAppConfig(filesystem=config))
    with pytest.raises(ValueError, match="unknown filesystem provider"):
        factory.create(name="nope")
