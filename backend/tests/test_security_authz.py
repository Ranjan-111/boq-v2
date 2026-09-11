"""Round 8 Lead slice — T124 security review: the cross-user authz matrix.

Pinned behavior (route functions + the ownership resolvers against a live
migrated scratch DB, the test_review_actions/test_list_endpoints idiom):

  * THE scale human gate (confirm_scale) is cross-user safe: a stranger to
    the project can neither READ another user's sheet nor CONFIRM scale on
    it — ownership is enforced through the project chain before any write.
  * The gate writes are audited project-scoped (R6 invariant): confirming
    scale appends an audit row whose subject + actor are the confirmer's,
    never the owner's.
  * confirm_scale body validation is a trust surface: nonpositive,
    nonfinite, and over-precise factors are refused (pydantic), and only
    the two human method literals are accepted (a machine method like
    "detected_from_dxf_units" is a schema violation, not a fallback).
  * The malformed-id discipline holds across resolvers: garbage sheet ids
    404 honestly (uuid-guard pre-SELECT), never a 500 asyncpg cast error.
  * No cross-project leakage through _owned_sheet: a second user's own
    sheet is visible to THEM and invisible to the first user, and sheet
    lookup never matches across projects with colliding ids.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.drawings import _owned_drawing
from backend.app.api.sheets import ScaleConfirmBody, _owned_sheet, confirm_scale
from backend.app.db.base import make_async_engine, make_sessionmaker
from backend.app.db.models import (
    AuditEntry,
    DrawingFile,
    DrawingSheet,
    Project,
    ScaleCalibrationModel,
    User,
)

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).resolve().parents[2] / "tests/fixtures/dxf/wall_plan.dxf"


def _http_status(exc: HTTPException) -> int:
    return int(getattr(exc, "code", getattr(exc, "status_code", 500)))


async def _user(session: AsyncSession, email_prefix: str = "u") -> User:
    user = User(id=str(uuid.uuid4()), email=f"{email_prefix}-{uuid.uuid4().hex}@x.io",
                password_hash=uuid.uuid4().hex, display_name="t", role="owner")
    session.add(user)
    await session.flush()
    return user


async def _project(session: AsyncSession, user: User, name: str = "P") -> Project:
    project = Project(id=str(uuid.uuid4()), name=name, region_code="IN",
                      currency="INR", created_by=user.id)
    session.add(project)
    await session.flush()
    return project


async def _sheet(session: AsyncSession, project: Project,
                 sheet_ref: str = "modelspace") -> tuple[DrawingFile, DrawingSheet]:
    """Stored drawing + sheet row (bytes never fetched — the gates here are
    ownership/validation, not parse behavior)."""
    data = FIXTURE.read_bytes()
    drawing = DrawingFile(id=str(uuid.uuid4()), project_id=project.id,
                          filename="wall_plan.dxf", format="dxf",
                          size_bytes=len(data),
                          storage_key=f"uploads/dxf/{uuid.uuid4().hex[:2]}/{uuid.uuid4().hex}",
                          sha256=uuid.uuid4().hex, uploaded_by=project.created_by,
                          parse_status="parsed")
    session.add(drawing)
    await session.flush()
    sheet = DrawingSheet(id=str(uuid.uuid4()), drawing_file_id=drawing.id,
                         page_number=0, title="Model", sheet_ref=sheet_ref,
                         sheet_type="plan")
    session.add(sheet)
    await session.flush()
    return drawing, sheet


class TestScaleGateAuthz:
    """THE human gate against cross-user access (T124 authz matrix)."""

    async def test_stranger_cannot_read_sheet(self, migrated_db: str) -> None:
        engine = make_async_engine(migrated_db)
        session = await make_sessionmaker(engine)().__aenter__()
        try:
            owner = await _user(session, "owner")
            project = await _project(session, owner)
            _, sheet = await _sheet(session, project)
            stranger = await _user(session, "stranger")
            with pytest.raises(HTTPException) as err:
                await _owned_sheet(sheet.id, user=stranger, session=session)
            assert _http_status(err.value) == 404  # never 403 — no existence leak
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()

    async def test_stranger_cannot_confirm_scale(self, migrated_db: str) -> None:
        """The trust-critical row: a stranger's confirm must not write."""
        engine = make_async_engine(migrated_db)
        session = await make_sessionmaker(engine)().__aenter__()
        try:
            owner = await _user(session, "owner")
            project = await _project(session, owner)
            _, sheet = await _sheet(session, project)
            # Owner's calibration proposed; the stranger tries to confirm.
            cal = ScaleCalibrationModel(id=str(uuid.uuid4()), sheet_id=sheet.id,
                                        status="proposed",
                                        method="detected_from_dxf_units")
            session.add(cal)
            await session.flush()
            stranger = await _user(session, "stranger")
            body = ScaleConfirmBody(units_per_drawing_unit=Decimal("1"),
                                     method="user_two_point")
            with pytest.raises(HTTPException) as err:
                await confirm_scale(sheet.id, body, user=stranger, session=session)
            assert _http_status(err.value) == 404
            # The refusal happened before any write: the calibration the
            # owner proposed is untouched (same transaction, in-session row
            # plus a fresh SELECT both show PROPOSED, confirmed_by NULL).
            assert cal.status == "proposed"
            assert cal.confirmed_by is None
            fresh = (await session.execute(
                select(ScaleCalibrationModel)
                .where(ScaleCalibrationModel.sheet_id == sheet.id)
            )).scalar_one()
            assert fresh.status == "proposed"
            assert fresh.confirmed_by is None
            audit_rows = (await session.execute(select(AuditEntry))).scalars().all()
            assert audit_rows == []  # no gate write, no audit trail entry
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()

    async def test_owner_confirm_writes_owner_scoped_audit(
        self, migrated_db: str,
    ) -> None:
        """Confirm by the OWNER is audited with the owner as actor."""
        engine = make_async_engine(migrated_db)
        session = await make_sessionmaker(engine)().__aenter__()
        try:
            owner = await _user(session, "owner")
            project = await _project(session, owner)
            _, sheet = await _sheet(session, project)
            cal = ScaleCalibrationModel(id=str(uuid.uuid4()), sheet_id=sheet.id,
                                        status="proposed",
                                        method="detected_from_dxf_units")
            session.add(cal)
            await session.flush()
            body = ScaleConfirmBody(units_per_drawing_unit=Decimal("1"),
                                     method="user_two_point")
            out = await confirm_scale(sheet.id, body, user=owner, session=session)
            assert out["status"] == "confirmed"
            assert out["confirmed_by"] == owner.id
            rows = (await session.execute(select(AuditEntry))).scalars().all()
            assert len(rows) == 1
            row = rows[0]
            assert str(row.actor) == owner.id
            assert str(row.subject_id) == sheet.id
            assert str(row.project_id) == project.id
            assert row.after is not None and row.after["method"] == "user_two_point"
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()

    async def test_malformed_sheet_id_404s_never_500(
        self, migrated_db: str,
    ) -> None:
        engine = make_async_engine(migrated_db)
        session = await make_sessionmaker(engine)().__aenter__()
        try:
            owner = await _user(session, "owner")
            for bad in ("not-a-uuid", "0", "", "규칙", f"{uuid.uuid4()}x"):
                with pytest.raises(HTTPException) as err:
                    await _owned_sheet(bad, user=owner, session=session)
                assert _http_status(err.value) == 404
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()

    async def test_drawing_resolver_same_guards(self, migrated_db: str) -> None:
        """The drawing resolvers carry the identical malformed-id + ownership
        discipline (the authz matrix applies to every resolver, not just the
        scale gate)."""
        engine = make_async_engine(migrated_db)
        session = await make_sessionmaker(engine)().__aenter__()
        try:
            owner = await _user(session, "owner")
            project = await _project(session, owner)
            drawing, _sheet_row = await _sheet(session, project)
            stranger = await _user(session, "stranger")
            # Owner resolves their drawing; stranger and garbage do not.
            d, p = await _owned_drawing(drawing.id, user=owner, session=session)
            assert str(d.id) == drawing.id
            assert str(p.id) == project.id
            with pytest.raises(HTTPException) as err:
                await _owned_drawing(drawing.id, user=stranger, session=session)
            assert _http_status(err.value) == 404
            for bad in ("not-a-uuid", "0", "", f"{uuid.uuid4()}x"):
                with pytest.raises(HTTPException) as err:
                    await _owned_drawing(bad, user=owner, session=session)
                assert _http_status(err.value) == 404
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()


class TestLoginBoundary:
    """The login endpoint re-SELECTs the user: the pgproto UUID trap.

    Pinned regression (found by the R8 deploy smoke test): the SELECTed
    row's id is an asyncpg pgproto UUID; a dict[str, str] response model
    rejects it and the whole login 500s. Register's in-session str id
    hides the bug, which is why only a real register-then-LOGIN round
    trip catches it.
    """

    async def test_register_then_login_roundtrip(self, migrated_db: str) -> None:
        from fastapi import HTTPException as _HTTPException

        from backend.app.api.auth import LoginBody, login
        from backend.app.auth.tokens import hash_password

        engine = make_async_engine(migrated_db)
        session = await make_sessionmaker(engine)().__aenter__()
        try:
            import uuid as _uuid

            email = f"authz-{_uuid.uuid4().hex}@x.io"
            user = User(id=str(_uuid.uuid4()), email=email,
                        password_hash=hash_password("authz-pass-123"),
                        display_name="t", role="owner")
            session.add(user)
            await session.flush()
            # Detach so login's fresh SELECT exercises the pgproto path.
            session.expunge_all()

            class _Settings:
                jwt_secret = "test-secret-that-is-long-enough"  # noqa: S105
                jwt_algorithm = "HS256"
                access_token_minutes = 30

            body = LoginBody(email=email, password="authz-pass-123")  # noqa: S106
            out = await login(body, request=None, session=session,  # type: ignore[arg-type]
                              settings=_Settings())  # type: ignore[arg-type]
            assert out.access_token
            assert out.user["id"] == user.id  # str, not pgproto UUID
            # Wrong password is an honest 401, never a 500.
            with pytest.raises(_HTTPException) as err:
                await login(LoginBody(email=email, password="wrong-password-x"),  # noqa: S106
                            request=None, session=session,  # type: ignore[arg-type]
                            settings=_Settings())  # type: ignore[arg-type]
            assert _http_status(err.value) == 401
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()


class TestScaleConfirmValidation:
    """The human's factor statement is a trust surface (pydantic-level)."""

    def test_nonpositive_factor_refused(self) -> None:
        for bad in (Decimal("0"), Decimal("-1"), Decimal("-0.0001")):
            with pytest.raises(ValidationError):
                ScaleConfirmBody(units_per_drawing_unit=bad,
                                 method="user_two_point")

    def test_over_precise_factor_refused(self) -> None:
        # 11 decimal places: beyond the documented 10dp scale precision.
        with pytest.raises(ValidationError):
            ScaleConfirmBody(
                units_per_drawing_unit=Decimal("1.12345678901"),
                method="user_two_point")

    def test_machine_method_refused(self) -> None:
        # A machine-detection method can never be claimed through the human
        # gate's schema — confirm is user_two_point/user_known_ratio only.
        # model_validate is used deliberately: passing the invalid literal
        # directly is itself a type error, which is exactly the guarantee.
        with pytest.raises(ValidationError):
            ScaleConfirmBody.model_validate(
                {"units_per_drawing_unit": "1", "method": "detected_from_dxf_units"})
        with pytest.raises(ValidationError):
            ScaleConfirmBody.model_validate(
                {"units_per_drawing_unit": "1", "method": "bar_scale_detected"})

    def test_nan_infinity_refused(self) -> None:
        for bad in (Decimal("nan"), Decimal("inf"), Decimal("-inf")):
            with pytest.raises(ValidationError):
                ScaleConfirmBody(units_per_drawing_unit=bad,
                                 method="user_two_point")


class TestNoCrossProjectLeak:
    """Ownership chains through the project; collisions never leak."""

    async def test_two_users_isolated_projects(self, migrated_db: str) -> None:
        engine = make_async_engine(migrated_db)
        session = await make_sessionmaker(engine)().__aenter__()
        try:
            a = await _user(session, "alice")
            pa = await _project(session, a, "A")
            b = await _user(session, "bob")
            pb = await _project(session, b, "B")
            _, sheet_a = await _sheet(session, pa)
            _, sheet_b = await _sheet(session, pb)
            # Each sees their own; neither sees the other's.
            sa, _ = await _owned_sheet(sheet_a.id, user=a, session=session)
            assert str(sa.id) == sheet_a.id
            sb, _ = await _owned_sheet(sheet_b.id, user=b, session=session)
            assert str(sb.id) == sheet_b.id
            with pytest.raises(HTTPException):
                await _owned_sheet(sheet_b.id, user=a, session=session)
            with pytest.raises(HTTPException):
                await _owned_sheet(sheet_a.id, user=b, session=session)
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()

    async def test_deleted_project_hides_sheet(self, migrated_db: str) -> None:
        engine = make_async_engine(migrated_db)
        session = await make_sessionmaker(engine)().__aenter__()
        try:
            owner = await _user(session, "owner")
            project = await _project(session, owner)
            _, sheet = await _sheet(session, project)
            project.deleted_at = __import__("datetime").datetime.now(
                __import__("datetime").UTC)
            await session.flush()
            with pytest.raises(HTTPException) as err:
                await _owned_sheet(sheet.id, user=owner, session=session)
            assert _http_status(err.value) == 404
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()
