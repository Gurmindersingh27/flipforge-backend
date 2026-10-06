"""Items saves are calculated on the server; reads never recalculate history."""
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models.saved_item import SavedItem
from app.db.models.item_assessment import SavedItemAssessment
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


def save_item(db: Session, user_id: str, body: SaveItemRequest) -> SavedItemResponse:
    try:
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
