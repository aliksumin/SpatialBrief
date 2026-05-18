from fastapi import APIRouter, Request
from fastapi.responses import Response
from typing import Any, Dict, List
import logging
import traceback

from app.ai_agents.rhino_exporter import export_to_dxf, _try_rhino3dm_export

router = APIRouter()
log = logging.getLogger(__name__)


@router.post("/export/rhino")
async def export_rhino(request: Request):
    """
    Export geometry as a layered DXF file (Rhino-compatible).

    Expects JSON body with:
    - geometry: list of geometry objects (raw_vector_objects)
    - constraints: optional list of constraint objects
    - project_name: optional project name
    - format: "dxf" (default) or "3dm" (requires rhino3dm)

    Returns the file as a binary download.
    """
    try:
        body = await request.json()

        geometry: List[Dict[str, Any]] = body.get("geometry", [])
        constraints: List[Dict[str, Any]] = body.get("constraints", [])
        project_name: str = body.get("project_name", "SpatialBrief Export")
        export_format: str = body.get("format", "dxf")

        if not geometry:
            return Response(
                content='{"error": "No geometry data provided"}',
                status_code=400,
                media_type="application/json",
            )

        log.info(
            "[Export] Exporting %d objects, %d constraints, format=%s",
            len(geometry), len(constraints), export_format,
        )

        # Try .3dm first if requested and available
        if export_format == "3dm":
            result = _try_rhino3dm_export(geometry, constraints)
            if result:
                return Response(
                    content=result,
                    media_type="application/octet-stream",
                    headers={
                        "Content-Disposition": 'attachment; filename="zoning_massing.3dm"',
                    },
                )
            log.info("[Export] Falling back to DXF (rhino3dm not available)")

        # DXF export (always available)
        dxf_bytes = export_to_dxf(geometry, constraints, project_name)

        return Response(
            content=dxf_bytes,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": 'attachment; filename="zoning_massing.dxf"',
            },
        )
    except Exception as e:
        log.error("[Export] Error: %s\n%s", e, traceback.format_exc())
        return Response(
            content=f'{{"error": "{str(e)}"}}',
            status_code=500,
            media_type="application/json",
        )
