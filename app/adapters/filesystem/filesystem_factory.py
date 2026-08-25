from dataclasses import dataclass
from typing import Any

from wireup import injectable

from app.core.config import AppConfig, FilesystemConfig, FilesystemProviderEntry
from app.infrastructures.filesystem.filesystem_provider import (
    FILESYSTEM_BUILDERS,
    Filesystem,
    FilesystemBuilder,
    FilesystemProvider,
)

import app.infrastructures.filesystem.local_provider  # noqa: F401  触发 @register 供应商注册
import app.infrastructures.filesystem.s3_provider  # noqa: F401


@injectable
@dataclass
class FilesystemFactory:
    """文件存储工厂：按 provider entry key 创建 Filesystem 契约实例。

    契约类型为 :class:`~app.infrastructures.filesystem.filesystem_provider.Filesystem`
    （read / put / delete / exists / list，对象存储语义），消费方（如
    知识库文件能力）只依赖该抽象，不感知本地磁盘 / S3 的差异。

    供应商客户端（S3Store 的 HTTP 连接池等重资源）按 entry key 缓存复用；
    Filesystem 封装本身无状态，每次 create 新建，由调用方持有。lazy：
    构造不发起网络请求，真正的连接发生在首次读写。

    默认宽松、显式严格：未指定 name 时用 ``FilesystemConfig.default``；
    显式指定但 providers 里不存在的 key，或 entry 的供应商 type 未注册
    构建器，则直接抛 ValueError。
    """

    app_config: AppConfig

    def __post_init__(self):
        self._clients: dict[str, Any] = {}

    def create(self, *, name: str | None = None) -> Filesystem:
        """按 provider entry key 创建文件存储实例。"""
        key = name if name is not None else self._config.default
        entry, builder = self._resolve(key)
        client = self._client_for(key, entry, builder)
        return builder.build(client)

    @property
    def _config(self) -> FilesystemConfig:
        return self.app_config.filesystem

    def _resolve(self, key: str) -> tuple[FilesystemProviderEntry, FilesystemBuilder]:
        entry = self._config.providers.get(key)
        if entry is None:
            raise ValueError(f"unknown filesystem provider: {key}, configured: {list(self._config.providers)}")

        try:
            provider = FilesystemProvider(entry.type)
        except ValueError:
            supported = [p.value for p in FilesystemProvider]
            raise ValueError(
                f"unsupported filesystem provider type: {entry.type} (entry: {key}), supported: {supported}"
            )
        return entry, FILESYSTEM_BUILDERS[provider]()

    def _client_for(self, key: str, entry: FilesystemProviderEntry, builder: FilesystemBuilder) -> Any:
        """按 entry key 取共享客户端，首次调用时构建并缓存。"""
        if key not in self._clients:
            self._clients[key] = builder.build_client(entry)
        return self._clients[key]


@injectable(lifetime="singleton")
def create_default_filesystem(factory: FilesystemFactory) -> Filesystem:
    """默认文件存储实例（``FilesystemConfig.default``）的注入入口。

    仿 ``db/db_factory.create_default_db`` 的惯例：消费方直接注入
    ``Filesystem`` 契约使用，无需感知工厂；需要指定其他 entry（如本地
    暂存）时注入 ``FilesystemFactory`` 自行 ``create(name=...)``。lazy——
    wireup 首次解析本函数才触发构建。
    """
    return factory.create()
