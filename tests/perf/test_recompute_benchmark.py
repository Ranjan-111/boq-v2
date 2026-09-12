"""T125 §G recompute benchmark — 5k-item BOQ recompute < 2s (live PG).

The measured operation is `boq_service.recompute_boq` against a REAL,
migrated, populated scratch PostgreSQL database (never SQLite, never a mock —
docs/testing-strategy.md §5). Setup seeds the honest worst case: 5,000 MAPPED
items each backed by one measurement row carrying an upstream
`corrected_value`, so every item's quantity changes and the recompute does
full work (5k row UPDATEs + the audit row). Timing wraps the recompute call
ONLY; seeding is setup outside the timed window.

The measured number is asserted against §G as written: "BOQ recompute of 5k
items: < 2s". Honesty over green: a miss stays red with the real number.
"""
from __future__ import annotations

import os
import time
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.base import make_async_engine, make_sessionmaker
from backend.app.db.models import (
    BoqItem,
    BoqModel,
    BoqSection,
    CatalogueItem,
    DrawingFile,
    DrawingSheet,
    Element,
    MeasurementModel,
    MeasurementRun,
    Project,
    RateModel,
    User,
)
from backend.app.services import boq_service
from tests.fixtures.generate_bench_fixtures import (
    BOQ_ITEMS,
    boq_measurement_rows,
)

# §G: "BOQ recompute of 5k items: < 2s"
RECOMPUTE_TARGET_SECONDS = 2.0

pytestmark = [
    pytest.mark.perf,
    pytest.mark.skipif(
        os.environ.get("BOQ_PERF", "") != "1",
        reason="BOQ_PERF=1 not set — perf benchmarks are opt-in (CI perf job)",
    ),
]


async def _seed_5k_boq(session: AsyncSession) -> tuple[BoqModel, str, str]:
    """One project/run/BOQ with 5k MAPPED items over 5k corrected measurements.

    Rows are added one-at-a-time with a flush per row — the documented
    asyncpg insertmanyvalues-sentinel trap idiom (backend idiom, see
    run_service._persist_run_output): >1 hand-built row with str ids for
    Uuid columns flushed together raises. Seeding cost is setup, not the
    measured window.
    """
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex}@x.io",
                password_hash=uuid.uuid4().hex, display_name="t", role="owner")
    session.add(user)
    await session.flush()
    project = Project(id=str(uuid.uuid4()), name="perf-bench", region_code="IN",
                      currency="INR", created_by=user.id)
    session.add(project)
    await session.flush()
    run = MeasurementRun(id=str(uuid.uuid4()), project_id=project.id,
                         status="completed", params={})
    session.add(run)
    await session.flush()
    drawing = DrawingFile(id=str(uuid.uuid4()), project_id=project.id,
                          filename="bench.dxf", format="dxf", size_bytes=0,
                          storage_key="bench", sha256="0" * 64,
                          uploaded_by=user.id, parse_status="parsed")
    session.add(drawing)
    await session.flush()
    sheet = DrawingSheet(id=str(uuid.uuid4()), drawing_file_id=drawing.id,
                         page_number=0, sheet_ref="modelspace", title="Model",
                         sheet_type="plan")
    session.add(sheet)
    await session.flush()
    catalogue_item = CatalogueItem(
        id=str(uuid.uuid4()), region_code="IN", code="2.1.1",
        description="Brick wall 230mm thick", unit="m",
        category_path="walls/brick", source="manual")
    session.add(catalogue_item)
    await session.flush()
    session.add(RateModel(id=str(uuid.uuid4()),
                          catalogue_item_id=catalogue_item.id, scope="default",
                          currency="INR", amount_minor=85_000))
    await session.flush()

    measurements = boq_measurement_rows(BOQ_ITEMS)
    # One wall element per measurement (a real run's shape) + the row.
    for spec in measurements:
        element = Element(id=str(uuid.uuid4()), run_id=run.id, sheet_id=sheet.id,
                          element_type="wall", type_source="rule",
                          label=spec["label"])
        session.add(element)
        await session.flush()
        session.add(MeasurementModel(
            id=str(uuid.uuid4()), run_id=run.id, element_id=element.id,
            measurement_id=spec["measurement_id"],
            quantity_type=spec["quantity_type"], value=spec["value"],
            unit=spec["unit"], rule_id=spec["rule_id"],
            engine_version=spec["engine_version"], inputs=[],
            state=spec["state"], label=spec["label"],
            corrected_value=spec["corrected_value"]))
        await session.flush()

    boq = BoqModel(id=str(uuid.uuid4()), project_id=project.id, version=1,
                   status="draft", from_run_id=run.id)
    session.add(boq)
    await session.flush()
    section = BoqSection(id=str(uuid.uuid4()), boq_id=boq.id, code="A",
                         title="Measured works", sort_order=0)
    session.add(section)
    await session.flush()
    # 5k MAPPED items — quantity seeded at the PRE-correction engine value so
    # the timed recompute has a real change to pull on every row.
    for i, spec in enumerate(measurements):
        session.add(BoqItem(
            id=str(uuid.uuid4()), section_id=section.id, origin="mapped",
            catalogue_item_id=catalogue_item.id,
            description=f"Brick wall 230mm thick — {spec['label']}",
            unit="m", quantity=spec["value"], rate_minor=85_000,
            rate_scope="default", markup_bp=0, total_minor=0,
            measurement_ids=[spec["measurement_id"]], sort_order=i))
        await session.flush()
    return boq, str(project.id), str(user.id)


class TestRecompute5k:
    async def test_recompute_5k_items_under_2s(self, migrated_db: str) -> None:
        engine = make_async_engine(migrated_db)
        session = await make_sessionmaker(engine)().__aenter__()
        try:
            boq, project_id, actor = await _seed_5k_boq(session)
            mapped = (await session.execute(
                select(BoqItem).where(BoqItem.origin == "mapped")
            )).scalars().all()
            assert len(mapped) == BOQ_ITEMS

            start = time.perf_counter()
            result = await boq_service.recompute_boq(
                session, project_id=project_id, boq_id=str(boq.id), actor=actor)
            elapsed = time.perf_counter() - start

            # Honest work check: every corrected measurement changed its item,
            # and the diff is the audited before/after shape.
            assert result["changed_items"] == BOQ_ITEMS, (
                f"expected all {BOQ_ITEMS} corrected upstreams to flow through, "
                f"got {result['changed_items']}")
            first = result["diff"][0]
            assert first["before"]["quantity"] == "0.000000"
            assert Decimal(first["after"]["quantity"]) == Decimal("1")
            assert elapsed < RECOMPUTE_TARGET_SECONDS, (
                f"BOQ 5k-item recompute took {elapsed:.2f}s "
                f"(§G target < {RECOMPUTE_TARGET_SECONDS:.0f}s)")

            # Idempotence is free honest work: a second pass with no new
            # corrections is a no-op (and still under target).
            start = time.perf_counter()
            again = await boq_service.recompute_boq(
                session, project_id=project_id, boq_id=str(boq.id), actor=actor)
            steady_state = time.perf_counter() - start
            assert again["changed_items"] == 0
            print(f"\nrecompute 5k: first pass {elapsed:.2f}s, "
                  f"no-change pass {steady_state:.2f}s")
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()
