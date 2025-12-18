from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models.deal import Deal
from app.schemas.deal import DealListItem, DealDashboardResponse
from app.services.deal_service import get_deal_dashboard

router = APIRouter()


@router.get("/", response_model=List[DealListItem])
def list_deals(db: Session = Depends(get_db)) -> List[DealListItem]:
    """
    Return a simple list of deals, newest first.
    """
    deals = db.query(Deal).order_by(Deal.created_at.desc()).all()
    return deals


@router.get("/{deal_id}", response_model=DealDashboardResponse)
def get_deal(deal_id: int, db: Session = Depends(get_db)) -> DealDashboardResponse:
    """
    Return full dashboard payload for a single deal:
    - basic deal info
    - latest analysis (metrics + score)
    """
    return get_deal_dashboard(deal_id=deal_id, db=db)
