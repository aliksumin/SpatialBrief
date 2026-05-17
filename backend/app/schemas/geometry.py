from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field

class GeometryStyle(BaseModel):
    stroke_color: Optional[str] = ""
    fill_color: Optional[str] = ""
    line_type: Optional[str] = ""
    hatch: Optional[str] = ""

class GeometryObject(BaseModel):
    id: str
    name: str
    semantic_type: str
    source_type: str = Field(description="dwg | dxf | vector_pdf | gis | raster | manual | generated")
    source_file: str
    source_page_or_layout: str
    source_layer: str
    source_label: str
    geometry_type: str = Field(description="point | polyline | polygon | mesh | brep | text_anchor | dimension")
    coordinates_local: List[Any]
    coordinates_world: List[Any]
    unit: str
    style: GeometryStyle
    linked_labels: List[str] = []
    linked_rules: List[str] = []
    confidence: str = Field(description="high | medium | low")
    is_binding_source_geometry: bool = True
    is_generated_geometry: bool = False
    validation_status: str = Field(description="unreviewed | accepted | needs_review | rejected")
    warnings: List[str] = []
