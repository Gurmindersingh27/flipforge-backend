from sqlalchemy import Column, Integer, ForeignKey, Text
from app.db.base import Base
from app.db.models.saved_deal import _json_column


class DealRevision(Base):
    """Additive metadata; existing saved_deals rows/columns stay unchanged."""
    __tablename__ = "deal_revisions"
    deal_id = Column(Integer, ForeignKey("saved_deals.id"), primary_key=True)
    parent_deal_id = Column(Integer, ForeignKey("saved_deals.id"), nullable=True, index=True)
    rehab_scope = Column(_json_column(), nullable=True)
    revision_note = Column(Text, nullable=False, default="")
