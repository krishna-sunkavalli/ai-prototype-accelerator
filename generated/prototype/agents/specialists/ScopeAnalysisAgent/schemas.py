from pydantic import BaseModel
from typing import List, Optional


class RiskItem(BaseModel):
    title: str
    severity: str  # "low" | "medium" | "high"
    rationale: Optional[str] = None


class ScopeAnalysisAgentResponse(BaseModel):
    summary: str
    confidence: float
    data_sources: List[str]
    recommended_action: str
    scope_items: List[str] = []
    risks: List[RiskItem] = []
    exclusions: List[str] = []
    clarifications: List[str] = []
