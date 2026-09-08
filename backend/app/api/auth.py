"""Auth API: register (dev bootstrap) + login + me."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.auth.tokens import TokenInvalid, issue_token, verify_password, verify_token
from backend.app.config import Settings, get_settings
from backend.app.db.models import User
from backend.app.main import session_dependency

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)


class RegisterBody(LoginBody):
    display_name: str = Field(min_length=1, max_length=200)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - auth-scheme label, not a credential
    user: dict


@router.post("/register", status_code=201, response_model=TokenResponse)
async def register(
    body: RegisterBody,
    request: Request,
    session: AsyncSession = Depends(session_dependency),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    from backend.app.auth.tokens import hash_password

    existing = await session.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail=[{"code": "email_taken", "message": "email already registered"}],
        )
    import uuid

    user = User(
        id=str(uuid.uuid4()),
        email=body.email,
        password_hash=hash_password(body.password),
        display_name=body.display_name,
        role="owner",
    )
    session.add(user)
    await session.flush()
    token = issue_token(
        user_id=user.id,
        role=user.role,
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        minutes=settings.access_token_minutes,
    )
    return TokenResponse(
        access_token=token,
        user={
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name,
            "role": user.role,
        },
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginBody,
    request: Request,
    session: AsyncSession = Depends(session_dependency),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    result = await session.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=401,
            detail=[{"code": "invalid_credentials", "message": "email or password incorrect"}],
        )
    token = issue_token(
        user_id=user.id,
        role=user.role,
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        minutes=settings.access_token_minutes,
    )
    return TokenResponse(
        access_token=token,
        user={
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name,
            "role": user.role,
        },
    )


@router.get("/me", response_model=dict)
async def me(
    request: Request,
    session: AsyncSession = Depends(session_dependency),
    settings: Settings = Depends(get_settings),
) -> dict:
    user = await _require_user(request, session, settings)
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
    }


async def _require_user(request: Request, session: AsyncSession, settings: Settings) -> User:
    authz = request.headers.get("Authorization", "")
    if not authz.startswith("Bearer "):
        raise HTTPException(
            status_code=401, detail=[{"code": "unauthenticated", "message": "missing bearer token"}]
        )
    try:
        claims = verify_token(
            authz.removeprefix("Bearer "),
            secret=settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
    except TokenInvalid as exc:
        raise HTTPException(
            status_code=401, detail=[{"code": "invalid_token", "message": str(exc)}]
        ) from exc
    result = await session.execute(select(User).where(User.id == claims.sub))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=401,
            detail=[{"code": "unknown_user", "message": "user not found"}],
        )
    return user


async def require_user(
    request: Request, session: AsyncSession = Depends(session_dependency)
) -> User:
    """Dependency other routers use for auth."""
    settings: Settings = request.app.state.settings
    return await _require_user(request, session, settings)
