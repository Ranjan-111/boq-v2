"""Secure upload foundation — validation BEFORE storage.

docs/api-contract.md non-negotiables: size caps, extension+magic-byte
validation, virus-scan hook, private storage. This module is the pure
validation core; the router wires it.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# Magic bytes: format sniffing that cannot be lied about by filename.
_PDF_MAGIC = b"%PDF-"
_DXF_CHECKS = (b"SECTION", b"ENTITIES")  # ASCII sections must appear early

MAX_FILENAME_LEN = 255
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,254}$")

ALLOWED_MIME = {
    "application/pdf": "pdf",
    "image/png": "raster",
    "image/jpeg": "raster",
    "image/webp": "raster",
    "application/dxf": "dxf",
    "application/acad": "dxf",
    # Chromium maps .dxf to image/vnd.dxf (there is no registered OS mime
    # type); like octet-stream it is an unknown-to-us DXF declaration that
    # falls through to the magic-byte sniff, which cannot be lied about.
    "image/vnd.dxf": None,
    "application/octet-stream": None,
}


class UploadRejected(ValueError):
    """Structured rejection with a machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ValidatedUpload:
    filename: str
    format: str  # pdf | dxf | raster
    size_bytes: int
    sha256: str
    storage_key: str


def sniff_format(data: bytes, filename: str) -> str:
    """Magic-byte-first format detection; filename extension corroborates."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if data.startswith(_PDF_MAGIC):
        return "pdf"
    if ext in ("png", "jpg", "jpeg", "webp"):
        if ext == "png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise UploadRejected("bad_magic", "not a real PNG")
        if ext in ("jpg", "jpeg") and not data.startswith(b"\xff\xd8"):
            raise UploadRejected("bad_magic", "not a real JPEG")
        if ext == "webp" and (data[0:4] != b"RIFF" or data[8:12] != b"WEBP"):
            raise UploadRejected("bad_magic", "not a real WEBP")
        return "raster"
    if ext == "dxf" or (b"SECTION" in data[:4096] and b"ENTITIES" in data[:65536]):
        # DXF is ASCII; require both section markers, else reject (do not guess).
        if b"SECTION" in data[:4096] and b"ENTITIES" in data[:65536]:
            return "dxf"
        raise UploadRejected("bad_magic", "DXF missing SECTION/ENTITIES markers")
    raise UploadRejected("unknown_format", f"cannot determine format of {filename!r}")


def validate_upload(
    *,
    filename: str,
    data: bytes,
    max_bytes: int,
    declared_mime: str | None = None,
) -> ValidatedUpload:
    """Full validation pipeline. Raises UploadRejected with a stable code."""
    if not filename or len(filename) > MAX_FILENAME_LEN:
        raise UploadRejected("bad_filename", "filename missing or too long")
    if not _SAFE_FILENAME.match(filename):
        # Allow unicode-ish names in practice? Keep strict: strip to safe subset.
        cleaned = re.sub(r"[^A-Za-z0-9._ -]", "_", filename)
        if not _SAFE_FILENAME.match(cleaned):
            raise UploadRejected("bad_filename", "filename not sanitizable")
        filename = cleaned
    if len(data) == 0:
        raise UploadRejected("empty", "file is empty")
    if len(data) > max_bytes:
        raise UploadRejected("too_large", f"{len(data)} > {max_bytes} bytes")
    if declared_mime is not None and declared_mime not in ALLOWED_MIME:
        raise UploadRejected("bad_mime", f"mime {declared_mime!r} not allowed")

    fmt = sniff_format(data, filename)
    sha = hashlib.sha256(data).hexdigest()
    # storage key: content-addressed under project-agnostic prefix (caller may
    # prepend project scope); the sha makes re-uploads idempotent.
    storage_key = f"uploads/{fmt}/{sha[:2]}/{sha}"
    return ValidatedUpload(
        filename=filename,
        format=fmt,
        size_bytes=len(data),
        sha256=sha,
        storage_key=storage_key,
    )
