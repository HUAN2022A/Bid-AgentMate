"""文件存储：本地磁盘实现 + Storage 抽象（Q18/Q25 定稿）。

MinIO 实现留待多租户时补，调用方只依赖 Storage 协议三方法。
内部统一 posix 相对路径，落盘时转换（Windows 开发机 / Linux 部署机兼容）。
"""
import hashlib
from pathlib import Path, PurePosixPath
from typing import Protocol

from app.core.config import settings


class Storage(Protocol):
    def put(self, relative_path: str, data: bytes) -> str: ...
    def get(self, relative_path: str) -> bytes: ...
    def abspath(self, relative_path: str) -> Path: ...  # 本地实现特有：给 extract 脚本用


class LocalStorage:
    def __init__(self, root: str | None = None):
        self.root = Path(root or settings.data_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _to_fs(self, relative_path: str) -> Path:
        # 防目录穿越：相对路径规范化后必须仍在 root 内
        posix = PurePosixPath(relative_path)
        if posix.is_absolute() or ".." in posix.parts:
            raise ValueError(f"非法存储路径: {relative_path}")
        p = self.root.joinpath(*posix.parts).resolve()
        if self.root not in p.parents and p != self.root:
            raise ValueError(f"非法存储路径: {relative_path}")
        return p

    def put(self, relative_path: str, data: bytes) -> str:
        p = self._to_fs(relative_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return relative_path

    def get(self, relative_path: str) -> bytes:
        return self._to_fs(relative_path).read_bytes()

    def abspath(self, relative_path: str) -> Path:
        return self._to_fs(relative_path)

    def exists(self, relative_path: str) -> bool:
        return self._to_fs(relative_path).exists()


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


storage = LocalStorage()
