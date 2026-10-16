"""Items saves are calculated on the server; reads never recalculate history."""
import hashlib
import sqlite3

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.saved_item import SavedItem
from app.db.models.item_assessment import SavedItemAssessment
from app.db.models.item_save_request import ItemSaveRequest
from sqlalchemy.orm import object_session
from app.item_analysis_engine import analyze_item
from app.saved_item_models import SaveItemRequest, SavedItemListResponse, SavedItemResponse, saved_inputs


def owned_item(db: Session, user_id: str, item_id: int) -> SavedItem:
    record = db.query(SavedItem).filter(SavedItem.id == item_id, SavedItem.user_id == user_id).first()
    if record is None:
        raise HTTPException(404, "Item not found.")
    return record


def item_response(record: SavedItem) -> SavedItemResponse:
    db = object_session(record)
    context = db.get(SavedItemAssessment, record.id) if db is not None else None
    return SavedItemResponse(
        id=record.id, schema_version=record.schema_version, created_at=record.created_at,
        parent_item_id=record.parent_item_id, root_item_id=record.root_item_id,
        inputs=record.inputs, analysis_result=record.analysis_result,
        listing_url=record.listing_url, notes=record.notes,
        assessment=context.context if context else None,
    )


def _replay(db: Session, request: ItemSaveRequest, user_id: str, fingerprint: str) -> SavedItemResponse:
    if request.user_id != user_id or request.request_hash != fingerprint:
        # The same response for both conflicts; never disclose a saved item ID.
        raise HTTPException(409, "Save request key already used. Check My Flips before saving again.")
    record = db.get(SavedItem, request.item_id)
    if record is None or record.user_id != user_id:
        raise HTTPException(404, "Item not found.")
    return item_response(record)


def _request_key_collision(error: IntegrityError, dialect: str) -> bool:
    """Only the mapping's primary key can turn an INSERT failure into a replay."""
    original = error.orig
    if dialect == "postgresql":
        return (getattr(original, "pgcode", None) == "23505"
                and getattr(getattr(original, "diag", None), "constraint_name", None)
                == "pk_item_save_requests")
    if dialect == "sqlite":
        return (getattr(original, "sqlite_errorcode", None) in
                (sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY, sqlite3.SQLITE_CONSTRAINT_UNIQUE)
                and str(original) == "UNIQUE constraint failed: item_save_requests.request_id")
    return False


def save_item(db: Session, user_id: str, body: SaveItemRequest) -> SavedItemResponse:
    try:
        request_id = str(body.request_id) if body.request_id is not None else None
        # Pydantic field order is stable. Keep field presence recursively:
        # an omitted input/default must not replay a save with explicit null/0.
        fingerprint = hashlib.sha256(body.model_dump_json(
            exclude={"request_id"}, exclude_unset=True,
        ).encode()).hexdigest() if request_id is not None else None
        if request_id is not None:
            previous = db.get(ItemSaveRequest, request_id)
            if previous is not None:
                return _replay(db, previous, user_id, fingerprint)
        # Local import keeps the public calculator independent of AI setup.
        from app.services.item_assessment_service import saved_context
        if body.assessment_id is None and body.assessment_confirmation is not None:
            raise HTTPException(422, "assessment_confirmation requires assessment_id.")
        context = saved_context(db, user_id, body) if body.assessment_id is not None else None
        parent = owned_item(db, user_id, body.parent_item_id) if body.parent_item_id is not None else None
        if parent is not None:
            owned_item(db, user_id, parent.root_item_id)
        result = analyze_item(body.inputs)
        record = SavedItem(
            user_id=user_id, schema_version=1, inputs=saved_inputs(body.inputs),
            analysis_result=result.model_dump(mode="json"), listing_url=body.listing_url,
            notes=body.notes, parent_item_id=body.parent_item_id,
            root_item_id=parent.root_item_id if parent is not None else 0,
        )
        db.add(record)
        db.flush()
        if parent is None:
            record.root_item_id = record.id
        if context is not None:
            db.add(SavedItemAssessment(item_id=record.id, assessment_id=str(body.assessment_id), context=context))
        db.flush()
        if request_id is not None:
            mapping = ItemSaveRequest(request_id=request_id, user_id=user_id,
                                      request_hash=fingerprint, item_id=record.id)
            db.add(mapping)
            try:
                db.flush([mapping])
            except IntegrityError as error:
                if not _request_key_collision(error, db.bind.dialect.name):
                    raise
                # Another transaction won. Roll back our snapshot/evidence too,
                # then read the winner in a fresh transaction (Postgres READ COMMITTED).
                db.rollback()
                previous = db.get(ItemSaveRequest, request_id)
                if previous is None:
                    raise
                return _replay(db, previous, user_id, fingerprint)
        response = item_response(record)
        db.commit()
        return response
    except Exception:
        db.rollback()
        raise


def list_items(db: Session, user_id: str, limit: int, offset: int) -> SavedItemListResponse:
    records = (db.query(SavedItem).filter(SavedItem.user_id == user_id)
               .order_by(SavedItem.created_at.desc(), SavedItem.id.desc())
               .offset(offset).limit(limit + 1).all())
    return SavedItemListResponse(
        items=[item_response(record) for record in records[:limit]], limit=limit, offset=offset,
        next_offset=offset + limit if len(records) > limit else None,
    )
