from sqlalchemy import Column, Integer, Float, String, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db.base import Base


class DealAnalysis(Base):
    __tablename__ = "deal_analyses"

    id = Column(Integer, primary_key=True, index=True)
    deal_id = Column(Integer, ForeignKey("deals.id"))

    purchase_price = Column(Float)
    arv = Column(Float)
    rehab_cost = Column(Float)
    total_cost = Column(Float)

    profit = Column(Float)
    margin_pct = Column(Float)
    roi_pct = Column(Float)
    annualized_roi_pct = Column(Float)

    max_offer_price_mao = Column(Float)
    breakeven_arv = Column(Float)

    score = Column(Integer)
    grade = Column(String)
    verdict = Column(String)
    risk_level = Column(String)

    subscores = Column(JSON)
    flags = Column(JSON)
    risk_notes = Column(JSON)
    profile_fit_score = Column(Integer)
    profile_fit_notes = Column(JSON)

    created_at = Column(DateTime, default=datetime.utcnow)

    deal = relationship("Deal", back_populates="analyses")
