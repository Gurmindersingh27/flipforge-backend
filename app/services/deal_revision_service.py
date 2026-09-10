from decimal import Decimal, ROUND_HALF_UP
from fastapi import HTTPException
from pydantic import ValidationError
from app.models import AnalyzeRequest, DraftDeal, RehabScope, SavedDealResponse
from app.analysis_engine import analyze_deal


def scope_total(scope: RehabScope) -> int:
    # Round each line to cents, then the contingency-inclusive budget to dollars.
    subtotal = sum((Decimal(str(i.quantity)) * Decimal(str(i.unit_cost))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    ) for i in scope.items)
    return int((subtotal * (1 + Decimal(str(scope.contingency_pct)))).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    ))


def canonical_revision(body):
    """Compute new scoped/revised saves from their inputs, never client verdicts."""
    try:
        draft = DraftDeal.model_validate(body.draft_input)
        fields = ("closing_cost_pct", "selling_cost_pct", "holding_months",
                  "annual_interest_rate", "loan_to_cost_pct", "required_profit_margin_pct", "region")
        req = AnalyzeRequest(
            purchase_price=draft.purchase_price.value, arv=draft.arv.value,
            rehab_budget=draft.rehab_budget.value,
            est_monthly_rent=draft.est_monthly_rent.value if (draft.est_monthly_rent.value or 0) > 0 else None,
            **{key: getattr(draft, key) for key in fields},
        )
    except (ValidationError, TypeError) as exc:
        raise HTTPException(422, "A scope or revision requires complete, valid deal inputs.") from exc
    if body.rehab_scope is not None and abs(scope_total(body.rehab_scope) - req.rehab_budget) > 0.001:
        raise HTTPException(422, "The itemized scope total must match the analyzed rehab budget. Apply the scope and re-analyze.")
    return analyze_deal(req).model_dump(mode="json")


def deal_response(record, revision=None):
    return SavedDealResponse(
        id=record.id, user_id=record.user_id, address=record.address,
        draft_input=record.draft_input, analysis_result=record.analysis_result,
        created_at=record.created_at.isoformat(),
        rehab_scope=revision.rehab_scope if revision else None,
        parent_deal_id=revision.parent_deal_id if revision else None,
        revision_note=revision.revision_note if revision else "",
    )
