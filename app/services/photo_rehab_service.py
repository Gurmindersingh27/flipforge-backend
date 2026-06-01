"""
Photo Rehab Analysis service.

Uses Anthropic Claude vision to classify property condition from photos,
then maps AI findings to controlled pricing ranges (rehab_pricing.py).

AI identifies condition/severity only. Dollar estimates are always controlled
by backend pricing constants — AI never invents final dollar totals.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Optional

from .rehab_pricing import (
    MAJOR_SYSTEMS,
    REHAB_RANGES,
    compute_contingency_pct,
    price_category,
)


VISION_PROMPT = """You are analyzing property photos for a real estate rehab cost estimator.

Analyze the provided photos and return a JSON object with your findings. Do NOT estimate dollar costs — only identify conditions and severity.

Return this exact JSON structure:
{
  "overall_condition": "light" | "medium" | "heavy" | "unknown",
  "confidence_score": <int 0-100>,
  "summary": "<1-2 sentence overall condition summary>",
  "rooms": [
    {
      "area_name": "<room/area name, e.g. kitchen, bathroom, exterior, living room>",
      "detected_condition": "light" | "medium" | "heavy" | "unknown",
      "visible_issues": ["<issue 1>", "<issue 2>"],
      "confidence": "high" | "medium" | "low",
      "notes": "<optional context>"
    }
  ],
  "categories": {
    "kitchen": {"severity": "none" | "light" | "medium" | "heavy" | "unknown", "issues": [], "confidence": "high" | "medium" | "low", "reasoning": ""},
    "bathrooms": {"severity": "...", "issues": [], "confidence": "...", "reasoning": "", "count": <int or null>},
    "flooring": {"severity": "...", "issues": [], "confidence": "...", "reasoning": ""},
    "paint_drywall": {"severity": "...", "issues": [], "confidence": "...", "reasoning": ""},
    "roof": {"severity": "...", "issues": [], "confidence": "...", "reasoning": ""},
    "hvac": {"severity": "...", "issues": [], "confidence": "...", "reasoning": ""},
    "electrical": {"severity": "...", "issues": [], "confidence": "...", "reasoning": ""},
    "plumbing": {"severity": "...", "issues": [], "confidence": "...", "reasoning": ""},
    "windows_exterior": {"severity": "...", "issues": [], "confidence": "...", "reasoning": ""}
  },
  "missing_areas": ["<areas not visible in any photo>"],
  "risk_flags": [
    {"label": "<short label>", "severity": "mild" | "moderate" | "critical", "explanation": "<why this matters>"}
  ]
}

Rules:
- Only report what you can actually SEE in the photos.
- If a system (roof, HVAC, electrical, plumbing) is not visible, set severity to "unknown".
- "none" means the category looks fine / no work needed.
- Be conservative — do not upgrade severity unless damage is clearly visible.
- confidence_score reflects how much of the property you can assess from these photos.
- Fewer photos or limited angles = lower confidence.
- Return ONLY the JSON object, no markdown fences, no explanation outside the JSON."""


def _get_dev_stub_response(photo_count: int, sqft: Optional[int]) -> dict:
    """Deterministic dev stub for UI wiring without live AI."""
    return {
        "overall_condition": "medium",
        "confidence_score": 45,
        "summary": "DEV STUB — real AI is not configured. This is sample data for development only.",
        "rooms": [
            {
                "area_name": "kitchen",
                "detected_condition": "medium",
                "visible_issues": ["Dated cabinets", "Worn countertops"],
                "confidence": "medium",
                "notes": "Dev stub data",
            },
            {
                "area_name": "bathroom",
                "detected_condition": "light",
                "visible_issues": ["Minor cosmetic wear"],
                "confidence": "low",
                "notes": "Dev stub data",
            },
        ],
        "categories": {
            "kitchen": {"severity": "medium", "issues": ["Dated cabinets"], "confidence": "medium", "reasoning": "Dev stub"},
            "bathrooms": {"severity": "light", "issues": ["Cosmetic wear"], "confidence": "low", "reasoning": "Dev stub", "count": 1},
            "flooring": {"severity": "light", "issues": ["Surface wear"], "confidence": "low", "reasoning": "Dev stub"},
            "paint_drywall": {"severity": "medium", "issues": ["Needs full repaint"], "confidence": "medium", "reasoning": "Dev stub"},
            "roof": {"severity": "unknown", "issues": [], "confidence": "low", "reasoning": "Not visible in dev stub"},
            "hvac": {"severity": "unknown", "issues": [], "confidence": "low", "reasoning": "Not visible in dev stub"},
            "electrical": {"severity": "unknown", "issues": [], "confidence": "low", "reasoning": "Not visible in dev stub"},
            "plumbing": {"severity": "unknown", "issues": [], "confidence": "low", "reasoning": "Not visible in dev stub"},
            "windows_exterior": {"severity": "light", "issues": ["Minor wear"], "confidence": "low", "reasoning": "Dev stub"},
        },
        "missing_areas": ["roof", "hvac", "electrical", "plumbing"],
        "risk_flags": [
            {"label": "Dev stub active", "severity": "mild", "explanation": "Real AI analysis is not configured. Set ANTHROPIC_API_KEY for production use."}
        ],
    }


def _call_anthropic_vision(
    images: list[tuple[bytes, str]],
    sqft: Optional[int] = None,
    property_type: Optional[str] = None,
    user_notes: Optional[str] = None,
) -> dict:
    """
    Call Anthropic Claude vision API with property photos.
    Returns parsed JSON dict from AI response.
    Raises on API/parsing failure.
    """
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514").strip()

    client = anthropic.Anthropic(api_key=api_key)

    content: list[dict] = []

    for image_bytes, mime_type in images:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": mime_type,
                "data": b64,
            },
        })

    context_parts = []
    if sqft:
        context_parts.append(f"Property square footage: {sqft} sqft")
    if property_type:
        context_parts.append(f"Property type: {property_type}")
    if user_notes:
        context_parts.append(f"User notes: {user_notes}")

    prompt_text = VISION_PROMPT
    if context_parts:
        prompt_text += "\n\nAdditional context:\n" + "\n".join(context_parts)

    content.append({"type": "text", "text": prompt_text})

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": content}],
    )

    raw_text = response.content[0].text.strip()

    if raw_text.startswith("```"):
        lines = raw_text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw_text = "\n".join(lines)

    return json.loads(raw_text)


def _normalize_ai_findings(raw: dict) -> dict:
    """Validate and normalize AI response to expected structure."""
    normalized = {
        "overall_condition": raw.get("overall_condition", "unknown"),
        "confidence_score": max(0, min(100, int(raw.get("confidence_score", 30)))),
        "summary": str(raw.get("summary", "Analysis completed.")),
        "rooms": [],
        "categories": {},
        "missing_areas": raw.get("missing_areas", []),
        "risk_flags": raw.get("risk_flags", []),
    }

    if normalized["overall_condition"] not in ("light", "medium", "heavy", "unknown"):
        normalized["overall_condition"] = "unknown"

    for room in raw.get("rooms", []):
        if not isinstance(room, dict):
            continue
        normalized["rooms"].append({
            "area_name": str(room.get("area_name", "unknown")),
            "detected_condition": room.get("detected_condition", "unknown"),
            "visible_issues": [str(i) for i in room.get("visible_issues", [])],
            "confidence": room.get("confidence", "low"),
            "notes": room.get("notes"),
        })

    valid_severities = {"none", "light", "medium", "heavy", "unknown"}
    for cat_key in REHAB_RANGES.keys():
        cat_data = raw.get("categories", {}).get(cat_key, {})
        if not isinstance(cat_data, dict):
            cat_data = {}

        severity = cat_data.get("severity", "unknown")
        if severity not in valid_severities:
            severity = "unknown"

        normalized["categories"][cat_key] = {
            "severity": severity,
            "issues": [str(i) for i in cat_data.get("issues", [])],
            "confidence": cat_data.get("confidence", "low"),
            "reasoning": str(cat_data.get("reasoning", "")),
            "count": cat_data.get("count"),
        }

    return normalized


def analyze_photos(
    images: list[tuple[bytes, str]],
    sqft: Optional[int] = None,
    region: Optional[str] = None,
    property_type: Optional[str] = None,
    user_notes: Optional[str] = None,
) -> dict:
    """
    Main entry point. Analyzes property photos and returns structured response.
    Returns a dict matching PhotoRehabAnalysisResponse shape.
    """
    photo_count = len(images)

    dev_stub = os.environ.get("PHOTO_REHAB_DEV_STUB", "").strip().lower() == "true"
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()

    if dev_stub:
        ai_findings = _get_dev_stub_response(photo_count, sqft)
        provider_status = "dev_stub"
    elif not api_key:
        return {
            "overall_condition": "unknown",
            "confidence_score": 0,
            "summary": "AI analysis is not configured. Set ANTHROPIC_API_KEY to enable photo rehab analysis.",
            "rooms": [],
            "rehab_items": [],
            "totals": {"low": 0, "mid": 0, "high": 0, "contingency_pct": 0, "subtotal_low": 0, "subtotal_mid": 0, "subtotal_high": 0},
            "risk_flags": [],
            "missing_photo_warnings": [],
            "notes": ["AI provider not configured. Contact admin to enable photo analysis."],
            "photos_analyzed": photo_count,
            "disclaimer": "AI analysis unavailable — ANTHROPIC_API_KEY not set.",
            "provider_status": "ai_not_configured",
        }
    else:
        try:
            raw_response = _call_anthropic_vision(
                images, sqft=sqft, property_type=property_type, user_notes=user_notes
            )
            ai_findings = _normalize_ai_findings(raw_response)
            provider_status = "live_success"
        except json.JSONDecodeError as e:
            return {
                "overall_condition": "unknown",
                "confidence_score": 0,
                "summary": "AI returned malformed response. Please try again.",
                "rooms": [],
                "rehab_items": [],
                "totals": {"low": 0, "mid": 0, "high": 0, "contingency_pct": 0, "subtotal_low": 0, "subtotal_mid": 0, "subtotal_high": 0},
                "risk_flags": [{"label": "AI parse error", "severity": "moderate", "explanation": f"Could not parse AI response: {str(e)[:100]}"}],
                "missing_photo_warnings": [],
                "notes": ["AI response was not valid JSON. This may be a transient issue."],
                "photos_analyzed": photo_count,
                "disclaimer": "Analysis failed due to AI response parsing error.",
                "provider_status": "ai_error",
            }
        except Exception as e:
            return {
                "overall_condition": "unknown",
                "confidence_score": 0,
                "summary": "AI analysis failed. Please try again.",
                "rooms": [],
                "rehab_items": [],
                "totals": {"low": 0, "mid": 0, "high": 0, "contingency_pct": 0, "subtotal_low": 0, "subtotal_mid": 0, "subtotal_high": 0},
                "risk_flags": [{"label": "AI error", "severity": "moderate", "explanation": f"AI call failed: {str(e)[:100]}"}],
                "missing_photo_warnings": [],
                "notes": ["AI service encountered an error. This may be transient or a configuration issue."],
                "photos_analyzed": photo_count,
                "disclaimer": "Analysis failed due to AI service error.",
                "provider_status": "ai_error",
            }

    # Map AI findings to controlled pricing
    confidence_score = ai_findings["confidence_score"]
    categories = ai_findings.get("categories", {})

    rehab_items = []
    all_warnings: list[str] = []

    bathroom_count = 1
    bath_data = categories.get("bathrooms", {})
    if bath_data.get("count") and isinstance(bath_data["count"], int) and bath_data["count"] > 0:
        bathroom_count = bath_data["count"]
    elif bath_data.get("severity") not in ("none", "unknown", None):
        all_warnings.append("Bathroom count not clearly detected — defaulting to 1 bathroom.")

    for cat_key in REHAB_RANGES.keys():
        cat_data = categories.get(cat_key, {})
        severity = cat_data.get("severity", "unknown")

        low, mid, high, warnings = price_category(
            category=cat_key,
            severity=severity,
            sqft=sqft,
            bathroom_count=bathroom_count,
        )
        all_warnings.extend(warnings)

        rehab_items.append({
            "category": cat_key,
            "severity": severity,
            "low": low,
            "mid": mid,
            "high": high,
            "reasoning": cat_data.get("reasoning", ""),
            "confidence": cat_data.get("confidence", "low"),
        })

    subtotal_low = sum(item["low"] for item in rehab_items)
    subtotal_mid = sum(item["mid"] for item in rehab_items)
    subtotal_high = sum(item["high"] for item in rehab_items)

    contingency_pct = compute_contingency_pct(confidence_score)
    total_low = int(subtotal_low * (1 + contingency_pct / 100))
    total_mid = int(subtotal_mid * (1 + contingency_pct / 100))
    total_high = int(subtotal_high * (1 + contingency_pct / 100))

    # Build missing photo warnings
    missing_photo_warnings = []
    for area in ai_findings.get("missing_areas", []):
        area_lower = area.lower().replace(" ", "_")
        if area_lower in MAJOR_SYSTEMS:
            missing_photo_warnings.append(
                f"No {area} photos — estimate may be low. Recommend professional inspection."
            )
        else:
            missing_photo_warnings.append(f"No {area} photos — cannot assess condition.")
    missing_photo_warnings.extend(all_warnings)

    # Build risk flags
    risk_flags = []
    for flag in ai_findings.get("risk_flags", []):
        if isinstance(flag, dict) and "label" in flag:
            sev = flag.get("severity", "mild")
            if sev not in ("mild", "moderate", "critical"):
                sev = "mild"
            risk_flags.append({
                "label": str(flag["label"]),
                "severity": sev,
                "explanation": str(flag.get("explanation", "")),
            })

    # Add risk flags for unknown major systems
    for sys_name in MAJOR_SYSTEMS:
        cat_data = categories.get(sys_name, {})
        if cat_data.get("severity") == "unknown":
            already_flagged = any(sys_name in f.get("label", "").lower() for f in risk_flags)
            if not already_flagged:
                risk_flags.append({
                    "label": f"{sys_name.upper()} not visible",
                    "severity": "moderate",
                    "explanation": f"No clear view of {sys_name} in photos. Actual condition unknown — budget may be understated.",
                })

    # Build notes
    notes = []
    if provider_status == "dev_stub":
        notes.append("DEV STUB ACTIVE — this is not real AI analysis. Set ANTHROPIC_API_KEY for production.")
    if confidence_score < 50:
        notes.append(f"Low confidence ({confidence_score}/100) — consider uploading more photos or getting professional inspection.")
    if photo_count <= 3:
        notes.append("Few photos provided. More angles typically improve accuracy.")
    notes.append("Estimates based on SE US contractor pricing (2026). Verify with local contractors.")

    return {
        "overall_condition": ai_findings["overall_condition"],
        "confidence_score": confidence_score,
        "summary": ai_findings["summary"],
        "rooms": [
            {
                "area_name": r["area_name"],
                "detected_condition": r["detected_condition"],
                "visible_issues": r["visible_issues"],
                "confidence": r["confidence"],
                "notes": r.get("notes"),
            }
            for r in ai_findings.get("rooms", [])
        ],
        "rehab_items": rehab_items,
        "totals": {
            "low": total_low,
            "mid": total_mid,
            "high": total_high,
            "contingency_pct": contingency_pct,
            "subtotal_low": subtotal_low,
            "subtotal_mid": subtotal_mid,
            "subtotal_high": subtotal_high,
        },
        "risk_flags": risk_flags,
        "missing_photo_warnings": missing_photo_warnings,
        "notes": notes,
        "photos_analyzed": photo_count,
        "disclaimer": "AI-assisted estimate for planning purposes only. Verify all findings with licensed contractors before making offers.",
        "provider_status": provider_status,
    }
