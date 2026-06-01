"""
Controlled rehab pricing constants and severity-to-dollar mapping.

Ranges match frontend RepairBudgetBuilder.tsx (SE US contractor pricing, 2026).
AI identifies condition/severity only — this module controls all dollar estimates.
"""

from __future__ import annotations

from typing import Optional

# [low, high] per severity level. Mid = (low + high) / 2.
# "flat" categories use these directly.
# "per_bath" categories multiply by bathroom count.
# "per_sqft" categories multiply by square footage.

REHAB_RANGES: dict[str, dict] = {
    "kitchen": {
        "type": "flat",
        "light": [3000, 7000],
        "medium": [12000, 20000],
        "heavy": [28000, 45000],
    },
    "bathrooms": {
        "type": "per_bath",
        "light": [1500, 3000],
        "medium": [5000, 9000],
        "heavy": [12000, 20000],
    },
    "flooring": {
        "type": "per_sqft",
        "light": [2.00, 4.00],
        "medium": [4.00, 6.50],
        "heavy": [7.00, 12.00],
    },
    "paint_drywall": {
        "type": "flat",
        "light": [2000, 4000],
        "medium": [4500, 8000],
        "heavy": [9000, 15000],
    },
    "roof": {
        "type": "flat",
        "light": [1000, 3000],
        "medium": [7000, 12000],
        "heavy": [14000, 22000],
    },
    "hvac": {
        "type": "flat",
        "light": [800, 2000],
        "medium": [4000, 8000],
        "heavy": [10000, 18000],
    },
    "electrical": {
        "type": "flat",
        "light": [1000, 2500],
        "medium": [4000, 8000],
        "heavy": [12000, 20000],
    },
    "plumbing": {
        "type": "flat",
        "light": [1000, 2000],
        "medium": [3000, 7000],
        "heavy": [9000, 18000],
    },
    "windows_exterior": {
        "type": "flat",
        "light": [2000, 5000],
        "medium": [6000, 12000],
        "heavy": [15000, 28000],
    },
}

MAJOR_SYSTEMS = {"roof", "hvac", "electrical", "plumbing"}


def compute_contingency_pct(confidence_score: int) -> int:
    """Return contingency percentage based on confidence score."""
    if confidence_score < 50:
        return 20
    elif confidence_score <= 75:
        return 15
    else:
        return 10


def price_category(
    category: str,
    severity: str,
    sqft: Optional[int] = None,
    bathroom_count: int = 1,
) -> tuple[int, int, int, list[str]]:
    """
    Map a category + severity to (low, mid, high) dollar amounts.
    Returns (low, mid, high, warnings).
    """
    warnings: list[str] = []

    if category not in REHAB_RANGES:
        return (0, 0, 0, [f"Unknown category '{category}' — not priced"])

    spec = REHAB_RANGES[category]

    if severity not in ("light", "medium", "heavy"):
        if category in MAJOR_SYSTEMS:
            warnings.append(
                f"No visible evidence for {category} — not priced. "
                f"Recommend inspection."
            )
        return (0, 0, 0, warnings)

    low_val, high_val = spec[severity]
    calc_type = spec["type"]

    if calc_type == "per_sqft":
        if not sqft or sqft <= 0:
            warnings.append(
                f"Square footage not provided — {category} estimate is $0. "
                f"Provide sqft for accurate flooring/paint estimate."
            )
            return (0, 0, 0, warnings)
        low = int(low_val * sqft)
        high = int(high_val * sqft)
    elif calc_type == "per_bath":
        if bathroom_count < 1:
            bathroom_count = 1
            warnings.append(
                f"Bathroom count not detected — defaulting to 1 bathroom."
            )
        low = int(low_val * bathroom_count)
        high = int(high_val * bathroom_count)
    else:
        low = int(low_val)
        high = int(high_val)

    mid = int((low + high) / 2)
    return (low, mid, high, warnings)
