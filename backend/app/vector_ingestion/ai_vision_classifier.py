"""
AI Vision-Based Zone/Building Classifier
=========================================

Uses Gemini Vision to classify extracted polygons by looking at
the actual rendered PDF drawing — not just geometric metrics.

Pipeline:
  1. Render the PDF page as a high-res image (PyMuPDF)
  2. Create an annotated overlay with numbered polygon outlines
  3. Generate cropped detail views of dense building clusters
  4. Send images + polygon metadata to Gemini (two-pass: zones then buildings)
  5. Parse structured JSON classifications
  6. Apply corrections to the polygon list

Falls back gracefully if no API key, API errors, or SDK issues.
"""
from __future__ import annotations

import io
import json
import logging
import math
import traceback
from typing import Any, Dict, List, Optional, Tuple

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
    "plinth",
}


def _render_page_image(page: fitz.Page, dpi: int = 150) -> bytes:
    """Render a PDF page to JPEG bytes for fast AI upload."""
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    return pix.tobytes("jpeg")


def _hsl_to_rgb(h: float, s: float, l: float):
    """Convert HSL (0-360, 0-1, 0-1) to RGB (0-1, 0-1, 0-1)."""
    import colorsys
    r, g, b = colorsys.hls_to_rgb(h / 360.0, l, s)
    return (r, g, b)


def _draw_polygon_overlay(
    page: fitz.Page,
    polygons: List[Dict[str, Any]],
    dpi: int = 150,
    page_area: float = 1.0,
) -> bytes:
    """
    Create an annotated image: render the PDF page then draw numbered
    polygon outlines on top.  Each polygon gets a distinct colour
    (HSL hue rotation) and a clear ID label so the AI can identify
    individual shapes even when packed together.
    """
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    src_doc = page.parent
    tmp_doc = fitz.open()
    tmp_doc.insert_pdf(src_doc, from_page=page.number, to_page=page.number)
    tmp_page = tmp_doc[0]

    n = max(len(polygons), 1)
    for i, poly in enumerate(polygons):
        pts_raw = poly.get("points", [])
        if len(pts_raw) < 3:
            continue

        # Distinct per-polygon colour via hue rotation
        hue = (i * 360.0 / n) % 360
        r, g, b = _hsl_to_rgb(hue, 0.90, 0.40)
        color = (r, g, b)

        fitz_pts = [fitz.Point(p[0], p[1]) for p in pts_raw]

        # Draw polygon outline — thick for visibility
        shape = tmp_page.new_shape()
        shape.draw_polyline(fitz_pts)
        if fitz_pts[0] != fitz_pts[-1]:
            shape.draw_line(fitz_pts[-1], fitz_pts[0])
        shape.finish(color=color, width=3.0, closePath=True)
        shape.commit()

        # Semi-transparent fill for small polygons (buildings) so they stand out
        area_pct = poly.get("area", 0) / page_area if page_area > 0 else 0
        if area_pct < 0.03 and len(fitz_pts) >= 3:
            shape_fill = tmp_page.new_shape()
            shape_fill.draw_polyline(fitz_pts)
            if fitz_pts[0] != fitz_pts[-1]:
                shape_fill.draw_line(fitz_pts[-1], fitz_pts[0])
            shape_fill.finish(
                color=None, fill=color, fill_opacity=0.20, closePath=True,
            )
            shape_fill.commit()

        # Draw ID label at centroid — larger font, high-contrast background
        cx = sum(p[0] for p in pts_raw) / len(pts_raw)
        cy = sum(p[1] for p in pts_raw) / len(pts_raw)
        label = str(i)
        tw = 4 + len(label) * 5
        th = 12
        rect = fitz.Rect(cx - tw / 2, cy - th / 2, cx + tw / 2, cy + th / 2)
        shape2 = tmp_page.new_shape()
        shape2.draw_rect(rect)
        shape2.finish(color=color, fill=(1, 1, 1), fill_opacity=0.95, width=0.5)
        shape2.commit()
        try:
            tmp_page.insert_text(
                fitz.Point(cx - tw / 2 + 2, cy + 4),
                label,
                fontsize=9,
                color=color,
            )
        except Exception:
            pass

    pix = tmp_page.get_pixmap(matrix=mat, alpha=False)
    result = pix.tobytes("jpeg")
    tmp_doc.close()
    return result


def _render_building_crops(
    page: fitz.Page,
    polygons: List[Dict[str, Any]],
    page_area: float,
    dpi: int = 200,
    max_crops: int = 1,
) -> List[bytes]:
    """Render zoomed-in crops of areas with dense building candidates.

    Returns up to max_crops PNG byte arrays focused on clusters of
    small polygons that are likely buildings but may be hard to see
    in the full-page overview.
    """
    # Find building-candidate polygons (small, compact, inside plot)
    candidates = []
    for i, poly in enumerate(polygons):
        area = poly.get("area", 0)
        area_pct = area / page_area if page_area > 0 else 0
        if 0.0001 < area_pct < 0.04:
            pts = poly.get("points", [])
            if len(pts) >= 3:
                cx = sum(p[0] for p in pts) / len(pts)
                cy = sum(p[1] for p in pts) / len(pts)
                candidates.append({"idx": i, "cx": cx, "cy": cy, "area": area})

    if len(candidates) < 2:
        return []

    # Cluster candidates spatially (simple greedy)
    clusters: List[List[dict]] = []
    used = set()
    RADIUS = 80  # PDF units
    for c in sorted(candidates, key=lambda x: -x["area"]):
        if c["idx"] in used:
            continue
        cluster = [c]
        used.add(c["idx"])
        for other in candidates:
            if other["idx"] in used:
                continue
            if math.dist((c["cx"], c["cy"]), (other["cx"], other["cy"])) < RADIUS:
                cluster.append(other)
                used.add(other["idx"])
        if len(cluster) >= 2:
            clusters.append(cluster)

    clusters.sort(key=lambda cl: -len(cl))
    crops = []
    mat = fitz.Matrix(dpi / 72, dpi / 72)

    for cluster in clusters[:max_crops]:
        xs = [c["cx"] for c in cluster]
        ys = [c["cy"] for c in cluster]
        margin = 40
        clip = fitz.Rect(
            min(xs) - margin, min(ys) - margin,
            max(xs) + margin, max(ys) + margin,
        )
        # Clamp to page
        clip = clip & page.rect
        if clip.is_empty or clip.width < 20 or clip.height < 20:
            continue

        src_doc = page.parent
        tmp_doc = fitz.open()
        tmp_doc.insert_pdf(src_doc, from_page=page.number, to_page=page.number)
        tmp_page = tmp_doc[0]

        # Draw overlays for polygons in this cluster
        n = max(len(polygons), 1)
        for c in cluster:
            i = c["idx"]
            poly = polygons[i]
            pts_raw = poly.get("points", [])
            if len(pts_raw) < 3:
                continue
            hue = (i * 360.0 / n) % 360
            r, g, b = _hsl_to_rgb(hue, 0.90, 0.40)
            color = (r, g, b)

            fitz_pts = [fitz.Point(p[0], p[1]) for p in pts_raw]
            shape = tmp_page.new_shape()
            shape.draw_polyline(fitz_pts)
            if fitz_pts[0] != fitz_pts[-1]:
                shape.draw_line(fitz_pts[-1], fitz_pts[0])
            shape.finish(color=color, width=3.5, closePath=True)
            shape.commit()

            # Fill
            shape_fill = tmp_page.new_shape()
            shape_fill.draw_polyline(fitz_pts)
            if fitz_pts[0] != fitz_pts[-1]:
                shape_fill.draw_line(fitz_pts[-1], fitz_pts[0])
            shape_fill.finish(color=None, fill=color, fill_opacity=0.25, closePath=True)
            shape_fill.commit()

            # Label
            cx_p = sum(p[0] for p in pts_raw) / len(pts_raw)
            cy_p = sum(p[1] for p in pts_raw) / len(pts_raw)
            label = str(i)
            tw = 5 + len(label) * 6
            th = 14
            rect = fitz.Rect(cx_p - tw/2, cy_p - th/2, cx_p + tw/2, cy_p + th/2)
            shape2 = tmp_page.new_shape()
            shape2.draw_rect(rect)
            shape2.finish(color=color, fill=(1,1,1), fill_opacity=0.95, width=0.5)
            shape2.commit()
            try:
                tmp_page.insert_text(
                    fitz.Point(cx_p - tw/2 + 2, cy_p + 5),
                    label, fontsize=11, color=color,
                )
            except Exception:
                pass

        pix = tmp_page.get_pixmap(matrix=mat, alpha=False, clip=clip)
        crops.append(pix.tobytes("jpeg"))
        tmp_doc.close()

    return crops


def _guess_category(poly: Dict[str, Any], page_area: float) -> str:
    """Rough initial guess for annotation colour coding."""
    area = poly.get("area", 0)
    filled = poly.get("fill") is not None
    strategy = poly.get("_extraction_strategy", "direct")
    area_pct = area / page_area if page_area > 0 else 0

    if area_pct > 0.15 and not filled:
        return "boundary_candidate"
    # Polygons from dashed-line reconstruction are almost always buildings
    if strategy in ("chain_join", "planar_face"):
        return "building_candidate"
    if area_pct > 0.04 and not filled:
        return "zone_candidate"
    if filled and area_pct > 0.005:
        return "zone_candidate"
    return "building_candidate"


def _build_prompt(polygons: List[Dict[str, Any]], page_area: float, n_crops: int = 0) -> str:
    """Build the structured classification prompt."""
    manifest = []
    for i, poly in enumerate(polygons):
        area = poly.get("area", 0)
        area_pct = round(area / page_area * 100, 2) if page_area > 0 else 0
        cx, cy = poly.get("centroid", (0, 0))
        filled = poly.get("fill") is not None
        strategy = poly.get("_extraction_strategy", "direct")
        n_verts = len(poly.get("points", []))
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

    # Build image reference description
    image_desc = """The FIRST image is the original rendered PDF page.
The SECOND image is the same page with numbered, colour-coded polygon overlays.
Each polygon has a unique colour outline and a white-background ID number label."""
    if n_crops > 0:
        image_desc += f"""
The NEXT {n_crops} image(s) are ZOOMED-IN DETAIL CROPS of areas containing
clusters of small polygons. These crops show building candidates at higher
resolution — examine them carefully to identify individual building footprints
that might be hard to distinguish in the full-page view."""

    prompt = f"""You are an expert in international urban planning, zoning, and land-use maps.
You have deep familiarity with Dutch bestemmingsplan drawings, German Bebauungsplan,
French PLU (Plan Local d'Urbanisme), and similar European planning conventions.

I am analyzing a zoning / land-use map extracted from a regulatory PDF.
I have extracted {len(polygons)} polygon regions from the vector data.

{image_desc}

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
  ⚠️ THIS IS THE MOST CRITICAL CATEGORY — err on the side of classifying
  MORE polygons as sub_zone rather than fewer. Missing a building is worse
  than misclassifying a zone as a building.

  Buildings appear in MANY forms on zoning plans:
    – Simple rectangles (detached houses, garages, sheds)
    – Chains of attached rectangles (Dutch rijtjeswoningen / row houses
      sharing party walls) — each unit in the row is a SEPARATE building
    – Complex footprints: L-shapes, T-shapes, U-shapes, courtyards
    – Proposed/planned buildings shown with dashed or dotted outlines
    – Small auxiliary structures (bijgebouwen): sheds, carports, garden
      rooms — these are STILL buildings even if very small (< 0.5% of page)
    – Buildings may be filled/hatched OR outline-only
    – A polygon inside a buildable zone that has a compact, rectangular,
      or architectural shape is almost certainly a building
    – Polygons extracted via "chain_join" or "planar_face" strategies
      are almost always building footprints

  CRITICAL: Look at the images carefully. Count the actual buildings you
  can see in the drawing. If you see N buildings in the image, there should
  be at least N polygons classified as sub_zone. Common Dutch zoning plans
  show 10-30+ individual building footprints (including row house units).

  ➜ When in doubt whether something is a building or a zone,
    ALWAYS classify as "sub_zone" (building).

• "no_build_zone" — areas where building is prohibited.

• "artifact" — NOT a real architectural feature.
  Stray lines, rendering debris, label-marker circles, extreme shapes,
  or decorative elements. Use sparingly — only for clearly non-architectural
  shapes.

─── KEY RULES ───
1. Exactly ONE plot_boundary.
2. Zones tile the plot area without large gaps.
3. Buildings (sub_zone) sit INSIDE zones. They are distinct, compact,
   closed shapes with architectural proportions.
4. ONLY mark as artifact if it is clearly rendering debris or a decorative
   element — never a compact, rectangular shape.
5. Large filled areas → zone. Small/medium compact shapes → building.
6. Multiple adjacent rectangles sharing edges → row houses → EACH ONE
   is a separate sub_zone. Do NOT merge them into one zone.
7. A polygon fully contained inside a buildable_envelope with
   building-like proportions → sub_zone.
8. When you see height numbers (e.g. "9", "12", "45") inside circles
   near a polygon, the polygon they annotate is most likely a building.
9. Polygons with extraction="chain_join" or "planar_face" are reconstructed
   from dashed outlines — they are almost certainly buildings (sub_zone).
10. If a polygon's current_guess is "building_candidate", give it strong
    consideration as sub_zone unless you can see clear visual evidence
    it is something else.

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
        for poly in polygons:
            poly["_ai_sdk_missing"] = True
        return polygons

    try:
        # Step 1: Assign initial guesses + shape metrics
        for poly in polygons:
            poly["_classification_guess"] = _guess_category(poly, page_area)
            sp = poly.get("shapely_poly")
            if sp:
                try:
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

        # Step 2: Render images at 150 DPI (sufficient for AI vision)
        log.info("[AI Vision] Rendering PDF page images (150 DPI)...")
        clean_png = _render_page_image(page, dpi=150)
        annotated_png = _draw_polygon_overlay(page, polygons, dpi=150, page_area=page_area)

        # Step 2b: Render zoomed crop of building clusters (1 max, 200 DPI)
        log.info("[AI Vision] Rendering building cluster crop (200 DPI)...")
        crop_pngs = _render_building_crops(page, polygons, page_area, dpi=200, max_crops=1)
        log.info("[AI Vision] Generated %d detail crop(s)", len(crop_pngs))

        # Step 3: Build prompt
        prompt = _build_prompt(polygons, page_area, n_crops=len(crop_pngs))

        # Step 4: Assemble content parts
        content_parts = [
            types.Part.from_bytes(data=clean_png, mime_type="image/jpeg"),
            types.Part.from_bytes(data=annotated_png, mime_type="image/jpeg"),
        ]
        for crop in crop_pngs:
            content_parts.append(types.Part.from_bytes(data=crop, mime_type="image/jpeg"))
        content_parts.append(prompt)

        log.info("[AI Vision] Calling Gemini Vision (%s) with %d polygons, %d images...",
                 model_name, len(polygons), 2 + len(crop_pngs))

        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=300_000),
        )
        response = client.models.generate_content(
            model=model_name,
            contents=content_parts,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )

        # Step 6: Parse response
        text = response.text.strip()
        log.debug("[AI Vision] Raw response: %s", text[:500])

        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        classifications = json.loads(text)
        if not isinstance(classifications, list):
            log.warning("[AI Vision] Response is not a list — skipping")
            return polygons

        # Step 7: Apply corrections with building-bias logic
        correction_map = {}
        for cls in classifications:
            idx = cls.get("id")
            zone_type = cls.get("zone_type", "")
            if idx is not None and zone_type in VALID_ZONE_TYPES:
                correction_map[idx] = cls

        applied = 0
        artifacts_removed = 0
        buildings_found = 0
        for i, poly in enumerate(polygons):
            if i in correction_map:
                cls = correction_map[i]
                new_type = cls["zone_type"]

                if new_type == "artifact":
                    # Don't artifact-flag polygons that look like buildings
                    guess = poly.get("_classification_guess", "")
                    strategy = poly.get("_extraction_strategy", "")
                    if guess == "building_candidate" or strategy in ("chain_join", "planar_face"):
                        # Override: keep as sub_zone instead of artifact
                        poly["_ai_zone_type"] = "sub_zone"
                        poly["_ai_confidence"] = 0.70
                        poly["_ai_reason"] = "AI said artifact but extraction suggests building — kept as sub_zone"
                        buildings_found += 1
                        applied += 1
                    else:
                        poly["_is_artifact"] = True
                        artifacts_removed += 1
                        log.debug("[AI Vision] Polygon %d → ARTIFACT (%s)",
                                  i, cls.get("reason", ""))
                else:
                    poly["_ai_zone_type"] = new_type
                    poly["_ai_confidence"] = cls.get("confidence", 0.8)
                    poly["_ai_reason"] = cls.get("reason", "")
                    applied += 1
                    if new_type == "sub_zone":
                        buildings_found += 1

        # Step 8: Safety net — if AI found disproportionately few buildings
        # compared to building_candidate polygons, force-classify remaining ones
        candidate_count = sum(
            1 for p in polygons
            if p.get("_classification_guess") == "building_candidate"
            and not p.get("_ai_zone_type")
            and not p.get("_is_artifact")
        )
        total_candidates = sum(
            1 for p in polygons
            if p.get("_classification_guess") == "building_candidate"
        )
        # Trigger if AI found fewer than 50% of building candidates as sub_zone
        expected_min = max(int(total_candidates * 0.5), 2)
        if buildings_found < expected_min and candidate_count > 0:
            log.warning(
                "[AI Vision] Only %d buildings found but expected at least %d "
                "(50%% of %d candidates) — force-classifying %d untagged candidates as sub_zone",
                buildings_found, expected_min, total_candidates, candidate_count,
            )
            for poly in polygons:
                if (poly.get("_classification_guess") == "building_candidate"
                        and not poly.get("_ai_zone_type")
                        and not poly.get("_is_artifact")):
                    poly["_ai_zone_type"] = "sub_zone"
                    poly["_ai_confidence"] = 0.65
                    poly["_ai_reason"] = "safety_net: unclassified building candidate"
                    buildings_found += 1
                    applied += 1

        # Step 9: Post-classification consistency check — polygons from
        # chain_join or planar_face extraction should always be sub_zone
        for poly in polygons:
            strategy = poly.get("_extraction_strategy", "")
            if strategy in ("chain_join", "planar_face"):
                ai_type = poly.get("_ai_zone_type", "")
                if ai_type and ai_type != "sub_zone" and not poly.get("_is_artifact"):
                    log.info(
                        "[AI Vision] Overriding polygon %s: AI said '%s' but extraction "
                        "strategy '%s' → forcing sub_zone",
                        poly.get("id", "?"), ai_type, strategy,
                    )
                    poly["_ai_zone_type"] = "sub_zone"
                    poly["_ai_confidence"] = max(poly.get("_ai_confidence", 0), 0.70)
                    poly["_ai_reason"] = (
                        poly.get("_ai_reason", "") +
                        f" [overridden: {strategy} → sub_zone]"
                    )
                    buildings_found += 1

        log.info("[AI Vision] Applied %d classifications, %d artifacts, %d buildings",
                 applied, artifacts_removed, buildings_found)

        # Clean up temp keys
        for poly in polygons:
            poly.pop("_classification_guess", None)
            poly.pop("_shape_metrics_for_ai", None)

        return polygons

    except Exception as e:
        log.error("[AI Vision] Classification failed: %s", e)
        log.error("[AI Vision] Traceback:\n%s", traceback.format_exc())
        for poly in polygons:
            poly.pop("_classification_guess", None)
            poly.pop("_shape_metrics_for_ai", None)
            poly["_ai_error"] = str(e)
        return polygons
