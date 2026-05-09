from __future__ import annotations

import os
from urllib.parse import quote
from typing import Any

import httpx
from fastapi import HTTPException

from ..models import EnrichAddressResponse, PropertyFacts, RentSignal, ValueSignal

_BASE = "https://api.rentcast.io/v1"
_TIMEOUT = 10.0


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
    Safely extract a single property dict regardless of response shape.

    RentCast shapes observed in the wild:
      - {"value": [{...}], "Count": 1}  ← /v1/properties
      - [{...}]                          ← bare list
      - {...}                            ← flat object ← /v1/avm/value, /v1/avm/rent/long-term

    Returns an empty dict if the data is missing, empty, or an unexpected type.
    """
    if isinstance(data, dict):
        inner = data.get("value")
        if isinstance(inner, list):
            return inner[0] if inner else {}
        # Flat dict — use as-is (avm/value and avm/rent/long-term shapes)
        return data
    if isinstance(data, list):
        return data[0] if data else {}
    return {}


def enrich_address(address: str) -> EnrichAddressResponse:
    api_key = _get_api_key()
    encoded = quote(address)
    headers = {"X-Api-Key": api_key, "Accept": "application/json"}

    with httpx.Client(timeout=_TIMEOUT) as client:
        prop_resp = client.get(f"{_BASE}/properties?address={encoded}", headers=headers)
        value_resp = client.get(f"{_BASE}/avm/value?address={encoded}&compCount=5", headers=headers)
        rent_resp = client.get(f"{_BASE}/avm/rent/long-term?address={encoded}&compCount=5", headers=headers)

    prop = _extract_object(_handle_response(prop_resp, "properties"))
    value = _extract_object(_handle_response(value_resp, "avm/value"))
    rent = _extract_object(_handle_response(rent_resp, "avm/rent/long-term"))

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

    return EnrichAddressResponse(
        property_facts=property_facts,
        value_signal=value_signal,
        rent_signal=rent_signal,
    )
