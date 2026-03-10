"""
URL extraction service for FlipForge.

Strategy:
  1. Fetch the page with a browser-like User-Agent via httpx.
  2. Parse OG meta tags + JSON-LD structured data via BeautifulSoup.
  3. Fall back to regex pattern matching on visible text.
  4. If the page blocks us (403/429) or anything fails, return a SOURCE_BLOCKED
     draft so the UI can show the manual-fill prompt immediately.

Only purchase_price can realistically come from a listing page.
ARV and rehab_budget are always MISSING — the investor must fill those.
"""

from __future__ import annotations

import json
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from ..models import DataPoint, DraftDeal

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

_PRICE_RE = re.compile(r"\$\s?([\d,]+(?:\.\d{2})?)")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_price(raw: str) -> Optional[float]:
    """Strip $, commas, spaces and convert to float. Returns None on failure."""
    try:
        cleaned = re.sub(r"[^\d.]", "", raw.replace(",", ""))
        val = float(cleaned)
        # Sanity: reject values that are obviously not property prices
        if val < 1_000 or val > 50_000_000:
            return None
        return val
    except (ValueError, TypeError):
        return None


def _extract_og_price(soup: BeautifulSoup) -> Optional[float]:
    """Try OG price tags (some listing aggregators include these)."""
    for prop in ("og:price:amount", "product:price:amount"):
        tag = soup.find("meta", property=prop)
        if tag and tag.get("content"):
            val = _parse_price(str(tag["content"]))
            if val:
                return val
    return None


def _extract_og_title(soup: BeautifulSoup) -> Optional[str]:
    tag = soup.find("meta", property="og:title")
    if tag and tag.get("content"):
        return str(tag["content"]).strip()
    return None


def _extract_json_ld_price(soup: BeautifulSoup) -> Optional[float]:
    """
    Many listing sites embed JSON-LD (schema.org/RealEstateListing or Product).
    Try to pull a price from there.
    """
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            # Handle single object or array of objects
            items = data if isinstance(data, list) else [data]
            for item in items:
                # schema.org price field
                price_raw = item.get("price") or item.get("offers", {}).get("price")
                if price_raw:
                    val = _parse_price(str(price_raw))
                    if val:
                        return val
        except (json.JSONDecodeError, AttributeError, TypeError):
            continue
    return None


def _extract_text_price(soup: BeautifulSoup) -> Optional[float]:
    """
    Last resort: scan visible text for the first large dollar amount.
    Most listing pages show the price prominently near the top.
    """
    # Limit search to the first 5000 chars of body text to stay fast
    body = soup.get_text(separator=" ", strip=True)[:5_000]
    matches = _PRICE_RE.findall(body)
    for raw in matches:
        val = _parse_price(raw)
        if val and val >= 10_000:  # filter out small incidental dollar amounts
            return val
    return None


def _extract_address(soup: BeautifulSoup, url: str) -> Optional[str]:
    """Try OG title, then page <title>. Returns None if nothing useful found."""
    address = _extract_og_title(soup)
    if address:
        return address

    title_tag = soup.find("title")
    if title_tag and title_tag.string:
        return title_tag.string.strip()

    return None


def _blocked_draft(url: str) -> DraftDeal:
    return DraftDeal(
        source="SOURCE_BLOCKED",
        url=url,
        notes=[
            "Site blocked extraction (403/429). Common for Zillow and Redfin.",
            "Fill Purchase Price, ARV, and Rehab Budget manually to proceed.",
        ],
        signals=["source_blocked"],
    )


def _error_draft(url: str, reason: str) -> DraftDeal:
    return DraftDeal(
        source="EXTRACTION_ERROR",
        url=url,
        notes=[
            f"Could not fetch listing: {reason}",
            "Fill fields manually to proceed.",
        ],
        signals=["extraction_error"],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def draft_from_url(url: str) -> DraftDeal:
    """
    Fetch a listing URL and extract as many deal fields as possible.
    Always returns a DraftDeal — never raises.
    Callers should check draft.source for "SOURCE_BLOCKED" / "EXTRACTION_ERROR".
    """
    try:
        resp = httpx.get(url, headers=_HEADERS, follow_redirects=True, timeout=10.0)
    except httpx.TimeoutException:
        return _error_draft(url, "request timed out")
    except httpx.RequestError as exc:
        return _error_draft(url, str(exc))

    if resp.status_code in (403, 429, 401):
        return _blocked_draft(url)

    if not resp.is_success:
        return _error_draft(url, f"HTTP {resp.status_code}")

    soup = BeautifulSoup(resp.text, "html.parser")

    # --- Price extraction (priority order) ---
    price: Optional[float] = (
        _extract_og_price(soup)
        or _extract_json_ld_price(soup)
        or _extract_text_price(soup)
    )

    # --- Address / title ---
    address = _extract_address(soup, url)

    # --- Build DataPoints ---
    if price is not None:
        purchase_price_dp = DataPoint(
            value=price,
            confidence="MEDIUM",   # scraped, not verified
            source="listing_page",
            evidence="Extracted from listing page price field",
        )
        signals = ["price_extracted"]
        notes = [
            "Listing price used as purchase price starting point — verify before analyzing.",
            "ARV and Rehab Budget must be estimated by the investor.",
        ]
        source = "opengraph" if _extract_og_price(soup) else "text_extraction"
    else:
        purchase_price_dp = DataPoint(confidence="MISSING")
        signals = ["price_not_found"]
        notes = [
            "Could not extract price from listing page.",
            "Fill Purchase Price, ARV, and Rehab Budget manually.",
        ]
        source = "partial"

    return DraftDeal(
        source=source,
        url=url,
        address=address,
        purchase_price=purchase_price_dp,
        # ARV and rehab are never on listing pages — investor must supply
        arv=DataPoint(confidence="MISSING"),
        rehab_budget=DataPoint(confidence="MISSING"),
        est_monthly_rent=DataPoint(confidence="MISSING"),
        notes=notes,
        signals=signals,
    )
