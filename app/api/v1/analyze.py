from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.schemas.analysis import DealInput, AnalyzeDealResponse
from app.services.analyze_service import analyze_deal_service
from app.db.session import get_db

router = APIRouter()


@router.get("/test")
def analyze_test():
    return {"status": "ok", "message": "Analyze endpoint is wired up"}


@router.post("/deal", response_model=AnalyzeDealResponse)
def analyze_deal(
    payload: DealInput,
    db: Session = Depends(get_db),
) -> AnalyzeDealResponse:
    """
    Analyze a deal and persist the result to the database.
    """
    return analyze_deal_service(payload, db=db)
