from typing import List, Optional
from pydantic import BaseModel, Field

class RuleSource(BaseModel):
    file: str
    page: Optional[int] = None
    section: str = ""
    bbox: Optional[List[float]] = None

class RuleObject(BaseModel):
    id: str
    name: str
    category: str = Field(description="use | height | setback | density | programme | parking | environmental | facade | heritage | access | public_realm | other")
    rule_text_summary: str
    raw_quote: str
    value: Optional[float] = None
    unit: str = ""
    condition: str = ""
    applies_to: str = ""
    status: str = Field(description="extracted_exact | extracted_interpreted | inferred | assumed | missing")
    binding_level: str = Field(description="binding | guideline | explanatory | inferred | unknown")
    source: RuleSource
    linked_geometry_ids: List[str] = []
    confidence: str = Field(description="high | medium | low")
    warnings: List[str] = []
