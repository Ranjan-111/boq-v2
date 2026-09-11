"""S3Storage adapter tests — verified against live MinIO (T128 slice 1).

Gated on S3_TEST_ENDPOINT (e.g. http://localhost:9000): unset → the whole
module skips so the default suite stays green; set (CI runs it with a minio
service) → a GENERATED bucket is created per session and deleted after, the
same never-collide discipline as the migrated_db fixture (backend/tests/
conftest.py) — no shared state, no stale buckets, parallel-safe.

Marker: integration (matches the live-Postgres backend/tests convention).
"""
from __future__ import annotations

import io
import os
import uuid
from collections.abc import Generator
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest

from backend.app.storage.base import (
    InvalidStorageKey,
    KeyNotFound,
    StorageError,
    storage_from_settings,
)

S3_TEST_ENDPOINT = os.environ.get("S3_TEST_ENDPOINT", "").strip()

if not S3_TEST_ENDPOINT:
    pytest.skip(
        "S3_TEST_ENDPOINT not set (needs a live MinIO/S3 endpoint) — skipping S3 adapter tests",
        allow_module_level=True,
    )

pytestmark = pytest.mark.integration

# Test harness credentials: local MinIO container only (docker-compose.yml),
# never a real cloud account. CI injects the same values.
_TEST_ACCESS_KEY = os.environ.get("S3_TEST_ACCESS_KEY", "boq")
_TEST_SECRET_KEY = os.environ.get("S3_TEST_SECRET_KEY", "boqboq123")


@pytest.fixture(scope="module")
def bucket_name() -> Generator[str]:
    """A generated, per-run bucket: create via boto3, delete after."""
    import boto3  # type: ignore[import-untyped]  # test-harness dependency

    name = f"boq-s3-test-{uuid.uuid4().hex}"

    client = boto3.client(
        "s3",
        endpoint_url=S3_TEST_ENDPOINT,
        aws_access_key_id=_TEST_ACCESS_KEY,
        aws_secret_access_key=_TEST_SECRET_KEY,
        region_name="us-east-1",
        config=boto3.session.Config(s3={"addressing_style": "path"}),
    )
    client.create_bucket(Bucket=name)
    try:
        yield name
    finally:
        # Empty the bucket first: S3 refuses to delete non-empty buckets.
        objs = client.list_objects_v2(Bucket=name)
        for obj in objs.get("Contents", []):
            client.delete_object(Bucket=name, Key=obj["Key"])
        client.delete_bucket(Bucket=name)


@pytest.fixture()
def store(bucket_name: str) -> S3StorageT:
    from backend.app.storage.s3 import S3Storage

    return S3Storage(
        endpoint_url=S3_TEST_ENDPOINT,
        bucket=bucket_name,
        access_key=_TEST_ACCESS_KEY,
        secret_key=_TEST_SECRET_KEY,
    )


from backend.app.storage.s3 import S3Storage as S3StorageT  # noqa: E402 — after the gate


class TestS3Storage:
    def test_put_get_roundtrip(self, store: S3StorageT) -> None:
        payload = b"hello s3" * 512  # 4 KiB — spans chunks, still tiny
        key = store.put("roundtrip/a.bin", io.BytesIO(payload), length=len(payload))
        assert key == "roundtrip/a.bin"
        assert store.get(key) == payload  # exact byte identity

    def test_exists(self, store: S3StorageT) -> None:
        assert not store.exists("exists/x.bin")
        store.put("exists/x.bin", io.BytesIO(b"x"), length=1)
        assert store.exists("exists/x.bin")

    def test_delete_idempotent(self, store: S3StorageT) -> None:
        store.put("del/x.bin", io.BytesIO(b"x"), length=1)
        store.delete("del/x.bin")
        store.delete("del/x.bin")  # second delete of a missing key: no error
        assert not store.exists("del/x.bin")

    def test_get_missing_raises_key_not_found(self, store: S3StorageT) -> None:
        with pytest.raises(KeyNotFound):
            store.get("missing/never-put.bin")

    def test_signed_url_fetches_bytes(self, store: S3StorageT) -> None:
        """The presigned URL must actually retrieve the object via plain HTTP GET."""
        payload = b"signed-url-proof"
        store.put("signed/x.bin", io.BytesIO(payload), length=len(payload))
        url = store.signed_url("signed/x.bin", expires_seconds=60)
        parsed = urlparse(url)
        assert parsed.scheme in ("http", "https")
        # Really presigned: SigV4 (X-Amz-Signature) or legacy SigV2 (Signature).
        assert "X-Amz-Signature" in url or "Signature=" in url
        with httpx.Client(timeout=httpx.Timeout(10.0)) as client:
            resp = client.get(url)
        assert resp.status_code == 200
        assert resp.content == payload

    def test_validate_key_reuse(self, store: S3StorageT) -> None:
        for bad in ("../etc/passwd", "/abs", "UPPER/x", "a//b", "x" * 600, ""):
            with pytest.raises(InvalidStorageKey):
                store.put(bad, io.BytesIO(b"x"), length=1)
            with pytest.raises(InvalidStorageKey):
                store.get(bad)

    def test_put_rejects_short_stream(self, store: S3StorageT) -> None:
        with pytest.raises(StorageError, match="short write"):
            store.put("short/x.bin", io.BytesIO(b"abc"), length=10)
        assert not store.exists("short/x.bin")

    def test_put_rejects_overlong_stream(self, store: S3StorageT) -> None:
        with pytest.raises(StorageError, match="exceeded declared length"):
            store.put("over/x.bin", io.BytesIO(b"abcdef"), length=3)
        assert not store.exists("over/x.bin")

    def test_get_returns_what_was_put_even_after_overwrite(self, store: S3StorageT) -> None:
        store.put("ow/x.bin", io.BytesIO(b"v1"), length=2)
        store.put("ow/x.bin", io.BytesIO(b"v2-longer"), length=9)  # b"v2-longer" is 9 bytes
        assert store.get("ow/x.bin") == b"v2-longer"


class TestStorageFromSettingsDispatch:
    """storage_from_settings("s3", ...) must read Settings env and build the adapter."""

    def test_dispatches_to_s3(self, bucket_name: str, monkeypatch: pytest.MonkeyPatch) -> None:
        from backend.app.config import get_settings

        monkeypatch.setenv("STORAGE_BACKEND", "s3")
        monkeypatch.setenv("S3_ENDPOINT", S3_TEST_ENDPOINT)
        monkeypatch.setenv("S3_BUCKET", bucket_name)
        monkeypatch.setenv("S3_ACCESS_KEY", _TEST_ACCESS_KEY)
        monkeypatch.setenv("S3_SECRET_KEY", _TEST_SECRET_KEY)
        # get_settings is lru_cached — clear before (pick up the test env)
        # and after (never leak the S3 test env into other tests).
        get_settings.cache_clear()
        try:
            storage = storage_from_settings("s3", "./data/uploads")
            assert storage.__class__.__name__ == "S3Storage"
            # Round-trip through the dispatched adapter proves wiring end to end.
            key = storage.put("dispatch/x.bin", io.BytesIO(b"wired"), length=5)
            assert storage.get(key) == b"wired"
        finally:
            get_settings.cache_clear()

    def test_local_dispatch_still_works(self, tmp_path: Path) -> None:
        from backend.app.storage.base import LocalStorage

        storage = storage_from_settings("local", str(tmp_path / "s"))
        assert isinstance(storage, LocalStorage)

    def test_unknown_backend_rejected(self) -> None:
        with pytest.raises(StorageError, match="unknown storage backend"):
            storage_from_settings("gcs", "./data/uploads")
