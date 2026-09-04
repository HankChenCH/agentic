"""统一文件存储契约：key 为 posix 风格相对路径（如 ``knowledge/doc/a.pdf``）。

语义对齐对象存储而非操作系统文件系统：无目录实体（``list`` 按
prefix 过滤）；``delete`` 幂等，key 不存在时静默成功；``read`` /
``exists`` 对不存在的 key 分别抛 ``FileNotFoundError`` / 返回 False。
接口为同步——项目当前各层均为同步风格，底层实现
（pathlib / obstore sync API）与之匹配。

实现住 ``app/adapters/filesystem/``（local / s3），经
``FilesystemFactory``/``create_default_filesystem`` 以本契约类型注入。
"""

from abc import ABC, abstractmethod
from typing import BinaryIO, Iterator


class Filesystem(ABC):
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

    def presign_get(self, key: str, expires_in: int) -> str | None:
        """签发预签名 GET URL（浏览器凭签名直拉对象存储，绕过后端字节中转）。

        返回 ``None`` 表示后端不具备签名能力（如本地磁盘实现），调用方应
        降级为 ``read()`` 由后端回源。``expires_in`` 单位秒。签名是纯本地
        计算（SigV4），不发起网络请求；URL 的 host/path 会签进签名，浏览器
        必须以完全一致的地址访问（部署形态见 S3FilesystemEntry.public_endpoint）。
        """
        return None
