"""Unit tests for upload validation — the security boundary."""
from __future__ import annotations

import pytest

from backend.app.uploads.validation import (
    UploadRejected,
    ValidatedUpload,
    sniff_format,
    validate_upload,
)

PDF = b"%PDF-1.7 ...rest of a real pdf..."


def _dxf(*, include_entities: bool = True) -> bytes:
    s = b"0\nSECTION\n2\nHEADER\n0\nENDSEC\n"
    if include_entities:
        s += b"0\nSECTION\n2\nENTITIES\n0\nENDSEC\n"
    return s


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
JPEG = b"\xff\xd8" + b"\x00" * 100
WEBP = b"RIFF\x24\x00\x00\x00WEBP" + b"\x00" * 100


class TestSniff:
    def test_pdf_by_magic(self) -> None:
        assert sniff_format(PDF, "drawing.pdf") == "pdf"

    def test_pdf_magic_beats_wrong_extension(self) -> None:
        assert sniff_format(PDF, "drawing.txt") == "pdf"

    def test_dxf_by_markers(self) -> None:
        assert sniff_format(_dxf(), "plan.dxf") == "dxf"

    def test_png_jpeg_webp(self) -> None:
        assert sniff_format(PNG, "a.png") == "raster"
        assert sniff_format(JPEG, "a.jpg") == "raster"
        assert sniff_format(WEBP, "a.webp") == "raster"

    def test_fake_png_rejected(self) -> None:
        with pytest.raises(UploadRejected) as e:
            sniff_format(b"\x89PNG-corrupt", "a.png")
        assert e.value.code == "bad_magic"

    def test_dxf_without_entities_rejected(self) -> None:
        with pytest.raises(UploadRejected) as e:
            sniff_format(_dxf(include_entities=False), "plan.dxf")
        assert e.value.code == "bad_magic"

    def test_unknown_binary_rejected(self) -> None:
        with pytest.raises(UploadRejected) as e:
            sniff_format(b"\x00\x01\x02", "file.xyz")
        assert e.value.code == "unknown_format"

    def test_exe_disguised_as_pdf_rejected(self) -> None:
        with pytest.raises(UploadRejected):
            sniff_format(b"MZ\x90\x00", "virus.pdf")  # PE magic


class TestValidate:
    def test_happy_path_pdf(self) -> None:
        v = validate_upload(filename="site plan.pdf", data=PDF, max_bytes=10_000)
        assert isinstance(v, ValidatedUpload)
        assert v.format == "pdf"
        assert len(v.sha256) == 64
        assert v.storage_key.startswith("uploads/pdf/")
        assert v.filename == "site plan.pdf"

    def test_empty_rejected(self) -> None:
        with pytest.raises(UploadRejected) as e:
            validate_upload(filename="a.pdf", data=b"", max_bytes=10)
        assert e.value.code == "empty"

    def test_too_large_rejected(self) -> None:
        with pytest.raises(UploadRejected) as e:
            validate_upload(filename="a.pdf", data=PDF, max_bytes=5)
        assert e.value.code == "too_large"

    def test_bad_mime_rejected(self) -> None:
        with pytest.raises(UploadRejected) as e:
            validate_upload(
                filename="a.pdf", data=PDF, max_bytes=1000, declared_mime="application/zip"
            )
        assert e.value.code == "bad_mime"

    def test_octet_stream_falls_through_to_magic_sniff(self) -> None:
        # Browsers send application/octet-stream for .dxf (no registered
        # OS mime type). The honest-unknown mime must reach the magic-byte
        # sniff, which is the check that cannot be lied about.
        v = validate_upload(
            filename="plan.dxf", data=_dxf(), max_bytes=1000,
            declared_mime="application/octet-stream",
        )
        assert v.format == "dxf"
        # ...and the sniff still refuses lies: octet-stream with fake-DXF
        # bytes (no SECTION/ENTITIES markers) is rejected by content.
        with pytest.raises(UploadRejected) as e:
            validate_upload(
                filename="fake.dxf", data=b"garbage bytes", max_bytes=1000,
                declared_mime="application/octet-stream",
            )
        assert e.value.code == "bad_magic"

    def test_unsanitizable_filename_rejected(self) -> None:
        with pytest.raises(UploadRejected):
            validate_upload(filename="", data=PDF, max_bytes=1000)

    def test_filename_sanitized(self) -> None:
        v = validate_upload(filename="plan<1>.pdf", data=PDF, max_bytes=1000)
        assert "<" not in v.filename and ">" not in v.filename

    def test_content_addressed_key_is_stable(self) -> None:
        a = validate_upload(filename="a.pdf", data=PDF, max_bytes=1000)
        b = validate_upload(filename="b.pdf", data=PDF, max_bytes=1000)
        assert a.storage_key == b.storage_key  # same content -> same key

    def test_dxf_uploads_clean(self) -> None:
        v = validate_upload(filename="plan.dxf", data=_dxf(), max_bytes=1_000_000)
        assert v.format == "dxf"
        assert v.storage_key.startswith("uploads/dxf/")
