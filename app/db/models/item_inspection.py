"""Append-only inspections belonging to an existing immutable item root."""
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, PrimaryKeyConstraint, String, UniqueConstraint

from app.db.base import Base
from app.db.models.saved_deal import _json_column


class ItemInspectionRevision(Base):
    __tablename__ = "item_inspection_revisions"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_item_inspection_revisions"),
        UniqueConstraint("root_item_id", "revision_number", name="uq_item_inspection_revision_number"),
        CheckConstraint("revision_number > 0", name="ck_item_inspection_positive_revision"),
    )

    id = Column(String(36), nullable=False)
    user_id = Column(String, nullable=False, index=True)
    root_item_id = Column(Integer, ForeignKey("saved_items.id"), nullable=False)
    previous_id = Column(String(36), ForeignKey("item_inspection_revisions.id"), nullable=True)
    revision_number = Column(Integer, nullable=False)
    schema_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    request_hash = Column(String(64), nullable=False)
    inputs = Column(_json_column(), nullable=False)
