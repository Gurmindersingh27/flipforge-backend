"""Append-only actuals; existing estimates and house tables are unchanged."""
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, PrimaryKeyConstraint, String, UniqueConstraint

from app.db.base import Base
from app.db.models.saved_deal import _json_column


class ItemOutcomeRevision(Base):
    __tablename__ = "item_outcome_revisions"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_item_outcome_revisions"),
        UniqueConstraint("root_item_id", "revision_number", name="uq_item_outcome_revision_number"),
        CheckConstraint("revision_number > 0", name="ck_item_outcome_positive_revision"),
    )

    # The client's request UUID is also the immutable revision identity.
    id = Column(String(36), nullable=False)
    user_id = Column(String, nullable=False, index=True)
    root_item_id = Column(Integer, ForeignKey("saved_items.id"), nullable=False)
    based_on_item_id = Column(Integer, ForeignKey("saved_items.id"), nullable=True)
    previous_id = Column(String(36), ForeignKey("item_outcome_revisions.id"), nullable=True)
    revision_number = Column(Integer, nullable=False)
    schema_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    request_hash = Column(String(64), nullable=False)
    inputs = Column(_json_column(), nullable=False)
    calculated = Column(_json_column(), nullable=False)
