"""Draft pilot materials allowances. Owner review is required before enabling AI.

Amounts are proposed allowances, NOT verified retail quotes or labor prices.
Version approval is explicit so a later edit cannot silently ship new prices.
"""
VERSION = "2026-10-06-draft2"
# label, materials allowance, scope. The same scope reaches the provider and UI.
CATALOG = {
    "clean": ("Clean and degrease", 5,
              "Routine cleaning; excludes mold, pests and odor remediation"),
    "scratch_touchup": ("Touch up light scratches", 8,
                        "Small cosmetic marks; excludes deep gouges and veneer damage"),
    "sand_seat": ("Sand and finish a chair seat", 15,
                  "This surface only: sanding and finish; excludes stripping, veneer damage and frame repairs"),
    "refinish_top": ("Sand and refinish a small top", 30,
                     "This surface only: simple sanding, stain and finish; excludes stripping and veneer damage"),
    "paint_chair": ("Prep and paint one chair", 25,
                    "Whole chair: routine cleaning, scratch prep, sanding and paint; "
                    "excludes upholstery, stripping, veneer damage and broken parts"),
    "paint_dresser": ("Prep and paint a small dresser", 50,
                      "Whole small dresser: routine cleaning, scratch prep, sanding, paint and primer/topcoat as needed; "
                      "excludes stripping, veneer damage, hardware and structural repairs"),
    "replace_knobs": ("Replace up to four basic knobs", 16,
                      "Up to four basic knobs; excludes pulls, extra knobs and drilling repairs"),
    "glue_joint": ("Reglue a loose wood joint", 8,
                   "One straightforward loose joint; excludes broken parts and structural rebuilds"),
    "seat_fabric": ("Recover one removable seat pad", 25,
                    "Basic fabric and staples for one pad; reuses sound foam and base, replacements need a separate budget"),
    "replace_glides": ("Replace basic furniture pads/glides", 6,
                       "Basic pads/glides; excludes replacement wooden feet, legs and casters"),
}

# Only whole-piece paint jobs include these smaller jobs. Seat/top work covers
# one surface and must not absorb cleaning or scratches elsewhere on the item.
INCLUDED_PAIRS = frozenset({
    ("clean", "paint_chair"),
    ("clean", "paint_dresser"),
    ("scratch_touchup", "paint_chair"),
    ("scratch_touchup", "paint_dresser"),
})
CUSTOM_TOTAL_PAIRS = (
    ("paint_dresser", "refinish_top",
     "Painting the dresser and refinishing its top may cover different surfaces or overlap; "
     "enter one total repair budget for the work you plan to do."),
)


def suggestions(report):
    seen, result = set(), []
    present = {repair.job_id for repair in report.repairs if repair.job_id in CATALOG}
    unknowns = list(report.repair_unknowns)
    for first, second, message in CUSTOM_TOTAL_PAIRS:
        if first in present and second in present:
            unknowns.append(message)
    for repair in report.repairs:
        if repair.job_id not in CATALOG:
            unknowns.append(repair.reason)
            continue
        if repair.job_id in seen:
            continue
        seen.add(repair.job_id)
        label, cost, scope = CATALOG[repair.job_id]
        # Catalog order makes the host stable even if the provider changes order.
        host = next((key for key in CATALOG
                     if key in present and (repair.job_id, key) in INCLUDED_PAIRS), None)
        if host is not None:
            label += f" - Included in {CATALOG[host][0]}"
            cost = 0
        label += f" ({scope})"
        result.append(dict(job_id=repair.job_id, label=label, materials_cost=cost,
                           reason=repair.reason, confirmed=False))
    return result, unknowns
