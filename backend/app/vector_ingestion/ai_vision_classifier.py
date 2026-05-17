"""
AI Vision-Based Zone/Building Classifier
=========================================

Uses Gemini Vision to classify extracted polygons by looking at
the actual rendered PDF drawing — not just geometric metrics.

Pipeline:
  1. Render the PDF page as a high-res image (PyMuPDF)
  2. Create an annotated overlay with numbered polygon outlines
  3. Send both images + polygon metadata to Gemini
  4. Parse structured JSON classifications
  5. Apply corrections to the polygon list

Falls back gracefully if no API key, API errors, or SDK issues.
"""
from __future__ import annotations

import io
import json
import logging
import math
import traceback
from typing import Any, Dict, List, Optional

import fitz  # PyMuPDF

log = logging.getLogger(__name__)

# ── Valid classification types ──
VALID_ZONE_TYPES = {
    "plot_boundary",
    "buildable_envelope",
    "landscape_zone",
    "infrastructure_zone",
    "traffic_zone",
    "sub_zone",
    "no_build_zone",
    "restriction_line",
    "artifact",
}


def _render_page_image(page: fitz.Page, dpi: int = 200) -> bytes:
    """Render a PDF page to PNG bytes."""
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    return pix.tobytes("png")


def _hsl_to_rgb(h: float, s: float, l: float):
    """Convert HSL (0-360, 0-1, 0-1) to RGB (0-1, 0-1, 0-1)."""
    import colorsys
    r, g, b = colorsys.hls_to_rgb(h / 360.0, l, s)
    return (r, g, b)


def _draw_polygon_overlay(
    page: fitz.Page,
    polygons: List[Dict[str, Any]],
    dpi: int = 200,
    page_area: float = 1.0,
) -> bytes:
    """
    Create an annotated image: render the PDF page then draw numbered
    polygon outlines on top.  Each polygon gets a distinct colour
    (HSL hue rotation) and an area-percentage label alongside its ID
    so the AI can discriminate individual shapes even when packed together.
    """
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    # Get a fresh copy of the page via a temporary doc so we can draw on it
    src_doc = page.parent
    tmp_doc = fitz.open()  # blank
    tmp_doc.insert_pdf(src_doc, from_page=page.number, to_page=page.number)
    tmp_page = tmp_doc[0]

    n = max(len(polygons), 1)
    for i, poly in enumerate(polygons):
        pts_raw = poly.get("points", [])
        if len(pts_raw) < 3:
            continue

        # Distinct per-polygon colour via hue rotation (high saturation, mid lightness)
        hue = (i * 360.0 / n) % 360
        r, g, b = _hsl_to_rgb(hue, 0.85, 0.45)
        color = (r, g, b)

        # Build fitz.Point list (PDF coords, not normalized)
        fitz_pts = [fitz.Point(p[0], p[1]) for p in pts_raw]

        # Draw polygon outline (thicker for visibility at higher DPI)
        shape = tmp_page.new_shape()
        shape.draw_polyline(fitz_pts)
        if fitz_pts[0] != fitz_pts[-1]:
            shape.draw_line(fitz_pts[-1], fitz_pts[0])
        shape.finish(color=color, width=2.0, closePath=True)
        shape.commit()

        # Draw ID + area label at centroid
        cx = sum(p[0] for p in pts_raw) / len(pts_raw)
        cy = sum(p[1] for p in pts_raw) / len(pts_raw)
        area_pct = round(poly.get("area", 0) / page_area * 100, 1) if page_area > 0 else 0
        label = f"{i} ({area_pct}%)"
        # Background rect for readability
        tw = 6 + len(label) * 4
        th = 10
        rect = fitz.Rect(cx - tw / 2, cy - th / 2, cx + tw / 2, cy + th / 2)
        shape2 = tmp_page.new_shape()
        shape2.draw_rect(rect)
        shape2.finish(color=None, fill=(1, 1, 1), fill_opacity=0.90)
        shape2.commit()
        # Text
        try:
            tmp_page.insert_text(
                fitz.Point(cx - tw / 2 + 2, cy + 3),
                label,
                fontsize=7,
                color=color,
            )
        except Exception:
            pass  # Font issues on some systems

    pix = tmp_page.get_pixmap(matrix=mat, alpha=False)
    result = pix.tobytes("png")
    tmp_doc.close()
    return result


def _guess_category(poly: Dict[str, Any], page_area: float) -> str:
    """Rough initial guess for annotation colour coding."""
    area = poly.get("area", 0)
    filled = poly.get("fill") is not None
    strategy = poly.get("_extraction_strategy", "direct")
    area_pct = area / page_area if page_area > 0 else 0

    # Plot boundary: largest unfilled polygon (>15% of page)
    if area_pct > 0.15 and not filled:
        return "boundary_candidate"

    # Subzone candidates from adaptive extraction
    if strategy in ("chain_join", "planar_face"):
        return "building_candidate"

    # Large unfilled = probably zone boundary
    if area_pct > 0.02 and not filled:
        return "zone_candidate"

    # Medium/Large filled = zone
    if filled and area_pct > 0.005:
        return "zone_candidate"

    return "building_candidate"


def _build_prompt(polygons: List[Dict[str, Any]], page_area: float) -> str:
    """Build the structured classification prompt."""
    manifest = []
    for i, poly in enumerate(polygons):
        area = poly.get("area", 0)
        area_pct = round(area / page_area * 100, 2) if page_area > 0 else 0
        cx, cy = poly.get("centroid", (0, 0))
        filled = poly.get("fill") is not None
        strategy = poly.get("_extraction_strategy", "direct")
        n_verts = len(poly.get("points", []))
        # Include shape metrics if available for the AI to reason about
        metrics = poly.get("_shape_metrics_for_ai")

        entry: Dict[str, Any] = {
            "id": i,
            "area_pct": area_pct,
            "filled": filled,
            "centroid": [round(cx, 1), round(cy, 1)],
            "n_vertices": n_verts,
            "extraction": strategy,
            "current_guess": poly.get("_classification_guess", "unknown"),
        }
        if metrics:
            entry["aspect_ratio"] = metrics.get("aspect_ratio")
            entry["compactness"] = metrics.get("compactness")
        manifest.append(entry)

    prompt = f"""You are an expert in international urban planning, zoning, and land-use maps.
You have deep familiarity with Dutch bestemmingsplan drawings, German Bebauungsplan,
French PLU (Plan Local d'Urbanisme), and similar European planning conventions.

I am analyzing a zoning / land-use map extracted from a regulatory PDF.
I have extracted {len(polygons)} polygon regions from the vector data.

The FIRST image is the original rendered PDF page.
The SECOND image is the same page with numbered, colour-coded polygon overlays.
Each polygon has a unique colour and a label showing "ID (area%)", e.g. "3 (2.1%)".

Here is metadata for each numbered polygon:
{json.dumps(manifest, indent=2)}

─── YOUR TASK ───
Classify each polygon into exactly ONE of these types:

• "plot_boundary" — the outer boundary of the entire building plot.
  There should be exactly ONE plot_boundary: the single largest outline
  that encompasses all zones.

• "buildable_envelope" — a zone where buildings are permitted.
  Usually shown with orange, yellow, or warm-toned hatching/fill.

• "landscape_zone" — green space, parks, gardens, water buffers.
  Usually shown with green hatching/fill or tree symbols.

• "infrastructure_zone" — roads, utilities, technical areas.

• "traffic_zone" — traffic/circulation areas ("verkeer" in Dutch).

• "sub_zone" — an ACTUAL BUILDING FOOTPRINT.
  This is the most critical category. Buildings can appear in many forms:
    – Simple rectangles (detached houses, garages, sheds)
    – Chains of attached rectangles (Dutch rijtjeswoningen / row houses
      sharing party walls)
    – Complex footprints: L-shapes, T-shapes, U-shapes, courtyards
    – Proposed/planned buildings shown with dashed or dotted outlines
    – Small auxiliary structures (bijgebouwen): sheds, carports, garden
      rooms — these are STILL buildings even if very small
    – Buildings may be filled/hatched OR outline-only
    – A polygon inside a buildable zone that has a compact, rectangular,
      or architectural shape is almost certainly a building
  ➜ When in doubt whether something is a building or a zone,
    classify as "sub_zone" (building) rather than a zone.

• "no_build_zone" — areas where building is prohibited.

• "artifact" — NOT a real architectural feature.
  Stray lines, rendering debris, label-marker circles, extreme shapes,
  or decorative elements.

─── KEY RULES ───
1. Exactly ONE plot_boundary.
2. Zones tile the plot area without large gaps.
3. Buildings (sub_zone) sit INSIDE zones. They are distinct, compact,
   closed shapes with architectural proportions (aspect ratio < 5,
   reasonably convex).
4. Extremely elongated, hair-thin, or spiky polygons → artifact.
5. Large filled areas → zone. Small/medium compact shapes → building.
6. Multiple adjacent rectangles sharing edges → row houses → sub_zone.
7. A polygon fully contained inside a buildable_envelope with
   building-like proportions → sub_zone.
8. When you see height numbers (e.g. "9", "12", "45") inside circles
   near a polygon, the polygon they annotate is most likely a building.

Return ONLY valid JSON — an array of objects, one per polygon:
[
  {{"id": 0, "zone_type": "plot_boundary", "confidence": 0.95, "reason": "Largest outline, encompasses all zones"}},
  {{"id": 1, "zone_type": "buildable_envelope", "confidence": 0.9, "reason": "Filled orange area inside plot"}},
  ...
]

You MUST classify ALL {len(polygons)} polygons. Do not skip any.
"""
    return prompt


def classify_polygons_with_vision(
    polygons: List[Dict[str, Any]],
    page: fitz.Page,
    page_area: float,
    api_key: str,
    model_name: str = "gemini-2.5-flash",
) -> List[Dict[str, Any]]:
    """
    Use Gemini Vision to classify all extracted polygons.

    Args:
        polygons:   List of polygon dicts (with 'points' in PDF coords,
                    'area', 'fill', '_extraction_strategy', etc.)
        page:       The fitz.Page object for rendering
        page_area:  Total page area in PDF units²
        api_key:    Gemini API key (from user settings)
        model_name: Which Gemini model to use

    Returns:
        The same polygon list with updated classifications.
    """
    if not api_key or not polygons:
        log.info("[AI Vision] No API key or no polygons — skipping")
        return polygons

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        log.warning("[AI Vision] google-genai SDK not installed — skipping")
        # Tag so caller knows it was a missing SDK, not an API failure
        for poly in polygons:
            poly["_ai_sdk_missing"] = True
        return polygons

    try:
        # Step 1: Assign initial guesses (for colour coding annotations)
        # Also attach lightweight shape metrics for the prompt manifest
        for poly in polygons:
            poly["_classification_guess"] = _guess_category(poly, page_area)
            sp = poly.get("shapely_poly")
            if sp:
                try:
                    from shapely.geometry import Polygon as _Poly
                    hull = sp.convex_hull
                    minr = sp.minimum_rotated_rectangle
                    coords = list(minr.exterior.coords)
                    w = math.dist(coords[0], coords[1])
                    h = math.dist(coords[1], coords[2])
                    poly["_shape_metrics_for_ai"] = {
                        "aspect_ratio": round(max(w, h) / max(min(w, h), 0.001), 2),
                        "compactness": round(sp.area / max(hull.area, 0.001), 2),
                    }
                except Exception:
                    pass

        # Step 2: Render both images at 200 DPI for sharper detail
        log.info("[AI Vision] Rendering PDF page images (200 DPI)...")
        clean_png = _render_page_image(page, dpi=200)
        annotated_png = _draw_polygon_overlay(page, polygons, dpi=200, page_area=page_area)

        # Step 3: Build prompt
        prompt = _build_prompt(polygons, page_area)

        # Step 4: Call Gemini Vision with strict JSON output
        log.info("[AI Vision] Calling Gemini Vision (%s) with %d polygons...",
                 model_name, len(polygons))

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model_name,
            contents=[
                types.Part.from_bytes(data=clean_png, mime_type="image/png"),
                types.Part.from_bytes(data=annotated_png, mime_type="image/png"),
                prompt,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )

        # Step 5: Parse response
        text = response.text.strip()
        log.debug("[AI Vision] Raw response: %s", text[:500])

        # Extract JSON from markdown code blocks if present
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        classifications = json.loads(text)
        if not isinstance(classifications, list):
            log.warning("[AI Vision] Response is not a list — skipping")
            return polygons

        # Step 6: Apply corrections
        correction_map = {}
        for cls in classifications:
            idx = cls.get("id")
            zone_type = cls.get("zone_type", "")
            if idx is not None and zone_type in VALID_ZONE_TYPES:
                correction_map[idx] = cls

        applied = 0
        artifacts_removed = 0
        for i, poly in enumerate(polygons):
            if i in correction_map:
                cls = correction_map[i]
                old_type = poly.get("_classification_guess", "unknown")
                new_type = cls["zone_type"]

                if new_type == "artifact":
                    poly["_is_artifact"] = True
                    artifacts_removed += 1
                    log.debug("[AI Vision] Polygon %d → ARTIFACT (%s)",
                              i, cls.get("reason", ""))
                else:
                    poly["_ai_zone_type"] = new_type
                    poly["_ai_confidence"] = cls.get("confidence", 0.8)
                    poly["_ai_reason"] = cls.get("reason", "")
                    applied += 1

        log.info("[AI Vision] Applied %d classifications, %d artifacts flagged",
                 applied, artifacts_removed)

        # Clean up temp keys
        for poly in polygons:
            poly.pop("_classification_guess", None)
            poly.pop("_shape_metrics_for_ai", None)

        return polygons

    except Exception as e:
        log.error("[AI Vision] Classification failed: %s", e)
        log.error("[AI Vision] Traceback:\n%s", traceback.format_exc())
        # Tag polygons with the error message so the caller can surface it
        for poly in polygons:
            poly.pop("_classification_guess", None)
            poly.pop("_shape_metrics_for_ai", None)
            poly["_ai_error"] = str(e)
        return polygons
