"""
Pipeline stages — Node 3 / Node 4 / Node 5

Separates the monolithic extract_vectors_from_pdf into distinct pipeline
stages matching the 9-node architecture:

  Node 3 — Extract Programme:  text, metadata, programme, site brief
  Node 4 — Detect Units:       units, scale, coordinate origin
  Node 5 — Extract Vector Geometry: path reconstruction + multi-agent ensemble
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────
# Node 3 — Extract Programme
# ────────────────────────────────────────────────────────────────────

def run_extract_programme(
    page,
    page_area: float,
    existing_polys: List[Dict[str, Any]],
    api_key: Optional[str] = None,
    model_name: str = "gemini-2.5-flash",
) -> Dict[str, Any]:
    """
    Stage 3: Extract all text-based metadata, programme info, and produce
    the site brief that guides downstream vector extraction.

    Returns:
        text_blocks:  list of {text, bbox, source}
        site_brief:   structured brief (zones, GFA targets, typology, rules)
    """
    from app.vector_ingestion.site_brief_analyzer import generate_site_brief

    # Extract raw text blocks from the page
    raw_blocks = page.get_text("blocks")
    text_blocks = [
        {"text": tb[4].strip(), "source": "page_text",
         "bbox": [tb[0], tb[1], tb[2], tb[3]]}
        for tb in raw_blocks
        if tb[6] == 0 and tb[4].strip()
    ]

    log.info("[Node3] Extracted %d text blocks from page", len(text_blocks))

    # Generate site brief — regulatory analysis + AI enrichment
    site_brief = None
    if api_key:
        try:
            site_brief = generate_site_brief(
                text_blocks, existing_polys, page_area,
                api_key=api_key,
                model_name=model_name,
            )
            log.info("[Node3] Site brief: %d zones, %d buildings expected, typology=%s",
                     site_brief.get("expected_buildable_zones", 0),
                     site_brief.get("expected_building_count", 0),
                     site_brief.get("dominant_typology", "?"))
        except Exception as e:
            log.warning("[Node3] Site brief generation failed: %s — continuing without", e)
            site_brief = None
    else:
        log.info("[Node3] No API key — skipping AI site brief, regex-only fallback")
        from app.vector_ingestion.site_brief_analyzer import _extract_brief_regex, _count_visual_zones
        regex_brief = _extract_brief_regex(text_blocks)
        zone_analysis = _count_visual_zones(existing_polys, page_area)
        site_brief = {
            "expected_buildable_zones": regex_brief.get("expected_zone_count") or max(len(zone_analysis.get("large_filled_zones", [])), 1),
            "expected_building_count": regex_brief.get("expected_building_count") or max(zone_analysis.get("building_candidates", 0), 1),
            "dominant_typology": regex_brief["typologies"][0] if regex_brief.get("typologies") else "apartment_block",
            "site_gfa_target": max(regex_brief["gfa_values"]) if regex_brief.get("gfa_values") else None,
            "max_height": max(regex_brief["height_values"]) if regex_brief.get("height_values") else None,
            "typologies_detected": regex_brief.get("typologies", []),
            "geometry_analysis": zone_analysis,
        }

    return {
        "text_blocks": text_blocks,
        "site_brief": site_brief,
    }


# ────────────────────────────────────────────────────────────────────
# Node 4 — Detect Units & Coordinates
# ────────────────────────────────────────────────────────────────────

def run_detect_units(
    page,
    text_blocks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Stage 4: Detect drawing units, scale factor, and coordinate origin.

    Returns:
        unit:    detected unit string ('meters', 'millimeters', 'feet', etc.)
        scale:   the normalisation scale factor
        origin:  coordinate origin point [x, y]
        crs:     coordinate reference system label
    """
    import re

    pw, ph = page.rect.width, page.rect.height
    all_text = " ".join(tb.get("text", "") for tb in text_blocks).lower()

    # Detect unit from text
    unit = "meters"  # default
    if re.search(r"\b(?:millimeter|mm)\b", all_text):
        unit = "millimeters"
    elif re.search(r"\b(?:feet|ft|foot)\b", all_text):
        unit = "feet"
    elif re.search(r"\b(?:centimeter|cm)\b", all_text):
        unit = "centimeters"
    elif re.search(r"\b(?:inches?|in)\b", all_text):
        unit = "inches"

    # Scale detection from common notation
    scale = 0.1  # default normalisation scale
    scale_match = re.search(r"1\s*:\s*(\d+)", all_text)
    if scale_match:
        scale_val = int(scale_match.group(1))
        if 50 <= scale_val <= 10000:
            log.info("[Node4] Detected drawing scale 1:%d", scale_val)

    # CRS — typically 'Local' for zoning drawings
    crs = "Local"
    if re.search(r"\b(?:RD|EPSG|WGS|UTM|Amersfoort)\b", all_text, re.IGNORECASE):
        crs = "Projected"

    # Origin = page center (used in normalisation)
    origin = [pw / 2, ph / 2]

    log.info("[Node4] Units=%s, Scale=%.3f, CRS=%s, Origin=[%.1f, %.1f]",
             unit, scale, crs, origin[0], origin[1])

    return {
        "unit": unit,
        "scale": scale,
        "origin": origin,
        "crs": crs,
        "page_width": pw,
        "page_height": ph,
    }
