from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from .geometry import GeometryObject
from .rules import RuleObject

class ProjectInfo(BaseModel):
    name: str = ""
    location: str = ""
    country: str = ""
    city: str = ""
    jurisdiction: str = ""
    coordinate_system: str = ""
    input_language: str = ""
    detected_project_type: str = ""
    source_documents: List[str] = []

class InputBundleAnalysis(BaseModel):
    files: List[str] = []
    detected_roles: List[str] = []
    missing_expected_inputs: List[str] = []
    classification_confidence: str = ""

class Objective(BaseModel):
    summary: str = ""
    design_intent: str = ""
    run_goal: str = ""
    source_status: str = Field(description="extracted | inferred | assumed | missing")

class RulesCollection(BaseModel):
    regulatory_framework: str = ""
    rule_objects: List[RuleObject] = []
    definitions: List[str] = []
    permitted_uses: List[str] = []
    prohibited_uses: List[str] = []
    numerical_constraints: List[str] = []
    geometric_constraints: List[str] = []
    programme_constraints: List[str] = []
    environmental_constraints: List[str] = []
    mobility_constraints: List[str] = []
    exceptions: List[str] = []
    source_references: List[str] = []

class GeometryCollection(BaseModel):
    source_files: List[str] = []
    coordinate_system: str = ""
    unit_system: str = ""
    calibration: Dict[str, Any] = {}
    raw_vector_objects: List[Any] = []
    semantic_objects_2d: List[GeometryObject] = []
    linked_rule_geometry_objects: List[Any] = []
    generated_constraint_volumes_3d: List[Any] = []
    manual_overrides: List[Any] = []
    low_confidence_objects: List[Any] = []

class RuleGeometryLink(BaseModel):
    id: str = ""
    rule_id: str = ""
    geometry_id: str = ""
    relation_type: str = ""
    evidence: List[str] = []
    confidence: str = Field(description="high | medium | low")
    warnings: List[str] = []

class Programme(BaseModel):
    source_status: str = Field(description="extracted | inferred | assumed | missing")
    total_gfa_m2: Optional[float] = None
    programme_components: List[Any] = []
    unit_mix: List[Any] = []
    parking: Dict[str, Any] = {}
    reasoning: str = ""
    assumptions: List[str] = []

class Variables(BaseModel):
    massing_variables: List[Any] = []
    programme_variables: List[Any] = []
    mobility_variables: List[Any] = []
    geometry_variables: List[Any] = []

class KPIs(BaseModel):
    suggested: List[Any] = []

class ViewportScene(BaseModel):
    objects: List[Any] = []
    layers: List[Any] = []
    stage_views: List[Any] = []
    camera_defaults: Dict[str, Any] = {}
    legends: List[Any] = []
    warnings: List[str] = []

class GrasshopperHandoff(BaseModel):
    parameters: List[Any] = []
    geometry_layers: List[Any] = []
    recommended_workflow: str = ""
    notes_for_engineer: List[str] = []

class DesignInputs(BaseModel):
    project: ProjectInfo
    input_bundle_analysis: InputBundleAnalysis
    objective: Objective
    rules: RulesCollection
    geometry: GeometryCollection
    rule_geometry_links: List[RuleGeometryLink] = []
    programme: Programme
    variables: Variables
    kpis: KPIs
    viewport_scene: ViewportScene
    grasshopper_handoff: GrasshopperHandoff
    assumptions: List[str] = []
    ambiguities: List[str] = []
    conflicts: List[str] = []
    validation_checklist: List[str] = []
    source_index: List[str] = []
