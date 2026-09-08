"""Unit tests for the storage abstraction (local + memory adapters)."""
from __future__ import annotations

import io
from pathlib import Path

import pytest

from backend.app.storage.base import (
    InvalidStorageKey,
    KeyNotFound,
    LocalStorage,
    MemoryStorage,
    StorageError,
)


@pytest.fixture
def store(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "s")


@pytest.fixture
def mem() -> MemoryStorage:
    return MemoryStorage()


class TestKeyValidation:
    @pytest.mark.parametrize(
        "bad",
        ["../etc/passwd", "/abs", "UPPER/x", "a//b", "a/../b", "x" * 600, "", "a b"],
    )
    def test_rejects_bad_keys(self, mem: MemoryStorage, bad: str) -> None:
        with pytest.raises(InvalidStorageKey):
            mem.put(bad, io.BytesIO(b"x"), length=1)

    def test_accepts_good_keys(self, mem: MemoryStorage) -> None:
        assert mem.put("p/2026/file-1.pdf", io.BytesIO(b"ab"), length=2) == (
            "p/2026/file-1.pdf"
        )


class TestLocalStorage:
    def test_put_get_roundtrip(self, store: LocalStorage) -> None:
        store.put("k/a.bin", io.BytesIO(b"hello"), length=5)
        assert store.get("k/a.bin") == b"hello"
        assert store.exists("k/a.bin")

    def test_missing_raises(self, store: LocalStorage) -> None:
        with pytest.raises(KeyNotFound):
            store.get("nope")

    def test_short_write_rejected(self, store: LocalStorage) -> None:
        with pytest.raises(StorageError):
            store.put("k/a.bin", io.BytesIO(b"hello"), length=3)

    def test_overlong_stream_rejected(self, store: LocalStorage) -> None:
        with pytest.raises(StorageError):
            store.put("k/a.bin", io.BytesIO(b"hello"), length=2)

    def test_delete(self, store: LocalStorage) -> None:
        store.put("k/a.bin", io.BytesIO(b"hello"), length=5)
        store.delete("k/a.bin")
        assert not store.exists("k/a.bin")

    def test_no_partial_files_on_failure(self, store: LocalStorage, tmp_path: Path) -> None:
        with pytest.raises(StorageError):
            store.put("k/bad.bin", io.BytesIO(b"hello"), length=99)
        # no tmp leftovers in the target dir
        target = tmp_path / "s" / "k"
        leftovers = list(target.glob("*.tmp*")) if target.exists() else []
        assert leftovers == []


class TestMemoryStorage:
    def test_roundtrip(self, mem: MemoryStorage) -> None:
        mem.put("a", io.BytesIO(b"xyz"), length=3)
        assert mem.get("a") == b"xyz"
