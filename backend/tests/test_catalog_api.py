"""Catalogue API — items, rates, fuzzy search (direct route-function calls).

  * manual item creation sets source="manual"; (region, code) duplicates 409,
  * PUT rate upserts one rate per (item, scope) and appends a set_rate audit,
  * vendor scope without a vendor, and currencies the money kernel cannot
    price, are refused with 400,
  * search ranks by lexical similarity within a region (case-insensitive),
    empty q is a 400.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from backend.app.api.catalog import (
    CatalogItemCreate,
    RateBody,
    create_item,
    get_item,
    list_rates,
    search_catalog,
    set_rate,
)
from backend.app.db.base import make_async_engine, make_sessionmaker
from backend.app.db.models import AuditEntry, CatalogueItem, RateModel, User

pytestmark = pytest.mark.integration


async def _make_user(migrated_db: str) -> tuple[AsyncEngine, AsyncSession, User]:
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
    return engine, session, user


async def _close(engine: AsyncEngine, session: AsyncSession) -> None:
    await session.rollback()
    await session.close()
    await engine.dispose()


def _brick_body(code: str = "2.1.1") -> CatalogItemCreate:
    return CatalogItemCreate(
        region_code="IN",
        code=code,
        description="Brick wall 230mm thick",
        unit="m",
        category_path="walls/brick",
    )


class TestCatalogItems:
    async def test_create_manual_item(self, migrated_db: str) -> None:
        engine, session, user = await _make_user(migrated_db)
        try:
            item = await create_item(_brick_body(), user, session)
            assert item["region_code"] == "IN"
            assert item["code"] == "2.1.1"
            assert item["source"] == "manual"
            row = (
                await session.execute(
                    select(CatalogueItem).where(CatalogueItem.id == item["id"])
                )
            ).scalar_one()
            assert row.source == "manual"
            await _close(engine, session)
        except BaseException:
            await _close(engine, session)
            raise

    async def test_duplicate_region_code_409(self, migrated_db: str) -> None:
        engine, session, user = await _make_user(migrated_db)
        try:
            await create_item(_brick_body(), user, session)
            with pytest.raises(HTTPException) as exc:
                await create_item(_brick_body(), user, session)
            assert exc.value.status_code == 409
            assert exc.value.detail[0]["code"] == "duplicate_item"  # type: ignore[index]
            # same code in another region is a different item
            other_region = _brick_body()
            other_region.region_code = "US"
            created = await create_item(other_region, user, session)
            assert created["region_code"] == "US"
            await _close(engine, session)
        except BaseException:
            await _close(engine, session)
            raise

    async def test_region_code_normalized_uppercase(self, migrated_db: str) -> None:
        engine, session, user = await _make_user(migrated_db)
        try:
            body = _brick_body()
            body.region_code = "in"
            item = await create_item(body, user, session)
            assert item["region_code"] == "IN"
            await _close(engine, session)
        except BaseException:
            await _close(engine, session)
            raise


class TestRates:
    async def test_set_default_rate_upserts_and_audits(self, migrated_db: str) -> None:
        engine, session, user = await _make_user(migrated_db)
        try:
            item = await create_item(_brick_body(), user, session)
            first = await set_rate(
                item["id"], "default",
                RateBody(amount_minor=85_000, currency="INR"), user, session,
            )
            assert first["scope"] == "default"
            assert first["amount_minor"] == 85_000
            # upsert: same (item, scope) replaces, not duplicates
            await set_rate(
                item["id"], "default",
                RateBody(amount_minor=90_000, currency="INR"), user, session,
            )
            rates = (
                await session.execute(
                    select(RateModel).where(RateModel.catalogue_item_id == item["id"])
                )
            ).scalars().all()
            assert len(rates) == 1
            assert rates[0].amount_minor == 90_000
            assert str(rates[0].entered_by) == str(user.id)
            audits = (
                await session.execute(
                    select(AuditEntry).where(
                        AuditEntry.subject_id == item["id"],
                        AuditEntry.action == "set_rate",
                    )
                )
            ).scalars().all()
            assert len(audits) == 2  # one audit row per PUT
            detail = await get_item(item["id"], user, session)
            assert [r["amount_minor"] for r in detail["rates"]] == [90_000]
            await _close(engine, session)
        except BaseException:
            await _close(engine, session)
            raise

    async def test_vendor_scope_requires_vendor(self, migrated_db: str) -> None:
        engine, session, user = await _make_user(migrated_db)
        try:
            item = await create_item(_brick_body(), user, session)
            with pytest.raises(HTTPException) as exc:
                await set_rate(
                    item["id"], "vendor",
                    RateBody(amount_minor=1, currency="INR"), user, session,
                )
            assert exc.value.status_code == 400
            assert exc.value.detail[0]["code"] == "vendor_required"  # type: ignore[index]
            ok = await set_rate(
                item["id"], "vendor",
                RateBody(amount_minor=1, currency="INR", vendor="Acme Bricks"),
                user, session,
            )
            assert ok["vendor"] == "Acme Bricks"
            await _close(engine, session)
        except BaseException:
            await _close(engine, session)
            raise

    async def test_unsupported_currency_refused(self, migrated_db: str) -> None:
        engine, session, user = await _make_user(migrated_db)
        try:
            item = await create_item(_brick_body(), user, session)
            # CHF is a valid ISO code but not one the money kernel prices
            # (two-decimal currencies only) — refuse at the boundary.
            with pytest.raises(HTTPException) as exc:
                await set_rate(
                    item["id"], "default",
                    RateBody(amount_minor=1, currency="CHF"), user, session,
                )
            assert exc.value.status_code == 400
            assert exc.value.detail[0]["code"] == "unsupported_currency"  # type: ignore[index]
            await _close(engine, session)
        except BaseException:
            await _close(engine, session)
            raise

    async def test_negative_amount_refused_by_pydantic(self) -> None:
        with pytest.raises(ValidationError):
            RateBody(amount_minor=-1, currency="INR")

    async def test_list_rates_and_missing_item_404(self, migrated_db: str) -> None:
        engine, session, user = await _make_user(migrated_db)
        try:
            with pytest.raises(HTTPException) as exc:
                await list_rates(str(uuid.uuid4()), user, session)
            assert exc.value.status_code == 404
            item = await create_item(_brick_body(), user, session)
            await set_rate(
                item["id"], "default",
                RateBody(amount_minor=85_000, currency="INR"), user, session,
            )
            rates = await list_rates(item["id"], user, session)
            assert [r["scope"] for r in rates["items"]] == ["default"]
            await _close(engine, session)
        except BaseException:
            await _close(engine, session)
            raise


class TestSearch:
    async def test_search_ranks_by_query_within_region(self, migrated_db: str) -> None:
        engine, session, user = await _make_user(migrated_db)
        try:
            await create_item(_brick_body("2.1.1"), user, session)
            plaster = CatalogItemCreate(
                region_code="IN", code="3.4.2", description="Cement plaster 12mm",
                unit="m2", category_path="finishes/plaster",
            )
            await create_item(plaster, user, session)
            us_only = CatalogItemCreate(
                region_code="US", code="9.9.9", description="Brick wall 230mm thick",
                unit="m", category_path="walls/brick",
            )
            await create_item(us_only, user, session)

            out = await search_catalog("brick wall", "IN", user, session)
            hits = out["items"]
            assert hits
            assert hits[0]["code"] == "2.1.1"  # best match ranks first
            assert all(i["region_code"] == "IN" for i in hits)  # region filter
            assert all(i["score"] > 0 for i in hits)
            # the US twin never leaks into the IN region's results
            assert all(i["code"] != "9.9.9" for i in hits)

            # region_code matches case-insensitively
            out_lower = await search_catalog("brick wall", "in", user, session)
            assert out_lower["items"][0]["code"] == "2.1.1"
            assert all(i["region_code"] == "IN" for i in out_lower["items"])
            await _close(engine, session)
        except BaseException:
            await _close(engine, session)
            raise

    async def test_search_empty_query_is_400(self, migrated_db: str) -> None:
        engine, session, user = await _make_user(migrated_db)
        try:
            with pytest.raises(HTTPException) as exc:
                await search_catalog("  ", "IN", user, session)
            assert exc.value.status_code == 400
            assert exc.value.detail[0]["code"] == "bad_query"  # type: ignore[index]
            await _close(engine, session)
        except BaseException:
            await _close(engine, session)
            raise
