"""
Site Brief Analyzer — Pre-Analysis for the Vector Extraction Pipeline
=====================================================================

Runs BEFORE Extract Vector Geometry to produce a structured "site brief"
that guides the ensemble classification agents.

The brief answers:
  - How many buildable zones are expected?
  - What building typologies are present? (towers, row houses, detached, etc.)
  - Target GFA per zone / site-wide
  - Expected building count and heights
  - Key dimensional constraints (setbacks, heights, coverage)

This data flows into the ensemble agents as BINDING RULES — the agents
must satisfy them, and the judge agent fills gaps when they can't.
"""
from __future__ import annotations

import json
import logging
import math
import re
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


# ── Regex-based pre-analysis ────────────────────────────────────

_ZONE_COUNT_RE = re.compile(
    r"(\d+)\s*(?:bouw(?:vlak(?:ken)?|zones?)|"
    r"building\s*zones?|buildable\s*(?:areas?|zones?|envelopes?)|"
    r"Baufelder?)",
    re.IGNORECASE,
)

_BUILDING_COUNT_RE = re.compile(
    r"(\d+)\s*(?:gebouwen|woningen|buildings?|dwellings?|units?|blokken|blocks?|"
    r"Gebäude|bâtiments?|logements?)",
    re.IGNORECASE,
)

_TYPOLOGY_PATTERNS = {
    "tower": re.compile(
        r"\b(?:toren|tower|high[- ]?rise|woontoren|"
        r"Hochhaus|tour|gratte[- ]?ciel)\b", re.IGNORECASE,
    ),
    "row_house": re.compile(
        r"\b(?:rijtjes?(?:woning(?:en)?|huis|huizen)?|row\s*hous(?:e|ing|es)|"
        r"terraced|Reihenhaus|Reihenhäuser|maison[s]?\s*en\s*bande)\b", re.IGNORECASE,
    ),
    "apartment_block": re.compile(
        r"\b(?:appartement(?:en)?(?:blok|complex|gebouw)?|apartment\s*(?:block|building)|"
        r"flat(?:gebouw)?|Mehrfamilienhaus|immeuble)\b", re.IGNORECASE,
    ),
    "detached": re.compile(
        r"\b(?:vrijstaand|detached|single[- ]?family|"
        r"Einfamilienhaus|maison\s*individuelle)\b", re.IGNORECASE,
    ),
    "semi_detached": re.compile(
        r"\b(?:twee[- ]?onder[- ]?een[- ]?kap|semi[- ]?detached|"
        r"Doppelhaus|maison\s*jumelée)\b", re.IGNORECASE,
    ),
    "plinth_tower": re.compile(
        r"\b(?:plint|plinth|podium|socle|onderbouw)\b", re.IGNORECASE,
    ),
    "mixed_use": re.compile(
        r"\b(?:gemengd|mixed[- ]?use|gemischt|mixte)\b", re.IGNORECASE,
    ),
}

_GFA_RE = re.compile(
    r"(?:GFA|BVO|BGF|gross\s*floor\s*area|bruto\s*vloer(?:opp(?:ervlak)?)?)"
    r"\s*[:=]?\s*([\d.,]+)\s*(?:m[²2]|sqm)?",
    re.IGNORECASE,
)

_HEIGHT_RE = re.compile(
    r"(?:max(?:imale?)?[\ s._-]*)?(?:nok|goot|bouw)?hoogte"
    r"[\s:=]*(\d+[.,]?\d*)\s*(?:m(?:eter)?)?",
    re.IGNORECASE,
)

_FLOOR_RE = re.compile(
    r"(\d+)\s*(?:verdieping(?:en)?|bouwla(?:ag|gen)|"
    r"floors?|stories?|storeys?|Geschosse?|étages?)",
    re.IGNORECASE,
)


def _extract_brief_regex(text_blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract site brief data using regex patterns from text blocks."""
    all_text = " ".join(tb.get("text", "") for tb in text_blocks)

    brief: Dict[str, Any] = {
        "expected_zone_count": None,
        "expected_building_count": None,
        "typologies": [],
        "gfa_values": [],
        "height_values": [],
        "floor_counts": [],
    }

    # Zone count
    m = _ZONE_COUNT_RE.search(all_text)
    if m:
        val = int(m.group(1))
        if 1 <= val <= 50:
            brief["expected_zone_count"] = val

    # Building count
    m = _BUILDING_COUNT_RE.search(all_text)
    if m:
        val = int(m.group(1))
        if 1 <= val <= 500:
            brief["expected_building_count"] = val

    # Typologies
    for typo, pat in _TYPOLOGY_PATTERNS.items():
        if pat.search(all_text):
            brief["typologies"].append(typo)

    # GFA values
    for m in _GFA_RE.finditer(all_text):
        try:
            val = float(m.group(1).replace(",", "."))
            if 50 < val < 5_000_000:
                brief["gfa_values"].append(round(val, 1))
        except (ValueError, IndexError):
            pass

    # Height values
    for m in _HEIGHT_RE.finditer(all_text):
        try:
            val = float(m.group(1).replace(",", "."))
            if 1 < val < 300:
                brief["height_values"].append(round(val, 1))
        except (ValueError, IndexError):
            pass

    # Floor counts
    for m in _FLOOR_RE.finditer(all_text):
        try:
            val = int(m.group(1))
            if 1 <= val <= 100:
                brief["floor_counts"].append(val)
        except (ValueError, IndexError):
            pass

    return brief


def _count_visual_zones(zones: List[Dict[str, Any]], page_area: float) -> Dict[str, Any]:
    """Analyse initial polygon geometry to count buildable zones and estimate
    building expectations from spatial properties."""
    zone_analysis: Dict[str, Any] = {
        "total_polygons": len(zones),
        "filled_count": 0,
        "unfilled_compact_count": 0,
        "large_filled_zones": [],
        "building_candidates": 0,
    }

    for z in zones:
        area = z.get("area", 0)
        area_pct = area / page_area * 100 if page_area > 0 else 0
        is_filled = z.get("fill") is not None

        if is_filled:
            zone_analysis["filled_count"] += 1
            if area_pct > 1.0:
                zone_analysis["large_filled_zones"].append({
                    "id": z.get("id", ""),
                    "area_pct": round(area_pct, 2),
                })
        else:
            # Compact unfilled shape = likely building
            sp = z.get("shapely_poly")
            if sp:
                try:
                    hull = sp.convex_hull
                    compactness = sp.area / max(hull.area, 0.001)
                    if compactness > 0.4 and 0.01 < area_pct < 5:
                        zone_analysis["unfilled_compact_count"] += 1
                        zone_analysis["building_candidates"] += 1
                except Exception:
                    pass

    return zone_analysis


# ── AI-powered site brief ───────────────────────────────────────

def _generate_brief_with_ai(
    text_blocks: List[Dict[str, Any]],
    regex_brief: Dict[str, Any],
    zone_analysis: Dict[str, Any],
    api_key: str,
    model_name: str,
    cost_tracker=None,
) -> Dict[str, Any]:
    """Use Gemini to produce a detailed site brief from all available context."""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return {}

    try:
        text_content = "\n".join(
            tb.get("text", "") for tb in text_blocks[:50]
        )

        prompt = f"""You are an expert urban planning analyst. I have a regulatory zoning
document. Your task is to produce a SITE BRIEF — a structured analysis that will
guide downstream building-extraction agents.

── EXTRACTED TEXT FROM DOCUMENT ──
{text_content[:4000]}

── REGEX PRE-ANALYSIS ──
{json.dumps(regex_brief, indent=2)}

── GEOMETRY PRE-ANALYSIS ──
{json.dumps(zone_analysis, indent=2)}

── YOUR TASK ──
Produce a comprehensive site brief. Pay special attention to PER-ZONE RULES —
different buildable zones often have different height limits, GFA targets, density
requirements, and typologies. Extract these individually.

Return ONLY valid JSON:
{{
  "expected_buildable_zones": <int>,
  "expected_building_count": <int>,
  "dominant_typology": "<tower|row_house|apartment_block|detached|semi_detached|plinth_tower|mixed_use>",
  "site_gfa_target": <float or null>,
  "max_height": <float or null>,
  "has_plinth_buildings": <bool>,
  "has_underground_parking": <bool>,
  "density_notes": "<string>",
  "special_rules": ["<rule1>", ...],
  "zone_rules": [
    {{
      "zone_index": 0,
      "zone_label": "<name from document, e.g. 'Gemengd' or 'Zone A'>",
      "max_height_m": <float or null>,
      "target_gfa_m2": <float or null>,
      "setback_m": <float or null>,
      "density_grz": <float 0-1 or null>,
      "fsi": <float or null>,
      "expected_buildings": <int>,
      "typology": "<tower|row_house|apartment_block|plinth_tower|mixed_use>",
      "use": "<residential|commercial|mixed_use|retail|office>"
    }},
    ...
  ]
}}

CRITICAL: "zone_rules" must have one entry per buildable zone. If the document
specifies different height/GFA/density for different zones, capture each separately.
If not specified per-zone, distribute the site-wide values proportionally.
"""

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model_name,
            contents=[prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        if cost_tracker is not None:
            cost_tracker.add(response, model_name, stage="site_brief")

        text = response.text.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        result = json.loads(text)
        if isinstance(result, dict):
            log.info("[SiteBrief] AI generated site brief: %d buildable zones, %d buildings, typology=%s",
                     result.get("expected_buildable_zones", "?"),
                     result.get("expected_building_count", "?"),
                     result.get("dominant_typology", "?"))
            return result

    except Exception as e:
        log.error("[SiteBrief] AI brief generation failed: %s", e)

    return {}


# ── Public API ──────────────────────────────────────────────────

def generate_site_brief(
    text_blocks: List[Dict[str, Any]],
    initial_polygons: List[Dict[str, Any]],
    page_area: float,
    api_key: Optional[str] = None,
    model_name: str = "gemini-2.5-flash",
    cost_tracker=None,
) -> Dict[str, Any]:
    """
    Produce a site brief that guides downstream extraction agents.

    Called after Separate Drawing Areas / Extract Metadata, before
    Extract Vector Geometry.

    Returns a structured brief dict with expectations about zones,
    buildings, typologies, and GFA targets.
    """
    log.info("[SiteBrief] Generating site brief from %d text blocks, %d polygons...",
             len(text_blocks), len(initial_polygons))

    # Step 1: Regex-based extraction
    regex_brief = _extract_brief_regex(text_blocks)
    log.info("[SiteBrief] Regex: zones=%s, buildings=%s, typologies=%s, gfa=%s",
             regex_brief["expected_zone_count"],
             regex_brief["expected_building_count"],
             regex_brief["typologies"],
             regex_brief["gfa_values"])

    # Step 2: Geometry analysis
    zone_analysis = _count_visual_zones(initial_polygons, page_area)
    log.info("[SiteBrief] Geometry: %d polygons, %d filled zones, %d building candidates",
             zone_analysis["total_polygons"],
             zone_analysis["filled_count"],
             zone_analysis["building_candidates"])

    # Step 3: AI enrichment
    ai_brief: Dict[str, Any] = {}
    if api_key:
        ai_brief = _generate_brief_with_ai(
            text_blocks, regex_brief, zone_analysis, api_key, model_name,
            cost_tracker=cost_tracker,
        )

    # Merge: AI > regex > geometry inference
    n_zones = (
        ai_brief.get("expected_buildable_zones")
        or regex_brief["expected_zone_count"]
        or max(len(zone_analysis["large_filled_zones"]), 1)
    )
    site_gfa = (
        ai_brief.get("site_gfa_target")
        or (max(regex_brief["gfa_values"]) if regex_brief["gfa_values"] else None)
    )
    max_h = (
        ai_brief.get("max_height")
        or (max(regex_brief["height_values"]) if regex_brief["height_values"] else None)
    )
    expected_floors = (
        max(regex_brief["floor_counts"]) if regex_brief["floor_counts"] else None
    )

    brief = {
        "expected_buildable_zones": n_zones,
        "expected_building_count": (
            ai_brief.get("expected_building_count")
            or regex_brief["expected_building_count"]
            or max(zone_analysis["building_candidates"], 1)
        ),
        "dominant_typology": (
            ai_brief.get("dominant_typology")
            or (regex_brief["typologies"][0] if regex_brief["typologies"] else "apartment_block")
        ),
        "site_gfa_target": site_gfa,
        "max_height": max_h,
        "expected_floors": expected_floors,
        "has_plinth_buildings": ai_brief.get("has_plinth_buildings", False),
        "has_underground_parking": ai_brief.get("has_underground_parking", False),
        "density_notes": ai_brief.get("density_notes", ""),
        "special_rules": ai_brief.get("special_rules", []),
        "typologies_detected": regex_brief["typologies"],
        "geometry_analysis": zone_analysis,
    }

    # ── Build zone_rules (per-zone constraints) ──
    ai_zone_rules = ai_brief.get("zone_rules", [])
    if ai_zone_rules and len(ai_zone_rules) > 0:
        brief["zone_rules"] = ai_zone_rules
        log.info("[SiteBrief] AI provided %d zone_rules", len(ai_zone_rules))
    else:
        # Fallback: generate suggested rules for each expected zone
        default_height = max_h or 15.0
        default_density = 0.4
        default_floors = expected_floors or max(1, int(default_height / 3.0))

        # Estimate site area from page_area (heuristic: ~10% of page is site)
        estimated_site_area = page_area * 0.08  # rough proxy in page units²

        zone_rules = []
        for i in range(n_zones):
            zone_area_share = estimated_site_area / max(n_zones, 1)
            zone_gfa = None
            if site_gfa:
                zone_gfa = round(site_gfa / max(n_zones, 1), 1)
            else:
                # Suggest: zone_area × density × floors
                zone_gfa = round(zone_area_share * default_density * default_floors, 1)

            zone_rules.append({
                "zone_index": i,
                "zone_label": f"Zone {i + 1}",
                "max_height_m": default_height,
                "target_gfa_m2": zone_gfa if zone_gfa and zone_gfa > 50 else None,
                "setback_m": 3.0,
                "density_grz": default_density,
                "fsi": None,
                "expected_buildings": max(1, brief["expected_building_count"] // max(n_zones, 1)),
                "typology": brief["dominant_typology"],
                "use": "mixed_use",
                "source": "ai_suggested",
            })
        brief["zone_rules"] = zone_rules
        log.info("[SiteBrief] Generated %d suggested zone_rules (no per-zone data in documents)",
                 len(zone_rules))

    log.info("[SiteBrief] Final brief: %d zones, %d buildings expected, typology=%s, GFA=%s",
             brief["expected_buildable_zones"],
             brief["expected_building_count"],
             brief["dominant_typology"],
             brief["site_gfa_target"])

    return brief

