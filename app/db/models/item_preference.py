"""Replaceable account preferences, separate from estimates and actuals."""
from sqlalchemy import Column, DateTime, Numeric, String

from app.db.base import Base


class ItemPreference(Base):
    __tablename__ = "item_preferences"

    user_id = Column(String, primary_key=True)
    hourly_value = Column(Numeric(6, 2), nullable=True)
    target_profit = Column(Numeric(9, 2), nullable=True)
    contingency_pct = Column(Numeric(7, 6), nullable=True)
    fee_pct = Column(Numeric(7, 6), nullable=True)
    pickup = Column(Numeric(9, 2), nullable=True)
    fee_fixed = Column(Numeric(9, 2), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False)
