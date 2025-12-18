from fastapi import FastAPI
from app.api.v1 import analyze, scenarios, deals

app = FastAPI(
    title="FlipForge API",
    version="0.1.0"
)

app.include_router(analyze.router, prefix="/api/v1/analyze", tags=["Analyze"])
app.include_router(scenarios.router, prefix="/api/v1/scenarios", tags=["Scenarios"])
app.include_router(deals.router, prefix="/api/v1/deals", tags=["Deals"])

@app.get("/")
def root():
    return {"message": "FlipForge Backend Running"}
