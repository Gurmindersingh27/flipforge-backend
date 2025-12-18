from pydantic import BaseModel
from app.schemas.analysis import DealInput, DealMetrics, DealScore


class ScenarioResult(BaseModel):
    name: str          # "base", "conservative", "aggressive"
    label: str         # nice label for UI
    input: DealInput
    metrics: DealMetrics
    score: DealScore


class ScenarioSet(BaseModel):
    base: ScenarioResult
    conservative: ScenarioResult
    aggressive: ScenarioResult
