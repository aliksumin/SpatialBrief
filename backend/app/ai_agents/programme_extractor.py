"""
Programme Extractor — AI-Powered Building Programme Extraction
================================================================

Extracts building programme data (GFA, uses, floor counts, parking ratios)
from text blocks and zone metadata.

Pipeline:
  1. Regex-based extraction — parse programme values from text
  2. AI extraction — structured Gemini analysis of all text + zone context
  3. AI per-building programme — suggest programme for each building floor
     when documents lack explicit programme data

Falls back gracefully when no API key is available.
"""
from __future__ import annotations

import json
import logging
import math
import re
import traceback
import uuid
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


# ── Regex patterns for programme data ──

_GFA_PATTERNS = [
    # "GFA: 13,500 m2", "gross floor area: 12000 sqm"
    re.compile(
        r"(?:GFA|gross\s*floor\s*area|bruto\s*vloer(?:opp(?:ervlak(?:te)?)?)?|BVO|BGF)"
        r"\s*[:=]?\s*([\d.,]+)\s*(?:m[²2]|sqm)?",
        re.IGNORECASE,
    ),
    # "total area: 5000 m2"
    re.compile(
        r"(?:total|totale?)\s*(?:floor\s*)?(?:area|oppervlak(?:te)?)"
        r"\s*[:=]?\s*([\d.,]+)\s*(?:m[²2]|sqm)?",
        re.IGNORECASE,
    ),
]

_FLOOR_COUNT_PATTERNS = [
    # "5 floors", "3 verdiepingen", "number of floors: 7", "4 Geschosse"
    re.compile(
        r"(\d+)\s*(?:floors?|stories?|storeys?|verdiepingen?|bouwlagen?|Geschosse?|étages?|niveaux?)",
        re.IGNORECASE,
    ),
    # "number of floors: 5"
    re.compile(
        r"(?:number\s*of\s*(?:floors?|stories?)|aantal\s*(?:bouw)?lagen)"
        r"\s*[:=]?\s*(\d+)",
        re.IGNORECASE,
    ),
]

_USE_PATTERNS = [
    # "residential", "commercial", "mixed-use", "office", "retail"
    re.compile(
        r"\b(residential|commercial|mixed[- ]?use|office|retail|wonen|"
        r"kantoor|winkel|gemengd|logement|bureau|commerce|Wohnbau|Gewerbe)\b",
        re.IGNORECASE,
    ),
]

_PARKING_RATIO_PATTERNS = [
    # "1.5 parking spaces per dwelling", "parkeerplaatsen: 2 per woning"
    re.compile(
        r"([\d.,]+)\s*(?:parkeer(?:plaatsen?)?|parking\s*(?:spaces?)?)"
        r"\s*(?:per|/)\s*(?:woning|unit|dwelling|apartment)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:parkeer(?:norm)?|parking\s*ratio)\s*[:=]?\s*([\d.,]+)",
        re.IGNORECASE,
    ),
]

_FLOOR_HEIGHT_PATTERNS = [
    # "floor height: 3.2m", "verdiepingshoogte: 3m"
    re.compile(
        r"(?:floor|storey|verdiepings?)\s*height\s*[:=]?\s*([\d.,]+)\s*(?:m(?:eter)?)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:verdiepings?hoogte)\s*[:=]?\s*([\d.,]+)\s*(?:m(?:eter)?)?",
        re.IGNORECASE,
    ),
]

_PLINTH_PATTERNS = [
    # "commercial ground floor", "plint", "plinth", "commerciële begane grond"
    re.compile(
        r"\b(?:commercial\s*ground\s*floor|plinth|plint|"
        r"commerci[ëe]le?\s*(?:begane\s*grond|plint)|"
        r"retail\s*(?:at\s*)?ground|ground\s*floor\s*(?:retail|commercial))\b",
        re.IGNORECASE,
    ),
]

_UNDERGROUND_PARKING_PATTERNS = [
    # "underground parking", "ondergrondse parkeergarage", "basement parking"
    re.compile(
        r"\b(?:underground\s*park(?:ing|eer)|ondergronds[e]?\s*park(?:ing|eer|garage)|"
        r"basement\s*park(?:ing)?|souterrain|kelder(?:park(?:ing|eer))?|"
        r"Tiefgarage|parking\s*(?:souterrain|sous-sol))\b",
        re.IGNORECASE,
    ),
]


def _parse_number(s: str) -> float:
    """Parse a number from string, handling comma decimals and thousands."""
    s = s.strip().replace(" ", "")
    # Handle European format: 13.500 (thousands) vs 13,5 (decimal)
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        # Could be decimal comma or thousands comma
        parts = s.split(",")
        if len(parts[-1]) == 3 and len(parts) > 1:
            s = s.replace(",", "")  # thousands separator
        else:
            s = s.replace(",", ".")  # decimal separator
    return float(s)


def _make_programme_entry(
    building_id: str,
    building_label: str,
    floors: int,
    floor_height: float,
    total_height: float,
    uses: List[Dict[str, Any]],
    gfa: float,
    has_plinth: bool,
    has_underground_parking: bool,
    parking_ratio: float,
    source: str = "extracted",
    confidence: float = 0.80,
) -> Dict[str, Any]:
    return {
        "id": f"prog_{uuid.uuid4().hex[:8]}",
        "building_id": building_id,
        "building_label": building_label,
        "floors": floors,
        "floor_height": floor_height,
        "total_height": total_height,
        "uses": uses,
        "gfa": gfa,
        "has_plinth": has_plinth,
        "has_underground_parking": has_underground_parking,
        "parking_ratio": parking_ratio,
        "source": source,
        "confidence": round(confidence, 2),
    }


# ── Step 1: Regex-based extraction ──

def _extract_regex(text_blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract programme-level values using regex from text blocks."""
    result: Dict[str, Any] = {
        "gfa_values": [],
        "floor_counts": [],
        "uses": [],
        "parking_ratios": [],
        "floor_heights": [],
        "has_plinth": False,
        "has_underground_parking": False,
    }

    all_text = " ".join(tb.get("text", "") for tb in text_blocks)

    # GFA
    for pat in _GFA_PATTERNS:
        for m in pat.finditer(all_text):
            try:
                val = _parse_number(m.group(1))
                if 10 < val < 1_000_000:
                    result["gfa_values"].append({"value": val, "quote": m.group(0).strip()[:150]})
            except (ValueError, IndexError):
                continue

    # Floor counts
    for pat in _FLOOR_COUNT_PATTERNS:
        for m in pat.finditer(all_text):
            try:
                val = int(m.group(1))
                if 1 <= val <= 100:
                    result["floor_counts"].append({"value": val, "quote": m.group(0).strip()[:150]})
            except (ValueError, IndexError):
                continue

    # Uses
    use_map = {
        "residential": "residential", "wonen": "residential", "logement": "residential", "wohnbau": "residential",
        "commercial": "commercial", "kantoor": "commercial", "bureau": "commercial", "gewerbe": "commercial",
        "office": "office",
        "retail": "retail", "winkel": "retail", "commerce": "retail",
        "mixed-use": "mixed_use", "mixed use": "mixed_use", "gemengd": "mixed_use",
    }
    for pat in _USE_PATTERNS:
        for m in pat.finditer(all_text):
            raw = m.group(1).lower().strip()
            mapped = use_map.get(raw, raw)
            if mapped not in [u["type"] for u in result["uses"]]:
                result["uses"].append({"type": mapped, "quote": m.group(0).strip()[:150]})

    # Parking ratios
    for pat in _PARKING_RATIO_PATTERNS:
        for m in pat.finditer(all_text):
            try:
                val = _parse_number(m.group(1))
                if 0.1 <= val <= 10:
                    result["parking_ratios"].append({"value": val, "quote": m.group(0).strip()[:150]})
            except (ValueError, IndexError):
                continue

    # Floor heights
    for pat in _FLOOR_HEIGHT_PATTERNS:
        for m in pat.finditer(all_text):
            try:
                val = _parse_number(m.group(1))
                if 2.0 <= val <= 8.0:
                    result["floor_heights"].append({"value": val, "quote": m.group(0).strip()[:150]})
            except (ValueError, IndexError):
                continue

    # Plinth detection
    for pat in _PLINTH_PATTERNS:
        if pat.search(all_text):
            result["has_plinth"] = True
            break

    # Underground parking detection
    for pat in _UNDERGROUND_PARKING_PATTERNS:
        if pat.search(all_text):
            result["has_underground_parking"] = True
            break

    return result


# ── Step 2: AI programme extraction ──

def _extract_with_ai(
    text_blocks: List[Dict[str, Any]],
    zones: List[Dict[str, Any]],
    constraints: List[Dict[str, Any]],
    regex_data: Dict[str, Any],
    api_key: str,
    model_name: str,
    cost_tracker=None,
) -> Dict[str, Any]:
    """Use Gemini to extract structured programme data from all context."""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        log.warning("[Programme] google-genai SDK not installed — skipping AI extraction")
        return {}

    try:
        text_content = "\n".join(
            f"[{tb.get('source', '?')}] {tb.get('text', '')}"
            for tb in text_blocks[:40]
        )

        buildings = [z for z in zones if z.get("zone_type") == "sub_zone"]
        building_summary = []
        for b in buildings[:20]:
            building_summary.append({
                "id": b.get("id", ""),
                "label": b.get("zone_label", ""),
                "area": b.get("area_pdf_units", 0),
            })

        constraint_summary = [
            {"name": c["name"], "category": c["category"], "value": c["value"], "unit": c["unit"]}
            for c in constraints[:20]
        ]

        prompt = f"""You are an expert in European urban planning and building programme analysis.

I have a zoning/planning document with the following context:

EXTRACTED TEXT:
{text_content}

BUILDINGS IDENTIFIED (from vector geometry):
{json.dumps(building_summary, indent=2)}

CONSTRAINTS FOUND:
{json.dumps(constraint_summary, indent=2)}

REGEX FINDINGS:
- GFA values: {json.dumps(regex_data.get('gfa_values', []))}
- Floor counts: {json.dumps(regex_data.get('floor_counts', []))}
- Uses detected: {json.dumps(regex_data.get('uses', []))}
- Plinth detected: {regex_data.get('has_plinth', False)}
- Underground parking detected: {regex_data.get('has_underground_parking', False)}

─── YOUR TASK ───
Extract the complete building programme. For EACH building, determine:
1. Number of floors (above ground)
2. Floor height in meters (default 3.0m if not specified)
3. Use per floor (residential, commercial, retail, office, mixed_use)
4. Whether it has a commercial plinth (ground floor retail/commercial)
5. Whether it has underground parking
6. Estimated GFA for this building
7. Parking ratio (spaces per dwelling)

If the document doesn't specify programme for specific buildings, SUGGEST a reasonable
programme based on the constraints, zone context, and typical European practice.

Return ONLY valid JSON:
{{
  "buildings": [
    {{
      "building_id": "...",
      "building_label": "...",
      "floors": 5,
      "floor_height": 3.0,
      "uses": [
        {{"floor": 0, "use": "retail", "label": "Commercial plinth"}},
        {{"floor": 1, "use": "residential", "label": "Apartments"}},
        {{"floor": 2, "use": "residential", "label": "Apartments"}},
        {{"floor": 3, "use": "residential", "label": "Apartments"}},
        {{"floor": 4, "use": "residential", "label": "Apartments"}}
      ],
      "gfa": 2500.0,
      "has_plinth": true,
      "has_underground_parking": true,
      "parking_ratio": 1.5,
      "confidence": 0.7,
      "source": "ai_extracted" or "ai_suggested"
    }}
  ],
  "site_programme": {{
    "total_gfa": 13500.0,
    "total_dwellings": 120,
    "total_parking_spaces": 180,
    "primary_use": "mixed_use"
  }}
}}
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
            cost_tracker.add(response, model_name, stage="programme_extraction")

        text = response.text.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        result = json.loads(text)
        if not isinstance(result, dict):
            return {}

        log.info("[Programme] AI extracted programme for %d buildings",
                 len(result.get("buildings", [])))
        return result

    except Exception as e:
        log.error("[Programme] AI extraction failed: %s", e)
        log.debug("[Programme] Traceback:\n%s", traceback.format_exc())
        return {}


# ── Step 3: Build programme entries from all sources ──

def _build_programme(
    regex_data: Dict[str, Any],
    ai_data: Dict[str, Any],
    zones: List[Dict[str, Any]],
    constraints: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Combine regex + AI data into per-building programme entries."""
    buildings = [z for z in zones if z.get("zone_type") == "sub_zone"]
    if not buildings:
        return []

    ai_buildings = {b["building_id"]: b for b in ai_data.get("buildings", [])
                    if "building_id" in b}

    # Find height constraint
    max_height = None
    for c in constraints:
        if c.get("category") == "height":
            if max_height is None or c["value"] > max_height:
                max_height = c["value"]

    # Default floor height from regex or standard
    default_floor_height = 3.0
    if regex_data.get("floor_heights"):
        default_floor_height = regex_data["floor_heights"][0]["value"]

    # Default floor count from regex
    default_floors = None
    if regex_data.get("floor_counts"):
        default_floors = regex_data["floor_counts"][0]["value"]

    # Determine if plinth/parking detected
    has_plinth_global = regex_data.get("has_plinth", False)
    has_parking_global = regex_data.get("has_underground_parking", False)

    # Default parking ratio
    default_parking_ratio = 1.0
    if regex_data.get("parking_ratios"):
        default_parking_ratio = regex_data["parking_ratios"][0]["value"]

    # Primary use from regex
    primary_uses = [u["type"] for u in regex_data.get("uses", [])]

    programmes = []

    for bldg in buildings:
        bid = bldg.get("id", "")
        blabel = bldg.get("zone_label", "") or f"Building {len(programmes) + 1}"

        # Check if AI has data for this building
        ai_bldg = ai_buildings.get(bid, {})

        if ai_bldg:
            # Use AI data
            floors = ai_bldg.get("floors", default_floors or 4)
            floor_height = ai_bldg.get("floor_height", default_floor_height)
            total_height = floors * floor_height
            if max_height and total_height > max_height:
                floors = max(1, int(max_height / floor_height))
                total_height = floors * floor_height

            uses = ai_bldg.get("uses", [])
            gfa = ai_bldg.get("gfa", 0)
            has_plinth = ai_bldg.get("has_plinth", has_plinth_global)
            has_parking = ai_bldg.get("has_underground_parking", has_parking_global)
            parking_ratio = ai_bldg.get("parking_ratio", default_parking_ratio)
            source = ai_bldg.get("source", "ai_extracted")
            confidence = ai_bldg.get("confidence", 0.75)
        else:
            # Infer from regex data + constraints
            if default_floors:
                floors = default_floors
            elif max_height:
                floors = max(1, int(max_height / default_floor_height))
            else:
                floors = 4  # safe default

            floor_height = default_floor_height
            total_height = floors * floor_height
            if max_height and total_height > max_height:
                floors = max(1, int(max_height / floor_height))
                total_height = floors * floor_height

            # Build floor uses
            uses = []
            primary_use = primary_uses[0] if primary_uses else "residential"
            if has_plinth_global and floors > 1:
                uses.append({"floor": 0, "use": "retail", "label": "Commercial plinth"})
                for f in range(1, floors):
                    uses.append({"floor": f, "use": primary_use, "label": primary_use.replace("_", " ").title()})
            else:
                for f in range(floors):
                    uses.append({"floor": f, "use": primary_use, "label": primary_use.replace("_", " ").title()})

            # Estimate GFA from area
            area = bldg.get("area_pdf_units", 0)
            gfa = area * floors if area > 0 else 0

            has_plinth = has_plinth_global
            has_parking = has_parking_global
            parking_ratio = default_parking_ratio
            source = "inferred"
            confidence = 0.55

        programmes.append(_make_programme_entry(
            building_id=bid,
            building_label=blabel,
            floors=floors,
            floor_height=floor_height,
            total_height=total_height,
            uses=uses,
            gfa=gfa,
            has_plinth=has_plinth,
            has_underground_parking=has_parking,
            parking_ratio=parking_ratio,
            source=source,
            confidence=confidence,
        ))

    return programmes


# ── Main entry point ──

def extract_programme(
    text_blocks: List[Dict[str, Any]],
    zones: List[Dict[str, Any]],
    constraints: List[Dict[str, Any]],
    api_key: Optional[str] = None,
    model_name: str = "gemini-2.5-flash",
    cost_tracker=None,
    site_brief: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Extract building programme from text blocks, zones, and constraints.

    Returns:
        {
            "programmes": [...],          # Per-building programme entries
            "site_programme": {...},      # Site-level summary
            "zone_programmes": [...],     # Per-zone typology + parking decisions
            "regex_data": {...},          # Raw regex findings
            "extraction_summary": {...},  # Stats
        }
    """
    log.info("[Programme] Starting programme extraction from %d text blocks, %d zones, %d constraints",
             len(text_blocks), len(zones), len(constraints))

    # Step 1: Regex extraction (always runs)
    regex_data = _extract_regex(text_blocks)
    log.info("[Programme] Regex found: %d GFA values, %d floor counts, %d uses, plinth=%s, parking=%s",
             len(regex_data["gfa_values"]), len(regex_data["floor_counts"]),
             len(regex_data["uses"]), regex_data["has_plinth"],
             regex_data["has_underground_parking"])

    # Step 2: AI extraction (when API key available)
    ai_data: Dict[str, Any] = {}
    if api_key:
        ai_data = _extract_with_ai(
            text_blocks, zones, constraints, regex_data, api_key, model_name,
            cost_tracker=cost_tracker,
        )

    # Step 3: Build per-building programme entries
    programmes = _build_programme(regex_data, ai_data, zones, constraints)

    # ── Step 4: Build zone_programmes (per-zone typology + parking) ──
    zone_programmes = _build_zone_programmes(
        site_brief=site_brief,
        zones=zones,
        regex_data=regex_data,
        constraints=constraints,
    )

    # Site-level summary
    site_programme = ai_data.get("site_programme", {})
    if not site_programme and programmes:
        total_gfa = sum(p["gfa"] for p in programmes)
        site_programme = {
            "total_gfa": round(total_gfa, 1),
            "total_buildings": len(programmes),
            "total_floors": sum(p["floors"] for p in programmes),
            "has_plinth": any(p["has_plinth"] for p in programmes),
            "has_underground_parking": any(zp.get("parking_levels", 0) > 0 for zp in zone_programmes),
            "primary_use": regex_data["uses"][0]["type"] if regex_data["uses"] else "residential",
        }

    # Summary
    summary = {
        "total_buildings": len(programmes),
        "extracted": len([p for p in programmes if p["source"] == "extracted"]),
        "ai_extracted": len([p for p in programmes if p["source"] == "ai_extracted"]),
        "ai_suggested": len([p for p in programmes if p["source"] == "ai_suggested"]),
        "inferred": len([p for p in programmes if p["source"] == "inferred"]),
        "zone_programmes": len(zone_programmes),
    }

    log.info("[Programme] Done: %d building programmes (%d extracted, %d AI, %d inferred), %d zone programmes",
             summary["total_buildings"], summary["extracted"],
             summary["ai_extracted"], summary["inferred"],
             summary["zone_programmes"])

    return {
        "programmes": programmes,
        "site_programme": site_programme,
        "zone_programmes": zone_programmes,
        "regex_data": regex_data,
        "extraction_summary": summary,
    }


# ── Typology catalog ──

TYPOLOGY_CATALOG = {
    "single_tower":    {"description": "One freestanding high-rise", "coverage": 0.35, "buildings": 1},
    "plinth_tower":    {"description": "Podium base with towers on top", "coverage": 0.70, "buildings": 2},
    "perimeter_block": {"description": "Buildings along zone edges, courtyard inside", "coverage": 0.55, "buildings": 3},
    "row_houses":      {"description": "Linear attached housing", "coverage": 0.45, "buildings": 4},
    "courtyard":       {"description": "U or L-shaped building around courtyard", "coverage": 0.50, "buildings": 1},
    "campus":          {"description": "Multiple detached buildings", "coverage": 0.30, "buildings": 4},
    "infill":          {"description": "Single building filling most of zone", "coverage": 0.75, "buildings": 1},
}


def _infer_typology(zone_area: float, max_height: Optional[float], use: str) -> str:
    """Infer typology from zone characteristics when not provided."""
    if use in ("mixed_use", "commercial") and max_height and max_height > 20:
        return "plinth_tower"
    if max_height and max_height > 40:
        return "single_tower"
    if zone_area > 8000:
        return "campus"
    if zone_area > 4000:
        return "perimeter_block"
    if zone_area > 1500:
        return "courtyard"
    return "infill"


def _is_traffic_or_road(zone: Dict[str, Any]) -> bool:
    """Check if a zone is a traffic/road zone that should never be extruded."""
    zt = zone.get("zone_type", "")
    if zt in ("traffic_zone", "infrastructure_zone", "landscape_zone"):
        return True
    label = (zone.get("zone_label") or "").lower()
    road_keywords = ("verkeer", "traffic", "road", "street", "weg", "straat",
                     "parking lot", "parkeerplaats", "fiets", "bicycle", "voetpad")
    return any(kw in label for kw in road_keywords)

def _build_zone_programmes(
    site_brief: Optional[Dict[str, Any]],
    zones: List[Dict[str, Any]],
    regex_data: Dict[str, Any],
    constraints: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build one programme entry per buildable zone with typology + parking + target GFA.

    Target GFA is the essential driver for the whole pipeline:
      - If found in document → use it
      - If NOT found → infer from: zone_area × coverage_ratio × max_floors
    """
    zone_rules = (site_brief.get("zone_rules", []) if site_brief else [])
    has_parking_global = regex_data.get("has_underground_parking", False)
    default_parking_ratio = 1.0
    if regex_data.get("parking_ratios"):
        default_parking_ratio = regex_data["parking_ratios"][0]["value"]

    # ── Collect all height constraints for per-zone assignment ──
    height_constraints = []
    for c in constraints:
        if c.get("category") == "height" and c.get("value") and c["value"] > 0:
            height_constraints.append(c)

    # Sort heights ascending for smart assignment
    height_values = sorted(set(c["value"] for c in height_constraints))
    log.info("[Programme] Height constraints available: %s", [f"{h:.0f}m" for h in height_values])

    # Global GFA values from regex (total site GFA)
    total_site_gfa = None
    if regex_data.get("gfa_values"):
        total_site_gfa = max(g["value"] for g in regex_data["gfa_values"])
        log.info("[Programme] Total site GFA from document: %.0fm²", total_site_gfa)

    # ── Non-buildable zone types — never extruded ──
    NON_BUILDABLE = {"traffic_zone", "infrastructure_zone", "landscape_zone",
                     "no_build_zone", "cad_context", "context_line", "minor_context"}

    # Find buildable zones
    buildable = [z for z in zones
                 if z.get("zone_type") in ("buildable_envelope", "filled_zone",
                                            "uncategorized_zone")
                 and z.get("zone_type") not in NON_BUILDABLE
                 and z.get("closed")
                 and not _is_traffic_or_road(z)]

    # Also include oversized sub_zones
    plot_area = max((z.get("area_pdf_units", 0) for z in zones
                     if z.get("zone_type") == "plot_boundary"), default=0)
    if not plot_area:
        plot_area = max((z.get("area_pdf_units", 0) for z in zones), default=1)
    for z in zones:
        if z.get("zone_type") == "sub_zone" and z.get("closed"):
            if z.get("area_pdf_units", 0) > plot_area * 0.4:
                if z not in buildable:
                    buildable.append(z)

    # ── Compute total buildable area for proportional GFA splitting ──
    total_buildable_area = sum(bz.get("area_pdf_units", 0) for bz in buildable)
    if total_buildable_area <= 0:
        total_buildable_area = 1

    result = []
    for i, bz in enumerate(buildable):
        bz_id = bz.get("id", f"zone_{i}")
        bz_label = bz.get("zone_label", f"Zone {i + 1}")
        bz_area = bz.get("area_pdf_units", 0)

        # Match to zone_rule
        matched_rule = None
        bz_label_lower = (bz_label or "").lower()
        for zr in zone_rules:
            zr_label = (zr.get("zone_label") or "").lower()
            if zr_label and bz_label_lower and (
                zr_label in bz_label_lower or bz_label_lower in zr_label
            ):
                matched_rule = zr
                break
        if not matched_rule and i < len(zone_rules):
            matched_rule = zone_rules[i]

        # ── Resolve GFA first (it's the primary driver) ──
        floor_height = 3.0
        gfa_source = "inferred"
        target_gfa = None

        # GFA Priority 1: Explicit from document zone_rule
        if matched_rule and matched_rule.get("target_gfa_m2"):
            target_gfa = matched_rule["target_gfa_m2"]
            gfa_source = matched_rule.get("source", "document")

        # GFA Priority 2: Split total site GFA proportionally by zone area
        if target_gfa is None and total_site_gfa and bz_area > 0:
            zone_share = bz_area / total_buildable_area
            target_gfa = round(total_site_gfa * zone_share, 0)
            gfa_source = "proportional"

        has_explicit_gfa = target_gfa is not None

        # ── Height (per-zone) — derived from GFA when possible ──
        height_source = "inferred"
        max_height = None

        # Height Priority 1: Explicit from document zone_rule
        if matched_rule and matched_rule.get("max_height_m"):
            max_height = matched_rule["max_height_m"]
            height_source = matched_rule.get("source", "document")

        # Height Priority 2: From document constraint values
        if max_height is None and height_values:
            if len(height_values) > 1 and len(buildable) > 1:
                # Multiple height values — assign proportionally by zone index
                idx_ratio = i / max(len(buildable) - 1, 1)
                height_idx = min(int(idx_ratio * len(height_values)), len(height_values) - 1)
                max_height = height_values[height_idx]
            else:
                max_height = height_values[0]
            height_source = "document"

        # Height Priority 3: Derive from explicit GFA
        # If we have a known GFA but no height, back-calculate:
        #   floors = GFA / (zone_area × coverage) → height = floors × floor_height
        if max_height is None and has_explicit_gfa and bz_area > 0:
            # Use a sensible coverage estimate for back-calculation
            use = (matched_rule.get("use", "") if matched_rule else "").lower() or "residential"
            temp_typology = _infer_typology(bz_area, None, use)
            temp_coverage = TYPOLOGY_CATALOG.get(temp_typology, {}).get("coverage", 0.5)
            footprint_area = bz_area * temp_coverage
            if footprint_area > 0:
                derived_floors = max(1, math.ceil(target_gfa / footprint_area))
                max_height = round(derived_floors * floor_height, 1)
                height_source = "derived_from_gfa"
                log.info("[Programme] Zone '%s': height derived from GFA: %.0fm² / %.0fm² footprint = %d floors = %.1fm",
                         bz_label, target_gfa, footprint_area, derived_floors, max_height)

        # Height Priority 4: No data at all — use regional/typology defaults
        if max_height is None:
            # Default heights by typical use
            use = (matched_rule.get("use", "") if matched_rule else "").lower() or "residential"
            if use in ("commercial", "office"):
                max_height = 24.0  # ~8 floors
            elif use == "mixed_use":
                max_height = 21.0  # ~7 floors
            else:
                max_height = 15.0  # ~5 floors, typical residential
            height_source = "default"
            log.info("[Programme] Zone '%s': no height data — using default %.0fm for '%s'",
                     bz_label, max_height, use)

        # ── Typology ──
        if matched_rule and matched_rule.get("typology"):
            typology = matched_rule["typology"]
        else:
            use = (matched_rule.get("use", "") if matched_rule else "").lower() or "residential"
            typology = _infer_typology(bz_area, max_height, use)

        coverage = TYPOLOGY_CATALOG.get(typology, {}).get("coverage", 0.5)
        max_floors = max(1, int(max_height / floor_height))

        # GFA Priority 3: Infer from zone geometry + typology + height
        # (only if no explicit GFA was found above)
        if target_gfa is None and bz_area > 0:
            target_gfa = round(bz_area * coverage * max_floors, 0)
            gfa_source = "inferred"
            log.info("[Programme] Zone '%s': inferred GFA = %.0f × %.0f%% × %d floors = %.0fm²",
                     bz_label, bz_area, coverage * 100, max_floors, target_gfa)

        # ── Parking decision ──
        parking_levels = 0
        if has_parking_global:
            parking_levels = 1
            if default_parking_ratio > 1.5:
                parking_levels = 2

        if matched_rule:
            if matched_rule.get("use") in ("commercial", "mixed_use", "office"):
                parking_levels = max(parking_levels, 1)

        result.append({
            "zone_id": bz_id,
            "zone_label": bz_label,
            "zone_index": i,
            "typology": typology,
            "target_gfa_m2": target_gfa,
            "gfa_source": gfa_source,
            "max_height_m": max_height,
            "height_source": height_source,
            "parking_levels": parking_levels,
            "parking_ratio": default_parking_ratio if parking_levels > 0 else 0,
            "use": (matched_rule.get("use") if matched_rule else None) or "residential",
            "expected_buildings": TYPOLOGY_CATALOG.get(typology, {}).get("buildings", 1),
            "source": matched_rule.get("source", "inferred") if matched_rule else "inferred",
        })

    log.info("[Programme] Built %d zone_programmes: %s",
             len(result),
             [(zp["zone_label"], zp["typology"],
               f"GFA={zp['target_gfa_m2']:.0f}m²" if zp['target_gfa_m2'] else "GFA=?",
               f"H={zp['max_height_m']:.0f}m",
               f"parking={zp['parking_levels']}") for zp in result])

    return result

