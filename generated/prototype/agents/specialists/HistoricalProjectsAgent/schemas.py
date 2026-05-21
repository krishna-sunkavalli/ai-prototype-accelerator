from pydantic import BaseModel
from typing import List, Optional


class ProjectRow(BaseModel):
    id: str
    project_name: str
    sector: Optional[str] = None
    location: Optional[str] = None
    year_completed: Optional[int] = None
    square_footage: Optional[int] = None
    final_cost_usd: Optional[float] = None
    cost_per_sqft_usd: Optional[float] = None
    schedule_months: Optional[int] = None
    outcome_notes: Optional[str] = None


class HistoricalProjectsAgentResponse(BaseModel):
    summary: str
    confidence: float
    data_sources: List[str]
    recommended_action: str
    projects: List[ProjectRow] = []
    cost_summary: Optional[dict] = None
    lessons_learned: Optional[List[str]] = None
