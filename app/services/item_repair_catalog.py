"""Draft pilot materials allowances. Owner review is required before enabling AI.

Amounts are proposed allowances, NOT verified retail quotes or labor prices.
Version approval is explicit so a later edit cannot silently ship new prices.
"""
VERSION = "2026-10-06-draft1"
CATALOG = {
    "clean": ("Clean and degrease", 5),
    "scratch_touchup": ("Touch up light scratches", 8),
    "sand_seat": ("Sand and finish a chair seat", 15),
    "refinish_top": ("Sand and refinish a small top", 25),
    "paint_chair": ("Prep and paint one chair", 20),
    "paint_dresser": ("Prep and paint a small dresser", 35),
    "replace_knobs": ("Replace up to four basic knobs", 16),
    "glue_joint": ("Reglue a loose wood joint", 8),
    "seat_fabric": ("Recover one removable seat pad", 25),
    "replace_glides": ("Replace furniture feet/glides", 6),
}


def suggestions(report):
    seen, result = set(), []
    unknowns = list(report.repair_unknowns)
    for repair in report.repairs:
        if repair.job_id not in CATALOG:
            unknowns.append(repair.reason)
            continue
        if repair.job_id in seen:
            continue
        seen.add(repair.job_id)
        label, cost = CATALOG[repair.job_id]
        result.append(dict(job_id=repair.job_id, label=label, materials_cost=cost,
                           reason=repair.reason, confirmed=False))
    return result, unknowns
