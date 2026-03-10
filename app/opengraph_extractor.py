"""
Open Graph Extractor for FlipForge

Minimal, best-effort extraction from property listing URLs.
Uses only Open Graph meta tags (no HTML scraping).
Never throws errors - returns partial DraftDeal on failure.
"""

from __future__ import annotations

import re
from typing import Optional, List, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from app.models import DraftDeal, DataPoint, ConfidenceLevel


def extract_from_url(url: str, timeout: int = 10) -> DraftDeal:
    """
    Extract property data from URL using Open Graph tags.
    
    Returns DraftDeal with whatever data could be extracted.
    Never raises exceptions - returns partial data on failure.
    """
    notes: List[str] = []
    signals: List[str] = []
    
    notes.append(f"Attempting extraction from: {url}")
    
    # Detect source
    source = _detect_source(url)
    signals.append(f"Detected source: {source}")
    
    # Fetch HTML
    html = _fetch_html(url, timeout, notes, signals)
    if not html:
        notes.append("Failed to fetch page content")
        return DraftDeal(
            source=source,
            url=url,
            notes=notes,
            signals=signals
        )
    
    # Parse Open Graph tags
    og_data = _parse_opengraph(html, notes, signals)
    
    # Build draft from extracted data
    return _build_draft(url, source, og_data, notes, signals)


def _detect_source(url: str) -> str:
    """Detect property listing source from URL domain"""
    domain = urlparse(url).netloc.lower()
    
    if "zillow.com" in domain:
        return "Zillow"
    elif "redfin.com" in domain:
        return "Redfin"
    elif "realtor.com" in domain:
        return "Realtor"
    else:
        return "OpenGraph"


def _fetch_html(url: str, timeout: int, notes: List[str], signals: List[str]) -> Optional[str]:
    """Fetch HTML content from URL. Returns None on failure."""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=timeout)

        if r.status_code == 403:
            signals.append("SOURCE_BLOCKED")
            notes.append("This site blocks automated reading (403). Enter numbers manually.")
            return None

        r.raise_for_status()
        signals.append("PAGE_FETCH_OK")
        return r.text

    except Exception as e:
        notes.append(f"Fetch error: {type(e).__name__}: {str(e)}")
        return None


def _parse_opengraph(html: str, notes: List[str], signals: List[str]) -> dict:
    """Extract Open Graph meta tags from HTML"""
    try:
        soup = BeautifulSoup(html, 'html.parser')
        og_data = {}
        
        for meta in soup.find_all('meta', property=re.compile(r'^og:')):
            prop = meta.get('property', '')
            content = meta.get('content', '')
            if prop and content:
                key = prop.replace('og:', '')
                og_data[key] = content
        
        if og_data:
            signals.append(f"Found {len(og_data)} Open Graph tags")
        else:
            notes.append("No Open Graph tags found")
        
        return og_data
    except Exception as e:
        notes.append(f"OG parse error: {str(e)}")
        return {}


def _build_draft(url: str, source: str, og_data: dict, notes: List[str], signals: List[str]) -> DraftDeal:
    """Build DraftDeal from extracted Open Graph data"""
    draft = DraftDeal(
        source=source,
        url=url,
        notes=notes,
        signals=signals
    )
    
    # Extract address from og:title
    address = _extract_address(og_data, signals)
    if address:
        draft.address = address
    
    # Extract price (stricter detection)
    price_data = _extract_price(og_data, notes, signals)
    if price_data:
        value, confidence, evidence = price_data
        draft.purchase_price = DataPoint(
            value=value,
            confidence=confidence,
            source=source,
            evidence=evidence
        )
    
    # Extract region/location
    region = _extract_region(og_data, signals)
    if region:
        draft.region = region
    
    return draft


def _extract_address(og_data: dict, signals: List[str]) -> Optional[str]:
    """Extract property address from Open Graph title"""
    title = og_data.get('title', '')
    if not title:
        return None
    
    # Common pattern: "123 Main St, City, ST 12345 | Site Name"
    # Extract everything before pipe/dash
    clean_title = re.split(r'[|–—]', title)[0].strip()
    
    if clean_title and len(clean_title) > 5:
        signals.append("Address extracted from og:title")
        return clean_title
    
    return None


def _extract_price(og_data: dict, notes: List[str], signals: List[str]) -> Optional[Tuple[float, ConfidenceLevel, str]]:
    """
    Extract price from Open Graph title and description.
    
    STRICT: Only matches dollar amounts with $ symbol or "USD" nearby.
    Returns (value, confidence, evidence) or None.
    """
    # Search both title and description for better hit rate
    title = og_data.get('title', '')
    desc = og_data.get('description', '')
    combined = f"{title} {desc}"
    
    if not combined.strip():
        return None
    
    # Pattern: Look for $ followed by number with optional commas
    # Must have $ to avoid matching beds/baths/sqft/years
    price_patterns = [
        (r'\$\s*([\d,]+)(?:\.\d{2})?', "og:title/description with $"),
        (r'([\d,]+)\s*USD', "og:title/description with USD"),
    ]
    
    for pattern, evidence in price_patterns:
        match = re.search(pattern, combined)
        if match:
            try:
                value_str = match.group(1).replace(',', '')
                value = float(value_str)
                
                # Sanity check: typical property price range
                if 10000 <= value <= 10000000:
                    signals.append(f"Price ${value:,.0f} found via {evidence}")
                    return (value, ConfidenceLevel.MEDIUM, evidence)
                else:
                    notes.append(f"Price ${value:,.0f} outside valid range (10k-10M)")
            except ValueError:
                continue
    
    notes.append("No valid price found in og:title or og:description")
    return None


def _extract_region(og_data: dict, signals: List[str]) -> Optional[str]:
    """Extract region/location from Open Graph data"""
    # Try locality + region first
    locality = og_data.get('locality', '')
    region = og_data.get('region', '')
    
    if locality and region:
        combined = f"{locality}, {region}"
        signals.append(f"Region extracted: {combined}")
        return combined
    elif locality:
        signals.append(f"Region extracted: {locality}")
        return locality
    elif region:
        signals.append(f"Region extracted: {region}")
        return region
    
    # Fallback: extract state from title
    title = og_data.get('title', '')
    state_match = re.search(r',\s*([A-Z]{2})\s+\d{5}', title)
    if state_match:
        state = state_match.group(1)
        signals.append(f"State extracted from title: {state}")
        return state
    
    return None