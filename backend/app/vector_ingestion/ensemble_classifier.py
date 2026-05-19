"""
Multi-Agent Ensemble Classifier for Building & Zone Recognition
================================================================

Three specialist agents analyse polygons concurrently from different angles,
then a judge agent merges their results into a final classification.

Agents:
  1. Visual Analyst   — image-based, "what does it LOOK like?"
  2. Geometric Analyst — shape metrics, "what SHAPE is it?"
  3. Contextual Analyst— labels + colors + containment, "what CONTEXT surrounds it?"
  4. Judge             — merges all 3, resolves disagreements
"""
from __future__ import annotations

import json
import logging
import math
import traceback
from typing import Any, Dict, List, Optional, Tuple

import fitz

log = logging.getLogger(__name__)

VALID_ZONE_TYPES = {
    "plot_boundary", "buildable_envelope", "landscape_zone",
    "infrastructure_zone", "traffic_zone", "sub_zone",
    "no_build_zone", "restriction_line", "artifact",
}

# ── Shared helpers ──────────────────────────────────────────────

def _format_site_brief_for_prompt(site_brief: Optional[Dict]) -> str:
    """Format the site brief as a binding-rules block for agent prompts."""
    if not site_brief:
        return ""

    lines = ["\n── SITE BRIEF (BINDING RULES from pre-analysis) ──"]

    ez = site_brief.get("expected_buildable_zones")
    if ez:
        lines.append(f"• Expected buildable zones: {ez}")

    eb = site_brief.get("expected_building_count")
    if eb:
        lines.append(f"• Expected building count: {eb} (you MUST find at least this many sub_zone polygons)")

    typo = site_brief.get("dominant_typology")
    if typo:
        lines.append(f"• Dominant typology: {typo}")

    gfa = site_brief.get("site_gfa_target")
    if gfa:
        lines.append(f"• Target GFA: {gfa} m²")

    mh = site_brief.get("max_height")
    if mh:
        lines.append(f"• Max building height: {mh}m")

    ef = site_brief.get("expected_floors")
    if ef:
        lines.append(f"• Expected floor count: {ef}")

    if site_brief.get("has_plinth_buildings"):
        lines.append("• Plinth+tower typology EXPECTED — large footprints containing smaller ones = plinth")

    if site_brief.get("has_underground_parking"):
        lines.append("• Underground parking is expected")

    # ── Per-zone rules with typology guidance ──
    zr = site_brief.get("zone_rules", [])
    if zr:
        lines.append("• Per-zone rules (BINDING — use these to guide classification):")
        lines.append("  Typology catalog: single_tower | plinth_tower | perimeter_block | "
                     "row_houses | courtyard | campus | infill")
        for z in zr:
            parts = [f"  Zone {z.get('zone_index', '?')} \"{z.get('zone_label', '?')}\""]
            if z.get("typology"):
                parts.append(f"typology={z['typology']}")
            if z.get("expected_buildings"):
                parts.append(f"buildings={z['expected_buildings']}")
            if z.get("target_gfa_m2"):
                parts.append(f"GFA={z['target_gfa_m2']}m²")
            if z.get("max_height_m"):
                parts.append(f"height≤{z['max_height_m']}m")
            if z.get("use"):
                parts.append(f"use={z['use']}")
            src = z.get("source", "document")
            parts.append(f"[{src}]")
            lines.append(", ".join(parts))
        lines.append("")
        lines.append("  If a zone has no typology defined, YOU MUST infer it from geometry:")
        lines.append("  - Large zone (>5000m²) with multiple buildings → campus or perimeter_block")
        lines.append("  - Medium zone with tall height limit → single_tower or plinth_tower")
        lines.append("  - Small zone / narrow shape → row_houses or infill")
        lines.append("  - Zone with mixed-use label → plinth_tower")

    rules = site_brief.get("special_rules", [])
    if rules:
        lines.append("• Special rules:")
        for r in rules:
            lines.append(f"  - {r}")

    dn = site_brief.get("density_notes")
    if dn:
        lines.append(f"• Density context: {dn}")

    lines.append("")
    return "\n".join(lines)


def _parse_json_response(text: str) -> list:
    """Extract JSON array from an LLM response."""
    text = text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
    result = json.loads(text)
    return result if isinstance(result, list) else []


def _build_polygon_manifest(polygons, page_area):
    """Build compact polygon metadata for text-only agents."""
    manifest = []
    for i, poly in enumerate(polygons):
        area = poly.get("area", 0)
        area_pct = round(area / page_area * 100, 2) if page_area > 0 else 0
        cx, cy = poly.get("centroid", (0, 0))
        entry = {
            "id": i,
            "area_pct": area_pct,
            "filled": poly.get("fill") is not None,
            "centroid": [round(cx, 1), round(cy, 1)],
            "n_vertices": len(poly.get("points", [])),
            "extraction": poly.get("_extraction_strategy", "direct"),
            "guess": poly.get("_classification_guess", "unknown"),
        }
        metrics = poly.get("_shape_metrics_for_ai")
        if metrics:
            entry["aspect_ratio"] = metrics.get("aspect_ratio")
            entry["compactness"] = metrics.get("compactness")
        manifest.append(entry)
    return manifest


def _get_client(api_key):
    """Create a Gemini client."""
    from google import genai
    from google.genai import types
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=300_000),
    )


def _call_gemini(client, model_name, content_parts, cost_tracker=None, stage=""):
    """Call Gemini and return parsed JSON list. Optionally tracks cost."""
    from google.genai import types
    response = client.models.generate_content(
        model=model_name,
        contents=content_parts,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
        ),
    )
    if cost_tracker is not None:
        cost_tracker.add(response, model_name, stage=stage)
    return _parse_json_response(response.text)


# ── Agent 1: Visual Analyst ─────────────────────────────────────

def _build_visual_prompt(n_polygons, n_crops, site_brief_text=""):
    image_desc = """The FIRST image is the original rendered PDF page.
The SECOND image is the same page with numbered, colour-coded polygon overlays."""
    if n_crops > 0:
        image_desc += f"""
The NEXT {n_crops} image(s) are ZOOMED-IN DETAIL CROPS of building clusters."""

    return f"""You are an expert urban planning map reader. You specialise in
VISUAL identification of buildings, zones, and boundaries on zoning plans.
{site_brief_text}
{image_desc}

There are {n_polygons} numbered polygons overlaid on the drawing.

YOUR TASK — Look at the images CAREFULLY and classify each polygon by what
you SEE in the drawing. Focus on visual appearance only:
- Shape outline style (solid, dashed, thick, thin)
- Fill pattern (hatching color, solid fill, empty)
- Visual context (what surrounds it, what's drawn inside it)
- Scale relative to other elements

Classify each polygon into exactly ONE type:
• "plot_boundary" — outer boundary of the entire site (exactly ONE)
• "buildable_envelope" — zone where building is permitted (warm-toned fill/hatching)
• "landscape_zone" — green/garden areas (green fill/hatching)
• "infrastructure_zone" — roads, utilities
• "traffic_zone" — circulation areas
• "sub_zone" — BUILDING FOOTPRINT (⚠️ CRITICAL: err on the side of MORE buildings)
  Buildings appear as: rectangles, L/T/U shapes, row house chains, small sheds,
  dashed outlines, anything that looks like an architectural footprint
• "no_build_zone" — prohibited building area
• "restriction_line" — setback or regulatory line
• "artifact" — clearly not real geometry (rendering debris only)

Return ONLY valid JSON array:
[{{"id": 0, "zone_type": "...", "confidence": 0.9, "reason": "..."}}]

Classify ALL {n_polygons} polygons. Do not skip any.
When in doubt between building and zone, ALWAYS choose "sub_zone".
"""


def _run_visual_agent(
    client, model_name, polygons, clean_img, annotated_img, crop_imgs,
    site_brief_text="", cost_tracker=None,
) -> List[Dict]:
    """Agent 1: Visual analysis using rendered images."""
    from google.genai import types

    prompt = _build_visual_prompt(len(polygons), len(crop_imgs), site_brief_text)
    parts = [
        types.Part.from_bytes(data=clean_img, mime_type="image/jpeg"),
        types.Part.from_bytes(data=annotated_img, mime_type="image/jpeg"),
    ]
    for crop in crop_imgs:
        parts.append(types.Part.from_bytes(data=crop, mime_type="image/jpeg"))
    parts.append(prompt)

    log.info("[Ensemble/Visual] Calling %s with %d images...", model_name, len(parts) - 1)
    return _call_gemini(client, model_name, parts, cost_tracker=cost_tracker, stage="ensemble_visual")


# ── Agent 2: Geometric Analyst ──────────────────────────────────

def _build_geometric_prompt(manifest, page_area, site_brief_text=""):
    return f"""You are a computational geometry analyst specialising in urban planning.
You classify polygons based ONLY on their geometric properties — no images.
{site_brief_text}
Page area: {page_area:.0f} sq units

Polygon data:
{json.dumps(manifest, indent=2)}

KEY GEOMETRIC RULES:
1. Plot boundary: largest unfilled polygon (>5% page area), exactly ONE
2. Zones: filled polygons with area >1% of page, moderate aspect ratio
3. Buildings (sub_zone): compact shapes (aspect_ratio <5, compactness >0.4),
   area typically 0.01-5% of page, often unfilled
4. Row houses: multiple adjacent rectangles sharing edges, each is a SEPARATE sub_zone
5. Artifacts: extremely elongated (aspect_ratio >10), very thin (compactness <0.15)
6. Polygons with extraction="chain_join" or "planar_face" are almost always buildings
7. If guess="building_candidate", give strong weight to sub_zone classification

CLASSIFICATION PRIORITY for ambiguous cases:
- Small + compact + inside plot → sub_zone (building)
- Large + filled → zone type
- Very elongated or tiny → artifact (use sparingly)

Return ONLY valid JSON array:
[{{"id": 0, "zone_type": "...", "confidence": 0.9, "reason": "..."}}]

Classify ALL {len(manifest)} polygons.
"""


def _run_geometric_agent(client, model_name, manifest, page_area, site_brief_text="", cost_tracker=None) -> List[Dict]:
    """Agent 2: Pure geometric/metric analysis."""
    prompt = _build_geometric_prompt(manifest, page_area, site_brief_text)
    log.info("[Ensemble/Geometric] Calling %s with %d polygons...", model_name, len(manifest))
    return _call_gemini(client, model_name, [prompt], cost_tracker=cost_tracker, stage="ensemble_geometric")


# ── Agent 3: Contextual Analyst ─────────────────────────────────

def _build_context_data(polygons, page_area, page):
    """Build contextual data: text labels, colors, containment info."""
    context = []
    # Get text blocks
    text_blocks = page.get_text("blocks") if page else []

    for i, poly in enumerate(polygons):
        cx, cy = poly.get("centroid", (0, 0))
        entry = {
            "id": i,
            "area_pct": round(poly.get("area", 0) / page_area * 100, 2) if page_area > 0 else 0,
            "extraction": poly.get("_extraction_strategy", "direct"),
            "guess": poly.get("_classification_guess", "unknown"),
        }

        # Fill/stroke color info
        fill = poly.get("fill")
        stroke = poly.get("stroke")
        if fill:
            r, g, b = fill[:3]
            entry["fill_color"] = f"rgb({int(r*255)},{int(g*255)},{int(b*255)})"
            # Classify color family
            if r > 0.7 and g > 0.5 and b < 0.6:
                entry["color_family"] = "warm/orange"
            elif g > 0.5 and r < 0.4 and b < 0.5:
                entry["color_family"] = "green"
            elif abs(r - g) < 0.1 and abs(g - b) < 0.1:
                entry["color_family"] = "gray"
            elif r > 0.7 and g < 0.3:
                entry["color_family"] = "red"
            else:
                entry["color_family"] = "other"
        else:
            entry["fill_color"] = None
            entry["color_family"] = "unfilled"

        if stroke:
            r, g, b = stroke[:3]
            entry["stroke_color"] = f"rgb({int(r*255)},{int(g*255)},{int(b*255)})"

        # Nearby text labels
        nearby_texts = []
        search_radius = 40
        for tb in text_blocks:
            tx = (tb[0] + tb[2]) / 2
            ty = (tb[1] + tb[3]) / 2
            if math.dist((cx, cy), (tx, ty)) < search_radius:
                t = tb[4].strip()
                if t and len(t) < 50:
                    nearby_texts.append(t)
        if nearby_texts:
            entry["nearby_text"] = nearby_texts[:5]

        # Marker labels already attached
        markers = poly.get("marker_labels", [])
        if markers:
            entry["marker_labels"] = markers

        context.append(entry)
    return context


def _build_contextual_prompt(context_data, site_brief_text=""):
    return f"""You are a regulatory zoning document analyst with deep knowledge of
Dutch (bestemmingsplan), German (Bebauungsplan), and French (PLU) planning conventions.

You classify polygons based on their CONTEXTUAL information — colors, text labels,
spatial relationships, and extraction metadata. No images are provided.
{site_brief_text}
Polygon context data:
{json.dumps(context_data, indent=2)}

CONTEXTUAL RULES:
1. Orange/warm-filled polygons → buildable_envelope
2. Green-filled → landscape_zone
3. Gray-filled → infrastructure_zone
4. Red stroke → restriction_line
5. Text labels: "GD" = Gemengd (mixed-use zone), "V" = Verkeer (traffic),
   "G" = Groen (green), height numbers near a polygon = it's a building
6. Unfilled + no text + compact = likely building (sub_zone)
7. extraction="chain_join" or "planar_face" → almost certainly a building
8. guess="building_candidate" → strong indicator of sub_zone
9. Marker labels with numbers (heights) → the polygon is a building

EXACTLY ONE plot_boundary.
When in doubt, prefer sub_zone (building) over artifact.

Return ONLY valid JSON array:
[{{"id": 0, "zone_type": "...", "confidence": 0.9, "reason": "..."}}]

Classify ALL {len(context_data)} polygons.
"""


def _run_contextual_agent(client, model_name, context_data, site_brief_text="", cost_tracker=None) -> List[Dict]:
    """Agent 3: Text/color/context analysis."""
    prompt = _build_contextual_prompt(context_data, site_brief_text)
    log.info("[Ensemble/Contextual] Calling %s with %d polygons...", model_name, len(context_data))
    return _call_gemini(client, model_name, [prompt], cost_tracker=cost_tracker, stage="ensemble_contextual")


# ── Judge Agent ─────────────────────────────────────────────────

def _build_judge_prompt(n_polygons, visual_results, geometric_results, contextual_results, manifest, site_brief_text=""):
    return f"""You are the CHIEF JUDGE in a multi-agent classification system for urban
planning maps. Three specialist agents have independently classified {n_polygons} polygons.
Your job is to produce the FINAL, authoritative classification.
{site_brief_text}
AGENT RESULTS:

Agent 1 (Visual Analyst — looked at the actual drawing images):
{json.dumps(visual_results, indent=2)}

Agent 2 (Geometric Analyst — analysed shape metrics only):
{json.dumps(geometric_results, indent=2)}

Agent 3 (Contextual Analyst — analysed text labels, colors, metadata):
{json.dumps(contextual_results, indent=2)}

POLYGON METADATA (for reference):
{json.dumps(manifest, indent=2)}

YOUR DECISION RULES:
1. UNANIMOUS (all 3 agree): Accept. Confidence = max of the three.
2. MAJORITY (2 of 3 agree): Accept majority. Explain why the dissenter was wrong.
3. FULL DISAGREEMENT: Make your own call based on ALL evidence. Explain thoroughly.

HARD RULES (non-negotiable):
- Exactly ONE plot_boundary (the largest outline encompassing all zones)
- Polygons with extraction="chain_join" or "planar_face" MUST be "sub_zone"
  unless you have overwhelming evidence otherwise (explain in detail)
- NEVER classify a compact, rectangular shape inside a buildable zone as "artifact"
- When in doubt between building and zone → choose "sub_zone"
- Missing buildings is WORSE than misclassifying a zone
- If the SITE BRIEF specifies an expected building count, you MUST classify
  at least that many polygons as "sub_zone". If agents didn't find enough,
  YOU must identify additional building candidates from unclassified polygons.
- If the SITE BRIEF specifies buildable zones, ensure each one contains
  at least one building unless it is explicitly empty.

For EACH polygon, state:
- Which agents agreed/disagreed
- Your final decision and why
- A confidence score reflecting agreement level

Return ONLY valid JSON array:
[{{"id": 0, "zone_type": "...", "confidence": 0.95, "reason": "...", "agreement": "unanimous|majority|judge_call"}}]

You MUST classify ALL {n_polygons} polygons.
"""


def _run_judge_agent(client, model_name, n_polygons, visual, geometric, contextual, manifest, site_brief_text="", cost_tracker=None) -> List[Dict]:
    """Judge: merge and resolve conflicts."""
    prompt = _build_judge_prompt(n_polygons, visual, geometric, contextual, manifest, site_brief_text)
    log.info("[Ensemble/Judge] Calling %s to merge %d polygon classifications...", model_name, n_polygons)
    return _call_gemini(client, model_name, [prompt], cost_tracker=cost_tracker, stage="ensemble_judge")


# ── Ensemble Orchestrator ───────────────────────────────────────

def classify_polygons_ensemble(
    polygons: List[Dict[str, Any]],
    page: fitz.Page,
    page_area: float,
    api_key: str,
    model_name: str = "gemini-2.5-flash",
    model_visual: Optional[str] = None,
    model_geometric: Optional[str] = None,
    model_contextual: Optional[str] = None,
    model_judge: Optional[str] = None,
    site_brief: Optional[Dict[str, Any]] = None,
    cost_tracker: Any = None,
) -> List[Dict[str, Any]]:
    """
    Classify polygons using 3 concurrent specialist agents + 1 judge.

    Per-agent model overrides default to `model_name` (user's global choice).
    `site_brief` provides binding rules from pre-analysis (zones, buildings, GFA).
    """
    if not api_key or not polygons:
        log.info("[Ensemble] No API key or no polygons — skipping")
        return polygons

    # Resolve per-agent models (default to user's global choice)
    m_visual = model_visual or model_name
    m_geometric = model_geometric or model_name
    m_contextual = model_contextual or model_name
    m_judge = model_judge or model_name

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        log.warning("[Ensemble] google-genai SDK not installed — skipping")
        for poly in polygons:
            poly["_ai_sdk_missing"] = True
        return polygons

    try:
        # ── Step 1: Prepare shared data ──
        from app.vector_ingestion.ai_vision_classifier import (
            _render_page_image, _draw_polygon_overlay, _render_building_crops,
            _guess_category,
        )

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

        # Render images
        log.info("[Ensemble] Rendering page images...")
        clean_img = _render_page_image(page, dpi=150)
        annotated_img = _draw_polygon_overlay(page, polygons, dpi=150, page_area=page_area)
        crop_imgs = _render_building_crops(page, polygons, page_area, dpi=200, max_crops=1)

        # Build shared metadata
        manifest = _build_polygon_manifest(polygons, page_area)
        context_data = _build_context_data(polygons, page_area, page)

        # Format site brief for prompts
        brief_text = _format_site_brief_for_prompt(site_brief)
        if brief_text:
            log.info("[Ensemble] Site brief injected into all agent prompts")

        # ── Step 2: Create clients ──
        client = _get_client(api_key)

        # ── Step 3: Run 3 agents concurrently via ThreadPoolExecutor ──
        log.info("[Ensemble] Launching 3 specialist agents concurrently...")

        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(
                    _run_visual_agent, client, m_visual,
                    polygons, clean_img, annotated_img, crop_imgs,
                    brief_text, cost_tracker,
                ): "Visual",
                executor.submit(
                    _run_geometric_agent, client, m_geometric,
                    manifest, page_area, brief_text, cost_tracker,
                ): "Geometric",
                executor.submit(
                    _run_contextual_agent, client, m_contextual,
                    context_data, brief_text, cost_tracker,
                ): "Contextual",
            }
            results_map: Dict[str, Any] = {}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    results_map[name] = future.result()
                except Exception as exc:
                    results_map[name] = exc

        results = [
            results_map.get("Visual", []),
            results_map.get("Geometric", []),
            results_map.get("Contextual", []),
        ]

        # Handle agent failures gracefully
        agent_names = ["Visual", "Geometric", "Contextual"]
        agent_results = []
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                log.error("[Ensemble/%s] Agent failed: %s", agent_names[i], res)
                agent_results.append([])
            else:
                log.info("[Ensemble/%s] Returned %d classifications", agent_names[i], len(res))
                agent_results.append(res)

        visual_res, geometric_res, contextual_res = agent_results

        # If all agents failed, return polygons unchanged
        successful = sum(1 for r in agent_results if len(r) > 0)
        if successful == 0:
            log.error("[Ensemble] All 3 agents failed — skipping classification")
            _cleanup_temp_keys(polygons)
            return polygons

        # ── Step 4: Run judge agent ──
        if successful >= 2:
            log.info("[Ensemble] Running judge agent to merge results...")
            try:
                judge_results = _run_judge_agent(
                    client, m_judge, len(polygons),
                    visual_res, geometric_res, contextual_res, manifest,
                    brief_text, cost_tracker,
                )
                log.info("[Ensemble/Judge] Returned %d final classifications", len(judge_results))
            except Exception as e:
                log.error("[Ensemble/Judge] Failed: %s — using majority vote fallback", e)
                judge_results = _majority_vote_fallback(
                    visual_res, geometric_res, contextual_res, len(polygons)
                )
        else:
            # Only 1 agent succeeded — use its results directly
            log.warning("[Ensemble] Only %d agent(s) succeeded — using single-agent results", successful)
            judge_results = next(r for r in agent_results if len(r) > 0)

        # ── Step 5: Apply results to polygons ──
        _apply_classifications(polygons, judge_results, page_area)

        # ── Step 6: Spatial conflict detection & resolution ──
        conflicts = _detect_spatial_conflicts(polygons)
        if conflicts:
            log.info("[Ensemble] Detected %d spatial conflicts — launching resolver agent...", len(conflicts))
            try:
                _resolve_spatial_conflicts(
                    polygons, conflicts, client, m_judge, manifest, page_area,
                    cost_tracker=cost_tracker,
                )
            except Exception as e:
                log.error("[Ensemble/Resolver] Failed: %s — applying rule-based fallback", e)
                _resolve_conflicts_rule_based(polygons, conflicts)
        else:
            log.info("[Ensemble] No spatial conflicts detected")

        # ── Step 7: Safety nets ──
        _enforce_hard_rules(polygons, page_area)

        _cleanup_temp_keys(polygons)
        return polygons

    except Exception as e:
        log.error("[Ensemble] Classification failed: %s", e)
        log.error("[Ensemble] Traceback:\n%s", traceback.format_exc())
        _cleanup_temp_keys(polygons)
        for poly in polygons:
            poly["_ai_error"] = str(e)
        return polygons


# ── Spatial Conflict Detection & Resolution ─────────────────────

def _detect_spatial_conflicts(polygons):
    """Detect building-vs-building containment and intersections.

    Returns a list of conflict dicts:
      - type: 'containment' | 'intersection'
      - outer_idx / inner_idx (containment) or idx_a / idx_b (intersection)
      - overlap_ratio: how much they overlap
    """
    from shapely.geometry import Polygon as ShapelyPolygon

    # Collect all sub_zone (building) polygons with valid geometry
    buildings = []
    for i, poly in enumerate(polygons):
        ai_type = poly.get("_ai_zone_type", "")
        if ai_type != "sub_zone":
            continue
        sp = poly.get("shapely_poly")
        if sp is None or not sp.is_valid:
            continue
        buildings.append((i, poly, sp))

    if len(buildings) < 2:
        return []

    conflicts = []
    checked = set()

    for ai, a_poly, a_sp in buildings:
        for bi, b_poly, b_sp in buildings:
            if ai >= bi:
                continue
            pair_key = (ai, bi)
            if pair_key in checked:
                continue
            checked.add(pair_key)

            try:
                if not a_sp.intersects(b_sp):
                    continue

                inter_area = a_sp.intersection(b_sp).area
                a_area = a_sp.area
                b_area = b_sp.area

                if a_area <= 0 or b_area <= 0:
                    continue

                # Containment: smaller is >70% inside larger
                if a_area > b_area:
                    ratio = inter_area / b_area
                    if ratio > 0.70:
                        conflicts.append({
                            "type": "containment",
                            "outer_idx": ai,
                            "inner_idx": bi,
                            "outer_area": round(a_area, 1),
                            "inner_area": round(b_area, 1),
                            "containment_ratio": round(ratio, 3),
                            "area_ratio": round(b_area / a_area, 3),
                        })
                        continue
                else:
                    ratio = inter_area / a_area
                    if ratio > 0.70:
                        conflicts.append({
                            "type": "containment",
                            "outer_idx": bi,
                            "inner_idx": ai,
                            "outer_area": round(b_area, 1),
                            "inner_area": round(a_area, 1),
                            "containment_ratio": round(ratio, 3),
                            "area_ratio": round(a_area / b_area, 3),
                        })
                        continue

                # Intersection: significant overlap but not containment
                overlap_a = inter_area / a_area
                overlap_b = inter_area / b_area
                if overlap_a > 0.15 or overlap_b > 0.15:
                    conflicts.append({
                        "type": "intersection",
                        "idx_a": ai,
                        "idx_b": bi,
                        "area_a": round(a_area, 1),
                        "area_b": round(b_area, 1),
                        "overlap_ratio_a": round(overlap_a, 3),
                        "overlap_ratio_b": round(overlap_b, 3),
                    })
            except Exception:
                continue

    log.info("[Ensemble/Conflicts] Found %d containments, %d intersections",
             sum(1 for c in conflicts if c["type"] == "containment"),
             sum(1 for c in conflicts if c["type"] == "intersection"))
    return conflicts


def _build_resolver_prompt(conflicts, manifest, page_area):
    """Build the prompt for the conflict resolver agent."""
    # Group conflicts by outer polygon for containment
    containment_groups = {}
    for c in conflicts:
        if c["type"] == "containment":
            outer = c["outer_idx"]
            containment_groups.setdefault(outer, []).append(c)

    return f"""You are an ARCHITECTURAL MASSING EXPERT specialising in plinth-and-tower
building typologies common in Dutch and European urban planning.

I have detected SPATIAL CONFLICTS between building polygons extracted from a
zoning plan. Your job is to resolve each conflict by determining the correct
architectural relationship.

── CONFLICT DATA ──
{json.dumps(conflicts, indent=2)}

── POLYGON METADATA ──
{json.dumps(manifest, indent=2)}

Page area: {page_area:.0f} sq units

── ARCHITECTURAL KNOWLEDGE ──

CONTAINMENT conflicts (building inside building):
This is VERY COMMON in Dutch zoning plans. The typical pattern is:

  • PLINTH + TOWER(S): A large rectangular footprint (the PLINTH / podium)
    contains one or more smaller footprints (the TOWER buildings on top).
    - The plinth is a lower base structure (commercial/parking, 1-3 floors)
    - The towers rise above the plinth (residential/office, 5-20+ floors)
    - The plinth should be classified as "plinth" (NOT sub_zone)
    - The towers inside remain "sub_zone" (buildings)

  • NESTED BUILDINGS: Sometimes a larger building outline contains a smaller
    courtyard building or annex. Both are buildings at the same level.
    - If the inner polygon is very small (<15% of outer), it's likely an
      internal feature → classify inner as "sub_zone", keep outer as "sub_zone"
    - If the inner polygon is 15-60% of outer, it's likely plinth+tower
      → outer = "plinth", inner = "sub_zone"

INTERSECTION conflicts (buildings overlapping each other):
  • SHARED WALLS: Adjacent row houses or attached buildings may slightly
    overlap due to extraction tolerance. Both should remain "sub_zone".
  • EXTRACTION ERRORS: If overlap is >30%, one polygon is likely a duplicate
    or misclassified zone boundary.
    - Keep the polygon with more building-like proportions
    - Reclassify the other as "buildable_envelope" or "artifact"

── YOUR TASK ──
For each conflict, decide:
1. What is the correct classification for EACH involved polygon?
2. Provide your architectural reasoning

Return ONLY a JSON array of RECLASSIFICATION decisions:
[{{
  "polygon_id": 0,
  "new_zone_type": "plinth",
  "confidence": 0.9,
  "reason": "Large footprint containing 3 tower buildings — classic plinth typology"
}}]

Only include polygons that NEED reclassification. If a polygon should stay
as "sub_zone", do NOT include it in the output.
"""


def _resolve_spatial_conflicts(polygons, conflicts, client, model_name, manifest, page_area, cost_tracker=None):
    """Run the conflict resolver AI agent."""
    prompt = _build_resolver_prompt(conflicts, manifest, page_area)
    log.info("[Ensemble/Resolver] Calling %s to resolve %d conflicts...", model_name, len(conflicts))
    results = _call_gemini(client, model_name, [prompt], cost_tracker=cost_tracker, stage="ensemble_resolver")
    log.info("[Ensemble/Resolver] Returned %d reclassifications", len(results))

    reclassified = 0
    for r in results:
        idx = r.get("polygon_id")
        new_type = r.get("new_zone_type", "")
        if idx is None or new_type not in VALID_ZONE_TYPES:
            continue
        if idx < 0 or idx >= len(polygons):
            continue

        poly = polygons[idx]
        old_type = poly.get("_ai_zone_type", "unknown")
        poly["_ai_zone_type"] = new_type
        poly["_ai_confidence"] = r.get("confidence", 0.85)
        poly["_ai_reason"] = (
            f"conflict_resolved: {old_type}→{new_type} | {r.get('reason', '')}"
        )
        reclassified += 1
        log.info("[Ensemble/Resolver] Polygon %d: %s → %s (%s)",
                 idx, old_type, new_type, r.get("reason", "")[:80])

    # Rule-based fallback for unresolved containment conflicts
    resolved_ids = {r.get("polygon_id") for r in results if r.get("polygon_id") is not None}
    for c in conflicts:
        if c["type"] == "containment":
            outer_idx = c["outer_idx"]
            if outer_idx not in resolved_ids:
                inner_count = sum(
                    1 for cc in conflicts
                    if cc["type"] == "containment" and cc["outer_idx"] == outer_idx
                )
                if inner_count >= 1 and c.get("area_ratio", 0) < 0.70:
                    poly = polygons[outer_idx]
                    if poly.get("_ai_zone_type") == "sub_zone":
                        poly["_ai_zone_type"] = "plinth"
                        poly["_ai_confidence"] = 0.75
                        poly["_ai_reason"] = (
                            f"auto_plinth: contains {inner_count} building(s), "
                            f"area_ratio={c.get('area_ratio', 0):.2f}"
                        )
                        reclassified += 1
                        log.info("[Ensemble/Resolver] Auto-plinth: polygon %d (contains %d buildings)",
                                 outer_idx, inner_count)

    log.info("[Ensemble/Resolver] Total reclassifications: %d", reclassified)


def _resolve_conflicts_rule_based(polygons, conflicts):
    """Pure rule-based fallback when the AI resolver fails."""
    reclassified = 0

    # Containment: outer = plinth if it contains buildings
    plinth_candidates = {}
    for c in conflicts:
        if c["type"] == "containment":
            outer = c["outer_idx"]
            plinth_candidates.setdefault(outer, []).append(c)

    for outer_idx, contained in plinth_candidates.items():
        poly = polygons[outer_idx]
        if poly.get("_ai_zone_type") != "sub_zone":
            continue
        # If the outer polygon contains multiple buildings, or the inner
        # buildings are a significant fraction, classify outer as plinth
        inner_area_sum = sum(c["inner_area"] for c in contained)
        outer_area = contained[0]["outer_area"]
        if len(contained) >= 1 and inner_area_sum / outer_area < 0.85:
            poly["_ai_zone_type"] = "plinth"
            poly["_ai_confidence"] = 0.70
            poly["_ai_reason"] = (
                f"rule_based_plinth: contains {len(contained)} building(s)"
            )
            reclassified += 1

    # Intersections: if overlap > 40% and one is much larger, the smaller
    # stays as building and the larger becomes plinth
    for c in conflicts:
        if c["type"] == "intersection":
            overlap_a = c.get("overlap_ratio_a", 0)
            overlap_b = c.get("overlap_ratio_b", 0)
            # Heavy overlap on one side — smaller is mostly inside larger
            if overlap_a > 0.5 and overlap_b < 0.3:
                # a is mostly inside b → b might be plinth
                pass  # Don't force — ambiguous without AI
            elif overlap_b > 0.5 and overlap_a < 0.3:
                pass
            # Both small overlap — likely shared walls, keep both

    log.info("[Ensemble/Resolver] Rule-based fallback: %d reclassifications", reclassified)


# ── Post-processing helpers ─────────────────────────────────────

def _majority_vote_fallback(visual, geometric, contextual, n_polygons):
    """Fallback: majority vote when judge agent fails."""
    from collections import Counter
    results = []
    for i in range(n_polygons):
        votes = []
        for agent_res in [visual, geometric, contextual]:
            for cls in agent_res:
                if cls.get("id") == i:
                    votes.append(cls.get("zone_type", "unknown"))
                    break
        if not votes:
            results.append({"id": i, "zone_type": "unknown", "confidence": 0.3, "reason": "no agent data"})
            continue
        counter = Counter(votes)
        winner, count = counter.most_common(1)[0]
        conf = 0.6 + (count / len(votes)) * 0.3
        results.append({
            "id": i, "zone_type": winner,
            "confidence": round(conf, 2),
            "reason": f"majority_vote ({count}/{len(votes)})",
            "agreement": "majority" if count >= 2 else "judge_call",
        })
    return results


def _apply_classifications(polygons, judge_results, page_area):
    """Apply judge classifications to polygon objects."""
    correction_map = {}
    for cls in judge_results:
        idx = cls.get("id")
        zone_type = cls.get("zone_type", "")
        if idx is not None and zone_type in VALID_ZONE_TYPES:
            correction_map[idx] = cls

    applied = 0
    buildings_found = 0
    artifacts_removed = 0

    for i, poly in enumerate(polygons):
        if i not in correction_map:
            continue
        cls = correction_map[i]
        new_type = cls["zone_type"]
        agreement = cls.get("agreement", "unknown")

        if new_type == "artifact":
            guess = poly.get("_classification_guess", "")
            strategy = poly.get("_extraction_strategy", "")
            if guess == "building_candidate" or strategy in ("chain_join", "planar_face"):
                poly["_ai_zone_type"] = "sub_zone"
                poly["_ai_confidence"] = 0.70
                poly["_ai_reason"] = f"ensemble({agreement}): artifact overridden→sub_zone (extraction suggests building)"
                buildings_found += 1
            else:
                poly["_is_artifact"] = True
                artifacts_removed += 1
        else:
            poly["_ai_zone_type"] = new_type
            poly["_ai_confidence"] = cls.get("confidence", 0.8)
            poly["_ai_reason"] = f"ensemble({agreement}): {cls.get('reason', '')}"
            if new_type == "sub_zone":
                buildings_found += 1
        applied += 1

    log.info("[Ensemble] Applied %d classifications, %d artifacts, %d buildings",
             applied, artifacts_removed, buildings_found)


def _enforce_hard_rules(polygons, page_area):
    """Enforce non-negotiable classification rules."""
    # Rule: chain_join/planar_face → always sub_zone
    for poly in polygons:
        strategy = poly.get("_extraction_strategy", "")
        if strategy in ("chain_join", "planar_face"):
            ai_type = poly.get("_ai_zone_type", "")
            if ai_type and ai_type != "sub_zone" and not poly.get("_is_artifact"):
                log.info("[Ensemble] Hard rule: %s → sub_zone (extraction=%s)",
                         poly.get("id", "?"), strategy)
                poly["_ai_zone_type"] = "sub_zone"
                poly["_ai_confidence"] = max(poly.get("_ai_confidence", 0), 0.70)
                poly["_ai_reason"] = (
                    poly.get("_ai_reason", "") +
                    f" [hard_rule: {strategy}→sub_zone]"
                )

    # Safety net: force-classify untagged building candidates
    candidate_count = sum(
        1 for p in polygons
        if p.get("_classification_guess") == "building_candidate"
        and not p.get("_ai_zone_type") and not p.get("_is_artifact")
    )
    total_candidates = sum(
        1 for p in polygons if p.get("_classification_guess") == "building_candidate"
    )
    buildings_found = sum(
        1 for p in polygons if p.get("_ai_zone_type") == "sub_zone"
    )
    expected_min = max(int(total_candidates * 0.5), 2)

    if buildings_found < expected_min and candidate_count > 0:
        log.warning("[Ensemble] Safety net: only %d buildings but expected %d — "
                    "forcing %d untagged candidates", buildings_found, expected_min, candidate_count)
        for poly in polygons:
            if (poly.get("_classification_guess") == "building_candidate"
                    and not poly.get("_ai_zone_type")
                    and not poly.get("_is_artifact")):
                poly["_ai_zone_type"] = "sub_zone"
                poly["_ai_confidence"] = 0.65
                poly["_ai_reason"] = "safety_net: unclassified building candidate"

    # Rule: buildable envelopes should contain at least one building
    # If a compact polygon sits inside a buildable_envelope but wasn't
    # classified, force it to sub_zone
    envelopes = [p for p in polygons if p.get("_ai_zone_type") == "buildable_envelope"]
    for env in envelopes:
        env_sp = env.get("shapely_poly")
        if not env_sp:
            continue
        has_building_inside = False
        for p in polygons:
            if p.get("_ai_zone_type") == "sub_zone" or p.get("_ai_zone_type") == "plinth":
                inner_sp = p.get("shapely_poly")
                if inner_sp and env_sp.contains(inner_sp.centroid):
                    has_building_inside = True
                    break
        if not has_building_inside:
            # Look for unclassified compact polygons inside this envelope
            for p in polygons:
                if p is env:
                    continue
                if p.get("_ai_zone_type") or p.get("_is_artifact"):
                    continue
                inner_sp = p.get("shapely_poly")
                if not inner_sp or not env_sp.contains(inner_sp.centroid):
                    continue
                # Check if it's compact enough to be a building
                area_ratio = inner_sp.area / max(env_sp.area, 1)
                if 0.01 < area_ratio < 0.95:
                    p["_ai_zone_type"] = "sub_zone"
                    p["_ai_confidence"] = 0.60
                    p["_ai_reason"] = "hard_rule: compact polygon inside buildable_envelope"
                    log.info("[Ensemble] Hard rule: polygon %s → sub_zone (inside buildable_envelope)",
                             p.get("id", "?"))

    # Rule: sub_zone containing other sub_zones → plinth
    # A plinth is a large footprint with smaller buildings on top.
    sub_zones = [p for p in polygons if p.get("_ai_zone_type") == "sub_zone"]
    if len(sub_zones) > 1:
        for outer in sub_zones:
            outer_sp = outer.get("shapely_poly")
            if not outer_sp:
                continue
            inner_count = 0
            for inner in sub_zones:
                if inner is outer:
                    continue
                inner_sp = inner.get("shapely_poly")
                if not inner_sp:
                    continue
                # Check containment: outer fully contains inner
                try:
                    if outer_sp.contains(inner_sp) or (
                        outer_sp.intersection(inner_sp).area > inner_sp.area * 0.8
                    ):
                        inner_count += 1
                except Exception:
                    continue
            if inner_count >= 1:
                log.info("[Ensemble] Plinth detected: polygon %s contains %d sub_zones → reclassifying as plinth",
                         outer.get("id", "?"), inner_count)
                outer["_ai_zone_type"] = "plinth"
                outer["_ai_confidence"] = max(outer.get("_ai_confidence", 0), 0.80)
                outer["_ai_reason"] = (
                    outer.get("_ai_reason", "") +
                    f" [plinth: contains {inner_count} buildings]"
                )


def _cleanup_temp_keys(polygons):
    """Remove temporary keys used during classification."""
    for poly in polygons:
        poly.pop("_classification_guess", None)
        poly.pop("_shape_metrics_for_ai", None)
