"""Storage abstraction — S3-compatible interface, local-FS dev adapter.

docs/architecture.md §D: files behind signed URLs; never web-root; local-FS
adapter for dev, S3 for prod. This module defines the port; adapters
implement it. Path safety: keys are validated, never used raw in filesystem
paths without containment checks.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO

_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9/_.-]{0,500}$")


class StorageError(RuntimeError):
    pass


class InvalidStorageKey(StorageError):
    pass


class KeyNotFound(StorageError):
    pass


def validate_key(key: str) -> str:
    """Storage keys: lowercase, forward-slash separated, no empty segments or traversal."""
    if not _KEY_RE.match(key) or "//" in key:
        raise InvalidStorageKey(f"invalid storage key: {key!r}")
    if ".." in key.split("/") or key.startswith("/"):
        raise InvalidStorageKey(f"traversal or absolute key rejected: {key!r}")
    return key


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Storage(ABC):
    """Object-storage port. All keys pass validate_key()."""

    @abstractmethod
    def put(self, key: str, data: BinaryIO, *, length: int) -> str:
        """Store bytes; returns the storage key."""

    @abstractmethod
    def get(self, key: str) -> bytes:
        """Fetch bytes; raises KeyNotFound."""

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def signed_url(self, key: str, *, expires_seconds: int = 3600) -> str:
        """A URL the client may use briefly. Dev adapter returns a local path marker."""


class LocalStorage(Storage):
    """Dev/test adapter: content-addressed layout under a root dir.

    Layout: <root>/<key> with key containment enforced by validate_key.
    Writes are atomic (tmp file + rename) so a crash never leaves partials.
    """

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, key: str) -> Path:
        validate_key(key)
        p = (self._root / key).resolve()
        if not p.is_relative_to(self._root):
            raise InvalidStorageKey(f"key escapes root: {key!r}")
        return p

    def put(self, key: str, data: BinaryIO, *, length: int) -> str:
        p = self._path_for(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + f".tmp{secrets.token_hex(4)}")
        try:
            written = 0
            with open(tmp, "wb") as out:
                while chunk := data.read(1024 * 1024):
                    out.write(chunk)
                    written += len(chunk)
                    if written > length:
                        raise StorageError(
                            f"stream exceeded declared length {length}"
                        )
            if written != length:
                raise StorageError(
                    f"short write: {written} != {length} for key {key}"
                )
            tmp.replace(p)  # atomic on same filesystem
        finally:
            tmp.unlink(missing_ok=True)
        return key

    def get(self, key: str) -> bytes:
        p = self._path_for(key)
        if not p.is_file():
            raise KeyNotFound(key)
        return p.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path_for(key).is_file()

    def delete(self, key: str) -> None:
        p = self._path_for(key)
        if p.is_file():
            p.unlink()

    def signed_url(self, key: str, *, expires_seconds: int = 3600) -> str:
        # Dev adapter: marker URL; the dev API serves /files/{key} with a token check.
        token = secrets.token_urlsafe(16)
        return f"local://{key}?token={token}&expires={expires_seconds}"


class MemoryStorage(Storage):
    """In-memory adapter for unit tests."""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    def put(self, key: str, data: BinaryIO, *, length: int) -> str:
        validate_key(key)
        payload = data.read()
        if len(payload) != length:
            raise StorageError(f"short write: {len(payload)} != {length}")
        self._objects[key] = payload
        return key

    def get(self, key: str) -> bytes:
        validate_key(key)
        if key not in self._objects:
            raise KeyNotFound(key)
        return self._objects[key]

    def exists(self, key: str) -> bool:
        validate_key(key)
        return key in self._objects

    def delete(self, key: str) -> None:
        validate_key(key)
        self._objects.pop(key, None)

    def signed_url(self, key: str, *, expires_seconds: int = 3600) -> str:
        validate_key(key)
        return f"memory://{key}"


def storage_from_settings(backend: str, local_dir: str) -> Storage:
    if backend == "local":
        return LocalStorage(local_dir)
    raise StorageError(
        f"s3 backend configured but boto3 adapter lands with deploy ticket (T128); "
        f"requested backend={backend!r}"
    )
