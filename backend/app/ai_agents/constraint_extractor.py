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

    return constraints


# ── Step 2: AI extraction ──

def _extract_with_ai(
    text_blocks: List[Dict[str, Any]],
    zones: List[Dict[str, Any]],
    existing_constraints: List[Dict[str, Any]],
    api_key: str,
    model_name: str,
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


# ── Step 4: Setback geometry generation ──

def _generate_setback_geometry(
    constraints: List[Dict[str, Any]],
    zones: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Generate offset polygon geometry for setback constraints.

    For each setback constraint, creates an inward-offset dashed line
    from the plot boundary polygon.
    """
    # Find the plot boundary
    plot = None
    for z in zones:
        if z.get("zone_type") == "plot_boundary" and z.get("closed"):
            plot = z
            break

    if not plot:
        log.info("[Constraints] No plot boundary found — skipping setback geometry")
        return []

    plot_points = plot.get("points", [])
    if len(plot_points) < 4:
        return []

    setback_constraints = [c for c in constraints if c["category"] == "setback"]
    if not setback_constraints:
        return []

    geometry = []

    for cst in setback_constraints:
        offset_dist = cst["value"]
        if offset_dist <= 0:
            continue

        # Try Shapely offset first
        try:
            from shapely.geometry import Polygon as ShapelyPolygon

            # Handle both 2D and 3D points
            pts_2d = []
            is_3d = len(plot_points[0]) == 3
            for pt in plot_points:
                if is_3d:
                    pts_2d.append((pt[0], pt[2]))  # x, z for 3D points
                else:
                    pts_2d.append((pt[0], pt[1]))

            # Ensure closed
            if pts_2d[0] != pts_2d[-1]:
                pts_2d.append(pts_2d[0])

            poly = ShapelyPolygon(pts_2d)
            if not poly.is_valid:
                from shapely.validation import make_valid
                poly = make_valid(poly)

            # Scale: we need to figure out the coordinate scale
            # The plot points are already in normalized world coordinates
            # where the viewport spans ~40 units. Setback in meters needs
            # to be scaled to match.
            # Use a heuristic: the plot boundary typically represents a
            # real-world area. We estimate the scale from the geometry span.
            coords = list(poly.exterior.coords)
            xs = [c[0] for c in coords]
            zs = [c[1] for c in coords]
            span = max(max(xs) - min(xs), max(zs) - min(zs), 1)

            # Assuming the plot is 50-200m wide in reality and spans ~20-40
            # viewport units, scale factor is roughly span/100
            # But we don't know the real-world size, so use a fixed proportion:
            # offset 5m on a 100m plot = 5% inset → offset_dist/100 * span
            # Use a reasonable default: 1m real = 0.4 viewport units
            viewport_offset = offset_dist * (span / 100.0)
            viewport_offset = max(viewport_offset, 0.3)  # minimum visible offset

            offset_poly = poly.buffer(-viewport_offset, join_style=2)

            if offset_poly.is_empty:
                continue

            # Extract the offset polygon coordinates
            if hasattr(offset_poly, 'exterior'):
                offset_coords = list(offset_poly.exterior.coords)
            elif hasattr(offset_poly, 'geoms'):
                # MultiPolygon — take largest
                largest = max(offset_poly.geoms, key=lambda g: g.area)
                offset_coords = list(largest.exterior.coords)
            else:
                continue

            # Convert back to 3D points
            pts_3d = []
            for c in offset_coords:
                if is_3d:
                    pts_3d.append([round(c[0], 4), 0, round(c[1], 4)])
                else:
                    pts_3d.append([round(c[0], 4), round(c[1], 4)])

            # Determine color by setback type
            name_lower = cst["name"].lower()
            if "front" in name_lower or "voor" in name_lower:
                color = "#ef4444"  # red
            elif "rear" in name_lower or "achter" in name_lower:
                color = "#f97316"  # orange
            elif "side" in name_lower or "zij" in name_lower:
                color = "#eab308"  # yellow
            else:
                color = "#ef4444"  # red default

            geometry.append({
                "id": f"cst_geo_{uuid.uuid4().hex[:8]}",
                "type": "Polygon",
                "zone_type": "setback_line",
                "zone_label": cst["name"],
                "points": pts_3d,
                "closed": True,
                "color_hint": color,
                "stroke_width": 1.5,
                "dashed": True,
                "confidence": cst["confidence"],
                "classification_method": "constraint_offset",
                "constraint_id": cst["id"],
                "constraint_value": f"{cst['value']}{cst['unit']}",
            })

            log.info("[Constraints] Generated setback geometry for '%s' (%.1f%s, offset=%.2f units)",
                     cst["name"], cst["value"], cst["unit"], viewport_offset)

        except Exception as e:
            log.warning("[Constraints] Setback geometry generation failed for '%s': %s",
                        cst["name"], e)
            continue

    return geometry


# ── Main entry point ──

def extract_constraints(
    text_blocks: List[Dict[str, Any]],
    zones: List[Dict[str, Any]],
    api_key: Optional[str] = None,
    model_name: str = "gemini-2.5-flash",
) -> Dict[str, Any]:
    """
    Extract regulatory constraints from text blocks and zone metadata.

    Returns:
        {
            "constraints": [...],           # List of constraint objects
            "constraint_geometry": [...],    # Setback offset geometry
            "extraction_summary": {...},     # Stats about extraction
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
        )

    # Combine
    all_constraints = regex_constraints + ai_constraints

    # Step 3: AI gap-fill (when data is sparse)
    suggestions = []
    if api_key:
        suggestions = _gap_fill_with_ai(
            all_constraints, text_blocks, api_key, model_name,
        )
    all_constraints.extend(suggestions)

    # Step 4: Generate setback geometry
    setback_geometry = _generate_setback_geometry(all_constraints, zones)

    # Summary
    summary = {
        "total": len(all_constraints),
        "regex_extracted": len(regex_constraints),
        "ai_extracted": len(ai_constraints),
        "ai_suggested": len(suggestions),
        "setback_geometries": len(setback_geometry),
        "categories": list({c["category"] for c in all_constraints}),
    }

    log.info("[Constraints] Done: %d total (%d regex, %d AI, %d suggested), %d setback geometries",
             summary["total"], summary["regex_extracted"],
             summary["ai_extracted"], summary["ai_suggested"],
             summary["setback_geometries"])

    return {
        "constraints": all_constraints,
        "constraint_geometry": setback_geometry,
        "extraction_summary": summary,
    }
