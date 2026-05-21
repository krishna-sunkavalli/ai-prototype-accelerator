from pydantic import BaseModel
from typing import List, Optional


class LaborRateRow(BaseModel):
    id: Optional[str] = None
    region: str
    trade: str
    rate_usd_per_hour: float
    productivity_factor: Optional[float] = None
    effective_date: Optional[str] = None
    source: Optional[str] = None


class CrewHourEstimate(BaseModel):
    trade: str
    hours: float
    crew_size: Optional[int] = None
    assumptions: Optional[str] = None


class LaborRatesAgentResponse(BaseModel):
    summary: str
    confidence: float
    data_sources: List[str]
    recommended_action: str
    labor_rates: List[LaborRateRow] = []
    crew_hour_estimate: Optional[CrewHourEstimate] = None
