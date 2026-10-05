"""New table only; committed rows are immutable application snapshots."""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.db.base import Base
from app.db.models.saved_deal import _json_column


class SavedItem(Base):
    __tablename__ = "saved_items"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    schema_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    inputs = Column(_json_column(), nullable=False)
    analysis_result = Column(_json_column(), nullable=False)
    listing_url = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    parent_item_id = Column(Integer, ForeignKey("saved_items.id"), nullable=True, index=True)
    # A new root needs its database-generated ID. The service replaces a private
    # transaction-local sentinel after INSERT, before COMMIT. The deferred FK
    # prevents committing that sentinel; all persisted rows have a real root.
    root_item_id = Column(Integer, ForeignKey(
        "saved_items.id", deferrable=True, initially="DEFERRED",
    ), nullable=False, index=True)
