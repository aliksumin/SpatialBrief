from fastapi import APIRouter, UploadFile, File, HTTPException, Request
from fastapi.responses import StreamingResponse
from typing import List
import asyncio
import json
import shutil
import os

from app.vector_ingestion.pdf_vector_extractor import extract_vectors_from_pdf
from app.vector_ingestion.pipeline_stages import run_extract_programme, run_detect_units
from app.vector_ingestion.cad_extractor import extract_from_dwg
from app.ai_agents.constraint_extractor import extract_constraints
from app.ai_agents.programme_extractor import extract_programme
from app.ai_agents.volume_generator import generate_volumes
from app.ai_agents.cost_tracker import CostTracker
from app.config import settings

router = APIRouter()

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.post("/upload")
async def upload_files_only(files: List[UploadFile] = File(...)):
    """Node 1 â€” Save files to disk only. No processing. Returns filenames."""
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    saved_files = []
    for file in files:
        file_location = os.path.join(UPLOAD_DIR, file.filename)
        with open(file_location, "wb+") as file_object:
            shutil.copyfileobj(file.file, file_object)
        saved_files.append(file.filename)

    return {
        "status": "success",
        "filenames": saved_files,
        "message": f"{len(saved_files)} files saved. Ready for processing.",
    }


@router.post("/process")
async def run_pipeline(request: Request):
    """
    Run the 9-node extraction pipeline, streaming progress via NDJSON.

    Each line is a JSON object: {"node": N, "result": {accumulated data}}
    The final line has {"node": "done"}.
    """
    body = await request.json()
    filenames = body.get("filenames", [])
    if not filenames:
        raise HTTPException(status_code=400, detail="No filenames provided")

    # Parse all config from headers
    api_key = (request.headers.get("X-Gemini-Api-Key") or settings.GEMINI_API_KEY or "").strip() or None
    gemini_model = (request.headers.get("X-Gemini-Model") or "").strip() or None
    agent_visual_model = (request.headers.get("X-Agent-Visual-Model") or "").strip() or None
    agent_geometric_model = (request.headers.get("X-Agent-Geometric-Model") or "").strip() or None
    agent_contextual_model = (request.headers.get("X-Agent-Contextual-Model") or "").strip() or None
    agent_judge_model = (request.headers.get("X-Agent-Judge-Model") or "").strip() or None
    resolved_model = gemini_model or "gemini-2.5-flash"

    async def event_stream():
        cost_tracker = CostTracker()
        extracted_geometry: list = []
        extracted_text_blocks: list = []
        zone_summary: dict = {}
        total_objects = 0
        classification_mode = "no_files"
        ai_error_detail = None
        ai_models: dict = {}
        constraint_result: dict = {}
        programme_result: dict = {}
        volume_result: dict = {}

        def _safe_json(obj):
            """Serialize to JSON, stripping non-serializable keys."""
            def _clean(o):
                if isinstance(o, dict):
                    return {k: _clean(v) for k, v in o.items()
                            if k not in ("shapely_poly", "_shape_metrics",
                                         "shapely_centroid", "_classification_guess")}
                elif isinstance(o, list):
                    return [_clean(item) for item in o]
                elif isinstance(o, (str, int, float, bool, type(None))):
                    return o
                else:
                    try:
                        json.dumps(o)
                        return o
                    except (TypeError, ValueError):
                        return str(o)
            return json.dumps(_clean(obj))

        def snapshot(extra=None):
            """Build the current accumulated response."""
            geo = {
                "source_files": filenames,
                "extracted_objects": total_objects,
                "raw_vector_objects": extracted_geometry,
                "extracted_text": extracted_text_blocks,
                "zone_summary": zone_summary,
                "classification_mode": classification_mode,
                "ai_models": ai_models,
                "semantic_objects_2d": [],
                "constraints": constraint_result.get("constraints", []),
                "constraint_summary": constraint_result.get("extraction_summary", {}),
                "programmes": programme_result.get("programmes", []),
                "site_programme": programme_result.get("site_programme", {}),
                "programme_summary": programme_result.get("extraction_summary", {}),
                "volumes": volume_result.get("volumes", []),
                "volume_summary": volume_result.get("volume_summary", {}),
                "zone_rules": constraint_result.get("zone_rules", []),
                "zone_programmes": programme_result.get("zone_programmes", []),
                "cost_summary": cost_tracker.summary(),
            }
            if extra:
                geo.update(extra)
            resp = {
                "status": "success",
                "filenames": filenames,
                "classification_mode": classification_mode,
                "ai_models": ai_models,
                "cost_summary": cost_tracker.summary(),
                "geometry": geo,
            }
            if ai_error_detail:
                resp["ai_error_detail"] = ai_error_detail
                resp["geometry"]["ai_error_detail"] = ai_error_detail
            return resp

        loop = asyncio.get_event_loop()

        for filename in filenames:
            file_location = os.path.join(UPLOAD_DIR, filename)
            if not os.path.exists(file_location):
                continue

            filename_lower = filename.lower()
            if filename_lower.endswith('.pdf'):
                import fitz
                doc = fitz.open(file_location)
                if len(doc) == 0:
                    doc.close()
                    continue
                page = doc[0]
                pw, ph = page.rect.width, page.rect.height
                pa = pw * ph

                # â”€â”€ Node 3: Extract Programme â”€â”€
                programme_stage = await loop.run_in_executor(
                    None,
                    lambda: run_extract_programme(
                        page, pa,
                        api_key=api_key,
                        model_name=resolved_model,
                        cost_tracker=cost_tracker,
                    ),
                )
                site_brief = programme_stage.get("site_brief")
                node3_text_blocks = programme_stage.get("text_blocks", [])
                extracted_text_blocks.extend([{"source": filename, **t} for t in node3_text_blocks])
                yield _safe_json({"node": 3, "result": snapshot()}) + "\n"

                # — Node 4: Detect Units & Coordinates —
                units_info = run_detect_units(page, node3_text_blocks)
                doc.close()
                yield _safe_json({"node": 4, "result": snapshot()}) + "\n"

                # ── Node 5: Extract Constraints (from text — no vectors needed) ──
                constraint_result = await loop.run_in_executor(
                    None,
                    lambda: extract_constraints(
                        text_blocks=extracted_text_blocks,
                        zones=[],  # no vectors yet
                        api_key=api_key,
                        model_name=resolved_model,
                        cost_tracker=cost_tracker,
                        site_brief=site_brief,
                    ),
                )
                if api_key:
                    if constraint_result.get("extraction_summary", {}).get("ai_extracted", 0) > 0 or \
                       constraint_result.get("extraction_summary", {}).get("ai_suggested", 0) > 0:
                        ai_models["constraint_extraction"] = resolved_model
                yield _safe_json({"node": 5, "result": snapshot()}) + "\n"

                # ── Node 6: Extract Vector Geometry ──
                result = await loop.run_in_executor(
                    None,
                    lambda: extract_vectors_from_pdf(
                        file_location,
                        gemini_api_key=api_key,
                        gemini_model=gemini_model,
                        agent_models={
                            "visual": agent_visual_model,
                            "geometric": agent_geometric_model,
                            "contextual": agent_contextual_model,
                            "judge": agent_judge_model,
                        },
                        site_brief=site_brief,
                        text_blocks=node3_text_blocks,
                        units_info=units_info,
                        cost_tracker=cost_tracker,
                    ),
                )
                if "vectors" in result:
                    extracted_geometry.extend(result["vectors"])
                    total_objects += result.get("extracted_objects", 0)
                if "extracted_text" in result:
                    for t in result["extracted_text"]:
                        t_src = {"source": filename, **t}
                        if t_src not in extracted_text_blocks:
                            extracted_text_blocks.append(t_src)
                if "zone_summary" in result:
                    for zt, count in result["zone_summary"].items():
                        zone_summary[zt] = zone_summary.get(zt, 0) + count
                if "classification_mode" in result:
                    classification_mode = result["classification_mode"]
                if "ai_error_detail" in result:
                    ai_error_detail = result["ai_error_detail"]

                # Now that we have vectors, generate constraint geometry
                from app.ai_agents.constraint_extractor import _generate_constraint_geometry
                constraint_geometry = _generate_constraint_geometry(
                    constraint_result.get("constraints", []),
                    extracted_geometry,
                )
                for cg in constraint_geometry:
                    cg.setdefault("area_pdf_units", 0)
                    cg.setdefault("centroid", [0, 0, 0])
                    cg.setdefault("filled", False)
                    cg.setdefault("source_layer", "constraints")
                extracted_geometry.extend(constraint_geometry)
                total_objects += len(constraint_geometry)
                constraint_result["constraint_geometry"] = constraint_geometry
                yield _safe_json({"node": 6, "result": snapshot()}) + "\n"

            elif filename_lower.endswith('.dwg') or filename_lower.endswith('.dxf'):
                result = extract_from_dwg(file_location)
                if "vectors" in result:
                    extracted_geometry.extend(result["vectors"])
                    total_objects += result.get("extracted_objects", 0)
                if "extracted_text" in result:
                    extracted_text_blocks.extend([{"source": filename, **t} for t in result["extracted_text"]])
                if "zone_summary" in result:
                    for zt, count in result["zone_summary"].items():
                        zone_summary[zt] = zone_summary.get(zt, 0) + count
                classification_mode = "rule_based"
                yield _safe_json({"node": 6, "result": snapshot()}) + "\n"

        # AI model tracking
        if api_key and classification_mode in ("ai_vision", "ai_ensemble"):
            ai_models["vision_classification"] = resolved_model

        # — Node 7: Extract Programme (full) + Generate Volumes —
        programme_result = await loop.run_in_executor(
            None,
            lambda: extract_programme(
                text_blocks=extracted_text_blocks,
                zones=extracted_geometry,
                constraints=constraint_result.get("constraints", []),
                api_key=api_key,
                model_name=resolved_model,
                cost_tracker=cost_tracker,
                site_brief=site_brief,
            ),
        )
        if api_key:
            if programme_result.get("extraction_summary", {}).get("ai_extracted", 0) > 0 or \
               programme_result.get("extraction_summary", {}).get("ai_suggested", 0) > 0:
                ai_models["programme_extraction"] = resolved_model

        volume_result = generate_volumes(
            zones=extracted_geometry,
            programmes=programme_result.get("programmes", []),
            constraints=constraint_result.get("constraints", []),
            site_brief=site_brief,
            zone_rules=constraint_result.get("zone_rules", []),
            zone_programmes=programme_result.get("zone_programmes", []),
            api_key=api_key,
            model_name=resolved_model,
        )
        volume_geometry = volume_result.get("volumes", [])
        extracted_geometry.extend(volume_geometry)
        total_objects += len(volume_geometry)

        # Add zone annotation tags
        annotation_geometry = volume_result.get("annotations", [])
        extracted_geometry.extend(annotation_geometry)
        total_objects += len(annotation_geometry)

        yield _safe_json({"node": 7, "result": snapshot()}) + "\n"

        # â”€â”€ Node 8: Validation Report â”€â”€
        yield _safe_json({"node": 8, "result": snapshot()}) + "\n"

        # Done
        yield _safe_json({"node": "done"}) + "\n"

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-cache"},
    )
