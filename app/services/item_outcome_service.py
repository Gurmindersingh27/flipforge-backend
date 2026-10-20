"""Append-only outcomes with root locking, stale-edit detection and safe replay."""
import hashlib
import sqlite3
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.item_outcome import ItemOutcomeRevision
from app.db.models.saved_item import SavedItem
from app.item_outcome_models import CASH_FIELDS, ItemOutcomeFacts, ItemOutcomeFigures, ItemOutcomeResponse, SaveItemOutcomeRequest


def calculate_outcome(facts: ItemOutcomeFacts) -> ItemOutcomeFigures:
    cash_missing = [name for name in CASH_FIELDS if getattr(facts, name) is None]
    sale_missing = ([] if facts.status == "sold" else ["status"]) + ([] if facts.sale_price is not None else ["sale_price"])
    profit_missing = cash_missing + sale_missing
    time_missing = profit_missing + [name for name in ("hours", "hourly_value") if getattr(facts, name) is None]
    days_missing = [name for name in ("listed_on", "sold_on") if getattr(facts, name) is None]
    held_missing = [name for name in ("purchased_on", "sold_on") if getattr(facts, name) is None]
    invested = None if cash_missing else sum((getattr(facts, name) for name in CASH_FIELDS), Decimal(0))
    profit = None if profit_missing else facts.sale_price - invested
    # As in Items estimates, keep decimal precision until MoneyResult serializes.
    after_time = None if time_missing else profit - facts.hours * facts.hourly_value
    return ItemOutcomeFigures(
        cash_invested=invested, cash_profit=profit, profit_after_time=after_time,
        days_to_sell=None if days_missing else (facts.sold_on - facts.listed_on).days,
        days_held=None if held_missing else (facts.sold_on - facts.purchased_on).days,
        missing_fields=dict(cash_invested=cash_missing, cash_profit=profit_missing,
                            profit_after_time=time_missing, days_to_sell=days_missing, days_held=held_missing),
    )


def outcome_response(row: ItemOutcomeRevision) -> ItemOutcomeResponse:
    return ItemOutcomeResponse(
        id=row.id, root_item_id=row.root_item_id, based_on_item_id=row.based_on_item_id,
        previous_id=row.previous_id, revision_number=row.revision_number,
        schema_version=row.schema_version, created_at=row.created_at,
        inputs=row.inputs, calculated=row.calculated,
    )


def _owned_root(db: Session, user_id: str, root_id: int, *, lock: bool = False) -> SavedItem:
    query = db.query(SavedItem).filter(SavedItem.id == root_id, SavedItem.root_item_id == root_id,
                                       SavedItem.user_id == user_id)
    row = (query.with_for_update() if lock else query).first()
    if row is None:
        raise HTTPException(404, "Item not found.")
    return row


def _latest(db: Session, user_id: str, root_id: int) -> ItemOutcomeRevision | None:
    return (db.query(ItemOutcomeRevision)
            .filter(ItemOutcomeRevision.root_item_id == root_id, ItemOutcomeRevision.user_id == user_id)
            .order_by(ItemOutcomeRevision.revision_number.desc()).first())


def latest_outcome(db: Session, user_id: str, root_id: int) -> ItemOutcomeResponse | None:
    _owned_root(db, user_id, root_id)
    row = _latest(db, user_id, root_id)
    return outcome_response(row) if row is not None else None


def _replay(row: ItemOutcomeRevision, user_id: str, root_id: int, fingerprint: str) -> ItemOutcomeResponse:
    if row.user_id != user_id or row.root_item_id != root_id or row.request_hash != fingerprint:
        raise HTTPException(409, "Outcome request key already used. Reload the outcome before editing.")
    return outcome_response(row)


def _request_key_collision(error: IntegrityError, dialect: str) -> bool:
    original = error.orig
    if dialect == "postgresql":
        return (getattr(original, "pgcode", None) == "23505"
                and getattr(getattr(original, "diag", None), "constraint_name", None) == "pk_item_outcome_revisions")
    if dialect == "sqlite":
        return (getattr(original, "sqlite_errorcode", None) in
                (sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY, sqlite3.SQLITE_CONSTRAINT_UNIQUE)
                and str(original) == "UNIQUE constraint failed: item_outcome_revisions.id")
    return False


def save_outcome(db: Session, user_id: str, root_id: int, body: SaveItemOutcomeRequest) -> ItemOutcomeResponse:
    try:
        request_id = str(body.request_id)
        fingerprint = hashlib.sha256(body.model_dump_json(exclude={"request_id"}, exclude_unset=True).encode()).hexdigest()
        previous = db.get(ItemOutcomeRevision, request_id)
        if previous is not None:
            return _replay(previous, user_id, root_id, fingerprint)
        # Lock the existing immutable root, not an absent outcome row. No root UPDATE.
        # PostgreSQL READ COMMITTED makes the next SELECT see the preceding writer.
        _owned_root(db, user_id, root_id, lock=True)
        previous = db.get(ItemOutcomeRevision, request_id)
        if previous is not None:
            response = _replay(previous, user_id, root_id, fingerprint)
            db.rollback()  # Release our root lock; this path never writes.
            return response
        latest = _latest(db, user_id, root_id)
        expected = str(body.expected_previous_id) if body.expected_previous_id is not None else None
        if expected != (latest.id if latest is not None else None):
            raise HTTPException(409, "Outcome changed. Reload it before saving your edit.")
        if body.based_on_item_id is not None:
            basis = (db.query(SavedItem).filter(SavedItem.id == body.based_on_item_id,
                     SavedItem.user_id == user_id, SavedItem.root_item_id == root_id).first())
            if basis is None:
                raise HTTPException(404, "Item not found.")
        facts = ItemOutcomeFacts.model_validate(body.model_dump(mode="json", exclude={
            "request_id", "expected_previous_id", "based_on_item_id",
        }))
        row = ItemOutcomeRevision(
            id=request_id, user_id=user_id, root_item_id=root_id, based_on_item_id=body.based_on_item_id,
            previous_id=expected, revision_number=latest.revision_number + 1 if latest is not None else 1,
            schema_version=1, request_hash=fingerprint, inputs=facts.model_dump(mode="json"),
            calculated=calculate_outcome(facts).model_dump(mode="json"),
        )
        db.add(row)
        try:
            db.flush([row])
        except IntegrityError as error:
            if not _request_key_collision(error, db.bind.dialect.name):
                raise
            # Different roots can race for the global request UUID despite root locks.
            db.rollback()
            winner = db.get(ItemOutcomeRevision, request_id)
            if winner is None:
                raise
            return _replay(winner, user_id, root_id, fingerprint)
        response = outcome_response(row)
        db.commit()
        return response
    except Exception:
        db.rollback()
        raise
