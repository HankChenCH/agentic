"""文件存储供应商机制：builder 注册表 + 供应商枚举。

``Filesystem`` 契约（抽象）住 ``app/domain/ports``——端口反转：domain 与
components 只依赖契约，本包（含 local/s3 实现）导入契约来实现。本模块保留
实现侧机件：供应商枚举、``FilesystemBuilder`` 两层构建抽象与 ``@register``
注册表，由 ``filesystem_factory`` 消费。
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, ClassVar

from app.core.config import FilesystemProviderEntry
from app.domain.ports import Filesystem

__all__ = [
    "FILESYSTEM_BUILDERS",
    "Filesystem",
    "FilesystemBuilder",
    "FilesystemProvider",
    "register",
]


class FilesystemProvider(str, Enum):
    """文件存储供应商标识，与 ``FilesystemProviderEntry.type`` 对应。"""

    LOCAL = "local"
    S3 = "s3"


class FilesystemBuilder(ABC):
    """文件存储构建器：每个供应商一个子类，把 provider entry 翻译成契约实例。

    分两层构建：``build_client`` 产出供应商原生资源（S3 客户端连接池等
    重资源，由工厂按 entry key 缓存复用；本地实现的「客户端」即已就绪的
    根目录 ``Path``）；``build`` 基于已有客户端构建 ``Filesystem`` 契约
    实例（无状态轻量封装，按需创建）。entry 已携带全部直连参数
    （bucket / endpoint / 凭据等），构建器自身无需再读全局配置。工厂按
    entry 的 ``type`` 路由到注册的构建器，保证类型与构建器一一对应。
    """

    provider: ClassVar[FilesystemProvider]

    @abstractmethod
    def build_client(self, entry: FilesystemProviderEntry) -> Any:
        """按 entry 创建供应商原生客户端（重资源，由工厂缓存复用）。"""

    @abstractmethod
    def build(self, client: Any) -> Filesystem:
        """基于已有客户端构建 Filesystem 契约实例（轻量封装）。"""


# 供应商注册表：@register 自动登记，新增供应商不改工厂
FILESYSTEM_BUILDERS: dict[FilesystemProvider, type[FilesystemBuilder]] = {}


def register(builder_cls: type[FilesystemBuilder]) -> type[FilesystemBuilder]:
    FILESYSTEM_BUILDERS[builder_cls.provider] = builder_cls
    return builder_cls
