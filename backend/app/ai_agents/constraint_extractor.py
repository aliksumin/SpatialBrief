"""
Constraint Extractor — AI-Powered Regulatory Constraint Extraction
===================================================================

Extracts regulatory constraints (heights, setbacks, densities, parking)
from extracted text blocks and zone metadata.

Pipeline:
  1. Regex-based extraction — parse numeric values from text
  2. AI extraction — structured Gemini analysis of all text + zone context
  3. AI gap-fill — suggest regional defaults when data is sparse
  4. Geometry generation — create setback offset polygons as dashed lines

Falls back gracefully when no API key is available.
"""
from __future__ import annotations

import json
import logging
import math
import re
import traceback
import uuid
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


# ── Regex patterns for common constraint values ──

_HEIGHT_PATTERNS = [
    # Dutch: "max hoogte: 12m", "nokhoogte: 9 m", "goothoogte 6.5m"
    re.compile(
        r"(?:max(?:imale?)?[\s._-]*)?(?:nok|goot|bouw)?hoogte"
        r"[\s:=]*(\d+[.,]?\d*)\s*(?:m(?:eter)?)?",
        re.IGNORECASE,
    ),
    # English: "max height: 12m", "height limit: 30m"
    re.compile(
        r"(?:max(?:imum)?[\s._-]*)?(?:building[\s._-]*)?height"
        r"[\s:=]*(\d+[.,]?\d*)\s*(?:m(?:eters?)?)?",
        re.IGNORECASE,
    ),
    # German: "Firsthöhe: 12m", "Traufhöhe: 9m"
    re.compile(
        r"(?:First|Trauf|Gebäude|max(?:imale?)?[\s._-]*)?"
        r"[Hh]öhe[\s:=]*(\d+[.,]?\d*)\s*(?:m(?:eter)?)?",
        re.IGNORECASE,
    ),
]

_SETBACK_PATTERNS = [
    # Dutch: "rooilijn: 5m", "afstand: 3m", "achtergevellijn"
    re.compile(
        r"(?:voor|achter|zij)?(?:gevel)?(?:rooilijn|afstand)"
        r"[\s:=]*(\d+[.,]?\d*)\s*(?:m(?:eter)?)?",
        re.IGNORECASE,
    ),
    # English: "setback: 5m", "front setback: 3m"
    re.compile(
        r"(?:front|rear|side|min(?:imum)?)?[\s._-]*setback"
        r"[\s:=]*(\d+[.,]?\d*)\s*(?:m(?:eters?)?)?",
        re.IGNORECASE,
    ),
    # German: "Abstand: 5m", "Baugrenze"
    re.compile(
        r"(?:Grenz)?[Aa]bstand[\s:=]*(\d+[.,]?\d*)\s*(?:m(?:eter)?)?",
        re.IGNORECASE,
    ),
]

_DENSITY_PATTERNS = [
    # Dutch: "bebouwingspercentage: 60%"
    re.compile(
        r"bebouwings[\s._-]*(?:percentage|graad)"
        r"[\s:=]*(\d+[.,]?\d*)\s*%?",
        re.IGNORECASE,
    ),
    # FSI / FAR
    re.compile(
        r"(?:FSI|FAR|floor[\s._-]*(?:space|area)[\s._-]*(?:index|ratio))"
        r"[\s:=]*(\d+[.,]?\d*)",
        re.IGNORECASE,
    ),
    # German: "GRZ: 0.4", "GFZ: 1.2"
    re.compile(r"(?:GRZ|GFZ)[\s:=]*(\d+[.,]?\d*)", re.IGNORECASE),
]

_PARKING_PATTERNS = [
    re.compile(
        r"(?:parkeer(?:plaatsen|norm)?|parking)"
        r"[\s:=]*(\d+[.,]?\d*)\s*(?:per|/)\s*(?:woning|unit|dwelling)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(\d+[.,]?\d*)\s*(?:parkeer(?:plaatsen)?|parking\s*(?:spaces?)?)"
        r"\s*(?:per|/)\s*(?:woning|unit|dwelling)",
        re.IGNORECASE,
    ),
]

_GFA_CONSTRAINT_PATTERNS = [
    # "GFA: 13,500 m2", "gross floor area: 12000 sqm", "BVO: 8500 m²"
    re.compile(
        r"(?:GFA|gross\s*floor\s*area|bruto\s*vloer(?:opp(?:ervlak(?:te)?)?)?|BVO|BGF)"
        r"\s*[:=]?\s*([\d.,]+)\s*(?:m[²2]|sqm)?",
        re.IGNORECASE,
    ),
    # "total area: 5000 m2", "totale oppervlakte: 8000 m2"
    re.compile(
        r"(?:total|totale?)\s*(?:floor\s*)?(?:area|oppervlak(?:te)?)"
        r"\s*[:=]?\s*([\d.,]+)\s*(?:m[²2]|sqm)?",
        re.IGNORECASE,
    ),
    # "target GFA: 12000", "beoogde BVO: 10000"
    re.compile(
        r"(?:target|beoogd[e]?|max(?:imale?)?|minimum?)\s*"
        r"(?:GFA|BVO|BGF|floor\s*area)"
        r"\s*[:=]?\s*([\d.,]+)\s*(?:m[²2]|sqm)?",
        re.IGNORECASE,
    ),
]


def _parse_number(s: str) -> float:
    """Parse a number from string, handling comma decimals."""
    return float(s.replace(",", "."))


def _make_constraint(
    name: str,
    category: str,
    value: float,
    unit: str,
    raw_quote: str,
    source: str = "extracted",
    confidence: float = 0.85,
    applies_to: str = "",
) -> Dict[str, Any]:
    return {
        "id": f"cst_{uuid.uuid4().hex[:8]}",
        "name": name,
        "category": category,
        "value": value,
        "unit": unit,
        "applies_to": applies_to,
        "source": source,
        "raw_quote": raw_quote.strip()[:200],
        "confidence": round(confidence, 2),
        "geometry": None,
    }


# ── Step 1: Regex-based extraction ──

def _extract_regex(text_blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract constraints using regex patterns from text blocks."""
    constraints: List[Dict[str, Any]] = []
    seen_values: set = set()  # (category, value) to avoid duplicates

    for tb in text_blocks:
        text = tb.get("text", "")
        if not text:
            continue

        # Heights
        for pat in _HEIGHT_PATTERNS:
            for m in pat.finditer(text):
                val = _parse_number(m.group(1))
                if val < 1 or val > 200:
                    continue
                key = ("height", val)
                if key in seen_values:
                    continue
                seen_values.add(key)

                # Determine sub-type from context
                ctx = m.group(0).lower()
                if "nok" in ctx or "first" in ctx or "ridge" in ctx:
                    name = "Ridge Height (nokhoogte)"
                elif "goot" in ctx or "trauf" in ctx or "gutter" in ctx:
                    name = "Gutter Height (goothoogte)"
                else:
                    name = "Maximum Building Height"

                constraints.append(_make_constraint(
                    name=name, category="height",
                    value=val, unit="m",
                    raw_quote=m.group(0),
                ))

        # Setbacks
        for pat in _SETBACK_PATTERNS:
            for m in pat.finditer(text):
                val = _parse_number(m.group(1))
                if val < 0.5 or val > 100:
                    continue
                key = ("setback", val)
                if key in seen_values:
                    continue
                seen_values.add(key)

                ctx = m.group(0).lower()
                if "voor" in ctx or "front" in ctx:
                    name = "Front Setback"
                elif "achter" in ctx or "rear" in ctx:
                    name = "Rear Setback"
                elif "zij" in ctx or "side" in ctx:
                    name = "Side Setback"
                else:
                    name = "Setback Line"

                constraints.append(_make_constraint(
                    name=name, category="setback",
                    value=val, unit="m",
                    raw_quote=m.group(0),
                ))

        # Density / Coverage
        for pat in _DENSITY_PATTERNS:
            for m in pat.finditer(text):
                val = _parse_number(m.group(1))
                ctx = m.group(0).upper()
                if "GRZ" in ctx or "bebouwing" in m.group(0).lower():
                    if val > 1:
                        val = val / 100  # Convert percentage to ratio
                    name = "Building Coverage (GRZ)"
                    unit = "ratio"
                elif "GFZ" in ctx or "FSI" in ctx or "FAR" in ctx:
                    name = "Floor Space Index (FSI)"
                    unit = "ratio"
                else:
                    name = "Build Percentage"
                    unit = "%"
                key = ("density", val)
                if key in seen_values:
                    continue
                seen_values.add(key)
                constraints.append(_make_constraint(
                    name=name, category="density",
                    value=val, unit=unit,
                    raw_quote=m.group(0),
                ))

        # Parking
        for pat in _PARKING_PATTERNS:
            for m in pat.finditer(text):
                val = _parse_number(m.group(1))
                if val < 0.1 or val > 20:
                    continue
                key = ("parking", val)
                if key in seen_values:
                    continue
                seen_values.add(key)
                constraints.append(_make_constraint(
                    name="Parking Norm",
                    category="parking",
                    value=val, unit="per dwelling",
                    raw_quote=m.group(0),
                ))

        # GFA / Target Floor Area
        for pat in _GFA_CONSTRAINT_PATTERNS:
            for m in pat.finditer(text):
                try:
                    val = _parse_number(m.group(1))
                except (ValueError, IndexError):
                    continue
                if val < 50 or val > 5_000_000:
                    continue
                key = ("gfa", val)
                if key in seen_values:
                    continue
                seen_values.add(key)

                ctx = m.group(0).lower()
                if "target" in ctx or "beoogd" in ctx:
                    name = "Target GFA"
                elif "max" in ctx:
                    name = "Maximum GFA"
                elif "min" in ctx:
                    name = "Minimum GFA"
                else:
                    name = "Gross Floor Area"

                constraints.append(_make_constraint(
                    name=name, category="gfa",
                    value=val, unit="m²",
                    raw_quote=m.group(0),
                ))

    return constraints


# ── Step 2: AI extraction ──

def _extract_with_ai(
    text_blocks: List[Dict[str, Any]],
    zones: List[Dict[str, Any]],
    existing_constraints: List[Dict[str, Any]],
    api_key: str,
    model_name: str,
    cost_tracker=None,
) -> List[Dict[str, Any]]:
    """Use Gemini to extract structured constraints from text + zone context."""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        log.warning("[Constraints] google-genai SDK not installed — skipping AI extraction")
        return []

    try:
        # Prepare text context
        text_content = "\n".join(
            f"[{tb.get('source', '?')}] {tb.get('text', '')}"
            for tb in text_blocks[:40]
        )

        # Prepare zone summary
        zone_summary = []
        for z in zones[:30]:
            zone_summary.append({
                "id": z.get("id", ""),
                "zone_type": z.get("zone_type", ""),
                "area": z.get("area_pdf_units", 0),
                "label": z.get("zone_label", ""),
            })

        # Already-found constraints
        existing_summary = [
            {"name": c["name"], "value": c["value"], "unit": c["unit"]}
            for c in existing_constraints
        ]

        prompt = f"""You are an expert in European urban planning regulations (Dutch bestemmingsplan,
German Bebauungsplan, French PLU, UK planning regulations).

I have extracted text and zone geometry from a regulatory zoning document.
Some constraints have already been found via pattern matching:
{json.dumps(existing_summary, indent=2)}

Here is the extracted text from the document:
{text_content}

Here are the classified zones:
{json.dumps(zone_summary, indent=2)}

─── YOUR TASK ───
Find ALL regulatory constraints that are stated or implied in the text.
Focus on constraints NOT already found above.

For each constraint, identify:
- name: descriptive name (e.g. "Maximum Building Height", "Front Setback")
- category: one of: height | setback | density | programme | parking | environmental | facade | access | other
- value: numeric value (float)
- unit: measurement unit (m, %, ratio, per dwelling, etc.)
- applies_to: which zone_type or zone id this applies to (or "all" if site-wide)
- raw_quote: the exact text this was extracted from
- confidence: 0.0-1.0

Return ONLY valid JSON — an array of constraint objects:
[
  {{"name": "...", "category": "...", "value": ..., "unit": "...", "applies_to": "...", "raw_quote": "...", "confidence": ...}}
]

If no additional constraints are found, return an empty array: []
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
            cost_tracker.add(response, model_name, stage="constraint_extraction")

        text = response.text.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        results = json.loads(text)
        if not isinstance(results, list):
            return []

        constraints = []
        for r in results:
            if not r.get("name") or r.get("value") is None:
                continue
            constraints.append(_make_constraint(
                name=r["name"],
                category=r.get("category", "other"),
                value=float(r["value"]),
                unit=r.get("unit", ""),
                raw_quote=r.get("raw_quote", ""),
                source="ai_extracted",
                confidence=r.get("confidence", 0.75),
                applies_to=r.get("applies_to", ""),
            ))

        log.info("[Constraints] AI extracted %d additional constraints", len(constraints))
        return constraints

    except Exception as e:
        log.error("[Constraints] AI extraction failed: %s", e)
        log.debug("[Constraints] Traceback:\n%s", traceback.format_exc())
        return []


# ── Step 3: AI gap-fill with regional defaults ──

_REGION_KEYWORDS = {
    "nl": ["bestemmingsplan", "bebouwingspercentage", "goothoogte", "nokhoogte",
           "rooilijn", "bouwvlak", "kavel", "grens", "woning", "perceel"],
    "de": ["bebauungsplan", "grundflächenzahl", "geschossflächenzahl",
           "firsthöhe", "traufhöhe", "baugrenze", "abstand"],
    "fr": ["plu", "urbanisme", "hauteur", "recul", "emprise", "cos"],
    "uk": ["planning permission", "storey", "setback", "building line"],
}


def _detect_region(text_blocks: List[Dict[str, Any]]) -> str:
    """Detect the likely region from text content."""
    all_text = " ".join(tb.get("text", "") for tb in text_blocks).lower()
    scores = {}
    for region, keywords in _REGION_KEYWORDS.items():
        scores[region] = sum(1 for kw in keywords if kw in all_text)
    best = max(scores, key=scores.get)
    return best if scores[best] >= 2 else "eu"


def _gap_fill_with_ai(
    existing_constraints: List[Dict[str, Any]],
    text_blocks: List[Dict[str, Any]],
    api_key: str,
    model_name: str,
    cost_tracker=None,
) -> List[Dict[str, Any]]:
    """If constraints are sparse, suggest regional defaults."""
    if len(existing_constraints) >= 3:
        return []  # Enough constraints found, no gap-fill needed

    # Check which categories are actually missing
    existing_categories = {c["category"] for c in existing_constraints}
    essential = {"height", "setback", "density", "parking"}
    missing_categories = essential - existing_categories
    if not missing_categories:
        return []  # All essential categories covered

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return []

    try:
        region = _detect_region(text_blocks)
        region_names = {
            "nl": "Dutch bestemmingsplan (Netherlands)",
            "de": "German Bebauungsplan (Germany)",
            "fr": "French PLU (France)",
            "uk": "UK planning regulations",
            "eu": "European urban planning",
        }
        region_name = region_names.get(region, "European urban planning")

        existing_categories = {c["category"] for c in existing_constraints}
        missing_categories = {"height", "setback", "density", "parking"} - existing_categories

        if not missing_categories:
            return []

        prompt = f"""You are an expert in {region_name} regulations.

A zoning document has been analyzed but the following constraint categories
were NOT found in the text: {', '.join(sorted(missing_categories))}

Already extracted constraints:
{json.dumps([{{"name": c["name"], "value": c["value"], "unit": c["unit"]}} for c in existing_constraints], indent=2)}

For a TYPICAL residential zoning plan in this jurisdiction, suggest reasonable
default values for the missing categories. These are SUGGESTIONS only, not
extracted values.

Return ONLY valid JSON — an array:
[
  {{"name": "...", "category": "...", "value": ..., "unit": "...", "rationale": "..."}}
]

Only suggest for categories that are genuinely missing. Return [] if unsure.
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
            cost_tracker.add(response, model_name, stage="constraint_gap_fill")

        text = response.text.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        results = json.loads(text)
        if not isinstance(results, list):
            return []

        suggestions = []
        for r in results:
            if not r.get("name") or r.get("value") is None:
                continue
            suggestions.append(_make_constraint(
                name=r["name"],
                category=r.get("category", "other"),
                value=float(r["value"]),
                unit=r.get("unit", ""),
                raw_quote=r.get("rationale", f"AI suggested ({region_name} default)"),
                source="ai_suggested",
                confidence=0.45,
                applies_to="all",
            ))

        log.info("[Constraints] AI suggested %d regional defaults (%s)",
                 len(suggestions), region_name)
        return suggestions

    except Exception as e:
        log.error("[Constraints] AI gap-fill failed: %s", e)
        return []


# ── Step 4: Constraint geometry generation ──

def _generate_constraint_geometry(
    constraints: List[Dict[str, Any]],
    zones: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Generate visual geometry for ALL constraint types.

    Produces three kinds of constraint geometry:
    1. Setback offset lines — inward offset from plot boundary
    2. Height limit rings — contour at max height on each building footprint
    3. Buildable envelope boundary — derived from existing zones when
       no explicit setback values were found

    Returns a list of geometry dicts ready for the viewport.
    """
    geometry: List[Dict[str, Any]] = []

    # ── Find key zones ──
    plot = None
    buildable = None
    buildings: List[Dict[str, Any]] = []
    no_build_zones: List[Dict[str, Any]] = []

    for z in zones:
        zt = z.get("zone_type", "")
        if zt == "plot_boundary" and z.get("closed"):
            if plot is None:
                plot = z
        elif zt == "buildable_envelope" and z.get("closed"):
            if buildable is None:
                buildable = z
        elif zt == "sub_zone" and z.get("closed"):
            buildings.append(z)
        elif zt in ("no_build_zone", "restriction_line") and z.get("closed"):
            no_build_zones.append(z)

    # Helper to extract 2D points from a zone
    def _to_2d(pts, is_3d):
        result = []
        for pt in pts:
            if is_3d:
                result.append((pt[0], pt[2]))
            else:
                result.append((pt[0], pt[1]))
        return result

    def _to_3d(coords_2d, is_3d, y=0):
        result = []
        for c in coords_2d:
            if is_3d:
                result.append([round(c[0], 4), round(y, 4), round(c[1], 4)])
            else:
                result.append([round(c[0], 4), round(c[1], 4)])
        return result

    # ── 1. Explicit setback offset lines ──
    if plot:
        plot_points = plot.get("points", [])
        if len(plot_points) >= 4:
            setback_constraints = [c for c in constraints if c["category"] == "setback"]
            for cst in setback_constraints:
                offset_dist = cst["value"]
                if offset_dist <= 0:
                    continue
                try:
                    from shapely.geometry import Polygon as ShapelyPolygon

                    is_3d = len(plot_points[0]) == 3
                    pts_2d = _to_2d(plot_points, is_3d)
                    if pts_2d[0] != pts_2d[-1]:
                        pts_2d.append(pts_2d[0])

                    poly = ShapelyPolygon(pts_2d)
                    if not poly.is_valid:
                        from shapely.validation import make_valid
                        poly = make_valid(poly)

                    coords = list(poly.exterior.coords)
                    xs = [c[0] for c in coords]
                    zs = [c[1] for c in coords]
                    span = max(max(xs) - min(xs), max(zs) - min(zs), 1)

                    viewport_offset = offset_dist * (span / 100.0)
                    viewport_offset = max(viewport_offset, 0.3)

                    offset_poly = poly.buffer(-viewport_offset, join_style=2)
                    if offset_poly.is_empty:
                        continue

                    if hasattr(offset_poly, 'exterior'):
                        offset_coords = list(offset_poly.exterior.coords)
                    elif hasattr(offset_poly, 'geoms'):
                        largest = max(offset_poly.geoms, key=lambda g: g.area)
                        offset_coords = list(largest.exterior.coords)
                    else:
                        continue

                    pts_3d = _to_3d(offset_coords, is_3d)

                    name_lower = cst["name"].lower()
                    if "front" in name_lower or "voor" in name_lower:
                        color = "#ef4444"
                    elif "rear" in name_lower or "achter" in name_lower:
                        color = "#f97316"
                    elif "side" in name_lower or "zij" in name_lower:
                        color = "#eab308"
                    else:
                        color = "#ef4444"

                    geometry.append({
                        "id": f"cst_geo_{uuid.uuid4().hex[:8]}",
                        "type": "Polygon",
                        "zone_type": "setback_line",
                        "zone_label": f"{cst['name']} ({cst['value']}{cst['unit']})",
                        "points": pts_3d,
                        "closed": True,
                        "color_hint": color,
                        "stroke_width": 2.0,
                        "dashed": True,
                        "confidence": cst["confidence"],
                        "classification_method": "constraint_offset",
                        "constraint_id": cst["id"],
                        "constraint_value": f"{cst['value']}{cst['unit']}",
                    })
                    log.info("[Constraints] Generated setback geometry for '%s' (%.1f%s)",
                             cst["name"], cst["value"], cst["unit"])

                except Exception as e:
                    log.warning("[Constraints] Setback geometry failed for '%s': %s",
                                cst["name"], e)

    # ── 2. Derived setback from buildable_envelope ──
    # If there's a buildable_envelope zone but no explicit setback geometry
    # was generated, re-emit the buildable_envelope as a constraint contour.
    has_setback_geo = any(g["zone_type"] == "setback_line" for g in geometry)
    if buildable and not has_setback_geo:
        be_pts = buildable.get("points", [])
        if len(be_pts) >= 4:
            is_3d = len(be_pts[0]) == 3
            pts_3d = _to_3d(_to_2d(be_pts, is_3d), is_3d)

            geometry.append({
                "id": f"cst_geo_{uuid.uuid4().hex[:8]}",
                "type": "Polygon",
                "zone_type": "setback_line",
                "zone_label": "Buildable Envelope (bouwvlak)",
                "points": pts_3d,
                "closed": True,
                "color_hint": "#ef4444",
                "stroke_width": 2.5,
                "dashed": True,
                "confidence": buildable.get("confidence", 0.7),
                "classification_method": "derived_from_buildable_envelope",
                "constraint_value": "derived",
                "marker_labels": [],
            })
            log.info("[Constraints] Derived setback contour from buildable_envelope zone")

    # ── 3. Height limit contours ──
    height_constraints = [c for c in constraints if c["category"] == "height"]
    if height_constraints and buildings:
        # Use the maximum height constraint
        max_height_cst = max(height_constraints, key=lambda c: c["value"])
        height_val = max_height_cst["value"]

        # Scale: viewport Y maps from PDF coordinates via 0.1 scale,
        # but heights in meters need their own mapping.
        # Use a reasonable heuristic: 1m ≈ 0.3 viewport units
        y_limit = height_val * 0.3

        for bldg in buildings:
            bldg_pts = bldg.get("points", [])
            if len(bldg_pts) < 3:
                continue

            is_3d = len(bldg_pts[0]) == 3

            # Create a ring at the height limit elevation
            pts_at_height = []
            for pt in bldg_pts:
                if is_3d:
                    pts_at_height.append([pt[0], round(y_limit, 4), pt[2]])
                else:
                    pts_at_height.append([pt[0], round(y_limit, 4), pt[1] if len(pt) > 1 else 0])

            # Close the ring
            if pts_at_height and pts_at_height[0] != pts_at_height[-1]:
                pts_at_height.append(pts_at_height[0])

            centroid = bldg.get("centroid", [0, 0, 0])

            geometry.append({
                "id": f"cst_geo_{uuid.uuid4().hex[:8]}",
                "type": "Polygon",
                "zone_type": "height_limit",
                "zone_label": f"Max Height {height_val}m",
                "points": pts_at_height,
                "closed": True,
                "color_hint": "#f59e0b",
                "stroke_width": 2.0,
                "dashed": True,
                "confidence": max_height_cst["confidence"],
                "classification_method": "height_limit_contour",
                "constraint_value": f"{height_val}m",
                "height_meters": height_val,
                "centroid": [centroid[0] if len(centroid) > 0 else 0,
                             round(y_limit / 2, 4),
                             centroid[2] if len(centroid) > 2 else 0],
                "y_bottom": 0,
                "y_top": round(y_limit, 4),
                "footprint_ground": [
                    [pt[0], 0, pt[2]] if is_3d else [pt[0], 0, pt[1] if len(pt) > 1 else 0]
                    for pt in bldg_pts
                ],
                "corner_posts": [
                    {
                        "base": [pt[0], 0, pt[2]] if is_3d else [pt[0], 0, pt[1] if len(pt) > 1 else 0],
                        "top": [pt[0], round(y_limit, 4), pt[2]] if is_3d else [pt[0], round(y_limit, 4), pt[1] if len(pt) > 1 else 0],
                    }
                    for pt in bldg_pts[:8]  # Limit to 8 corners
                ],
                "marker_labels": [],
            })

        log.info("[Constraints] Generated %d height limit contour(s) at %.1fm",
                 len(buildings), height_val)

    # ── 4. No-build zone boundaries as constraints ──
    for nbz in no_build_zones:
        nbz_pts = nbz.get("points", [])
        if len(nbz_pts) < 3:
            continue

        is_3d = len(nbz_pts[0]) == 3
        pts_3d = _to_3d(_to_2d(nbz_pts, is_3d), is_3d)

        geometry.append({
            "id": f"cst_geo_{uuid.uuid4().hex[:8]}",
            "type": "Polygon",
            "zone_type": "setback_line",
            "zone_label": f"No-Build Zone ({nbz.get('zone_label', '')})" if nbz.get("zone_label") else "No-Build Zone",
            "points": pts_3d,
            "closed": True,
            "color_hint": "#dc2626",
            "stroke_width": 2.5,
            "dashed": True,
            "confidence": nbz.get("confidence", 0.65),
            "classification_method": "no_build_boundary",
            "marker_labels": [],
        })

    if no_build_zones:
        log.info("[Constraints] Added %d no-build zone constraint boundaries",
                 len(no_build_zones))

    log.info("[Constraints] Total constraint geometry: %d items", len(geometry))
    return geometry


# ── Main entry point ──

def extract_constraints(
    text_blocks: List[Dict[str, Any]],
    zones: List[Dict[str, Any]],
    api_key: Optional[str] = None,
    model_name: str = "gemini-2.5-flash",
    cost_tracker=None,
    site_brief: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Extract regulatory constraints from text blocks and zone metadata.

    Returns:
        {
            "constraints": [...],           # List of constraint objects
            "constraint_geometry": [...],    # Setback offset geometry
            "extraction_summary": {...},     # Stats about extraction
            "zone_rules": [...],            # Per-zone constraint rules (merged)
        }
    """
    log.info("[Constraints] Starting constraint extraction from %d text blocks, %d zones",
             len(text_blocks), len(zones))

    # Step 1: Regex extraction (always runs)
    regex_constraints = _extract_regex(text_blocks)
    log.info("[Constraints] Regex extracted %d constraints", len(regex_constraints))

    # Step 2: AI extraction (when API key available)
    ai_constraints = []
    if api_key:
        ai_constraints = _extract_with_ai(
            text_blocks, zones, regex_constraints, api_key, model_name,
            cost_tracker=cost_tracker,
        )

    # Combine
    all_constraints = regex_constraints + ai_constraints

    # Step 3: AI gap-fill (when data is sparse)
    suggestions = []
    if api_key:
        suggestions = _gap_fill_with_ai(
            all_constraints, text_blocks, api_key, model_name,
            cost_tracker=cost_tracker,
        )
    all_constraints.extend(suggestions)

    # ── Step 3b: Tag constraints with zone_id using text matching ──
    # Try to match constraints to specific buildable zones by zone label
    buildable = [z for z in zones
                 if z.get("zone_type") in ("buildable_envelope", "filled_zone",
                                            "uncategorized_zone")
                 and z.get("closed")]
    for c in all_constraints:
        c.setdefault("zone_id", None)  # site-wide by default
        applies = c.get("applies_to", "").lower()
        raw_q = c.get("raw_quote", "").lower()
        # Try matching by zone label text
        for bz in buildable:
            label = (bz.get("zone_label") or "").lower()
            if label and len(label) > 2:
                if label in applies or label in raw_q:
                    c["zone_id"] = bz["id"]
                    log.debug("[Constraints] Matched constraint '%s' → zone '%s'",
                              c.get("name"), label)
                    break

    # ── Step 3c: Merge with zone_rules from site brief ──
    zone_rules = []
    if site_brief and site_brief.get("zone_rules"):
        zone_rules = list(site_brief["zone_rules"])  # copy

        # Enrich zone_rules with extracted per-zone constraints
        for c in all_constraints:
            if c.get("zone_id"):
                # Find matching zone_rule by label similarity
                for zr in zone_rules:
                    zr_label = (zr.get("zone_label") or "").lower()
                    bz_match = next(
                        (z for z in buildable if z["id"] == c["zone_id"]),
                        None
                    )
                    if bz_match:
                        bz_label = (bz_match.get("zone_label") or "").lower()
                        if zr_label and bz_label and (
                            zr_label in bz_label or bz_label in zr_label
                        ):
                            # Override zone_rule with document-extracted value
                            cat = c.get("category")
                            if cat == "height" and c.get("value"):
                                zr["max_height_m"] = c["value"]
                                zr["source"] = "document"
                            elif cat == "gfa" and c.get("value"):
                                zr["target_gfa_m2"] = c["value"]
                                zr["source"] = "document"
                            elif cat == "setback" and c.get("value"):
                                zr["setback_m"] = c["value"]
                                zr["source"] = "document"
                            elif cat == "density" and c.get("value"):
                                zr["density_grz"] = c["value"]
                                zr["source"] = "document"

        log.info("[Constraints] Merged %d zone_rules with extracted constraints",
                 len(zone_rules))

    # Step 4: Generate constraint geometry (setbacks, height limits, no-build zones)
    constraint_geometry = _generate_constraint_geometry(all_constraints, zones)

    # Summary
    summary = {
        "total": len(all_constraints),
        "regex_extracted": len(regex_constraints),
        "ai_extracted": len(ai_constraints),
        "ai_suggested": len(suggestions),
        "constraint_geometries": len(constraint_geometry),
        "categories": list({c["category"] for c in all_constraints}),
    }

    log.info("[Constraints] Done: %d total (%d regex, %d AI, %d suggested), %d constraint geometries",
             summary["total"], summary["regex_extracted"],
             summary["ai_extracted"], summary["ai_suggested"],
             summary["constraint_geometries"])

    return {
        "constraints": all_constraints,
        "constraint_geometry": constraint_geometry,
        "extraction_summary": summary,
        "zone_rules": zone_rules,
    }

