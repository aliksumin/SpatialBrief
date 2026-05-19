from fastapi import APIRouter, UploadFile, File, HTTPException, Request
from typing import List
import asyncio
import shutil
import os

from app.vector_ingestion.pdf_vector_extractor import extract_vectors_from_pdf
from app.vector_ingestion.pipeline_stages import run_extract_programme, run_detect_units
from app.vector_ingestion.cad_extractor import extract_from_dwg
from app.ai_agents.constraint_extractor import extract_constraints
from app.ai_agents.programme_extractor import extract_programme
from app.ai_agents.volume_generator import generate_volumes
from app.config import settings

router = APIRouter()

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.post("/upload")
async def upload_files_only(files: List[UploadFile] = File(...)):
    """Node 1 — Save files to disk only. No processing. Returns filenames."""
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
    Run the full 9-node extraction pipeline on previously uploaded files.

    Pipeline stages:
      Node 2 — Classify Documents (implicit from file type)
      Node 3 — Extract Programme (text, metadata, site brief)
      Node 4 — Detect Units & Coordinates
      Node 5 — Extract Vector Geometry (multi-agent ensemble)
      Node 6 — Extract Constraints
      Node 7 — Generate Volumes
    """
    body = await request.json()
    filenames = body.get("filenames", [])
    if not filenames:
        raise HTTPException(status_code=400, detail="No filenames provided")

    # API key: prefer header from frontend, fall back to server config
    api_key = (request.headers.get("X-Gemini-Api-Key") or settings.GEMINI_API_KEY or "").strip() or None
    # Model: prefer header from frontend, fall back to default
    gemini_model = (request.headers.get("X-Gemini-Model") or "").strip() or None
    # Per-agent model overrides for ensemble classifier
    agent_visual_model = (request.headers.get("X-Agent-Visual-Model") or "").strip() or None
    agent_geometric_model = (request.headers.get("X-Agent-Geometric-Model") or "").strip() or None
    agent_contextual_model = (request.headers.get("X-Agent-Contextual-Model") or "").strip() or None
    agent_judge_model = (request.headers.get("X-Agent-Judge-Model") or "").strip() or None

    extracted_geometry = []
    extracted_text_blocks = []
    zone_summary = {}
    total_objects = 0
    classification_mode = "no_files"
    ai_error_detail = None
    resolved_model = gemini_model or "gemini-2.5-flash"

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

            # ── Node 3: Extract Programme ──
            # Extract text, metadata, produce site brief with GFA targets,
            # typology expectations, and binding rules for vector extraction.
            # Get initial polygon outlines for the site brief analyser
            from app.vector_ingestion.pdf_vector_extractor import (
                _reconstruct_paths, _collect_boundary_zones,
            )
            raw_paths = _reconstruct_paths(page)
            boundaries_prelim, _, _ = _collect_boundary_zones(raw_paths, pa)
            # Build lightweight polygon list for site brief
            from shapely.geometry import Polygon as ShapelyPolygon
            prelim_polys = []
            for b in boundaries_prelim:
                sp = b.get("_shapely")
                if sp:
                    prelim_polys.append({
                        "shapely_poly": sp,
                        "area": b.get("_area", 0),
                        "fill": b.get("fill"),
                    })

            loop = asyncio.get_event_loop()
            programme_stage = await loop.run_in_executor(
                None,
                lambda: run_extract_programme(
                    page, pa, prelim_polys,
                    api_key=api_key,
                    model_name=resolved_model,
                ),
            )

            site_brief = programme_stage.get("site_brief")
            node3_text_blocks = programme_stage.get("text_blocks", [])
            extracted_text_blocks.extend([{"source": filename, **t} for t in node3_text_blocks])

            # ── Node 4: Detect Units & Coordinates ──
            units_info = run_detect_units(page, node3_text_blocks)

            doc.close()

            # ── Node 5: Extract Vector Geometry ──
            # Multi-agent ensemble extraction using site_brief from Node 3
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
                ),
            )

            if "vectors" in result:
                extracted_geometry.extend(result["vectors"])
                total_objects += result.get("extracted_objects", 0)
            if "extracted_text" in result:
                # Merge any additional text from vector extraction
                for t in result["extracted_text"]:
                    t_with_source = {"source": filename, **t}
                    # Avoid duplicates
                    if t_with_source not in extracted_text_blocks:
                        extracted_text_blocks.append(t_with_source)
            if "zone_summary" in result:
                for zt, count in result["zone_summary"].items():
                    zone_summary[zt] = zone_summary.get(zt, 0) + count
            if "classification_mode" in result:
                classification_mode = result["classification_mode"]
            if "ai_error_detail" in result:
                ai_error_detail = result["ai_error_detail"]

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

    # ── Node 6: Extract Constraints ──
    loop = asyncio.get_event_loop()

    constraint_result = await loop.run_in_executor(
        None,
        lambda: extract_constraints(
            text_blocks=extracted_text_blocks,
            zones=extracted_geometry,
            api_key=api_key,
            model_name=resolved_model,
        ),
    )

    # ── Node 3 continued: Extract Programme (with constraints) ──
    programme_result = await loop.run_in_executor(
        None,
        lambda: extract_programme(
            text_blocks=extracted_text_blocks,
            zones=extracted_geometry,
            constraints=constraint_result.get("constraints", []),
            api_key=api_key,
            model_name=resolved_model,
        ),
    )

    # Merge constraint geometry into the main geometry list
    constraint_geometry = constraint_result.get("constraint_geometry", [])
    for cg in constraint_geometry:
        cg.setdefault("area_pdf_units", 0)
        cg.setdefault("centroid", [0, 0, 0])
        cg.setdefault("filled", False)
        cg.setdefault("source_layer", "constraints")
    extracted_geometry.extend(constraint_geometry)
    total_objects += len(constraint_geometry)

    # ── Node 7: Generate Volumes ──
    volume_result = generate_volumes(
        zones=extracted_geometry,
        programmes=programme_result.get("programmes", []),
        constraints=constraint_result.get("constraints", []),
    )

    volume_geometry = volume_result.get("volumes", [])
    extracted_geometry.extend(volume_geometry)
    total_objects += len(volume_geometry)

    # Track which AI models were used for each task
    ai_models = {}
    if api_key:
        if classification_mode == "ai_vision":
            ai_models["vision_classification"] = resolved_model
        if constraint_result.get("extraction_summary", {}).get("ai_extracted", 0) > 0 or \
           constraint_result.get("extraction_summary", {}).get("ai_suggested", 0) > 0:
            ai_models["constraint_extraction"] = resolved_model
        if programme_result.get("extraction_summary", {}).get("ai_extracted", 0) > 0 or \
           programme_result.get("extraction_summary", {}).get("ai_suggested", 0) > 0:
            ai_models["programme_extraction"] = resolved_model

    response = {
        "status": "success",
        "filenames": filenames,
        "classification_mode": classification_mode,
        "ai_models": ai_models,
        "geometry": {
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
        }
    }
    if ai_error_detail:
        response["ai_error_detail"] = ai_error_detail
        response["geometry"]["ai_error_detail"] = ai_error_detail
    return response
