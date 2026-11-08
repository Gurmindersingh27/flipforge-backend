"""Owner-scoped project reads and deterministic estimate/actual comparisons."""
from datetime import timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.db.models.item_assessment import SavedItemAssessment
from app.db.models.item_outcome import ItemOutcomeRevision
from app.db.models.saved_item import SavedItem
from app.item_analysis_engine import analyze_item
from app.item_models import CENT, FINANCIAL_INPUTS, ItemAnalyzeRequest
from app.item_outcome_models import ItemOutcomeFacts
from app.item_project_models import (
    ItemProjectListResponse, ItemProjectResponse, ItemProjectSummary,
    OutcomeComparison, OutcomeEffect, OutcomeExplanation, OutcomeLine,
)
from app.saved_item_models import SavedItemResponse
from app.services.item_outcome_service import (
    _latest, _owned_root, calculate_outcome, outcome_response,
)


ZERO = Decimal(0)


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _version(record, context):
    # item_response() performs an assessment SELECT per row. These reads load
    # the companion context in the same query, including absent contexts.
    return SavedItemResponse(
        id=record.id, schema_version=record.schema_version, created_at=record.created_at,
        parent_item_id=record.parent_item_id, root_item_id=record.root_item_id,
        inputs=record.inputs, analysis_result=record.analysis_result,
        listing_url=record.listing_url, notes=record.notes,
        assessment=context.context if context is not None else None,
    )


def _summary(version, count, outcome, updated_at=None):
    updated = _utc(version.created_at)
    if outcome is not None:
        updated = max(updated, _utc(outcome.created_at))
    return ItemProjectSummary(
        root_item_id=version.root_item_id, latest_version=version, version_count=count,
        stage=outcome.inputs.status if outcome is not None else "considering",
        calculated=outcome.calculated if outcome is not None else None,
        updated_at=max(updated, _utc(updated_at)) if updated_at is not None else updated,
    )


def list_projects(db: Session, user_id: str, limit: int, offset: int) -> ItemProjectListResponse:
    ranked = db.query(
        SavedItem.id.label("id"),
        func.count().over(partition_by=SavedItem.root_item_id).label("version_count"),
        func.row_number().over(partition_by=SavedItem.root_item_id,
                               order_by=(SavedItem.created_at.desc(), SavedItem.id.desc())).label("rank"),
    ).filter(SavedItem.user_id == user_id).subquery()
    activity = db.query(
        ItemOutcomeRevision.root_item_id.label("root_id"),
        func.max(ItemOutcomeRevision.created_at).label("updated_at"),
    ).filter(ItemOutcomeRevision.user_id == user_id).group_by(ItemOutcomeRevision.root_item_id).subquery()
    updated = case((activity.c.updated_at > SavedItem.created_at, activity.c.updated_at),
                   else_=SavedItem.created_at)
    records = (db.query(SavedItem, SavedItemAssessment, ranked.c.version_count, updated)
               .join(ranked, ranked.c.id == SavedItem.id)
               .outerjoin(SavedItemAssessment, SavedItemAssessment.item_id == SavedItem.id)
               .outerjoin(activity, activity.c.root_id == SavedItem.root_item_id)
               .filter(ranked.c.rank == 1, SavedItem.user_id == user_id)
               .order_by(updated.desc(), SavedItem.root_item_id.desc())
               .offset(offset).limit(limit + 1).all())
    page = records[:limit]
    outcomes = {}
    if page:
        roots = [record.root_item_id for record, _, _, _ in page]
        latest = db.query(
            ItemOutcomeRevision.root_item_id.label("root_id"),
            func.max(ItemOutcomeRevision.revision_number).label("revision"),
        ).filter(ItemOutcomeRevision.user_id == user_id, ItemOutcomeRevision.root_item_id.in_(roots)) \
            .group_by(ItemOutcomeRevision.root_item_id).subquery()
        rows = (db.query(ItemOutcomeRevision).join(latest,
                (ItemOutcomeRevision.root_item_id == latest.c.root_id)
                & (ItemOutcomeRevision.revision_number == latest.c.revision))
                .filter(ItemOutcomeRevision.user_id == user_id).all())
        outcomes = {row.root_item_id: outcome_response(row) for row in rows}
    return ItemProjectListResponse(
        items=[_summary(_version(row, context), count, outcomes.get(row.root_item_id), updated_at)
               for row, context, count, updated_at in page],
        limit=limit, offset=offset, next_offset=offset + limit if len(records) > limit else None,
    )


def get_project(db: Session, user_id: str, root_id: int) -> ItemProjectResponse:
    _owned_root(db, user_id, root_id)
    # Read the outcome before its immutable estimate family. A concurrent save
    # cannot make this outcome reference a version omitted by an earlier read.
    row = _latest(db, user_id, root_id)
    outcome = outcome_response(row) if row is not None else None
    records = (db.query(SavedItem, SavedItemAssessment)
               .outerjoin(SavedItemAssessment, SavedItemAssessment.item_id == SavedItem.id)
               .filter(SavedItem.user_id == user_id, SavedItem.root_item_id == root_id)
               .order_by(SavedItem.created_at.desc(), SavedItem.id.desc()).all())
    versions = [_version(row, context) for row, context in records]
    basis = next((version for version in versions
                  if outcome is not None and version.id == outcome.based_on_item_id), None)
    comparison = None
    if outcome is None:
        reason = "No outcome recorded"
    elif basis is None:
        reason = "No estimate linked"
    elif basis.analysis_result.status == "needs_info":
        reason = "The estimate was incomplete"
    else:
        reason = None
        comparison = compare_outcome(basis, outcome.inputs)
    return ItemProjectResponse(
        summary=_summary(versions[0], len(versions), outcome), versions=versions,
        latest_outcome=outcome, comparison=comparison, comparison_unavailable_reason=reason,
    )


def _cents(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _explanation(baseline_sale, baseline, actual, effects):
    # Preserve the Decimal identity before rounding. At the UI's cent precision,
    # expose any rounding residual instead of silently losing a cent.
    total = actual - baseline
    assert baseline + sum((effect.amount for effect in effects), ZERO) == actual
    display_effects = [effect.model_copy(update={"amount": _cents(effect.amount)}) for effect in effects]
    display_total = _cents(actual) - _cents(baseline)
    residual = display_total - sum((effect.amount for effect in display_effects), ZERO)
    if residual:
        display_effects.append(OutcomeEffect(key="rounding", label="Rounding to cents", amount=residual))
    magnitude = sum((abs(effect.amount) for effect in effects), ZERO)
    ranked = sorted(effects, key=lambda effect: abs(effect.amount), reverse=True)
    dominant = None
    if total and magnitude and ranked:
        candidate = ranked[0]
        # Do not call a tie a dominant cause, or attribute a loss to a saving.
        if (abs(candidate.amount) * 2 >= magnitude
                and candidate.amount * total > 0
                and (len(ranked) == 1 or abs(candidate.amount) > abs(ranked[1].amount))
                and display_total and _cents(candidate.amount)):
            dominant = candidate
    return OutcomeExplanation(
        baseline_sale_price=baseline_sale, baseline_profit=_cents(baseline), actual_profit=_cents(actual),
        total_effect=display_total, effects=display_effects,
        dominant_effect=dominant.key if dominant else None,
        headline=f"Most of the gap came from {dominant.label[0].lower()}{dominant.label[1:]}." if dominant else None,
    )


def compare_outcome(estimate: SavedItemResponse, outcome: ItemOutcomeFacts) -> OutcomeComparison:
    """Caller resolves the outcome's explicit basis within its owned root family."""
    with localcontext() as context:
        context.prec = 40
        return _compare_outcome(estimate, outcome)


def _compare_outcome(estimate, facts):
    saved = estimate.analysis_result
    # Supply every resolved value explicitly (including null). Never reload
    # today's preferences or let a changed application default fill a field.
    values = {key: getattr(saved.assumptions, key).value for key in FINANCIAL_INPUTS}
    payload = {key: float(value) if value is not None else None for key, value in values.items()}
    payload.update(item_name=saved.assumptions.item_name.value, category=saved.assumptions.category.value,
                   purchase_price=float(facts.purchase_price) if facts.purchase_price is not None else None)
    rebuilt = analyze_item(ItemAnalyzeRequest.model_validate(payload))
    actual = calculate_outcome(facts)
    repairs = values["repairs"] * (1 + values["contingency_pct"])
    time = values["hours"] * values["hourly_value"]
    low, high = values["resale_low"], values["resale_high"]
    fee_pct, fee_fixed = values["fee_pct"], values["fee_fixed"]

    def line(key, label, expected_low, expected_high, fields):
        missing = [field for field in fields if getattr(facts, field) is None]
        recorded = None if missing else sum((getattr(facts, field) for field in fields), ZERO)
        if key == "time" and not missing:
            recorded = facts.hours * facts.hourly_value
        difference_low = difference_high = None
        if recorded is not None and expected_low is not None and key != "purchase_price":
            if key == "sale_price":
                difference_low, difference_high = recorded - expected_low, recorded - expected_high
            else:
                difference_low, difference_high = expected_low - recorded, expected_high - recorded
        return OutcomeLine(key=key, label=label, expected_low=expected_low, expected_high=expected_high,
                           actual=recorded, difference_low=difference_low, difference_high=difference_high,
                           missing_fields=missing)

    lines = [
        line("purchase_price", "Purchase price", facts.purchase_price, facts.purchase_price, ["purchase_price"]),
        line("sale_price", "Sale price", low, high, ["sale_price"]),
        line("repairs", "Repairs including contingency", repairs, repairs, ["materials", "hired_labor"]),
        *[line(key, key.capitalize(), values[key], values[key], [key]) for key in ("pickup", "delivery", "storage")],
        line("selling_fees", "Selling fees", low * fee_pct + fee_fixed, high * fee_pct + fee_fixed, ["selling_fees"]),
        line("other_expenses", "Other expenses (no estimate allowance)", ZERO, ZERO, ["other_expenses"]),
        line("time", "Value of your time", time, time, ["hours", "hourly_value"]),
    ]
    cash = after_time = None
    if actual.cash_profit is not None:
        sale = facts.sale_price
        clamped = min(max(sale, low), high)
        baseline = (clamped * (1 - fee_pct) - fee_fixed - facts.purchase_price
                    - repairs - values["pickup"] - values["delivery"] - values["storage"])
        label = (f"Selling ${low - sale:,.2f} under the low estimate" if sale < low else
                 f"Selling ${sale - high:,.2f} above the high estimate" if sale > high else "Sale within the estimated range")
        if fee_pct and sale != clamped:
            label += " (after expected selling fees)"
        effects = [OutcomeEffect(key="sale_price", label=label, amount=(sale - clamped) * (1 - fee_pct))]
        for entry in lines:
            if entry.key in ("purchase_price", "sale_price", "time"):
                continue
            # Net sale effect already includes the expected variable-fee change.
            # Compare actual fees to expected fees at ACTUAL sale to avoid
            # counting that change twice when the sale is outside the range.
            expected = sale * fee_pct + fee_fixed if entry.key == "selling_fees" else entry.expected_low
            effects.append(OutcomeEffect(key=entry.key, label=entry.label, amount=expected - entry.actual))
        cash = _explanation(clamped, baseline, actual.cash_profit, effects)
        if actual.profit_after_time is not None:
            time_effect = OutcomeEffect(key="time", label="Value of your time", amount=time - facts.hours * facts.hourly_value)
            after_time = _explanation(clamped, baseline - time, actual.profit_after_time, [*effects, time_effect])
    source = "user_estimate" if estimate.assessment is None else estimate.assessment.get("confirmation", {}).get("resale_source", "unknown")
    if source not in ("assessment", "national_reference", "user_estimate"):
        source = "unknown"
    return OutcomeComparison(
        original_estimate=estimate, resale_source=source,
        expected_at_purchase_price=rebuilt if facts.purchase_price is not None else None,
        expected_missing_fields=[] if facts.purchase_price is not None else ["purchase_price"],
        lines=lines, actual=actual, cash_explanation=cash, after_time_explanation=after_time,
    )
