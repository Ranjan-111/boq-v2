"""S3-compatible storage adapter (boto3) — prod backend behind the Storage port.

Verified against MinIO; works against any S3-compatible endpoint (AWS S3,
MinIO, ...). boto3 is imported lazily so the app boots without the `s3`
extra when storage_backend=local; configuring backend=s3 without boto3
raises a clear StorageError instead of an import crash.

Port parity (read backend/app/storage/base.py first):
- every key passes validate_key();
- get() raises KeyNotFound on a miss; delete() is idempotent (LocalStorage
  deletes a missing path silently — we match, and S3 already answers 204
  for absent keys);
- put() enforces the DECLARED length exactly like LocalStorage: a short or
  overlong stream raises StorageError and no object is stored (MinIO/S3
  enforce the ContentLength header server-side too — the wrapper makes the
  failure local and unmistakable instead of a wire error);
- signed_url(): LocalStorage returns a `local://` marker the dev API
  resolves with a token check; this adapter returns a real time-limited
  presigned GET URL. Storage is never public in either mode.

Single-request puts (put_object) are used rather than multipart — the API
caps uploads at max_upload_mb (200 MB by default), far below the 5 GB
single-put ceiling.
"""
from __future__ import annotations

import io
from typing import Any, BinaryIO

from backend.app.storage.base import (
    KeyNotFound,
    Storage,
    StorageError,
    validate_key,
)


class _DeclaredLengthReader:
    """Stream wrapper enforcing the declared length while boto3 reads the body.

    Mirrors LocalStorage's contract: reading past `length` raises (overlong
    stream), a stream that ends early raises too (short write).

    botocore's request path treats bodies as real file-likes: it calls
    tell() before and after its CRC checksum read and then seek()s back to
    the start so urllib3 can re-read the same bytes. We therefore buffer
    the declared bytes into a BytesIO (bounded by the API's upload cap —
    max_upload_mb, 200 MB default — NOT unbounded) and expose a genuinely
    seekable file-like. The enforcement happens while filling the buffer,
    before any network I/O, so a bad stream never stores anything.
    """

    def __init__(self, source: BinaryIO, length: int, key: str) -> None:
        self._declared = length
        buf = bytearray()
        while len(buf) < length:
            chunk = source.read(length - len(buf))
            if not chunk:
                raise StorageError(
                    f"short write: {len(buf)} != {length} for key {key!r}"
                )
            buf += chunk
        if source.read(1):
            raise StorageError(
                f"stream exceeded declared length {length} for key {key!r}"
            )
        self._buf = io.BytesIO(bytes(buf))

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True  # botocore checksum: tell() → read → seek(start)

    def tell(self) -> int:
        return self._buf.tell()

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._buf.seek(offset, whence)

    def read(self, amt: int = -1) -> bytes:
        return self._buf.read(amt)

    def close(self) -> None:
        self._buf.close()

    def __len__(self) -> int:
        # botocore's S3Body may probe len() for the content length.
        return self._declared


class S3Storage(Storage):
    """Object-storage adapter over boto3 (S3 / MinIO compatible)."""

    def __init__(
        self,
        endpoint_url: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
    ) -> None:
        if not endpoint_url:
            raise StorageError("storage_backend=s3 requires a non-empty S3_ENDPOINT")
        if not bucket:
            raise StorageError("storage_backend=s3 requires a non-empty S3_BUCKET")
        self._bucket = bucket
        try:
            import boto3  # type: ignore[import-untyped]  # lazy: boots without s3 extra
            from botocore.exceptions import (  # type: ignore[import-untyped]
                ClientError,
            )
        except ImportError as exc:  # pragma: no cover — environment-dependent
            raise StorageError(
                "storage_backend=s3 requires boto3 — install the extra: "
                '`uv pip install -e ".[s3]"`'
            ) from exc
        # boto3/botocore ship no py.typed — everything from them is Any here.
        # ClientError stored as a plain attribute: `except self._client_error`
        # then binds exc: Any, so response/Error access needs no casts.
        self._client_error: Any = ClientError
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,  # MinIO ignores the region value
            # Path-style addressing (bucket in the path, not the hostname):
            # MinIO requires it; AWS S3 accepts it. s3v4 forces modern
            # signing — boto3 defaults presigning to legacy SigV2 otherwise.
            config=boto3.session.Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
            ),
        )

    def put(self, key: str, data: BinaryIO, *, length: int) -> str:
        key = validate_key(key)
        body = _DeclaredLengthReader(data, length, key)
        try:
            self._client.put_object(
                Bucket=self._bucket, Key=key, Body=body, ContentLength=length
            )
        except StorageError:
            raise  # short/overlong stream — already the exact port error
        except self._client_error as exc:
            raise StorageError(f"s3 put failed for key {key!r}: {exc}") from exc
        return key

    def get(self, key: str) -> bytes:
        key = validate_key(key)
        try:
            resp = self._client.get_object(Bucket=self._bucket, Key=key)
        except self._client_error as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in ("NoSuchKey", "404"):
                raise KeyNotFound(key) from exc
            raise StorageError(f"s3 get failed for key {key!r}: {exc}") from exc
        return bytes(resp["Body"].read())

    def exists(self, key: str) -> bool:
        key = validate_key(key)
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except self._client_error as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            raise StorageError(f"s3 exists failed for key {key!r}: {exc}") from exc

    def delete(self, key: str) -> None:
        key = validate_key(key)
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except self._client_error as exc:
            raise StorageError(f"s3 delete failed for key {key!r}: {exc}") from exc

    def signed_url(self, key: str, *, expires_seconds: int = 3600) -> str:
        key = validate_key(key)
        try:
            return str(
                self._client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self._bucket, "Key": key},
                    ExpiresIn=expires_seconds,
                )
            )
        except self._client_error as exc:
            raise StorageError(f"s3 signed_url failed for key {key!r}: {exc}") from exc
