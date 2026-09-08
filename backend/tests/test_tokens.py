"""Unit tests for auth tokens + password hashing."""
from __future__ import annotations

import pytest

from backend.app.auth.tokens import (
    TokenInvalid,
    hash_password,
    issue_token,
    verify_password,
    verify_token,
)

SECRET = "test-secret-32-chars-minimum-length"  # noqa: S105


class TestPasswords:
    def test_hash_roundtrip(self) -> None:
        h = hash_password("correct horse battery staple")  # 28 chars
        assert verify_password("correct horse battery staple", h)
        assert not verify_password("wrong", h)

    def test_short_password_rejected(self) -> None:
        with pytest.raises(ValueError):
            hash_password("short")

    def test_hash_is_salted(self) -> None:
        a = hash_password("same-password-123")
        b = hash_password("same-password-123")
        assert a != b


class TestTokens:
    def test_issue_and_verify(self) -> None:
        t = issue_token(
            user_id="9c8b2d3a-1111-4a3a-9a2b-000000000001",
            role="estimator",
            secret=SECRET,
            algorithm="HS256",
            minutes=5,
        )
        claims = verify_token(t, secret=SECRET, algorithm="HS256")
        assert claims.sub.endswith("0001")
        assert claims.role == "estimator"

    def test_wrong_secret_rejected(self) -> None:
        t = issue_token(
            user_id="9c8b2d3a-1111-4a3a-9a2b-000000000001",
            role="estimator",
            secret=SECRET,
            algorithm="HS256",
            minutes=5,
        )
        with pytest.raises(TokenInvalid):
            verify_token(
                t, secret="another-secret-entirely", algorithm="HS256"  # noqa: S106
            )

    def test_refresh_not_accepted_as_access(self) -> None:
        t = issue_token(
            user_id="9c8b2d3a-1111-4a3a-9a2b-000000000001",
            role="estimator",
            secret=SECRET,
            algorithm="HS256",
            minutes=5,
            token_type="refresh",  # noqa: S106 - type label
        )
        with pytest.raises(TokenInvalid):
            verify_token(t, secret=SECRET, algorithm="HS256", expected_type="access")

    def test_garbage_token_rejected(self) -> None:
        with pytest.raises(TokenInvalid):
            verify_token("not.a.jwt", secret=SECRET, algorithm="HS256")

    def test_expired_rejected(self) -> None:
        # negative minutes = already expired
        t = issue_token(
            user_id="9c8b2d3a-1111-4a3a-9a2b-000000000001",
            role="estimator",
            secret=SECRET,
            algorithm="HS256",
            minutes=-5,
        )
        with pytest.raises(TokenInvalid):
            verify_token(t, secret=SECRET, algorithm="HS256")
