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
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────
# Node 3 — Extract Programme
# ────────────────────────────────────────────────────────────────────

def _is_relevant_image_block(block, page_area: float) -> bool:
    """
    Quick pre-screen: does this image block likely contain valuable
    spatial / regulatory data (site plan, diagram, table with GFA)?

    Returns False for tiny icons, logos, decorative images, and
    very large full-page backgrounds. Only True for mid-sized images
    that are likely to be diagrams or site plans.
    """
    # block format: (x0, y0, x1, y1, image_data, block_no, block_type)
    # block_type == 1 means image block
    if len(block) < 7 or block[6] != 1:
        return False

    x0, y0, x1, y1 = block[0], block[1], block[2], block[3]
    w = x1 - x0
    h = y1 - y0
    area = w * h

    # Skip tiny images (icons, bullets, logos) — less than 1% of page
    if area < page_area * 0.01:
        return False

    # Skip full-page background images — more than 90% of page
    if area > page_area * 0.90:
        return False

    # Skip very narrow strips (decorative borders, lines)
    aspect = max(w, h) / max(min(w, h), 1)
    if aspect > 15:
        return False

    return True


def run_extract_programme(
    page,
    page_area: float,
    api_key: Optional[str] = None,
    model_name: str = "gemini-2.5-flash",
    cost_tracker=None,
) -> Dict[str, Any]:
    """
    Stage 3: Extract all text-based metadata and programme info, and
    produce the site brief that guides downstream vector extraction.

    This is a FAST stage — it works from text only.
    No heavy polygon reconstruction; no image rendering.
    Image blocks are pre-screened and only counted (not sent to AI).

    Returns:
        text_blocks:  list of {text, bbox, source}
        site_brief:   structured brief (zones, GFA targets, typology, rules)
        image_count:  number of relevant images detected (for info)
    """
    t0 = time.time()

    # ── Extract text blocks (instant — local PDF parsing) ──
    raw_blocks = page.get_text("blocks")
    text_blocks = []
    image_count = 0
    relevant_images = 0

    for tb in raw_blocks:
        if tb[6] == 0 and tb[4].strip():
            # Text block
            text_blocks.append({
                "text": tb[4].strip(),
                "source": "page_text",
                "bbox": [tb[0], tb[1], tb[2], tb[3]],
            })
        elif tb[6] == 1:
            # Image block — pre-screen for relevance
            image_count += 1
            if _is_relevant_image_block(tb, page_area):
                relevant_images += 1

    log.info("[Node3] Extracted %d text blocks, %d images (%d relevant) in %.1fs",
             len(text_blocks), image_count, relevant_images,
             time.time() - t0)

    # ── Generate site brief — text-only, no geometry needed ──
    # The site brief only needs text data to extract programme info.
    # Heavy geometry analysis happens in Node 5.
    site_brief = None
    if api_key:
        from app.vector_ingestion.site_brief_analyzer import generate_site_brief
        try:
            t1 = time.time()
            site_brief = generate_site_brief(
                text_blocks,
                [],   # No polygons needed — keep this stage fast
                page_area,
                api_key=api_key,
                model_name=model_name,
                cost_tracker=cost_tracker,
            )
            log.info("[Node3] Site brief generated in %.1fs: %d zones, %d buildings, typology=%s",
                     time.time() - t1,
                     site_brief.get("expected_buildable_zones", 0),
                     site_brief.get("expected_building_count", 0),
                     site_brief.get("dominant_typology", "?"))
        except Exception as e:
            log.warning("[Node3] Site brief generation failed: %s — continuing without", e)
            site_brief = None
    else:
        log.info("[Node3] No API key — regex-only site brief")
        from app.vector_ingestion.site_brief_analyzer import _extract_brief_regex
        regex_brief = _extract_brief_regex(text_blocks)
        site_brief = {
            "expected_buildable_zones": regex_brief.get("expected_zone_count") or 1,
            "expected_building_count": regex_brief.get("expected_building_count") or 1,
            "dominant_typology": regex_brief["typologies"][0] if regex_brief.get("typologies") else "apartment_block",
            "site_gfa_target": max(regex_brief["gfa_values"]) if regex_brief.get("gfa_values") else None,
            "max_height": max(regex_brief["height_values"]) if regex_brief.get("height_values") else None,
            "typologies_detected": regex_brief.get("typologies", []),
        }

    log.info("[Node3] Total time: %.1fs", time.time() - t0)

    return {
        "text_blocks": text_blocks,
        "site_brief": site_brief,
        "image_count": image_count,
        "relevant_images": relevant_images,
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
