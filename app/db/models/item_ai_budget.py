"""One durable global gate, with separate UTC monthly accounting."""
from sqlalchemy import CheckConstraint, Column, Integer, String

from app.db.base import Base


class ItemAIGate(Base):
    __tablename__ = "item_ai_gate"
    id = Column(Integer, primary_key=True)
    active_id = Column(String(36), nullable=True)
    __table_args__ = (CheckConstraint("id = 1"),)


class ItemAIMonth(Base):
    __tablename__ = "item_ai_months"
    month = Column(String(7), primary_key=True)
    spent_micros = Column(Integer, nullable=False, default=0)
    held_micros = Column(Integer, nullable=False, default=0)
    __table_args__ = (CheckConstraint("spent_micros >= 0 AND held_micros >= 0"),)
