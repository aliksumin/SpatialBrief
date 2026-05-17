from fastapi import APIRouter, UploadFile, File, HTTPException, Request
from typing import List
import shutil
import os

from app.vector_ingestion.pdf_vector_extractor import extract_vectors_from_pdf
from app.vector_ingestion.cad_extractor import extract_from_dwg
from app.ai_agents.ai_zone_validator import validate_zones_with_ai
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
    """Node 2+ — Run the full extraction pipeline on previously uploaded files."""
    body = await request.json()
    filenames = body.get("filenames", [])
    if not filenames:
        raise HTTPException(status_code=400, detail="No filenames provided")

    # API key: prefer header from frontend, fall back to server config
    api_key = (request.headers.get("X-Gemini-Api-Key") or settings.GEMINI_API_KEY or "").strip() or None
    # Model: prefer header from frontend, fall back to default
    gemini_model = (request.headers.get("X-Gemini-Model") or "").strip() or None

    extracted_geometry = []
    extracted_text_blocks = []
    zone_summary = {}
    total_objects = 0
    classification_mode = "no_files"
    ai_error_detail = None

    for filename in filenames:
        file_location = os.path.join(UPLOAD_DIR, filename)
        if not os.path.exists(file_location):
            continue

        filename_lower = filename.lower()
        if filename_lower.endswith('.pdf'):
            result = extract_vectors_from_pdf(file_location, gemini_api_key=api_key, gemini_model=gemini_model)
            if "vectors" in result:
                extracted_geometry.extend(result["vectors"])
                total_objects += result.get("extracted_objects", 0)
            if "extracted_text" in result:
                extracted_text_blocks.extend([{"source": filename, **t} for t in result["extracted_text"]])
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

    # Optional AI text-based validation pass
    if api_key and extracted_geometry:
        extracted_geometry = validate_zones_with_ai(
            extracted_geometry, extracted_text_blocks, api_key
        )

    response = {
        "status": "success",
        "filenames": filenames,
        "classification_mode": classification_mode,
        "geometry": {
            "source_files": filenames,
            "extracted_objects": total_objects,
            "raw_vector_objects": extracted_geometry,
            "extracted_text": extracted_text_blocks,
            "zone_summary": zone_summary,
            "classification_mode": classification_mode,
            "semantic_objects_2d": []
        }
    }
    if ai_error_detail:
        response["ai_error_detail"] = ai_error_detail
        response["geometry"]["ai_error_detail"] = ai_error_detail
    return response
