"""Auth: JWT issue/verify + password hashing. No I/O — pure crypto boundary."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
from jose import JWTError, jwt

# Password hashing
_BCRYPT_ROUNDS = 12


def hash_password(plain: str) -> str:
    if len(plain) < 8:
        raise ValueError("password must be at least 8 characters")
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


# Token model
@dataclass(frozen=True, slots=True)
class TokenClaims:
    sub: str  # user id (uuid string)
    role: str
    exp: datetime
    token_type: str  # access | refresh

    def as_dict(self) -> dict[str, Any]:
        return {
            "sub": self.sub,
            "role": self.role,
            "exp": int(self.exp.timestamp()),
            "typ": self.token_type,
        }


def issue_token(
    *,
    user_id: str,
    role: str,
    secret: str,
    algorithm: str,
    minutes: int,
    token_type: str = "access",  # noqa: S107 - type label, not a password default
) -> str:
    if not _is_uuid(user_id):
        raise ValueError("user_id must be a uuid")
    claims = TokenClaims(
        sub=user_id,
        role=role,
        exp=datetime.now(UTC) + timedelta(minutes=minutes),
        token_type=token_type,
    )
    return jwt.encode(claims.as_dict(), secret, algorithm=algorithm)


class TokenInvalid(RuntimeError):
    """Raised for any malformed/expired/wrong-type token."""


def verify_token(
    token: str,
    *,
    secret: str,
    algorithm: str,
    expected_type: str = "access",
) -> TokenClaims:
    try:
        payload = jwt.decode(token, secret, algorithms=[algorithm])
    except JWTError as exc:
        raise TokenInvalid("malformed or expired token") from exc
    if payload.get("typ") != expected_type:
        raise TokenInvalid(f"expected {expected_type} token")
    sub = payload.get("sub")
    role = payload.get("role")
    if not _is_uuid(str(sub)) or not isinstance(role, str):
        raise TokenInvalid("missing claims")
    return TokenClaims(
        sub=str(sub),
        role=role,
        exp=datetime.fromtimestamp(payload["exp"], tz=UTC),
        token_type=expected_type,
    )


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError):
        return False
