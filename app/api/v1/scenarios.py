from fastapi import APIRouter
from app.schemas.analysis import DealInput
from app.schemas.scenario import ScenarioSet
from app.services.scenario_service import generate_scenarios

router = APIRouter()


@router.post("/deal", response_model=ScenarioSet)
def scenarios_for_deal(payload: DealInput) -> ScenarioSet:
    """
    Given a base deal input, return Base / Conservative / Aggressive scenarios.
    """
    return generate_scenarios(payload)
