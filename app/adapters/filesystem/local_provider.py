import shutil
from pathlib import Path
from typing import Any, BinaryIO, ClassVar, Iterator

from app.core.config import LocalFilesystemEntry
from app.adapters.filesystem.filesystem_provider import (
    Filesystem,
    FilesystemBuilder,
    FilesystemProvider,
    register,
)


class LocalFilesystem(Filesystem):
    """本地磁盘实现：key 映射为 root 下的相对路径，pathlib 读写。

    ``list`` 全量遍历后按字符串前缀过滤并排序（字典序，对齐 S3 list
    语义）；本地开发规模下足够，海量文件场景应换 S3 entry。
    """

    def __init__(self, root: Path):
        self._root = root.resolve()

    def _path(self, key: str) -> Path:
        # 拒绝绝对路径与 .. 逃逸，保证 key 始终落在 root 内
        path = (self._root / key).resolve()
        if not path.is_relative_to(self._root):
            raise ValueError(f"key escapes filesystem root: {key!r}")
        return path

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def put(self, key: str, data: bytes | BinaryIO) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, (bytes, bytearray, memoryview)):
            path.write_bytes(data)
        else:
            with path.open("wb") as target:
                shutil.copyfileobj(data, target)

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list(self, prefix: str = "") -> Iterator[str]:
        for path in sorted(self._root.rglob("*")):
            if not path.is_file():
                continue
            key = path.relative_to(self._root).as_posix()
            if key.startswith(prefix):
                yield key


@register
class LocalFilesystemBuilder(FilesystemBuilder):
    """本地磁盘构建器：「客户端」即已就绪的根目录 ``Path``（轻资源）。"""

    provider: ClassVar[FilesystemProvider] = FilesystemProvider.LOCAL

    def build_client(self, entry: LocalFilesystemEntry) -> Path:
        # 仿 sqlite engine 的惯例提前建目录，避免首次写入失败
        root = Path(entry.root)
        root.mkdir(parents=True, exist_ok=True)
        return root

    def build(self, client: Any) -> Filesystem:
        return LocalFilesystem(root=client)
