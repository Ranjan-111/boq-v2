"""Scale confirmation API — THE human gate (docs/api-contract.md rule 3).

Direct route-function calls against a real migrated scratch Postgres:
  * confirm writes CONFIRMED + factor + actor + timestamp, and appends an
    AuditEntry row every time,
  * parse wrote PROPOSED only; no CONFIRMED row exists without this route,
  * re-confirm updates the factor and audits again,
  * factor <= 0 / non-finite / >10dp / unknown method are pydantic-refused,
  * another user's sheet is 404, never 403.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from backend.app.api.drawings import do_upload
from backend.app.api.projects import ProjectCreate, create_project
from backend.app.api.sheets import ScaleConfirmBody, confirm_scale, get_sheet
from backend.app.db.base import make_async_engine, make_sessionmaker
from backend.app.db.models import (
    AuditEntry,
    DrawingSheet,
    ScaleCalibrationModel,
    User,
)
from backend.app.services import parse_service
from backend.app.storage.base import MemoryStorage

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "dxf"


async def _setup(
    migrated_db: str, fixture: str = "wall_plan.dxf"
) -> tuple[AsyncEngine, AsyncSession, User, DrawingSheet, MemoryStorage]:
    """User+project+uploaded drawing+parsed modelspace sheet (+PROPOSED cal)."""
    engine = make_async_engine(migrated_db)
    session = await make_sessionmaker(engine)().__aenter__()
    user = User(
        id=str(uuid.uuid4()),
        email=f"{uuid.uuid4().hex}@example.com",
        password_hash=uuid.uuid4().hex,
        display_name="t",
        role="owner",
    )
    session.add(user)
    await session.flush()
    project = await create_project(
        ProjectCreate(name="P", currency="INR", region_code="IN"), user, session
    )
    storage = MemoryStorage()
    data = (FIXTURES / fixture).read_bytes()
    up = await do_upload(
        session, project=project, user=user, filename=fixture, data=data,
        declared_mime="application/dxf", max_bytes=10 * 1024 * 1024, storage=storage,
    )
    parsed = await parse_service.execute_parse(
        session, drawing_file_id=up["drawing_file_id"], storage=storage
    )
    assert parsed["ok"] is True
    sheet = (
        await session.execute(
            select(DrawingSheet)
            .where(
                DrawingSheet.drawing_file_id == up["drawing_file_id"],
                DrawingSheet.sheet_ref == "modelspace",
            )
        )
    ).scalar_one()
    return engine, session, user, sheet, storage


async def _close(engine: AsyncEngine, session: AsyncSession) -> None:
    await session.close()
    await engine.dispose()


class TestScaleConfirm:
    async def test_confirm_sets_confirmed_and_audits(self, migrated_db: str) -> None:
        engine, session, user, sheet, _storage = await _setup(migrated_db)
        try:
            body = ScaleConfirmBody(
                units_per_drawing_unit=Decimal("0.001"),
                method="user_two_point",
                points=[[0.0, 0.0], [1000.0, 0.0]],
            )
            out = await confirm_scale(sheet.id, body, user, session)
            assert out["status"] == "confirmed"
            assert out["method"] == "user_two_point"
            assert Decimal(out["units_per_drawing_unit"]) == Decimal("0.001")
            assert out["confirmed_by"] == str(user.id)
            assert out["confirmed_at"] is not None

            cal = (
                await session.execute(
                    select(ScaleCalibrationModel).where(
                        ScaleCalibrationModel.sheet_id == sheet.id
                    )
                )
            ).scalar_one()
            assert cal.status == "confirmed"
            assert cal.units_per_drawing_unit == Decimal("0.001")
            assert str(cal.confirmed_by) == str(user.id)

            audit = (
                await session.execute(
                    select(AuditEntry).where(
                        AuditEntry.subject_id == sheet.id,
                        AuditEntry.action == "confirm_scale",
                    )
                )
            ).scalar_one()
            assert str(audit.actor) == str(user.id)
            assert audit.subject_type == "sheet"
            before: dict[str, Any] = audit.before or {}
            after: dict[str, Any] = audit.after or {}
            assert before["status"] == "proposed"
            assert Decimal(before["units_per_drawing_unit"]) == Decimal(1)
            assert after == {
                "status": "confirmed",
                "method": "user_two_point",
                "units_per_drawing_unit": "0.001",
            }
            await session.rollback()
        finally:
            await _close(engine, session)

    async def test_second_confirm_updates_factor_and_audits_each_time(
        self, migrated_db: str
    ) -> None:
        engine, session, user, sheet, _storage = await _setup(migrated_db)
        try:
            first = await confirm_scale(
                sheet.id,
                ScaleConfirmBody(
                    units_per_drawing_unit=Decimal("0.001"), method="user_two_point"
                ),
                user,
                session,
            )
            second = await confirm_scale(
                sheet.id,
                ScaleConfirmBody(
                    units_per_drawing_unit=Decimal("0.01"), method="user_known_ratio"
                ),
                user,
                session,
            )
            assert second["id"] == first["id"]  # same calibration row, updated
            cal = (
                await session.execute(
                    select(ScaleCalibrationModel).where(
                        ScaleCalibrationModel.sheet_id == sheet.id
                    )
                )
            ).scalar_one()
            assert cal.units_per_drawing_unit == Decimal("0.01")
            audit_rows = (
                await session.execute(
                    select(AuditEntry)
                    .where(AuditEntry.subject_id == sheet.id)
                    .order_by(AuditEntry.at)
                )
            ).scalars().all()
            assert len(audit_rows) == 2  # an audit row per confirmation
            await session.rollback()
        finally:
            await _close(engine, session)

    async def test_parse_writes_proposed_only_never_confirmed(
        self, migrated_db: str
    ) -> None:
        engine, session, _user, sheet, _storage = await _setup(migrated_db)
        try:
            cal = (
                await session.execute(
                    select(ScaleCalibrationModel).where(
                        ScaleCalibrationModel.sheet_id == sheet.id
                    )
                )
            ).scalar_one()
            assert cal.status == "proposed"
            assert cal.method == "detected_from_dxf_units"
            confirmed = (
                await session.execute(
                    select(ScaleCalibrationModel).where(
                        ScaleCalibrationModel.status == "confirmed"
                    )
                )
            ).scalars().all()
            assert confirmed == []  # no CONFIRMED row exists without a human
            await session.rollback()
        finally:
            await _close(engine, session)

    async def test_nonpositive_or_imprecise_factors_refused_by_pydantic(self) -> None:
        # Pure pydantic contract — the gate's input validation (no DB needed).
        for bad in [
            {"units_per_drawing_unit": Decimal("0"), "method": "user_two_point"},
            {"units_per_drawing_unit": Decimal("-1"), "method": "user_two_point"},
            {"units_per_drawing_unit": Decimal("NaN"), "method": "user_two_point"},
            {"units_per_drawing_unit": Decimal("Infinity"), "method": "user_two_point"},
            {"units_per_drawing_unit": Decimal("0.00000000001"), "method": "user_two_point"},
            {"units_per_drawing_unit": Decimal("1"), "method": "bar_scale_detected"},
        ]:
            with pytest.raises(ValidationError):
                ScaleConfirmBody(**bad)  # type: ignore[arg-type]

    async def test_sheet_detail_shows_calibration(self, migrated_db: str) -> None:
        engine, session, user, sheet, _storage = await _setup(migrated_db, "no_units.dxf")
        try:
            detail = await get_sheet(sheet.id, user, session)
            assert detail["sheet_ref"] == "modelspace"
            assert detail["is_modelspace"] is True
            assert detail["parse_status"] == "parsed"
            assert detail["calibration"] == {
                "status": "proposed",
                "method": "detected_from_dxf_units",
                "units_per_drawing_unit": None,
                "confirmed_at": None,
            }
            await confirm_scale(
                sheet.id,
                ScaleConfirmBody(
                    units_per_drawing_unit=Decimal("1"), method="user_two_point"
                ),
                user,
                session,
            )
            detail = await get_sheet(sheet.id, user, session)
            assert detail["calibration"]["status"] == "confirmed"
            assert Decimal(detail["calibration"]["units_per_drawing_unit"]) == Decimal(1)
            await session.rollback()
        finally:
            await _close(engine, session)

    async def test_other_user_gets_404_and_nothing_written(
        self, migrated_db: str
    ) -> None:
        engine, session, _user, sheet, _storage = await _setup(migrated_db)
        try:
            stranger = User(
                id=str(uuid.uuid4()),
                email=f"{uuid.uuid4().hex}@example.com",
                password_hash=uuid.uuid4().hex,
                display_name="t",
                role="owner",
            )
            session.add(stranger)
            await session.flush()
            body = ScaleConfirmBody(
                units_per_drawing_unit=Decimal("0.001"), method="user_two_point"
            )
            with pytest.raises(HTTPException) as forbidden:
                await confirm_scale(sheet.id, body, stranger, session)
            assert forbidden.value.status_code == 404
            cal = (
                await session.execute(
                    select(ScaleCalibrationModel).where(
                        ScaleCalibrationModel.sheet_id == sheet.id
                    )
                )
            ).scalar_one()
            assert cal.status == "proposed"  # the gate refused the stranger
            await session.rollback()
        finally:
            await _close(engine, session)

    async def test_confirm_creates_calibration_when_missing(
        self, migrated_db: str
    ) -> None:
        engine, session, user, sheet, _storage = await _setup(migrated_db)
        try:
            cal = (
                await session.execute(
                    select(ScaleCalibrationModel).where(
                        ScaleCalibrationModel.sheet_id == sheet.id
                    )
                )
            ).scalar_one()
            await session.delete(cal)  # e.g. legacy row cleaned up
            await session.flush()
            out = await confirm_scale(
                sheet.id,
                ScaleConfirmBody(
                    units_per_drawing_unit=Decimal("2.5"), method="user_known_ratio"
                ),
                user,
                session,
            )
            assert out["status"] == "confirmed"
            assert Decimal(out["units_per_drawing_unit"]) == Decimal("2.5")
            await session.rollback()
        finally:
            await _close(engine, session)
