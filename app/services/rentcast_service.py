from __future__ import annotations

import os
import re
from datetime import datetime
from urllib.parse import quote
from typing import Any, Optional

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import EnrichAddressResponse, PropertyFacts, RentSignal, ValueSignal
from ..db.models.rentcast_cache import RentCastCache

_BASE = "https://api.rentcast.io/v1"
_TIMEOUT = 10.0
CACHE_TTL_DAYS = 30

# Fields used to score which property record has the richest data.
_SCORE_FIELDS = ("squareFootage", "bedrooms", "bathrooms", "yearBuilt", "lastSalePrice")


def _normalize_address(address: str) -> str:
    return re.sub(r"\s+", " ", address.strip().lower())


def _get_api_key() -> str:
    key = os.environ.get("RENTCAST_API_KEY", "").strip()
    if not key:
        raise HTTPException(
            status_code=503,
            detail="RENTCAST_API_KEY is not configured on this server.",
        )
    return key


def _handle_response(resp: httpx.Response, label: str) -> Any:
    if resp.status_code == 404:
        return {}
    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail=f"RentCast {label}: invalid API key.")
    if resp.status_code == 403:
        raise HTTPException(status_code=403, detail=f"RentCast {label}: access forbidden.")
    if resp.status_code == 429:
        raise HTTPException(status_code=429, detail=f"RentCast {label}: rate limit exceeded.")
    if resp.status_code >= 500:
        raise HTTPException(status_code=502, detail=f"RentCast {label}: upstream error {resp.status_code}.")
    resp.raise_for_status()
    return resp.json()


def _extract_object(data: Any) -> dict:
    """
    Safely extract a single dict from AVM endpoint responses.

    RentCast shapes:
      - {"value": [{...}], "Count": N}  — unwrap and take first
      - [{...}]                          — bare list, take first
      - {...}                            — flat object (avm/value, avm/rent/long-term)

    Returns {} if data is missing, empty, or unexpected type.
    """
    if isinstance(data, dict):
        inner = data.get("value")
        if isinstance(inner, list):
            return inner[0] if inner else {}
        return data
    if isinstance(data, list):
        return data[0] if data else {}
    return {}


def _score_record(record: dict) -> tuple:
    """Score a property record by data completeness, then square footage."""
    non_null = sum(1 for f in _SCORE_FIELDS if record.get(f) is not None)
    sqft = record.get("squareFootage") or 0
    return (non_null, sqft)


def _select_best_property(data: Any, address: str) -> dict:
    """
    Select the best property record from a /v1/properties response.

    1. Unwrap the value list.
    2. Filter to records whose formattedAddress matches the input address
       (case-insensitive, stripped). Fall back to all records if none match.
    3. Among candidates, prefer the record with the most non-null values
       across squareFootage/bedrooms/bathrooms/yearBuilt/lastSalePrice.
       Ties broken by largest squareFootage, then position.
    """
    if isinstance(data, dict):
        inner = data.get("value")
        records = inner if isinstance(inner, list) else ([data] if data else [])
    elif isinstance(data, list):
        records = data
    else:
        return {}

    if not records:
        return {}

    normalized = address.strip().lower()
    matches = [
        r for r in records
        if isinstance(r, dict)
        and (r.get("formattedAddress") or "").strip().lower() == normalized
    ]
    candidates = matches if matches else [r for r in records if isinstance(r, dict)]

    if not candidates:
        return {}

    return max(candidates, key=_score_record)


def enrich_address(address: str, db: Optional[Session] = None) -> EnrichAddressResponse:
    cache_key = _normalize_address(address)

    # --- Cache read ---
    if db is not None:
        cached = db.query(RentCastCache).filter(RentCastCache.cache_key == cache_key).first()
        if cached is not None:
            age_days = (datetime.utcnow() - cached.cached_at).days
            if age_days < CACHE_TTL_DAYS:
                return EnrichAddressResponse(
                    **cached.payload,
                    from_cache=True,
                    cached_at=cached.cached_at.isoformat(),
                    provider_status="cache_hit",
                )

    # --- Live RentCast call ---
    try:
        api_key = _get_api_key()
        encoded = quote(address)
        headers = {"X-Api-Key": api_key, "Accept": "application/json"}

        with httpx.Client(timeout=_TIMEOUT) as client:
            prop_resp = client.get(f"{_BASE}/properties?address={encoded}", headers=headers)
            value_resp = client.get(f"{_BASE}/avm/value?address={encoded}&compCount=5", headers=headers)
            rent_resp = client.get(f"{_BASE}/avm/rent/long-term?address={encoded}&compCount=5", headers=headers)

        prop = _select_best_property(_handle_response(prop_resp, "properties"), address)
        value = _extract_object(_handle_response(value_resp, "avm/value"))
        rent = _extract_object(_handle_response(rent_resp, "avm/rent/long-term"))

    except HTTPException as exc:
        status = "quota_exhausted" if exc.status_code == 429 else "provider_unavailable"
        return EnrichAddressResponse(
            property_facts=PropertyFacts(),
            value_signal=ValueSignal(),
            rent_signal=RentSignal(),
            from_cache=False,
            provider_status=status,
            provider_error=str(exc.detail),
        )
    except httpx.RequestError as exc:
        return EnrichAddressResponse(
            property_facts=PropertyFacts(),
            value_signal=ValueSignal(),
            rent_signal=RentSignal(),
            from_cache=False,
            provider_status="provider_unavailable",
            provider_error=str(exc),
        )

    property_facts = PropertyFacts(
        formatted_address=prop.get("formattedAddress"),
        property_type=prop.get("propertyType"),
        bedrooms=prop.get("bedrooms"),
        bathrooms=prop.get("bathrooms"),
        square_footage=prop.get("squareFootage"),
        lot_size=prop.get("lotSize"),
        year_built=prop.get("yearBuilt"),
        last_sale_date=prop.get("lastSaleDate"),
        last_sale_price=prop.get("lastSalePrice"),
    )
    value_signal = ValueSignal(
        estimate=value.get("price"),
        low=value.get("priceRangeLow"),
        high=value.get("priceRangeHigh"),
    )
    rent_signal = RentSignal(
        estimate=rent.get("rent"),
        low=rent.get("rentRangeLow"),
        high=rent.get("rentRangeHigh"),
    )

    result = EnrichAddressResponse(
        property_facts=property_facts,
        value_signal=value_signal,
        rent_signal=rent_signal,
        from_cache=False,
        provider_status="live_success",
    )

    # --- Cache write — only on live success, never on failure ---
    if db is not None:
        record = RentCastCache(
            cache_key=cache_key,
            payload=result.model_dump(exclude={"from_cache", "cached_at", "provider_status", "provider_error"}),
            cached_at=datetime.utcnow(),
        )
        db.merge(record)
        db.commit()

    return result
