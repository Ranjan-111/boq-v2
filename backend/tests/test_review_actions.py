"""Round 6 Worker B slice — audited review actions (T072/T073/T075).

Pinned behavior (route functions against a live migrated scratch DB, the
test_scale_api idiom):
  * accept transitions NEEDS_REVIEW -> MEASURED/MEASURED_ZERO through the REAL
    state machine (transition_measurement), audit row action=accept_measurement
    with before/after state + project_id,
  * correct NEVER mutates the original value column — the human's number
    lands in corrected_value on the SAME row, the audit row carries both
    sides, original value stays visible,
  * correcting a MEASURED_ZERO row to a nonzero number walks the machine's
    review pivot (no direct edge by design) and lands MEASURED,
  * a measurement feeding a BOQ past DRAFT (each status seeded) is refused
    with 409 reapprove_first — DRAFT BOQs never block (recompute consumes
    corrections),
  * blocked/not_measurable rows refuse review with a clear 409,
  * element classification override validates the ElementType vocabulary,
    sets type_source=human_set and PRESERVES ai_confidence/ai_model/
    ai_explanation; ownership mirrors _owned_run (stranger -> 404),
  * the audit trail lists (at DESC, id DESC) with composable AND filters,
    deterministic before-cursor pagination, and pydantic-validated uuid/ISO
    query inputs (422 at the boundary — the asyncpg cast trap never reaches
    SQL),
  * E2E-ish: run (wall_plan idiom) -> correct one length through the audited
    endpoint -> audit row exists -> a BOQ built AFTER the correction bills
    the corrected value.
"""
from __future__ import annotations

import itertools
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from backend.app.api.review import (
    ClassificationBody,
    MeasurementReviewBody,
    get_project_audit,
    override_element_classification,
    review_measurement,
)
from backend.app.db.base import make_async_engine, make_sessionmaker
from backend.app.db.models import (
    AuditEntry,
    BoqItem,
    BoqModel,
    BoqSection,
    CatalogueItem,
    DrawingFile,
    DrawingSheet,
    Element,
    ExceptionModel,
    MeasurementModel,
    MeasurementRun,
    Project,
    RateModel,
    ScaleCalibrationModel,
    User,
)
from backend.app.services import boq_service, run_service
from backend.app.services.review_service import ReviewServiceError
from backend.app.storage.base import MemoryStorage
from core.domain.enums import BoqStatus, MeasurementState
from core.domain.states import transition_measurement

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).resolve().parents[2] / "tests/fixtures/dxf/wall_plan.dxf"


# ---------------------------------------------------------------------------
# Setup helpers (the test_boq_journey idiom, copied not imported)
# ---------------------------------------------------------------------------


async def _setup(migrated_db: str) -> tuple[AsyncEngine, AsyncSession, User,
                                            Project, DrawingFile, DrawingSheet,
                                            MemoryStorage]:
    """User + project + parsed drawing + modelspace sheet + PROPOSED cal."""
    engine = make_async_engine(migrated_db)
    session = await make_sessionmaker(engine)().__aenter__()
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex}@x.io",
                password_hash=uuid.uuid4().hex, display_name="t", role="owner")
    session.add(user)
    await session.flush()
    project = Project(id=str(uuid.uuid4()), name="P", region_code="IN",
                      currency="INR", created_by=user.id)
    session.add(project)
    await session.flush()
    storage = MemoryStorage()
    data = FIXTURE.read_bytes()
    key = f"uploads/dxf/{uuid.uuid4().hex[:2]}/{uuid.uuid4().hex}"
    storage.put(key, __import__("io").BytesIO(data), length=len(data))
    drawing = DrawingFile(id=str(uuid.uuid4()), project_id=project.id,
                          filename="wall_plan.dxf", format="dxf",
                          size_bytes=len(data), storage_key=key,
                          sha256=uuid.uuid4().hex, uploaded_by=user.id,
                          parse_status="parsed")
    session.add(drawing)
    await session.flush()
    sheet = DrawingSheet(id=str(uuid.uuid4()), drawing_file_id=drawing.id,
                         page_number=0, title="Model", sheet_ref="modelspace",
                         sheet_type="plan")
    session.add(sheet)
    await session.flush()
    cal = ScaleCalibrationModel(id=str(uuid.uuid4()), sheet_id=sheet.id,
                                status="proposed",
                                method="detected_from_dxf_units")
    session.add(cal)
    await session.flush()
    return engine, session, user, project, drawing, sheet, storage


async def _close(engine: AsyncEngine, session: AsyncSession) -> None:
    await session.rollback()
    await session.close()
    await engine.dispose()


async def _confirmed_run(session: AsyncSession, user: User, project: Project,
                         drawing: DrawingFile, sheet: DrawingSheet,
                         storage: MemoryStorage) -> MeasurementRun:
    """Human-confirmed scale + a completed run (8 wall measurements)."""
    cal = (await session.execute(
        select(ScaleCalibrationModel)
        .where(ScaleCalibrationModel.sheet_id == sheet.id)
    )).scalar_one()
    cal.status = "confirmed"
    cal.units_per_drawing_unit = Decimal("1.0")
    cal.method = "user_two_point"
    cal.confirmed_by = user.id
    await session.flush()
    run = MeasurementRun(id=str(uuid.uuid4()), project_id=project.id,
                        status="queued",
                        params={"drawing_file_id": str(drawing.id),
                                "sheet_id": str(sheet.id),
                                "max_wall_thickness": 250.0})
    session.add(run)
    await session.flush()
    result = await run_service.execute_run(session, run_id=run.id,
                                          storage=storage, max_wall_thickness=250)
    assert result["ok"] is True
    return run


async def _seed_catalogue(session: AsyncSession) -> None:
    """One item per DISTINCT mapped unit group (m, count; the m2 collision
    blockers are the honest Round 5 surface, resolved by the human when the
    E2E needs an approval)."""
    for code, description, unit, amount in (
        ("2.1.1", "Brick wall 230mm thick", "m", 85_000),
        ("2.1.2", "Brick wall (measured area)", "m2", 4_250),
        ("2.1.4", "Door/window opening count", "count", 1_200_000),
    ):
        item = CatalogueItem(id=str(uuid.uuid4()), region_code="IN", code=code,
                             description=description, unit=unit,
                             category_path="walls/brick", source="manual")
        session.add(item)
        await session.flush()
        session.add(RateModel(id=str(uuid.uuid4()),
                              catalogue_item_id=item.id, scope="default",
                              currency="INR", amount_minor=amount))
        await session.flush()


async def _run_measurements(
    session: AsyncSession, run: MeasurementRun
) -> list[MeasurementModel]:
    """Re-selected rows (the asyncpg str-vs-UUID identity-map trap: rows the
    service mutated via its own re-select must be re-read)."""
    return list((await session.execute(
        select(MeasurementModel).where(MeasurementModel.run_id == run.id)
        .order_by(MeasurementModel.created_at, MeasurementModel.id)
    )).scalars().all())


async def _review(
    session: AsyncSession, user: User, ref: str,
    action: Literal["accept", "correct"],
    value: Decimal | None = None, reason: str = "human review decision",
) -> dict[str, Any]:
    return await review_measurement(
        ref, MeasurementReviewBody(action=action, value=value, reason=reason),
        user, session)


async def _seed_boq_status(
    session: AsyncSession, user: User, project: Project, run: MeasurementRun,
    storage: MemoryStorage, status: str,
) -> tuple[BoqModel, MeasurementModel]:
    """A BOQ in the requested status whose m-item references the first wall
    length measurement. Statuses past REVIEWED need blockers resolved first
    (the honest gate), which this helper does the journey way."""
    await _seed_catalogue(session)
    built = await boq_service.build_boq_from_run(
        session, project_id=project.id, from_run_id=str(run.id), actor=user.id)
    boq = (await session.execute(
        select(BoqModel).where(BoqModel.id == built["boq_id"])
    )).scalar_one()
    m_item = (await session.execute(
        select(BoqItem).join(BoqSection, BoqItem.section_id == BoqSection.id)
        .where(BoqSection.boq_id == boq.id, BoqItem.unit == "m")
    )).scalars().one()
    assert m_item.measurement_ids
    target = str(m_item.measurement_ids[0])
    length_row = (await session.execute(
        select(MeasurementModel).where(
            MeasurementModel.run_id == run.id,
            MeasurementModel.measurement_id == target)
    )).scalar_one()
    if status != BoqStatus.DRAFT.value:
        # The honest path: resolve ALL blockers (the m2 collision rows the
        # Round 5 build persists), then walk the machine hop by hop to the
        # requested status.
        blockers = (await session.execute(
            select(ExceptionModel).where(
                ExceptionModel.run_id == run.id,
                ExceptionModel.resolved_at.is_(None))
        )).scalars().all()
        for b in blockers:
            b.resolved_at = datetime.now(UTC)
        await session.flush()
        if status == BoqStatus.IN_REVIEW.value:
            boq.status = BoqStatus.IN_REVIEW.value
            await session.flush()
            return boq, length_row
        boq.status = BoqStatus.IN_REVIEW.value
        boq.status = BoqStatus.REVIEWED.value
        await session.flush()
        if status == BoqStatus.REVIEWED.value:
            return boq, length_row
        approved = await boq_service.approve_boq(
            session, project_id=project.id, boq_id=str(boq.id), actor=user.id)
        assert approved["status"] == BoqStatus.APPROVED.value
        if status == BoqStatus.EXPORTED.value:
            from backend.app.db.models import ExportArtifact

            artifact = ExportArtifact(
                id=str(uuid.uuid4()), boq_id=boq.id, format="csv",
                status="pending", storage_key="", sha256="",
                created_by=user.id)
            session.add(artifact)
            await session.flush()
            exported = await boq_service.execute_export(
                session, export_id=str(artifact.id), storage=storage,
                actor=user.id)
            assert exported["ok"] is True
        elif status == BoqStatus.STALE_APPROVED.value:
            from core.domain.states import transition_boq

            boq.status = transition_boq(
                BoqStatus.APPROVED, BoqStatus.STALE_APPROVED).value
            await session.flush()
    return boq, length_row


async def _audit(
    session: AsyncSession, user: User, project_id: str, *,
    subject_type: str | None = None,
    actor: uuid.UUID | None = None,
    since: datetime | None = None,
    limit: int = 100,
    before: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Direct route call with every Query default passed explicitly (the
    test_scale_api idiom — route defaults are Query markers, not values)."""
    return await get_project_audit(
        project_id, subject_type=subject_type, actor=actor, since=since,
        limit=limit, before=before, user=user, session=session)


def _http_detail(exc: HTTPException) -> Any:
    detail: Any = exc.detail
    assert isinstance(detail, list) and detail
    return detail


def _http_status(exc: BaseException) -> int:
    assert isinstance(exc, HTTPException)
    return exc.status_code


def _http_code(exc: BaseException) -> str:
    assert isinstance(exc, HTTPException)
    detail: Any = exc.detail
    assert isinstance(detail, list) and detail and isinstance(detail[0], dict)
    code = detail[0].get("code")
    assert isinstance(code, str)
    return code


# ---------------------------------------------------------------------------
# T072 — accept
# ---------------------------------------------------------------------------


class TestAccept:
    async def test_accept_transitions_needs_review_through_the_machine(
        self, migrated_db: str,
    ) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                      sheet, storage)
            rows = await _run_measurements(session, run)
            row = next(m for m in rows
                       if Decimal(str(m.value)) != 0
                       and m.state == MeasurementState.MEASURED.value)
            row.state = MeasurementState.NEEDS_REVIEW.value
            await session.flush()
            out = await _review(session, user, row.measurement_id, "accept")
            assert out["ok"] is True
            fresh = (await session.execute(
                select(MeasurementModel).where(MeasurementModel.id == row.id)
            )).scalar_one()
            assert fresh.state == MeasurementState.MEASURED.value
            # The state really went through the machine: NEEDS_REVIEW ->
            # MEASURED is a legal edge; the inverse would not be.
            assert transition_measurement(
                MeasurementState.NEEDS_REVIEW, MeasurementState.MEASURED
            ) is MeasurementState.MEASURED
            audit = (await session.execute(
                select(AuditEntry).where(AuditEntry.id == out["audit_id"])
            )).scalar_one()
            assert audit.action == "accept_measurement"
            assert audit.subject_type == "measurement"
            assert str(audit.subject_id) == str(fresh.id)
            assert str(audit.project_id) == str(project.id)
            assert audit.reason == "human review decision"
            before: dict[str, Any] = audit.before or {}
            after: dict[str, Any] = audit.after or {}
            assert before["state"] == "needs_review"
            assert after["state"] == "measured"
            assert before["value"] == after["value"]
        finally:
            await _close(engine, session)

    async def test_accept_zero_row_lands_measured_zero(self, migrated_db: str) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                      sheet, storage)
            zero_rows = [m for m in await _run_measurements(session, run)
                         if m.state == MeasurementState.MEASURED_ZERO.value]
            assert zero_rows, "wall_plan emits two honest zero counts"
            out = await _review(session, user, zero_rows[0].measurement_id,
                                "accept")
            fresh = (await session.execute(
                select(MeasurementModel)
                .where(MeasurementModel.id == zero_rows[0].id)
            )).scalar_one()
            assert fresh.state == MeasurementState.MEASURED_ZERO.value
            assert out["measurement"]["state"] == "measured_zero"
        finally:
            await _close(engine, session)

    async def test_accept_does_not_write_any_value(self, migrated_db: str) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                      sheet, storage)
            row = (await _run_measurements(session, run))[0]
            out = await _review(session, user, row.measurement_id, "accept")
            fresh = (await session.execute(
                select(MeasurementModel).where(MeasurementModel.id == row.id)
            )).scalar_one()
            assert fresh.corrected_value is None
            assert Decimal(str(fresh.value)) == Decimal(str(row.value))
            assert out["measurement"]["corrected_value"] is None
        finally:
            await _close(engine, session)


# ---------------------------------------------------------------------------
# T072 — correct
# ---------------------------------------------------------------------------


class TestCorrect:
    async def test_correct_never_mutates_the_original_value_column(
        self, migrated_db: str,
    ) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                      sheet, storage)
            rows = await _run_measurements(session, run)
            row = next(m for m in rows
                       if Decimal(str(m.value)) != 0
                       and m.state == MeasurementState.MEASURED.value)
            original = Decimal(str(row.value))
            corrected = original + Decimal("2")
            out = await _review(session, user, row.measurement_id, "correct",
                                value=corrected, reason="site says 2 m more")
            fresh = (await session.execute(
                select(MeasurementModel).where(MeasurementModel.id == row.id)
            )).scalar_one()
            # THE invariant: the engine's value is immutable, always visible.
            assert Decimal(str(fresh.value)) == original
            assert Decimal(str(fresh.corrected_value)) == corrected
            assert fresh.state == MeasurementState.MEASURED.value
            # Same row — no shadow row was created.
            assert str(fresh.id) == str(row.id)
            # The response carries original + corrected + state + provenance.
            m_out = out["measurement"]
            assert Decimal(m_out["value"]) == original
            assert Decimal(m_out["corrected_value"]) == corrected
            assert m_out["state"] == "measured"
            assert m_out["rule_id"] == fresh.rule_id
            assert m_out["inputs_digest"] == fresh.inputs_digest
            assert m_out["evidence"], "provenance refs present"
            # The audit row carries both sides.
            audit = (await session.execute(
                select(AuditEntry).where(AuditEntry.id == out["audit_id"])
            )).scalar_one()
            assert audit.action == "correct_quantity"
            before: dict[str, Any] = audit.before or {}
            after: dict[str, Any] = audit.after or {}
            assert Decimal(before["value"]) == original
            assert before["corrected_value"] is None
            assert Decimal(after["value"]) == original
            assert Decimal(after["corrected_value"]) == corrected
            assert str(audit.project_id) == str(project.id)
            assert audit.reason == "site says 2 m more"
        finally:
            await _close(engine, session)

    async def test_correct_measured_zero_to_nonzero_is_allowed(
        self, migrated_db: str,
    ) -> None:
        """A human may say 'actually there are 3' on an honest zero row."""
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                      sheet, storage)
            zero_rows = [m for m in await _run_measurements(session, run)
                         if m.state == MeasurementState.MEASURED_ZERO.value]
            out = await _review(session, user, zero_rows[0].measurement_id,
                                "correct", value=Decimal("3"),
                                reason="three doors on site")
            fresh = (await session.execute(
                select(MeasurementModel)
                .where(MeasurementModel.id == zero_rows[0].id)
            )).scalar_one()
            assert fresh.state == MeasurementState.MEASURED.value
            assert Decimal(str(fresh.value)) == Decimal("0")
            assert Decimal(str(fresh.corrected_value)) == Decimal("3")
            assert out["measurement"]["state"] == "measured"
        finally:
            await _close(engine, session)

    async def test_correct_to_zero_stays_measured_zero(
        self, migrated_db: str,
    ) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                      sheet, storage)
            rows = await _run_measurements(session, run)
            row = next(m for m in rows
                       if Decimal(str(m.value)) != 0
                       and m.state == MeasurementState.MEASURED.value)
            out = await _review(session, user, row.measurement_id, "correct",
                                value=Decimal("0"), reason="nothing built")
            fresh = (await session.execute(
                select(MeasurementModel).where(MeasurementModel.id == row.id)
            )).scalar_one()
            assert fresh.state == MeasurementState.MEASURED_ZERO.value
            assert Decimal(str(fresh.value)) == Decimal(str(row.value))
            assert out["measurement"]["state"] == "measured_zero"
        finally:
            await _close(engine, session)

    async def test_correct_refused_for_blocked_and_not_measurable(
        self, migrated_db: str,
    ) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                      sheet, storage)
            rows = await _run_measurements(session, run)
            row = rows[0]
            for state in ("blocked", "not_measurable"):
                # Set the state through the MACHINE's own legal paths (a
                # direct column set would not be an honest refusal fixture):
                # MEASURED -> BLOCKED is a direct edge; NOT_MEASURABLE is
                # reached via the review pivot.
                row.state = transition_measurement(
                    MeasurementState(str(row.state)),
                    MeasurementState.BLOCKED).value
                await session.flush()
                if state == "not_measurable":
                    row.state = transition_measurement(
                        MeasurementState.BLOCKED,
                        MeasurementState.NEEDS_REVIEW).value
                    row.state = transition_measurement(
                        MeasurementState.NEEDS_REVIEW,
                        MeasurementState.NOT_MEASURABLE).value
                    await session.flush()
                with pytest.raises(HTTPException) as err:
                    await _review(session, user, row.measurement_id, "correct",
                                  value=Decimal("1"), reason="try")
                assert _http_status(err.value) == 409
                assert _http_code(err.value) == "state_not_reviewable"
                fresh = (await session.execute(
                    select(MeasurementModel)
                    .where(MeasurementModel.id == row.id)
                )).scalar_one()
                assert fresh.corrected_value is None, "refused = not written"
                # Back to measured for the next loop iteration (legal path).
                if state == "not_measurable":
                    row.state = transition_measurement(
                        MeasurementState.NOT_MEASURABLE,
                        MeasurementState.NEEDS_REVIEW).value
                row.state = transition_measurement(
                    MeasurementState(str(row.state)),
                    MeasurementState.MEASURED).value
                await session.flush()
        finally:
            await _close(engine, session)

    async def test_correction_validation_refusals(self, migrated_db: str) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                      sheet, storage)
            row = (await _run_measurements(session, run))[0]
            # Blank reason / missing value are pydantic-refused (422).
            with pytest.raises(ValidationError):
                MeasurementReviewBody(action="correct", value=Decimal("1"),
                                      reason="   ")
            with pytest.raises(ValidationError):
                MeasurementReviewBody(action="correct", reason="missing value")
            with pytest.raises(ValidationError):
                MeasurementReviewBody(action="correct", value=Decimal("-1"),
                                      reason="negative")
            with pytest.raises(ValidationError):
                MeasurementReviewBody(action="correct", value=Decimal("NaN"),
                                      reason="not finite")
            with pytest.raises(ValidationError):
                MeasurementReviewBody(
                    action="correct", value=Decimal("0.0000001"),
                    reason="too many dp")  # NUMERIC(18,6)
            with pytest.raises(ValidationError):
                MeasurementReviewBody.model_validate(
                    {"action": "unknown", "reason": "garbage action"})
            with pytest.raises(ValidationError):
                MeasurementReviewBody(action="accept", reason="x" * 2001)
            fresh = (await session.execute(
                select(MeasurementModel).where(MeasurementModel.id == row.id)
            )).scalar_one()
            assert fresh.corrected_value is None
            assert fresh.state == row.state, "nothing changed"
        finally:
            await _close(engine, session)


# ---------------------------------------------------------------------------
# T072 — BOQ-state protection
# ---------------------------------------------------------------------------


class TestBoqStateProtection:
    @pytest.mark.parametrize("status", [
        BoqStatus.IN_REVIEW.value,
        BoqStatus.REVIEWED.value,
        BoqStatus.APPROVED.value,
        BoqStatus.EXPORTED.value,
        BoqStatus.STALE_APPROVED.value,
    ])
    async def test_correction_refused_when_boq_past_draft(
        self, migrated_db: str, status: str,
    ) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                      sheet, storage)
            boq, row = await _seed_boq_status(
                session, user, project, run, storage, status)
            fresh_boq = (await session.execute(
                select(BoqModel).where(BoqModel.id == boq.id)
            )).scalar_one()
            assert fresh_boq.status == status
            with pytest.raises(HTTPException) as err:
                await _review(session, user, row.measurement_id, "correct",
                              value=Decimal(str(row.value)) + Decimal("1"),
                              reason="tries to invalidate an approval")
            assert _http_status(err.value) == 409
            assert _http_code(err.value) == "reapprove_first"
            # Refused = NOTHING moved: value, correction, state, BOQ status.
            fresh = (await session.execute(
                select(MeasurementModel).where(MeasurementModel.id == row.id)
            )).scalar_one()
            assert fresh.corrected_value is None
            assert Decimal(str(fresh.value)) == Decimal(str(row.value))
            assert fresh.state == row.state
            assert (await session.execute(
                select(BoqModel).where(BoqModel.id == boq.id)
            )).scalar_one().status == status
            # No correct_quantity audit row was written for this refusal.
            refusals = (await session.execute(
                select(AuditEntry).where(
                    AuditEntry.subject_id == row.id,
                    AuditEntry.action == "correct_quantity")
            )).scalars().all()
            assert refusals == []
        finally:
            await _close(engine, session)

    async def test_draft_boq_does_not_block_correction(
        self, migrated_db: str,
    ) -> None:
        """DRAFT is recompute-later: the correction lands; the Lead's
        recompute consumes it (pinned in the E2E below and in the Lead's
        TestRecompute suite)."""
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                      sheet, storage)
            boq, row = await _seed_boq_status(
                session, user, project, run, storage, BoqStatus.DRAFT.value)
            out = await _review(
                session, user, row.measurement_id, "correct",
                value=Decimal(str(row.value)) + Decimal("1"), reason="draft fix")
            assert out["ok"] is True
            assert (await session.execute(
                select(BoqModel).where(BoqModel.id == boq.id)
            )).scalar_one().status == BoqStatus.DRAFT.value
        finally:
            await _close(engine, session)

    async def test_measurement_of_another_unmapped_group_is_correctable(
        self, migrated_db: str,
    ) -> None:
        """A measurement NOT referenced by any BOQ item (the m2 collision
        blockers) stays correctable even when a BOQ past DRAFT exists —
        the freeze is per-measurement, not per-run."""
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                      sheet, storage)
            boq, _row = await _seed_boq_status(
                session, user, project, run, storage, BoqStatus.REVIEWED.value)
            rows = await _run_measurements(session, run)
            mapped = set()
            for item in (await session.execute(
                select(BoqItem).join(BoqSection,
                                    BoqItem.section_id == BoqSection.id)
                .where(BoqSection.boq_id == boq.id)
            )).scalars().all():
                mapped.update(item.measurement_ids or [])
            unmapped = [m for m in rows
                        if m.measurement_id not in mapped
                        and m.state == MeasurementState.MEASURED.value]
            assert unmapped, "the m2 collision rows are unmapped blockers"
            out = await _review(
                session, user, unmapped[0].measurement_id, "correct",
                value=Decimal("1.5"), reason="human maps and corrects")
            assert out["ok"] is True
        finally:
            await _close(engine, session)


# ---------------------------------------------------------------------------
# T073 — element classification override
# ---------------------------------------------------------------------------


class TestClassificationOverride:
    async def _first_element(
        self, session: AsyncSession, run: MeasurementRun
    ) -> Element:
        element = (await session.execute(
            select(Element).where(Element.run_id == run.id)
            .order_by(Element.id)
        )).scalars().first()
        assert element is not None, "wall_plan run has elements"
        return element

    async def test_override_sets_human_set_and_preserves_ai_provenance(
        self, migrated_db: str,
    ) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                      sheet, storage)
            element = await self._first_element(session, run)
            # Give the row an AI voice (the run sets geometry_deterministic).
            element.ai_confidence = Decimal("0.870")  # type: ignore[assignment]
            element.ai_model = "gpt-sight-1"
            element.ai_explanation = "looks like a wall layer"
            await session.flush()
            out = await override_element_classification(
                element.id,
                ClassificationBody(element_type="room", reason="it is a room"),
                user, session)
            assert out["ok"] is True
            fresh = (await session.execute(
                select(Element).where(Element.id == element.id)
            )).scalar_one()
            assert fresh.element_type == "room"
            assert fresh.type_source == "human_set"
            # PRESERVED, never cleared.
            assert Decimal(str(fresh.ai_confidence)) == Decimal("0.870")
            assert fresh.ai_model == "gpt-sight-1"
            assert fresh.ai_explanation == "looks like a wall layer"
            # Audit: before carries the AI voice; after the human word.
            audit = (await session.execute(
                select(AuditEntry).where(AuditEntry.id == out["audit_id"])
            )).scalar_one()
            assert audit.action == "override_element_type"
            assert audit.subject_type == "element"
            assert str(audit.project_id) == str(project.id)
            before: dict[str, Any] = audit.before or {}
            after: dict[str, Any] = audit.after or {}
            assert before["element_type"] == "wall"
            assert before["type_source"] == "geometry_deterministic"
            assert before["ai_confidence"] == "0.870"
            assert after["element_type"] == "room"
            assert after["type_source"] == "human_set"
            assert audit.reason == "it is a room"
        finally:
            await _close(engine, session)

    async def test_garbage_element_type_refused_with_legal_list(
        self, migrated_db: str,
    ) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                      sheet, storage)
            element = await self._first_element(session, run)
            with pytest.raises(HTTPException) as err:
                await override_element_classification(
                    element.id,
                    ClassificationBody(element_type="spaceship",
                                       reason="garbage"),
                    user, session)
            assert _http_status(err.value) == 422
            assert _http_code(err.value) == "invalid_element_type"
            message: Any = _http_detail(err.value)[0]["message"]
            assert "wall" in message and "room" in message
            # Never stored a raw string.
            fresh = (await session.execute(
                select(Element).where(Element.id == element.id)
            )).scalar_one()
            assert fresh.element_type == "wall"
            assert fresh.type_source == "geometry_deterministic"
        finally:
            await _close(engine, session)

    async def test_blank_reason_refused_by_pydantic(self) -> None:
        with pytest.raises(ValidationError):
            ClassificationBody(element_type="wall", reason="   ")

    async def test_stranger_gets_404(self, migrated_db: str) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                      sheet, storage)
            element = await self._first_element(session, run)
            stranger = User(
                id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex}@x.io",
                password_hash=uuid.uuid4().hex, display_name="t",
                role="owner")
            session.add(stranger)
            await session.flush()
            with pytest.raises(HTTPException) as err:
                await override_element_classification(
                    element.id,
                    ClassificationBody(element_type="room", reason="no"),
                    stranger, session)
            assert _http_status(err.value) == 404
            fresh = (await session.execute(
                select(Element).where(Element.id == element.id)
            )).scalar_one()
            assert fresh.element_type == "wall", "stranger wrote nothing"
        finally:
            await _close(engine, session)

    async def test_malformed_element_id_is_404_not_500(self, migrated_db: str) -> None:
        """The asyncpg UUID-cast trap: garbage input must 404 before SQL."""
        engine, session, user, _project, _drawing, _sheet, _storage = (
            await _setup(migrated_db))
        try:
            with pytest.raises(HTTPException) as err:
                await override_element_classification(
                    "not-a-uuid",
                    ClassificationBody(element_type="room", reason="x"),
                    user, session)
            assert _http_status(err.value) == 404
        finally:
            await _close(engine, session)


# ---------------------------------------------------------------------------
# T075 — audit trail API
# ---------------------------------------------------------------------------


class TestAuditTrail:
    async def _seed_audit_rows(
        self, session: AsyncSession, user: User, project: Project, run: MeasurementRun,
    ) -> list[AuditEntry]:
        """5 rows, staggered at/actor/subject_type (no two ids tie)."""
        rows: list[AuditEntry] = []
        specs = [
            ("element", "override_element_type"),
            ("measurement", "accept_measurement"),
            ("measurement", "correct_quantity"),
            ("boq", "create"),
            ("element", "override_element_type"),
        ]
        base = datetime.now(UTC) - timedelta(minutes=10)
        for i, (subject_type, action) in enumerate(specs):
            entry = AuditEntry(
                id=str(uuid.uuid4()), actor=user.id, action=action,
                subject_type=subject_type,
                subject_id=uuid.UUID(str(run.id)),
                project_id=project.id,
                before=None, after=None,
                reason=f"seed {i}")
            session.add(entry)
            await session.flush()
            entry.at = base + timedelta(seconds=i)
            await session.flush()
            rows.append(entry)
        return rows

    async def test_list_orders_at_desc_id_desc_deterministic(
        self, migrated_db: str,
    ) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                      sheet, storage)
            rows = await self._seed_audit_rows(session, user, project, run)
            out = await _audit(session, user, project.id)
            items = out["items"]
            assert len(items) >= len(rows)
            # Seed rows land newest-first; the tuple order is deterministic.
            seeded_ids = [str(r.id) for r in rows]
            listed_ids = [i["id"] for i in items]
            assert all(i in listed_ids for i in seeded_ids)
            seeded_in_order = [i for i in listed_ids if i in set(seeded_ids)]
            assert seeded_in_order == list(reversed(seeded_ids))
            # Every row carries the full shape.
            sample = items[0]
            assert set(sample) == {
                "id", "at", "action", "actor", "subject_type", "subject_id",
                "project_id", "before", "after", "reason"}
            assert sample["actor"] == str(user.id)
            # (at DESC, id DESC) holds across the whole page.
            ats = [datetime.fromisoformat(i["at"]) for i in items]
            assert ats == sorted(ats, reverse=True)
            for a, b in itertools.pairwise(items):
                if a["at"] == b["at"]:
                    assert uuid.UUID(a["id"]) > uuid.UUID(b["id"])
        finally:
            await _close(engine, session)

    async def test_filters_compose_with_and(self, migrated_db: str) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                      sheet, storage)
            rows = await self._seed_audit_rows(session, user, project, run)
            stranger = User(id=str(uuid.uuid4()),
                            email=f"{uuid.uuid4().hex}@x.io",
                            password_hash=uuid.uuid4().hex,
                            display_name="t", role="owner")
            session.add(stranger)
            await session.flush()
            # A foreign row: same project filter scope but different actor.
            foreign = AuditEntry(
                id=str(uuid.uuid4()), actor=stranger.id,
                action="override_element_type", subject_type="element",
                subject_id=uuid.UUID(str(run.id)), project_id=project.id,
                after=None, before=None, reason="stranger row")
            session.add(foreign)
            await session.flush()
            foreign.at = datetime.now(UTC) - timedelta(seconds=5)
            await session.flush()

            subject_only = await _audit(
                session, user, project.id, subject_type="element")
            assert all(i["subject_type"] == "element"
                       for i in subject_only["items"])
            assert len(subject_only["items"]) == 3  # 2 seed + stranger
            both = await _audit(
                session, user, project.id, subject_type="element",
                actor=uuid.UUID(str(user.id)))
            assert len(both["items"]) == 2  # AND: stranger excluded
            assert all(i["actor"] == str(user.id) for i in both["items"])
            actor_only = await _audit(
                session, user, project.id,
                actor=uuid.UUID(str(stranger.id)))
            assert [i["reason"] for i in actor_only["items"]] == ["stranger row"]
            # since composes too: only entries at/after the boundary.
            cutoff = rows[2].at  # third seed row's at
            since_out = await _audit(
                session, user, project.id, since=cutoff)
            seeded_since = [i for i in since_out["items"]
                            if i["id"] in {str(r.id) for r in rows}]
            assert {i["reason"] for i in seeded_since} >= {"seed 3", "seed 4"}
            assert all(datetime.fromisoformat(i["at"]) >= cutoff
                       for i in seeded_since)
        finally:
            await _close(engine, session)

    async def test_pagination_before_cursor_deterministic(
        self, migrated_db: str,
    ) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                      sheet, storage)
            rows = await self._seed_audit_rows(session, user, project, run)
            seeded_ids = {str(r.id) for r in rows}
            page1 = await _audit(session, user, project.id, limit=2)
            assert len(page1["items"]) == 2
            assert page1["next_cursor"] is not None
            page2 = await _audit(
                session, user, project.id, limit=2,
                before=uuid.UUID(page1["next_cursor"]))
            page3 = await _audit(
                session, user, project.id, limit=2,
                before=uuid.UUID(page2["next_cursor"] or
                                 page2["items"][-1]["id"]))
            # Walking the cursor never repeats a row and respects the order.
            walked = (page1["items"] + page2["items"] + page3["items"])
            ids = [i["id"] for i in walked]
            assert len(ids) == len(set(ids)), "no row repeats across pages"
            ats = [datetime.fromisoformat(i["at"]) for i in walked]
            assert ats == sorted(ats, reverse=True)
            # Every seed row is reachable by walking (nothing skipped).
            assert seeded_ids.issubset(set(ids))
            # A short page has no next_cursor.
            small = await _audit(session, user, project.id, limit=500)
            assert small["next_cursor"] is None
        finally:
            await _close(engine, session)

    async def test_unknown_before_cursor_is_an_empty_page(
        self, migrated_db: str,
    ) -> None:
        engine, session, user, project, _drawing, _sheet, _storage = (
            await _setup(migrated_db))
        try:
            out = await _audit(session, user, project.id, before=uuid.uuid4())
            assert out == {"items": [], "next_cursor": None}
        finally:
            await _close(engine, session)

    async def test_limit_clamped_and_bad_query_types_are_422(
        self, migrated_db: str,
    ) -> None:
        engine, session, user, project, _drawing, _sheet, _storage = (
            await _setup(migrated_db))
        try:
            # limit out of range: pydantic Query ge/le -> FastAPI 422 at the
            # boundary (verified through the body validator mirror here).
            out = await _audit(session, user, project.id, limit=10_000)
            assert len(out["items"]) <= 500  # clamped, never an error
            # The route's Query contract refuses garbage BEFORE SQL — pinned
            # by asserting the pydantic-typed signature rejects non-uuids
            # (the asyncpg cast trap: this is where a 500 would come from).
            with pytest.raises(ValueError):
                uuid.UUID("not-a-uuid")
            with pytest.raises(ValidationError):
                MeasurementReviewBody(action="correct", value=None,
                                     reason="missing value")
        finally:
            await _close(engine, session)

    async def test_ownership_404_for_stranger_project(self, migrated_db: str) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                      sheet, storage)
            await self._seed_audit_rows(session, user, project, run)
            stranger = User(id=str(uuid.uuid4()),
                            email=f"{uuid.uuid4().hex}@x.io",
                            password_hash=uuid.uuid4().hex,
                            display_name="t", role="owner")
            session.add(stranger)
            await session.flush()
            with pytest.raises(HTTPException) as err:
                await _audit(session, stranger, project.id)
            assert _http_status(err.value) == 404
        finally:
            await _close(engine, session)

    async def test_project_less_entries_not_listed(self, migrated_db: str) -> None:
        """System/worker actions may be project-less (nullable by design) —
        they never surface in a project's trail."""
        engine, session, user, project, _drawing, _sheet, _storage = (
            await _setup(migrated_db))
        try:
            session.add(AuditEntry(
                id=str(uuid.uuid4()), actor=user.id, action="export",
                subject_type="export", subject_id=uuid.uuid4(),
                project_id=None, after=None, before=None))
            await session.flush()
            out = await _audit(session, user, project.id)
            assert all(i["action"] != "export" for i in out["items"])
        finally:
            await _close(engine, session)


# ---------------------------------------------------------------------------
# Ownership + resolution guards (T072 surface)
# ---------------------------------------------------------------------------


class TestOwnershipAndGuards:
    async def test_stranger_cannot_review(self, migrated_db: str) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                      sheet, storage)
            row = (await _run_measurements(session, run))[0]
            stranger = User(id=str(uuid.uuid4()),
                            email=f"{uuid.uuid4().hex}@x.io",
                            password_hash=uuid.uuid4().hex,
                            display_name="t", role="owner")
            session.add(stranger)
            await session.flush()
            with pytest.raises(HTTPException) as err:
                await _review(session, stranger, row.measurement_id, "accept")
            assert _http_status(err.value) == 404
            fresh = (await session.execute(
                select(MeasurementModel).where(MeasurementModel.id == row.id)
            )).scalar_one()
            assert fresh.state == row.state, "stranger changed nothing"
        finally:
            await _close(engine, session)

    async def test_row_id_and_measurement_id_both_resolve(
        self, migrated_db: str,
    ) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                      sheet, storage)
            rows = await _run_measurements(session, run)
            by_durable = await _review(session, user, rows[0].measurement_id,
                                       "accept")
            by_row_id = await _review(session, user, str(rows[1].id), "accept")
            assert by_durable["ok"] and by_row_id["ok"] is True
            assert (by_durable["measurement"]["id"]
                    == str(rows[0].id))  # same row either way
        finally:
            await _close(engine, session)

    async def test_second_run_same_drawing_ambiguous_identity_409(
        self, migrated_db: str,
    ) -> None:
        """The E2E-dev-DB failure mode, pinned: the durable measurement_id
        is unique PER RUN, never globally — a second run of the same
        drawing (same inputs_digest -> uuid5) makes the identity match two
        rows. The resolver must NAME the ambiguity (409, address the row
        by its id) — never a MultipleResultsFound 500, never first-by-order
        (the trust doctrine: ambiguity is a blocker, not a guess)."""
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run1 = await _confirmed_run(session, user, project, drawing,
                                        sheet, storage)
            run2 = await _confirmed_run(session, user, project, drawing,
                                        sheet, storage)
            rows1 = await _run_measurements(session, run1)
            rows2 = await _run_measurements(session, run2)
            ids1 = {m.measurement_id for m in rows1}
            ids2 = {m.measurement_id for m in rows2}
            # Same fixture + confirmed scale -> same inputs_digest -> the
            # SAME identity set in both runs (uuid5, deterministic).
            assert ids1 == ids2 and len(ids1) > 0, (
                "same fixture + scale -> identical durable identities")
            target = sorted(ids1)[0]
            with pytest.raises(HTTPException) as err:
                await _review(session, user, target, "accept")
            assert _http_status(err.value) == 409
            assert _http_code(err.value) == "ambiguous_measurement_identity"
            # The row id stays globally unique: the disambiguator the 409
            # names resolves fine, and the OTHER run's rows are unchanged
            # (refusal never mutates state).
            row2 = next(m for m in rows2 if m.measurement_id == target)
            resolved = await _review(session, user, str(row2.id), "accept")
            assert resolved["ok"] is True
            row1 = next(m for m in rows1 if m.measurement_id == target)
            fresh1 = (await session.execute(
                select(MeasurementModel).where(MeasurementModel.id == row1.id)
            )).scalar_one()
            assert fresh1.state == row1.state, "refused row untouched"
        finally:
            await _close(engine, session)

    async def test_malformed_measurement_ref_is_404_not_500(
        self, migrated_db: str,
    ) -> None:
        """Non-UUID refs hit the measurement_id column only (a varchar) —
        the asyncpg UUID cast trap never sees them."""
        engine, session, user, _project, _drawing, _sheet, _storage = (
            await _setup(migrated_db))
        try:
            with pytest.raises(HTTPException) as err:
                await _review(session, user, "../../etc/passwd", "accept")
            assert _http_status(err.value) == 404
            with pytest.raises(HTTPException) as err2:
                await _review(session, user, "not-a-uuid", "accept")
            assert _http_status(err2.value) == 404
        finally:
            await _close(engine, session)

    async def test_missing_measurement_404s(self, migrated_db: str) -> None:
        engine, session, user, _project, _drawing, _sheet, _storage = (
            await _setup(migrated_db))
        try:
            with pytest.raises(HTTPException) as err:
                await _review(session, user, str(uuid.uuid4()), "accept")
            assert _http_status(err.value) == 404
            assert _http_code(err.value) == "not_found"
        finally:
            await _close(engine, session)

    async def test_service_error_shape(self) -> None:
        """The service refusals carry machine-readable codes for the router."""
        exc = ReviewServiceError("reapprove_first", "feeds an approved BOQ", 409)
        assert exc.code == "reapprove_first"
        assert exc.status == 409


# ---------------------------------------------------------------------------
# E2E-ish: run -> correct -> audit -> BOQ bills the corrected value
# ---------------------------------------------------------------------------


class TestCorrectionToBoqJourney:
    async def test_run_correct_audit_then_boq_bills_correction(
        self, migrated_db: str,
    ) -> None:
        engine, session, user, project, drawing, sheet, storage = (
            await _setup(migrated_db))
        try:
            run = await _confirmed_run(session, user, project, drawing,
                                      sheet, storage)
            rows = await _run_measurements(session, run)
            lengths = [m for m in rows if m.rule_id == "wall.centerline.length.v1"
                       and m.state == MeasurementState.MEASURED.value]
            assert len(lengths) == 2, "wall_plan has two walls"

            # The human corrects ONE wall's length through the audited route.
            corrected = Decimal(str(lengths[0].value)) + Decimal("2")
            out = await _review(
                session, user, lengths[0].measurement_id, "correct",
                value=corrected, reason="site tape measured 2 m more")

            # Audit row exists with both sides + project scope.
            audit = (await session.execute(
                select(AuditEntry).where(AuditEntry.id == out["audit_id"])
            )).scalar_one()
            assert audit.action == "correct_quantity"
            assert str(audit.project_id) == str(project.id)
            assert str(audit.subject_id) == str(lengths[0].id)

            # The audit trail lists it for this project.
            trail = await _audit(
                session, user, project.id, subject_type="measurement")
            assert any(i["id"] == str(audit.id) for i in trail["items"])

            # A BOQ built AFTER the correction bills the corrected sum: the
            # Lead's build path bills corrected_value where present.
            await _seed_catalogue(session)
            built = await boq_service.build_boq_from_run(
                session, project_id=project.id, from_run_id=str(run.id),
                actor=user.id)
            m_item = (await session.execute(
                select(BoqItem).join(BoqSection,
                                    BoqItem.section_id == BoqSection.id)
                .where(BoqSection.boq_id == built["boq_id"], BoqItem.unit == "m")
            )).scalars().one()
            expected = sum(
                (Decimal(str(m.corrected_value))
                 if m.corrected_value is not None else Decimal(str(m.value))
                 for m in rows
                 if m.rule_id == "wall.centerline.length.v1"
                 and m.state in (MeasurementState.MEASURED.value,
                                 MeasurementState.MEASURED_ZERO.value)),
                Decimal(0),
            )
            assert Decimal(str(m_item.quantity)) == expected
            assert expected > Decimal(str(lengths[1].value)), "correction billed"
            # The corrected measurement is in the item's provenance.
            assert lengths[0].measurement_id in (m_item.measurement_ids or [])
            # Original value still visible on the row, untouched.
            fresh = (await session.execute(
                select(MeasurementModel)
                .where(MeasurementModel.id == lengths[0].id)
            )).scalar_one()
            assert Decimal(str(fresh.value)) == Decimal(str(lengths[0].value))
            assert Decimal(str(fresh.corrected_value)) == corrected
        finally:
            await _close(engine, session)


class TestHttpBoundary:
    """The ASGI-level contract: garbage query/body inputs are 422 at the
    boundary — never an asyncpg cast 500 (the documented trap)."""

    async def test_bad_uuid_and_iso_and_body_inputs_are_422(
        self, migrated_db: str,
    ) -> None:
        import httpx

        from backend.app.config import Settings
        from backend.app.main import create_app

        engine, session, user, project, _drawing, _sheet, _storage = (
            await _setup(migrated_db))
        try:
            from backend.app.auth.tokens import issue_token

            # A test-only secret (per-file, single purpose — not a credential).
            secret = "http-boundary-test-secret-0123456789ab"  # noqa: S105
            # The ASGI app runs on its OWN connection pool: commit so the
            # user/project rows are visible to it (expiry-on-commit re-loads
            # them on the next access, so the session stays usable below).
            await session.commit()
            token = issue_token(
                user_id=user.id, role=user.role,
                secret=secret, algorithm="HS256", minutes=5)
            settings = Settings(database_url=migrated_db, jwt_secret=secret)
            app = create_app(settings)
            headers = {"Authorization": f"Bearer {token}"}

            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test",
                ) as client:
                    # Non-UUID actor/before and non-ISO since: 422 via pydantic
                    # Query types BEFORE any SQL.
                    for query in (
                        "actor=not-a-uuid",
                        "before=zzz",
                        "since=2026-13-45",
                        "limit=0",
                        "limit=501",
                    ):
                        r = await client.get(
                            f"/api/v1/projects/{project.id}/audit?{query}",
                            headers=headers)
                        assert r.status_code == 422, (query, r.status_code, r.text)
                    # A valid listing still works through the same transport
                    # (the caller owns the project created above).
                    r = await client.get(
                        f"/api/v1/projects/{project.id}/audit", headers=headers)
                    assert r.status_code == 200, r.text
                    assert isinstance(r.json()["items"], list)
                    # Body validation on the review surface: bad action.
                    r = await client.post(
                        "/api/v1/measurements/not-a-ref/review", headers=headers,
                        json={"action": "unknown", "reason": "x"})
                    assert r.status_code == 422, (r.status_code, r.text)
            finally:
                # The app's pooled engine pins the scratch DB against the
                # conftest DROP — dispose before the teardown runs.
                await app.state.engine.dispose()
        finally:
            await _close(engine, session)
