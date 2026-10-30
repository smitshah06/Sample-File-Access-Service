"""File bytes storage. Local disk here; Azure Blob Storage in production (same interface).

Uploads are resumable: bytes are written chunk by chunk into `<key>.part`, at explicit
offsets, and only become `<key>` when the upload is completed. This mirrors Azure block
blobs (stage blocks, then commit the block list).
"""

import hashlib
import os
import shutil
from pathlib import Path
from typing import Protocol

_HASH_READ = 8 * 1024 * 1024


class FileStore(Protocol):
    def create_part(self, key: str) -> None: ...
    def write_part(self, key: str, offset: int, data: bytes) -> None: ...
    def finalize(self, key: str) -> tuple[int, str]: ...
    def delete_part(self, key: str) -> None: ...
    def path(self, key: str) -> Path: ...
    def delete(self, key: str) -> None: ...
    def free_bytes(self) -> int: ...


class LocalDiskStore:
    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Keys are server-generated UUIDs; the client's filename never touches the filesystem.
        if not key or "/" in key or "\\" in key or key.startswith("."):
            raise ValueError("invalid storage key")
        return self.root / key

    def _part(self, key: str) -> Path:
        return self._path(key).with_name(key + ".part")

    def create_part(self, key: str) -> None:
        self._part(key).touch(exist_ok=False)

    def write_part(self, key: str, offset: int, data: bytes) -> None:
        """Write at an explicit offset, so retrying the same chunk is harmless."""
        with open(self._part(key), "r+b") as f:
            f.seek(offset)
            f.write(data)

    def finalize(self, key: str) -> tuple[int, str]:
        """Hash the completed part and move it into place. Returns (size, sha256_hex)."""
        part = self._part(key)
        digest = hashlib.sha256()
        size = 0
        with open(part, "rb") as f:
            while chunk := f.read(_HASH_READ):
                size += len(chunk)
                digest.update(chunk)
        os.replace(part, self._path(key))  # atomic: a file is never visible half-written
        return size, digest.hexdigest()

    def delete_part(self, key: str) -> None:
        self._part(key).unlink(missing_ok=True)

    def path(self, key: str) -> Path:
        return self._path(key)

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def free_bytes(self) -> int:
        return shutil.disk_usage(self.root).free
