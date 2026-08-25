from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, BinaryIO, ClassVar, Iterator

from app.core.config import FilesystemProviderEntry


class FilesystemProvider(str, Enum):
    """文件存储供应商标识，与 ``FilesystemProviderEntry.type`` 对应。"""

    LOCAL = "local"
    S3 = "s3"


class Filesystem(ABC):
    """统一文件存储契约：key 为 posix 风格相对路径（如 ``knowledge/doc/a.pdf``）。

    语义对齐对象存储而非操作系统文件系统：无目录实体（``list`` 按
    prefix 过滤）；``delete`` 幂等，key 不存在时静默成功；``read`` /
    ``exists`` 对不存在的 key 分别抛 ``FileNotFoundError`` / 返回 False。
    接口为同步——项目当前各层均为同步风格（见 AGENTS.md），底层实现
    （pathlib / obstore sync API）与之匹配。
    """

    @abstractmethod
    def read(self, key: str) -> bytes:
        """读取整个对象，key 不存在时抛 ``FileNotFoundError``。"""

    @abstractmethod
    def put(self, key: str, data: bytes | BinaryIO) -> None:
        """写入对象（整体覆盖），``data`` 为字节或二进制流。"""

    @abstractmethod
    def delete(self, key: str) -> None:
        """删除对象；幂等，key 不存在时静默成功。"""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """对象是否存在。"""

    @abstractmethod
    def list(self, prefix: str = "") -> Iterator[str]:
        """按前缀列出对象 key（字典序），空前缀列出全部。"""


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
