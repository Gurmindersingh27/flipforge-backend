"""Companion tables only: no migration of saved_items or house tables."""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from app.db.base import Base
from app.db.models.saved_deal import _json_column


class ItemAssessment(Base):
    __tablename__ = "item_assessments"

    id = Column(String(36), primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    request_hash = Column(String(64), nullable=False)
    month = Column(String(7), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="processing")
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    reserved_micros = Column(Integer, nullable=False)
    actual_micros = Column(Integer, nullable=True)
    usage = Column(_json_column(), nullable=True)
    result = Column(_json_column(), nullable=True)
    failure_code = Column(String(40), nullable=True)


class SavedItemAssessment(Base):
    __tablename__ = "saved_item_assessments"

    item_id = Column(Integer, ForeignKey("saved_items.id"), primary_key=True)
    assessment_id = Column(String(36), ForeignKey("item_assessments.id"), nullable=False, index=True)
    # Copy the evidence plus confirmations into the immutable saved snapshot.
    context = Column(_json_column(), nullable=False)
