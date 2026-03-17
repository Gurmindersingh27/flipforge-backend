from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON
from sqlalchemy import event
from datetime import datetime

from app.db.base import Base


def _json_column():
    """Return JSONB on Postgres, JSON on SQLite."""
    return JSONB().with_variant(JSON(), "sqlite")


class SavedDeal(Base):
    __tablename__ = "saved_deals"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)   # Clerk sub claim
    address = Column(Text, nullable=True)
    draft_input = Column(_json_column(), nullable=True)    # serialized DraftDeal
    analysis_result = Column(_json_column(), nullable=False)  # serialized AnalyzeResponse
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
