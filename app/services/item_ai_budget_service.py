"""Reserve before spending. Never hold a SQL transaction during provider I/O.

The $0.50 hold and $20 target are intentionally soft, not proven cost ceilings.
An unresolved provider outcome keeps the global gate closed across restarts.
"""
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.models.item_ai_budget import ItemAIGate, ItemAIMonth
from app.db.models.item_assessment import ItemAssessment
from app.item_assessment_models import BudgetResponse

TARGET = 20_000_000
WARNING = 16_000_000
HOLD = 500_000
PAUSED = "AI estimates are paused until the 1st. Enter what you think it'll sell for."


def utc_now():
    return datetime.now(timezone.utc)


def month_key():
    return utc_now().strftime("%Y-%m")


def _ensure(db, model, values):
    insert = sqlite_insert if db.bind.dialect.name == "sqlite" else pg_insert
    db.execute(insert(model).values(**values).on_conflict_do_nothing())


def reserve(db, user_id, request_id, fingerprint):
    """Returns (assessment, newly_reserved); duplicate keys never spend twice."""
    month = month_key()
    try:
        _ensure(db, ItemAIGate, dict(id=1))
        # UPDATE is the lock on BOTH SQLite and Postgres, even for duplicates.
        db.execute(update(ItemAIGate).where(ItemAIGate.id == 1).values(id=1))
        old = db.get(ItemAssessment, request_id)
        if old is not None:
            if old.user_id != user_id or old.request_hash != fingerprint:
                raise HTTPException(409, "Request key already used. Start a new assessment.")
            db.commit()
            return old, False
        gate = db.get(ItemAIGate, 1, populate_existing=True)
        if gate.active_id is not None:
            raise HTTPException(409, {"code": "assessment_busy", "message":
                "An AI estimate is already running or awaiting cost confirmation. The manual calculator still works."})
        _ensure(db, ItemAIMonth, dict(month=month, spent_micros=0, held_micros=0))
        reserved = db.execute(update(ItemAIMonth).where(
            ItemAIMonth.month == month,
            ItemAIMonth.spent_micros + ItemAIMonth.held_micros + HOLD <= TARGET,
        ).values(held_micros=ItemAIMonth.held_micros + HOLD))
        if reserved.rowcount != 1:
            raise HTTPException(429, {"code": "budget_paused", "message": PAUSED})
        gate.active_id = request_id
        record = ItemAssessment(id=request_id, user_id=user_id, request_hash=fingerprint,
                                month=month, reserved_micros=HOLD, status="processing")
        db.add(record)
        db.commit()
        return record, True
    except Exception:
        db.rollback()
        raise


def settle(db, request_id, actual_micros, usage, result=None, failure_code=None):
    """Known usage settles once, including paid calls with unusable answers."""
    if type(actual_micros) is not int or actual_micros < 0:
        raise ValueError("invalid actual cost")
    try:
        db.execute(update(ItemAIGate).where(ItemAIGate.id == 1).values(id=1))
        record = db.get(ItemAssessment, request_id, populate_existing=True)
        if record.actual_micros is not None:
            db.commit()
            return record
        budget = db.get(ItemAIMonth, record.month, populate_existing=True)
        budget.held_micros -= record.reserved_micros
        budget.spent_micros += actual_micros
        record.actual_micros, record.usage = actual_micros, usage
        record.result, record.failure_code = result, failure_code
        record.status = "completed" if result is not None else "failed"
        gate = db.get(ItemAIGate, 1, populate_existing=True)
        if gate.active_id == request_id:
            gate.active_id = None
        db.commit()
        return record
    except Exception:
        db.rollback()
        raise


def uncertain(db, request_id):
    # Do NOT expire or release the gate on timeout; the upstream may still run.
    try:
        db.execute(update(ItemAssessment).where(
            ItemAssessment.id == request_id, ItemAssessment.actual_micros.is_(None),
        ).values(status="uncertain", failure_code="cost_unconfirmed"))
        db.commit()
    except Exception:
        db.rollback()
        raise


def budget_status(db, configured=True):
    month = month_key()
    row = db.get(ItemAIMonth, month)
    gate = db.get(ItemAIGate, 1)
    spent, held = (row.spent_micros, row.held_micros) if row else (0, 0)
    reason = None
    if not configured:
        reason = "not_configured"
    elif gate and gate.active_id:
        reason = "assessment_busy"
    elif spent + held + HOLD > TARGET:
        reason = "budget_paused"
    year, mon = map(int, month.split("-"))
    reset = datetime(year + (mon == 12), 1 if mon == 12 else mon + 1, 1, tzinfo=timezone.utc)
    money = lambda micros: Decimal(micros) / 1_000_000
    return BudgetResponse(month=month, spent=money(spent), reserved=money(held), target=money(TARGET),
                          reservation_per_run=money(HOLD), warning=spent + held >= WARNING,
                          available=reason is None, reason=reason,
                          message=PAUSED if reason == "budget_paused" else None,
                          resets_at=reset.isoformat())
