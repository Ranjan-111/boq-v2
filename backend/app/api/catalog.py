"""Catalogue API — region-scoped items, rates, fuzzy search.

The catalogue is workspace-level in V1 (items belong to a region_code, not a
project) — require_user only, no project ownership. Rates are integer minor
units; the currency must be one the money kernel supports (two-decimal
currencies, the set boq/assembly and exports validate against) — validated via
Money itself, never a hand-copied list.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from rapidfuzz import fuzz, process
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.auth import require_user
from backend.app.api.scope import problem_error
from backend.app.db.dependencies import session_dependency
from backend.app.db.models import AuditEntry, CatalogueItem, RateModel, User
from core.domain.enums import AuditAction
from core.units.money import Currency, Money

router = APIRouter(prefix="/catalog", tags=["catalog"])

SEARCH_LIMIT = 20
RATE_SCOPES = ("default", "project", "vendor")


def _supported_currency(currency: str) -> bool:
    """The money kernel decides: only currencies it can price (2dp) pass.

    Money() accepts any 3-letter uppercase code, so gate on the set the
    pricing/export kernel enforces — INR/USD/EUR/GBP (boq.assembly and
    exports.csv_export both reject everything else; a rate in any other
    currency could never be used).
    """
    try:
        Money(0, Currency(currency))
    except (TypeError, ValueError):
        return False
    return currency in {"INR", "USD", "EUR", "GBP"}


class CatalogItemCreate(BaseModel):
    region_code: str = Field(min_length=2, max_length=8, pattern="^[A-Za-z0-9-]+$")
    code: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1)
    unit: str = Field(min_length=1, max_length=8)
    category_path: str = Field(min_length=1, max_length=512)
    source_attribution: str | None = Field(default=None, max_length=2048)


class RateBody(BaseModel):
    amount_minor: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    vendor: str | None = Field(default=None, max_length=200)
    valid_from: datetime | None = None

    @field_validator("currency")
    @classmethod
    def _uppercase(cls, v: str) -> str:
        return v.upper()


def _item_json(item: CatalogueItem) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "region_code": item.region_code,
        "code": item.code,
        "description": item.description,
        "unit": item.unit,
        "category_path": item.category_path,
        "source": item.source,
        "source_attribution": item.source_attribution,
    }


def _rate_json(rate: RateModel) -> dict[str, Any]:
    return {
        "id": str(rate.id),
        "catalogue_item_id": str(rate.catalogue_item_id),
        "scope": rate.scope,
        "vendor": rate.vendor,
        "currency": rate.currency,
        "amount_minor": rate.amount_minor,
        "valid_from": rate.valid_from.isoformat() if rate.valid_from else None,
    }


async def _owned_item(
    item_id: str, session: AsyncSession
) -> CatalogueItem:
    item = (
        await session.execute(
            select(CatalogueItem).where(CatalogueItem.id == item_id)
        )
    ).scalar_one_or_none()
    if item is None:
        raise problem_error(404, "not_found", "catalogue item not found")
    return item


@router.get("/search")
async def search_catalog(
    q: str,
    region_code: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    if not q.strip():
        raise problem_error(400, "bad_query", "q must be a non-empty search string")
    region = region_code.upper()
    items = (
        await session.execute(
            select(CatalogueItem).where(CatalogueItem.region_code == region)
        )
    ).scalars().all()
    # V1 catalogue is small (hand-authored hundreds): lexical scoring over the
    # region's full set is exact and cheap. Later rounds add an index.
    matches = process.extract(
        q,
        [f"{item.code} {item.description}" for item in items],
        scorer=fuzz.WRatio,
        limit=SEARCH_LIMIT,
    )
    by_text = {f"{item.code} {item.description}": item for item in items}
    return {
        "items": [
            {**_item_json(by_text[text]), "score": score}
            for text, score, _rank in matches
        ]
    }


@router.get("/items/{item_id}")
async def get_item(
    item_id: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    item = await _owned_item(item_id, session)
    rates = (
        await session.execute(
            select(RateModel)
            .where(RateModel.catalogue_item_id == item.id)
            .order_by(RateModel.scope)
        )
    ).scalars().all()
    return {**_item_json(item), "rates": [_rate_json(r) for r in rates]}


@router.post("/items", status_code=201)
async def create_item(
    body: CatalogItemCreate,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    duplicate = (
        await session.execute(
            select(CatalogueItem).where(
                CatalogueItem.region_code == body.region_code.upper(),
                CatalogueItem.code == body.code,
            )
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise problem_error(
            409,
            "duplicate_item",
            f"catalogue item {body.region_code.upper()}/{body.code} already exists",
        )
    item = CatalogueItem(
        id=str(uuid.uuid4()),
        region_code=body.region_code.upper(),
        code=body.code,
        description=body.description,
        unit=body.unit,
        category_path=body.category_path,
        source="manual",
        source_attribution=body.source_attribution,
    )
    session.add(item)
    await session.flush()
    return _item_json(item)


@router.get("/items/{item_id}/rates")
async def list_rates(
    item_id: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    item = await _owned_item(item_id, session)
    rates = (
        await session.execute(
            select(RateModel)
            .where(RateModel.catalogue_item_id == item.id)
            .order_by(RateModel.scope)
        )
    ).scalars().all()
    return {"items": [_rate_json(r) for r in rates]}


@router.put("/items/{item_id}/rates/{scope}")
async def set_rate(
    item_id: str,
    scope: Literal["default", "project", "vendor"],
    body: RateBody,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    item = await _owned_item(item_id, session)
    if scope not in RATE_SCOPES:
        raise problem_error(400, "bad_scope", f"scope must be one of {RATE_SCOPES}")
    if scope == "vendor" and not (body.vendor or "").strip():
        raise problem_error(400, "vendor_required", "vendor scope requires a vendor")
    if not _supported_currency(body.currency):
        raise problem_error(
            400,
            "unsupported_currency",
            f"currency {body.currency!r} is not a supported two-decimal currency",
        )

    # Upsert: V1 keeps one rate per (item, scope[, vendor]).
    existing = (
        await session.execute(
            select(RateModel).where(
                RateModel.catalogue_item_id == item.id,
                RateModel.scope == scope,
                *(
                    [RateModel.vendor == body.vendor]
                    if scope == "vendor"
                    else [RateModel.vendor.is_(None)]
                ),
            )
        )
    ).scalar_one_or_none()
    before = (
        {
            "amount_minor": existing.amount_minor,
            "currency": existing.currency,
        }
        if existing is not None
        else None
    )
    if existing is not None:
        existing.amount_minor = body.amount_minor
        existing.currency = body.currency
        existing.vendor = body.vendor
        existing.valid_from = body.valid_from
        existing.entered_by = user.id
        rate = existing
    else:
        rate = RateModel(
            id=str(uuid.uuid4()),
            catalogue_item_id=item.id,
            scope=scope,
            vendor=body.vendor,
            currency=body.currency,
            amount_minor=body.amount_minor,
            valid_from=body.valid_from or datetime.now(UTC),
            entered_by=user.id,
        )
        session.add(rate)
    await session.flush()

    session.add(AuditEntry(
        id=str(uuid.uuid4()),
        actor=user.id,
        action=AuditAction.SET_RATE.value,
        subject_type="catalogue_item",
        subject_id=item.id,
        before=before,
        after={
            "scope": scope,
            "amount_minor": body.amount_minor,
            "currency": body.currency,
            "vendor": body.vendor,
        },
    ))
    await session.flush()
    return _rate_json(rate)
