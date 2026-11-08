"""Append-only inspection snapshots with owner checks, safe replay and root locks."""
import hashlib
import json
import sqlite3

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.item_inspection import ItemInspectionRevision
from app.db.models.saved_item import SavedItem
from app.item_inspection_models import ItemInspectionFacts, ItemInspectionResponse, SaveItemInspectionRequest


def inspection_response(row: ItemInspectionRevision) -> ItemInspectionResponse:
    return ItemInspectionResponse(
        id=row.id, root_item_id=row.root_item_id, previous_id=row.previous_id,
        revision_number=row.revision_number, schema_version=row.schema_version,
        created_at=row.created_at, inputs=row.inputs,
    )


def _owned_root(db: Session, user_id: str, root_id: int, *, lock: bool = False) -> SavedItem:
    query = db.query(SavedItem).filter(SavedItem.id == root_id, SavedItem.root_item_id == root_id,
                                      SavedItem.user_id == user_id)
    row = (query.with_for_update() if lock else query).first()
    if row is None:
        raise HTTPException(404, "Item not found.")
    return row


def _latest(db: Session, user_id: str, root_id: int) -> ItemInspectionRevision | None:
    return (db.query(ItemInspectionRevision)
            .filter(ItemInspectionRevision.root_item_id == root_id, ItemInspectionRevision.user_id == user_id)
            .order_by(ItemInspectionRevision.revision_number.desc()).first())


def latest_inspection(db: Session, user_id: str, root_id: int) -> ItemInspectionResponse | None:
    _owned_root(db, user_id, root_id)
    row = _latest(db, user_id, root_id)
    return inspection_response(row) if row is not None else None


def _replay(row: ItemInspectionRevision, user_id: str, root_id: int, fingerprint: str) -> ItemInspectionResponse:
    if row.user_id != user_id or row.root_item_id != root_id or row.request_hash != fingerprint:
        raise HTTPException(409, "Inspection request key already used. Reload the inspection before editing.")
    return inspection_response(row)


def _request_key_collision(error: IntegrityError, dialect: str) -> bool:
    original = error.orig
    if dialect == "postgresql":
        return (getattr(original, "pgcode", None) == "23505"
                and getattr(getattr(original, "diag", None), "constraint_name", None) == "pk_item_inspection_revisions")
    if dialect == "sqlite":
        return (getattr(original, "sqlite_errorcode", None) in
                (sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY, sqlite3.SQLITE_CONSTRAINT_UNIQUE)
                and str(original) == "UNIQUE constraint failed: item_inspection_revisions.id")
    return False


def save_inspection(db: Session, user_id: str, root_id: int, body: SaveItemInspectionRequest) -> ItemInspectionResponse:
    try:
        request_id = str(body.request_id)
        normalized = body.model_dump(mode="json", exclude={"request_id"})
        fingerprint = hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        previous = db.get(ItemInspectionRevision, request_id)
        if previous is not None:
            return _replay(previous, user_id, root_id, fingerprint)
        # Serialize on the existing root, including concurrent first saves. This
        # never UPDATEs a saved estimate. PostgreSQL uses READ COMMITTED.
        _owned_root(db, user_id, root_id, lock=True)
        previous = db.get(ItemInspectionRevision, request_id)
        if previous is not None:
            response = _replay(previous, user_id, root_id, fingerprint)
            db.rollback()
            return response
        latest = _latest(db, user_id, root_id)
        expected = str(body.expected_previous_id) if body.expected_previous_id is not None else None
        if expected != (latest.id if latest is not None else None):
            raise HTTPException(409, "Inspection changed. Reload it before saving your edit.")
        facts = ItemInspectionFacts.model_validate(body.model_dump(mode="json", exclude={"request_id", "expected_previous_id"}))
        row = ItemInspectionRevision(
            id=request_id, user_id=user_id, root_item_id=root_id, previous_id=expected,
            revision_number=latest.revision_number + 1 if latest is not None else 1,
            schema_version=1, request_hash=fingerprint, inputs=facts.model_dump(mode="json"),
        )
        db.add(row)
        try:
            db.flush([row])
        except IntegrityError as error:
            if not _request_key_collision(error, db.bind.dialect.name):
                raise
            # Root locks do not serialize the same UUID used on different items.
            db.rollback()
            winner = db.get(ItemInspectionRevision, request_id)
            if winner is None:
                raise
            return _replay(winner, user_id, root_id, fingerprint)
        response = inspection_response(row)
        db.commit()
        return response
    except Exception:
        db.rollback()
        raise
