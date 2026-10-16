"""Request identities for Items saves; existing snapshot tables stay unchanged."""
from sqlalchemy import Column, ForeignKey, Integer, PrimaryKeyConstraint, String

from app.db.base import Base


class ItemSaveRequest(Base):
    __tablename__ = "item_save_requests"
    __table_args__ = (PrimaryKeyConstraint("request_id", name="pk_item_save_requests"),)

    request_id = Column(String(36), nullable=False)
    user_id = Column(String, nullable=False)
    request_hash = Column(String(64), nullable=False)
    item_id = Column(Integer, ForeignKey("saved_items.id"), nullable=False)
