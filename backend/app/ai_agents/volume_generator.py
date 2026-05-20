"""
Volume Generator — Floor-by-Floor 3D Volume Generation
========================================================

Generates 3D volumes (extruded polygons) for buildings, plinths, and
underground parking from building footprints, programme data, and constraints.

Each volume is represented as geometry with:
  - Per-floor extrusion from footprint polygon
  - Separate layers: buildings, plinth, underground_parking
  - Sublayers per building and per floor
  - Color-coded by use type

Falls back gracefully when programme data is incomplete.
"""
from __future__ import annotations

import logging
import math
import uuid
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# ── Color scheme per use type ──
USE_COLORS: Dict[str, str] = {
    "residential": "#8b5cf6",    # purple
    "commercial": "#3b82f6",     # blue
    "office": "#06b6d4",         # cyan
    "retail": "#f59e0b",         # amber
    "mixed_use": "#ec4899",      # pink
    "parking": "#64748b",        # slate
    "underground_parking": "#475569",  # dark slate
    "plinth": "#f97316",         # orange
}

# ── Default floor height if not specified (meters) ──
DEFAULT_FLOOR_HEIGHT = 3.0
DEFAULT_PLINTH_HEIGHT = 4.5
DEFAULT_PARKING_DEPTH = 3.0

# ── Max realistic footprint per building type (m²) ──
# Real buildings have limited footprint sizes. When a sub_zone footprint
# exceeds these, we cap the effective area for floor count calculation
# so the building gets taller instead of staying as a 1-2 floor slab.
MAX_FOOTPRINT_BY_USE: Dict[str, float] = {
    "residential": 1200.0,   # typical residential tower/block
    "commercial": 2500.0,    # commercial/office building
    "office": 2000.0,
    "retail": 3000.0,        # retail can be larger (mall podium)
    "mixed_use": 1800.0,
    "industrial": 5000.0,    # factories can be large and low
}
DEFAULT_MAX_FOOTPRINT = 1500.0  # general fallback


class CADLayoutGenerator:
    """Deterministic, geometric layout generator for building footprints inside buildable envelopes.

    Uses rotation-based alignment to align proposed rectangular footprints with the major orientation
    axis of the envelope/setback polygon.
    """
    def __init__(self, envelope_poly: Any, setback_m: float = 3.0):
        self.envelope_poly = envelope_poly
        self.setback_m = max(0.0, setback_m)

        # Calculate setback polygon
        if self.setback_m > 0:
            try:
                self.setback_poly = self.envelope_poly.buffer(-self.setback_m)
                if self.setback_poly.is_empty or not self.setback_poly.is_valid:
                    self.setback_poly = self.envelope_poly
            except Exception:
                self.setback_poly = self.envelope_poly
        else:
            self.setback_poly = self.envelope_poly

    def _get_major_angle(self, poly: Any) -> float:
        """Find the angle of the longest edge of the polygon's exterior boundary."""
        try:
            coords = list(poly.exterior.coords)
            max_len = 0.0
            best_angle = 0.0
            for i in range(len(coords) - 1):
                p1 = coords[i]
                p2 = coords[i+1]
                dx = p2[0] - p1[0]
                dy = p2[1] - p1[1]
                length = math.hypot(dx, dy)
                if length > max_len:
                    max_len = length
                    best_angle = math.atan2(dy, dx)
            return best_angle
        except Exception:
            return 0.0

    def generate_layout(
        self,
        typology: str = "row_houses",
        count: int = 3,
        spacing_m: float = 5.0,
        building_width_m: float = 12.0,
        building_length_m: float = 20.0,
    ) -> List[Any]:
        """Generate perfect orthogonal building footprints inside the setback boundary.

        Returns:
          A list of Shapely Polygon objects representing building footprints.
        """
        from shapely.affinity import rotate
        from shapely.geometry import Polygon as ShapelyPoly

        count = max(1, count)
        spacing_m = max(0.0, spacing_m)
        building_width_m = max(4.0, building_width_m)
        building_length_m = max(5.0, building_length_m)

        # 1. Get major orientation angle of setback polygon
        theta = self._get_major_angle(self.setback_poly)

        # 2. Rotate setback polygon by -theta around its centroid to align with X axis
        origin = self.setback_poly.centroid
        rotated_poly = rotate(self.setback_poly, -theta, use_radians=True, origin=origin)

        # 3. Get bounds of the rotated polygon
        minx, miny, maxx, maxy = rotated_poly.bounds
        dx = maxx - minx
        dy = maxy - miny

        # Determine layout direction based on bounds aspect ratio
        layout_along_x = dx >= dy

        # b_len: dimension along layout axis, b_wid: dimension perpendicular to layout axis
        if layout_along_x:
            b_len = building_length_m
            b_wid = building_width_m
            axis_size = dx
            perp_size = dy
        else:
            b_len = building_width_m
            b_wid = building_length_m
            axis_size = dy
            perp_size = dx

        # Adjust layout parameters if they don't fit inside bounds
        total_len = count * b_len + (count - 1) * spacing_m
        if total_len > axis_size:
            # If count is too high, reduce count or spacing
            if count > 1:
                spacing_m = max(2.0, (axis_size - count * b_len) / (count - 1))
                total_len = count * b_len + (count - 1) * spacing_m
            if total_len > axis_size:
                # Still too big, shrink building length to fit
                b_len = max(5.0, (axis_size - (count - 1) * spacing_m) / count)
                total_len = count * b_len + (count - 1) * spacing_m

        if b_wid > perp_size:
            b_wid = max(4.0, perp_size - 1.0)

        # Generate building boxes in rotated coordinate system
        bldg_polys = []
        margin = (axis_size - total_len) / 2.0

        for k in range(count):
            # Centered coordinate along layout axis
            c_axis = (minx if layout_along_x else miny) + margin + k * (b_len + spacing_m) + b_len / 2.0
            # Centered coordinate along perpendicular axis
            c_perp = ((miny + maxy) if layout_along_x else (minx + maxx)) / 2.0

            if layout_along_x:
                cx, cy = c_axis, c_perp
                x1, y1 = cx - b_len / 2.0, cy - b_wid / 2.0
                x2, y2 = cx + b_len / 2.0, cy + b_wid / 2.0
            else:
                cx, cy = c_perp, c_axis
                x1, y1 = cx - b_wid / 2.0, cy - b_len / 2.0
                x2, y2 = cx + b_wid / 2.0, cy - b_len / 2.0

            rect_poly = ShapelyPoly([
                (x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)
            ])

            # Ensure polygon is valid
            if not rect_poly.is_valid:
                continue

            # Clip with the rotated setback polygon to make sure it is strictly inside
            clipped_poly = rotated_poly.intersection(rect_poly)
            if not clipped_poly.is_empty and clipped_poly.is_valid:
                if clipped_poly.area > rect_poly.area * 0.3:
                    bldg_polys.append(clipped_poly)
                else:
                    bldg_polys.append(rect_poly)
            else:
                bldg_polys.append(rect_poly)

        # 4. Rotate generated shapes back by +theta to original coordinate space
        final_polys = []
        for bp in bldg_polys:
            orig_bp = rotate(bp, theta, use_radians=True, origin=origin)
            if orig_bp.is_valid and not orig_bp.is_empty:
                final_polys.append(orig_bp)

        return final_polys


def _compute_centroid(points: List[List[float]]) -> List[float]:
    """Compute 2D centroid of a polygon (using x, z for 3D points)."""
    if not points:
        return [0, 0, 0]
    n = len(points)
    if len(points[0]) == 3:
        cx = sum(p[0] for p in points) / n
        cz = sum(p[2] for p in points) / n
        return [round(cx, 4), 0, round(cz, 4)]
    else:
        cx = sum(p[0] for p in points) / n
        cy = sum(p[1] for p in points) / n
        return [round(cx, 4), round(cy, 4)]


def _offset_points_y(points: List[List[float]], y_offset: float) -> List[List[float]]:
    """Create a copy of 3D points with a different Y coordinate."""
    result = []
    for p in points:
        if len(p) == 3:
            result.append([p[0], round(y_offset, 4), p[2]])
        else:
            result.append([p[0], round(y_offset, 4), p[1] if len(p) > 1 else 0])
    return result


def _create_floor_volume(
    footprint: List[List[float]],
    y_bottom: float,
    y_top: float,
    building_id: str,
    building_label: str,
    floor_index: int,
    floor_label: str,
    use_type: str,
    volume_type: str,  # "building_floor", "plinth", "underground_parking"
    confidence: float = 0.75,
) -> Dict[str, Any]:
    """Create a single floor volume as 3D geometry (all coordinates in meters)."""

    bottom_pts = _offset_points_y(footprint, y_bottom)
    top_pts = _offset_points_y(footprint, y_top)
    centroid = _compute_centroid(footprint)
    centroid_y = (y_bottom + y_top) / 2

    color = USE_COLORS.get(use_type, USE_COLORS.get(volume_type, "#94a3b8"))

    # Compute footprint area (m²)
    try:
        from shapely.geometry import Polygon as SPoly
        fp_2d = [(p[0], p[2] if len(p) > 2 else (p[1] if len(p) > 1 else 0)) for p in footprint]
        area_m2 = round(SPoly(fp_2d).area, 1)
    except Exception:
        area_m2 = 0.0

    return {
        "id": f"vol_{uuid.uuid4().hex[:8]}",
        "type": "Volume",
        "zone_type": volume_type,
        "zone_label": f"{building_label} · {floor_label}",
        "building_id": building_id,
        "building_label": building_label,
        "floor_index": floor_index,
        "floor_label": floor_label,
        "use_type": use_type,
        "points": bottom_pts,
        "points_top": top_pts,
        "y_bottom": round(y_bottom, 4),
        "y_top": round(y_top, 4),
        "height": round(y_top - y_bottom, 4),
        "closed": True,
        "color_hint": color,
        "centroid": [centroid[0], round(centroid_y, 4), centroid[2] if len(centroid) > 2 else 0],
        "confidence": round(confidence, 2),
        "classification_method": "volume_generation",
        "filled": True,
        "area_pdf_units": 0,
        "area_m2": area_m2,
        "source_layer": "volumes",
    }


def _propose_building_footprints_with_ai(
    envelope_poly: Any,
    target_gfa: Optional[float],
    max_height: Optional[float],
    typology: Optional[str],
    api_key: Optional[str],
    model_name: Optional[str],
    is_3d: bool,
) -> Optional[List[Dict[str, Any]]]:
    """Use Gemini to propose realistic, orthogonal building footprints inside the buildable envelope."""
    if not api_key:
        return None

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        model = model_name or "gemini-2.5-flash"

        envelope_area = envelope_poly.area

        # Calculate required footprint area based on target_gfa and max_height (number of floors)
        estimated_floors = 3
        if max_height and max_height > 0:
            estimated_floors = max(1, int(max_height / 3.0))

        if target_gfa and target_gfa > 0:
            required_footprint = target_gfa / estimated_floors
        else:
            # Fallback coverage ratio of 35% of envelope area
            required_footprint = envelope_area * 0.35

        # Clamp required footprint to a reasonable ratio of envelope area (max 85%)
        required_footprint = min(required_footprint, envelope_area * 0.85)

        prompt = f"""
You are an expert urban planner and CAD layout generator.
Given a 2D buildable envelope polygon, you must propose layout parameters to generate realistic building footprints that fit strictly INSIDE it.

Buildable Envelope:
- Area: {envelope_area:.1f} m²

Constraints:
- Target GFA: {f"{target_gfa:.0f} m²" if target_gfa else "Not specified"}
- Max Height: {f"{max_height:.0f}m" if max_height else "Not specified"}
- Recommended Typology: {typology or "residential_block"}
- Target Total Footprint Area: {required_footprint:.1f} m² (estimated assuming {estimated_floors} floors)

Based on these parameters, decide the optimal layout configuration:
- "layout_typology": one of "row_houses", "apartment_blocks", "tower_plinth", or "campus".
- "count": number of building blocks to generate (typically 1 to 4).
- "setback_m": distance in meters from the envelope edge (typically 3.0 to 5.0m).
- "spacing_m": spacing between building blocks (typically 5.0 to 12.0m).
- "building_width_m": width of each block (typically 10.0 to 15.0m).
- "building_length_m": length of each block (typically 15.0 to 45.0m).

CRITICAL RULE:
The sum of the footprints of your proposed blocks (i.e. `count * building_width_m * building_length_m`) MUST approximate the Target Total Footprint Area of {required_footprint:.1f} m² as closely as possible, while fitting inside the envelope with the specified setbacks.
- If the Target Total Footprint Area is large, increase `count` (up to 4) and use larger block dimensions (e.g. width of 12-16m, length of 25-45m) to ensure buildings are realistic, proportional, and cover the requested area. Do NOT generate tiny building footprints in the middle of large envelopes.

Return ONLY a valid JSON object matching this schema:
{{
  "layout_typology": "row_houses",
  "count": 3,
  "setback_m": 3.0,
  "spacing_m": 5.0,
  "building_width_m": 12.0,
  "building_length_m": 20.0
}}
"""

        log.info("[Volumes/AI-Proposer] Querying %s for building proposals...", model)
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
            )
        )

        text = response.text.strip()
        # Strip markdown code fences if model returned them anyway
        if text.startswith("```"):
            lines = text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        config = json.loads(text)
        if not isinstance(config, dict) or not config:
            return None

        # Instantiate CADLayoutGenerator
        setback = float(config.get("setback_m", 3.0))
        generator = CADLayoutGenerator(envelope_poly, setback_m=setback)

        layout_typology = config.get("layout_typology", "row_houses")
        count = int(config.get("count", 3))
        spacing = float(config.get("spacing_m", 5.0))
        width = float(config.get("building_width_m", 12.0))
        length = float(config.get("building_length_m", 20.0))

        # Enforce realistic thickness/width limits for residential/commercial uses
        is_residential = not typology or "residential" in typology.lower() or "home" in typology.lower()
        if is_residential:
            # Cap width to 14.0m for residential
            width = min(width, 14.0)
        else:
            # Cap width to 18.0m for commercial/office
            width = min(width, 18.0)

        log.info("[Volumes/AI-Proposer] CAD Generator parameters: typology=%s, count=%d, spacing=%.1fm, width=%.1fm (capped from %.1fm), length=%.1fm",
                 layout_typology, count, spacing, width, float(config.get("building_width_m", 12.0)), length)

        layout_polys = generator.generate_layout(
            typology=layout_typology,
            count=count,
            spacing_m=spacing,
            building_width_m=width,
            building_length_m=length,
        )

        derived = []
        for i, bldg_poly in enumerate(layout_polys):
            pts_cleaned = list(bldg_poly.exterior.coords)

            if is_3d:
                out_pts = [[round(c[0], 4), 0, round(c[1], 4)] for c in pts_cleaned]
            else:
                out_pts = [[round(c[0], 4), round(c[1], 4)] for c in pts_cleaned]

            centroid = bldg_poly.centroid
            ct = [round(centroid.x, 4), 0, round(centroid.y, 4)] if is_3d else [round(centroid.x, 4), round(centroid.y, 4)]

            derived.append({
                "id": f"ai_proposed_bldg_{i+1}",
                "type": "Polygon",
                "zone_type": "sub_zone",
                "zone_label": f"Proposed Building {i+1}",
                "points": out_pts,
                "closed": True,
                "color_hint": "#8b5cf6",
                "confidence": 0.85,
                "classification_method": f"ai_proposed_cad (Gemini:{model})",
                "filled": False,
                "area_pdf_units": round(bldg_poly.area, 1),
                "area_m2": round(bldg_poly.area, 1),
                "centroid": ct,
                "stroke_width": 1.0,
                "marker_labels": [],
                "_derived": True,
            })

        if derived:
            log.info("[Volumes/AI-Proposer] Successfully accepted %d AI-proposed building footprints", len(derived))
            return derived

    except Exception as e:
        log.error("[Volumes/AI-Proposer] Failed to generate AI footprints: %s", e)
        log.error(traceback.format_exc())

    return None


def _derive_footprints_from_envelope(
    zones: List[Dict[str, Any]],
    constraints: List[Dict[str, Any]],
    api_key: Optional[str] = None,
    model_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """When no building outlines exist, derive footprints from the buildable
    envelope + constraints (target GFA, density, height, setbacks).

    Strategy:
      1. Find the buildable_envelope (or fall back to plot_boundary)
      2. Apply setback insets if available
      3. Compute required footprint area from target GFA ÷ floors
      4. If the required area fits within the envelope, use the full envelope
         as a single building footprint
      5. If GFA and density suggest multiple buildings, subdivide
    """
    from shapely.geometry import Polygon as ShapelyPoly
    from shapely import affinity

    # Find usable envelope
    envelope = None
    plot = None
    for z in zones:
        zt = z.get("zone_type", "")
        if zt == "buildable_envelope" and z.get("closed"):
            envelope = z
            break
        elif zt == "plot_boundary" and z.get("closed"):
            plot = z

    base_zone = envelope or plot
    if not base_zone:
        log.info("[Volumes/Derive] No buildable envelope or plot boundary — cannot derive footprints")
        return []

    pts = base_zone.get("points", [])
    if len(pts) < 3:
        return []

    # Build 2D polygon
    is_3d = len(pts[0]) == 3
    pts_2d = [(p[0], p[2]) if is_3d else (p[0], p[1]) for p in pts]
    try:
        poly = ShapelyPoly(pts_2d)
        if not poly.is_valid:
            from shapely.validation import make_valid
            poly = make_valid(poly)
    except Exception:
        return []

    # Apply setback inset if we have a value
    setback_dist = None
    for c in constraints:
        if c.get("category") == "setback":
            if setback_dist is None or c["value"] < setback_dist:
                setback_dist = c["value"]

    if setback_dist and setback_dist > 0:
        # Buffer inward by setback distance (meters — same unit as XZ coordinates)
        inset = poly.buffer(-setback_dist, join_style=2)
        if not inset.is_empty and inset.area > poly.area * 0.1:
            if hasattr(inset, 'exterior'):
                poly = inset
            elif hasattr(inset, 'geoms'):
                poly = max(inset.geoms, key=lambda g: g.area)

    envelope_area = poly.area

    # Gather constraints
    max_height = None
    target_gfa = None
    density_ratio = None  # GRZ = ground coverage ratio
    fsi = None  # FSI = floor space index

    for c in constraints:
        cat = c.get("category", "")
        if cat == "height":
            if max_height is None or c["value"] < max_height:
                max_height = c["value"]
        elif cat == "gfa":
            if target_gfa is None or c["value"] > target_gfa:
                target_gfa = c["value"]
        elif cat == "density":
            name = c.get("name", "").lower()
            if "grz" in name or "coverage" in name or "bebouwing" in name:
                density_ratio = c["value"]
            elif "fsi" in name or "gfz" in name or "far" in name:
                fsi = c["value"]

    # Determine floor count
    floor_height = DEFAULT_FLOOR_HEIGHT
    if max_height:
        floors = max(1, int(max_height / floor_height))
    else:
        floors = 4

    # Try using AI footprint proposal if API key is available
    if api_key:
        typology = None
        for z in zones:
            if z.get("typology"):
                typology = z["typology"]
                break
        ai_derived = _propose_building_footprints_with_ai(
            envelope_poly=poly,
            target_gfa=target_gfa,
            max_height=max_height,
            typology=typology,
            api_key=api_key,
            model_name=model_name,
            is_3d=is_3d,
        )
        if ai_derived:
            # Set target floors/gfa on first derived building for floor count calculations
            ai_derived[0]["_target_floors"] = floors
            ai_derived[0]["_target_gfa"] = target_gfa
            return ai_derived

    # Compute required footprint area
    # Priority: target_gfa > fsi > density_ratio > use full envelope
    footprint_area = None
    method = "full_envelope"

    if target_gfa and target_gfa > 0 and floors > 0:
        footprint_area = target_gfa / floors
        method = f"gfa_derived ({target_gfa:.0f}m² ÷ {floors} floors)"
    elif fsi and fsi > 0 and envelope_area > 0:
        total_gfa = fsi * envelope_area
        footprint_area = total_gfa / floors
        target_gfa = total_gfa
        method = f"fsi_derived (FSI={fsi})"
    elif density_ratio and density_ratio > 0 and envelope_area > 0:
        footprint_area = envelope_area * density_ratio
        target_gfa = footprint_area * floors
        method = f"density_derived (GRZ={density_ratio})"

    # If footprint area exceeds envelope, clamp it
    if footprint_area and footprint_area > envelope_area:
        footprint_area = envelope_area * 0.85

    # Build footprint polygon(s)
    derived = []

    # Try rule-based CADLayoutGenerator fallback first before scaling down
    layout_polys = []
    if footprint_area and footprint_area < envelope_area:
        try:
            # Use 12m width for residential, 16m for commercial
            is_res = not typology or "res" in typology.lower() or "home" in typology.lower()
            b_width = 12.0 if is_res else 16.0
            b_length = 35.0
            b_area = b_width * b_length

            # Estimate count
            est_count = max(1, round(footprint_area / b_area))
            est_count = min(4, est_count) # cap at 4 blocks

            # Recalculate length to match required footprint area
            b_length = max(15.0, min(50.0, footprint_area / (est_count * b_width)))

            setback_val = setback_dist or 3.0
            generator = CADLayoutGenerator(poly, setback_m=setback_val)
            layout_polys = generator.generate_layout(
                typology="apartment_blocks",
                count=est_count,
                spacing_m=6.0,
                building_width_m=b_width,
                building_length_m=b_length
            )
            if layout_polys:
                log.info("[Volumes/Derive] Generated %d rule-based fallback footprints using CADLayoutGenerator", len(layout_polys))
                for i, bldg_poly in enumerate(layout_polys):
                    pts_cleaned = list(bldg_poly.exterior.coords)
                    if is_3d:
                        out_pts = [[round(c[0], 4), 0, round(c[1], 4)] for c in pts_cleaned]
                    else:
                        out_pts = [[round(c[0], 4), round(c[1], 4)] for c in pts_cleaned]
                    centroid = bldg_poly.centroid
                    ct = [round(centroid.x, 4), 0, round(centroid.y, 4)] if is_3d else [round(centroid.x, 4), round(centroid.y, 4)]

                    derived.append({
                        "id": f"derived_bldg_{i+1}",
                        "type": "Polygon",
                        "zone_type": "sub_zone",
                        "zone_label": f"Derived Building {i+1}",
                        "points": out_pts,
                        "closed": True,
                        "color_hint": "#8b5cf6",
                        "confidence": 0.55,
                        "classification_method": "derived_from_envelope (CAD_rule_fallback)",
                        "filled": False,
                        "area_pdf_units": round(bldg_poly.area, 1),
                        "area_m2": round(bldg_poly.area, 1),
                        "centroid": ct,
                        "stroke_width": 1.0,
                        "marker_labels": [],
                        "_derived": True,
                        "_target_floors": floors,
                        "_target_gfa": target_gfa,
                    })
        except Exception as e:
            log.warning("[Volumes/Derive] Rule-based fallback layout generation failed: %s", e)

    # If rule-based fallback didn't produce anything, use the scaling method as a last resort
    if not derived and footprint_area and footprint_area < envelope_area * 0.95:
        # Scale the envelope down to match required footprint area
        scale_factor = math.sqrt(footprint_area / max(envelope_area, 1))
        cx, cy = poly.centroid.x, poly.centroid.y
        scaled = affinity.scale(poly, xfact=scale_factor, yfact=scale_factor,
                                origin=(cx, cy))
        if scaled.is_valid and not scaled.is_empty:
            coords = list(scaled.exterior.coords)
            if is_3d:
                out_pts = [[round(c[0], 4), 0, round(c[1], 4)] for c in coords]
            else:
                out_pts = [[round(c[0], 4), round(c[1], 4)] for c in coords]

            centroid = scaled.centroid
            ct = [round(centroid.x, 4), 0, round(centroid.y, 4)] if is_3d else [round(centroid.x, 4), round(centroid.y, 4)]

            derived.append({
                "id": "derived_bldg_1",
                "type": "Polygon",
                "zone_type": "sub_zone",
                "zone_label": "Derived Building 1",
                "points": out_pts,
                "closed": True,
                "color_hint": "#8b5cf6",
                "confidence": 0.55,
                "classification_method": f"derived_from_envelope ({method})",
                "filled": False,
                "area_pdf_units": round(scaled.area, 1),
                "area_m2": round(scaled.area, 1),
                "centroid": ct,
                "stroke_width": 1.0,
                "marker_labels": [],
                "_derived": True,
                "_target_floors": floors,
                "_target_gfa": target_gfa,
            })
            log.info("[Volumes/Derive] Created derived footprint: %.0f m² (method=%s)",
                     scaled.area, method)

    else:
        # Apply a default setback inset (never use the full zone as-is)
        default_inset = max(poly.length * 0.03, 2.0)  # ~3% of perimeter or 2m minimum
        inset_poly = poly.buffer(-default_inset)
        if inset_poly.is_empty or not inset_poly.is_valid:
            inset_poly = poly  # fallback if inset collapses
        # Then scale to ~60% density (typical urban coverage)
        scale_factor = math.sqrt(0.6)
        cx, cy = inset_poly.centroid.x, inset_poly.centroid.y
        scaled = affinity.scale(inset_poly, xfact=scale_factor, yfact=scale_factor,
                                origin=(cx, cy))
        if scaled.is_empty or not scaled.is_valid:
            scaled = inset_poly

        use_poly = scaled
        try:
            coords = list(use_poly.exterior.coords)
        except AttributeError:
            # MultiPolygon — take the largest
            use_poly = max(use_poly.geoms, key=lambda g: g.area)
            coords = list(use_poly.exterior.coords)

        if is_3d:
            out_pts = [[round(c[0], 4), 0, round(c[1], 4)] for c in coords]
        else:
            out_pts = [[round(c[0], 4), round(c[1], 4)] for c in coords]

        centroid = use_poly.centroid
        ct = [round(centroid.x, 4), 0, round(centroid.y, 4)] if is_3d else [round(centroid.x, 4), round(centroid.y, 4)]

        derived.append({
            "id": f"derived_bldg_1",
            "type": "Polygon",
            "zone_type": "sub_zone",
            "zone_label": "Derived Building 1",
            "points": out_pts,
            "closed": True,
            "color_hint": "#8b5cf6",
            "confidence": 0.45,
            "classification_method": f"derived_from_envelope (inset_60pct)",
            "filled": False,
            "area_pdf_units": round(use_poly.area, 1),
            "area_m2": round(use_poly.area, 1),
            "centroid": ct,
            "stroke_width": 1.0,
            "marker_labels": [],
            "_derived": True,
            "_target_floors": floors,
            "_target_gfa": target_gfa,
        })
        log.info("[Volumes/Derive] Inset envelope to 60%% coverage as footprint (%.0f m²)",
                 use_poly.area)

    return derived


def _polygon_area_2d(pts: List[List[float]]) -> float:
    """Compute area of a 2D polygon via shoelace formula (works for 3D pts too, uses x,z)."""
    n = len(pts)
    if n < 3:
        return 0
    is_3d = len(pts[0]) >= 3
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        x_i = pts[i][0]
        y_i = pts[i][2] if is_3d else pts[i][1]
        x_j = pts[j][0]
        y_j = pts[j][2] if is_3d else pts[j][1]
        area += x_i * y_j - x_j * y_i
    return abs(area) / 2.0


def generate_volumes(
    zones: List[Dict[str, Any]],
    programmes: List[Dict[str, Any]],
    constraints: List[Dict[str, Any]],
    site_brief: Optional[Dict[str, Any]] = None,
    zone_rules: Optional[List[Dict[str, Any]]] = None,
    zone_programmes: Optional[List[Dict[str, Any]]] = None,
    api_key: Optional[str] = None,
    model_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generate 3D floor-by-floor volumes from building footprints + programme.

    Key principles:
      - GFA-driven: floor count = target_gfa / footprint_area, capped by height
      - Overlap prevention: skip buildings whose footprint overlaps existing ones
      - Plinth relationship: towers sit ON the plinth (base_y = plinth_height)
      - Zone-level parking: full zone outline, not per-building
    """
    log.info("[Volumes] Starting volume generation: %d zones, %d programmes, %d constraints",
             len(zones), len(programmes), len(constraints))

    from shapely.geometry import Polygon as ShapelyPoly
    from shapely.validation import make_valid

    # ── Build per-zone rules & programme lookups ──
    zr_list = zone_rules or (site_brief.get("zone_rules", []) if site_brief else [])
    zp_list = zone_programmes or []
    zp_by_zone_id: Dict[str, Dict] = {zp["zone_id"]: zp for zp in zp_list if "zone_id" in zp}
    log.info("[Volumes] %d zone_rules, %d zone_programmes available", len(zr_list), len(zp_list))

    # ── Helper: make shapely poly from points ──
    def make_poly(pts: List[List[float]]) -> Optional[Any]:
        if len(pts) < 3:
            return None
        is_3d = len(pts[0]) >= 3
        pts_2d = [(p[0], p[2]) if is_3d else (p[0], p[1]) for p in pts]
        try:
            poly = ShapelyPoly(pts_2d)
            if not poly.is_valid:
                poly = make_valid(poly)
            if poly.is_empty or poly.area < 0.01:
                return None
            return poly
        except Exception:
            return None

    # ── Non-buildable zone types — never extruded ──
    NON_BUILDABLE_LABELS = ("verkeer", "traffic", "road", "street", "weg",
                            "straat", "fiets", "bicycle", "voetpad", "parking lot")

    # ── Collect buildable envelopes ──
    buildable_envelopes = []
    for z in zones:
        zt = z.get("zone_type", "")
        if zt not in ("buildable_envelope", "filled_zone"):
            continue
        if not z.get("closed"):
            continue
        # Exclude traffic/road zones
        if zt in ("traffic_zone", "infrastructure_zone", "landscape_zone"):
            continue
        label = (z.get("zone_label") or "").lower()
        if any(kw in label for kw in NON_BUILDABLE_LABELS):
            log.info("[Volumes] Skipping non-buildable zone: '%s' (label contains road/traffic keyword)", label)
            continue
        sp = make_poly(z.get("points", []))
        if sp:
            z["_shapely"] = sp
            buildable_envelopes.append(z)

    # ── Ensure every buildable envelope has a zone programme with target GFA & Max Height ──
    for env in buildable_envelopes:
        env_id = env.get("id", "")
        if not env_id:
            continue
        zp = zp_by_zone_id.get(env_id)

        # If no zp exists, create a default one
        if not zp:
            zp = {
                "zone_id": env_id,
                "use": env.get("typology", "residential"),
            }
            zp_by_zone_id[env_id] = zp

        # Ensure max_height_m is set
        if not zp.get("max_height_m"):
            zp["max_height_m"] = max_height_global or 15.0

        # Ensure target_gfa_m2 is set
        if not zp.get("target_gfa_m2") or zp["target_gfa_m2"] <= 0:
            env_sp = env.get("_shapely")
            if env_sp:
                # Suggest target GFA based on FSI matching typology
                typology = env.get("typology") or zp.get("use") or (site_brief.get("dominant_typology") if site_brief else "residential")
                fsi = 1.5
                if typology:
                    typology = typology.lower()
                    if "office" in typology or "commercial" in typology:
                        fsi = 2.0
                    elif "retail" in typology:
                        fsi = 1.0
                    elif "industrial" in typology:
                        fsi = 0.6
                zp["target_gfa_m2"] = round(env_sp.area * fsi, 1)
                log.info("[Volumes] Zone '%s' has no target GFA. Suggested fallback target GFA of %.1f m² (FSI=%.1f, typology=%s)",
                         env.get("zone_label", env_id), zp["target_gfa_m2"], fsi, typology)

    # ── Plot area for sizing reference ──
    plot_area = 0
    for z in zones:
        if z.get("zone_type") == "plot_boundary" and z.get("area_pdf_units", 0) > 0:
            plot_area = max(plot_area, z["area_pdf_units"])
    if not plot_area:
        plot_area = max((z.get("area_pdf_units", 0) for z in zones), default=1)

    # ── Collect building footprints (sub_zone only) ──
    buildings: Dict[str, Dict] = {}
    for z in zones:
        zt = z.get("zone_type", "")
        if zt != "sub_zone":
            continue
        z_area = z.get("area_pdf_units", 0)
        # Skip sub_zones larger than 40% of plot — zone boundaries, not buildings
        if plot_area > 0 and z_area > plot_area * 0.4:
            log.info("[Volumes] Skipping oversized sub_zone '%s' (%.0f%%)",
                     z.get("zone_label", z["id"]), z_area / plot_area * 100)
            continue
        sp = make_poly(z.get("points", []))
        if sp:
            try:
                minr = sp.minimum_rotated_rectangle
                coords = list(minr.exterior.coords)
                if len(coords) >= 3:
                    w = math.dist(coords[0], coords[1])
                    h = math.dist(coords[1], coords[2])
                    bldg_width = min(w, h)
                    if bldg_width < 4.0:
                        log.info("[Volumes] Skipping narrow sub_zone '%s' (width = %.2fm < 4m)",
                                 z.get("zone_label", z["id"]), bldg_width)
                        continue
            except Exception as e:
                log.warning("[Volumes] Error checking width of sub_zone: %s", e)

            z["_shapely"] = sp
            buildings[z["id"]] = z

    log.info("[Volumes] %d building footprints collected", len(buildings))

    # ── Map each building to its parent buildable zone ──
    bldg_parent_zone: Dict[str, Dict] = {}
    for bid, bldg in buildings.items():
        b_sp = bldg.get("_shapely")
        if not b_sp:
            continue
        for env in buildable_envelopes:
            e_sp = env.get("_shapely")
            if not e_sp:
                continue
            try:
                if e_sp.contains(b_sp.centroid) or e_sp.intersection(b_sp).area > b_sp.area * 0.3:
                    bldg_parent_zone[bid] = env
                    break
            except Exception:
                continue

    # ── Derive footprints for empty buildable zones ──
    derived_footprints: List[Dict[str, Any]] = []
    if not buildings:
        log.info("[Volumes] No buildings — deriving from all buildable envelopes")
        for env in buildable_envelopes:
            zp = zp_by_zone_id.get(env.get("id", ""))
            zone_gfa = zp.get("target_gfa_m2") if zp else None
            zone_height = zp.get("max_height_m") if zp else None
            zone_constraints = list(constraints)
            if zone_gfa:
                zone_constraints.append({"category": "gfa", "value": zone_gfa, "unit": "m²", "name": "Zone GFA"})
            if zone_height:
                zone_constraints.append({"category": "height", "value": zone_height, "unit": "m", "name": "Zone Height"})
            dfs = _derive_footprints_from_envelope([env], zone_constraints, api_key=api_key, model_name=model_name)
            for df in dfs:
                sp = make_poly(df.get("points", []))
                if sp:
                    df["_shapely"] = sp
                buildings[df["id"]] = df
                bldg_parent_zone[df["id"]] = env
                zones.append(df)
                derived_footprints.append(df)
    else:
        # Check for empty zones that have no buildings inside
        for env in buildable_envelopes:
            e_sp = env.get("_shapely")
            if not e_sp:
                continue
            has_bldg = any(
                bldg_parent_zone.get(bid) and bldg_parent_zone[bid].get("id") == env.get("id")
                for bid in buildings
            )
            if has_bldg:
                continue
            # Empty zone — derive a building
            zp = zp_by_zone_id.get(env.get("id", ""))
            zone_gfa = zp.get("target_gfa_m2") if zp else None
            zone_height = zp.get("max_height_m") if zp else None
            zone_constraints = list(constraints)
            if zone_gfa:
                zone_constraints.append({"category": "gfa", "value": zone_gfa, "unit": "m²", "name": "Zone GFA"})
            if zone_height:
                zone_constraints.append({"category": "height", "value": zone_height, "unit": "m", "name": "Zone Height"})
            log.info("[Volumes] Empty zone '%s' — deriving footprint", env.get("zone_label", env.get("id")))
            dfs = _derive_footprints_from_envelope([env], zone_constraints, api_key=api_key, model_name=model_name)
            for df in dfs:
                df["id"] = f"derived_{env['id']}"
                df["zone_label"] = f"Derived ({env.get('zone_label', 'Building')})"
                sp = make_poly(df.get("points", []))
                if sp:
                    df["_shapely"] = sp
                buildings[df["id"]] = df
                bldg_parent_zone[df["id"]] = env
                zones.append(df)
                derived_footprints.append(df)

    if not buildings:
        log.info("[Volumes] No buildings to generate volumes for")
        return {"volumes": [], "volume_summary": {"total": 0}, "derived_footprints": [], "annotations": []}

    # ── Global constraints fallback ──
    max_height_global = None
    all_heights = []
    for c in constraints:
        if c.get("category") == "height":
            v = c.get("value", 0)
            if v > 0:
                all_heights.append(v)
                if max_height_global is None or v < max_height_global:
                    max_height_global = v

    # Safety: if NO height constraint found, use a reasonable default (45m ≈ 15 floors)
    if max_height_global is None:
        max_height_global = 45.0
        log.warning("[Volumes] No height constraints found — using default cap of %.0fm", max_height_global)
    else:
        log.info("[Volumes] Height constraints found: %s → using most restrictive = %.1fm",
                 [f"{h:.0f}m" for h in all_heights], max_height_global)



    # ── Generate zone-level volumes: plinths + parking ──
    volumes: List[Dict[str, Any]] = []
    plinth_heights: Dict[str, float] = {}

    for env in buildable_envelopes:
        env_id = env.get("id", "")
        env_pts = env.get("points", [])
        if len(env_pts) < 3:
            continue

        zp = zp_by_zone_id.get(env_id)
        if not zp:
            env_label = (env.get("zone_label") or "").lower()
            for zpx in zp_list:
                zp_label = (zpx.get("zone_label") or "").lower()
                if zp_label and env_label and (zp_label in env_label or env_label in zp_label):
                    zp = zpx
                    break

        typology = zp.get("typology", "infill") if zp else "infill"
        parking_levels = zp.get("parking_levels", 0) if zp else 0

        # ── Plinth (only for plinth_tower typology) ──
        if typology == "plinth_tower":
            plinth_height = DEFAULT_PLINTH_HEIGHT
            # Plinth should be an inset of the zone, not the full boundary
            e_sp = env.get("_shapely")
            plinth_pts = env_pts  # fallback to full envelope
            if e_sp:
                # Inset by ~10% of the zone perimeter for a realistic podium
                inset_dist = max(e_sp.length * 0.04, 1.5)
                inset = e_sp.buffer(-inset_dist, join_style=2)
                if not inset.is_empty and inset.area > e_sp.area * 0.3:
                    if hasattr(inset, 'exterior'):
                        inset_poly = inset
                    elif hasattr(inset, 'geoms'):
                        inset_poly = max(inset.geoms, key=lambda g: g.area)
                    else:
                        inset_poly = None
                    if inset_poly and hasattr(inset_poly, 'exterior'):
                        is_3d = len(env_pts[0]) >= 3
                        plinth_pts = [[round(c[0], 4), 0, round(c[1], 4)] if is_3d
                                      else [round(c[0], 4), round(c[1], 4)]
                                      for c in inset_poly.exterior.coords]
                        log.info("[Volumes] Plinth inset: %.0f → %.0fm² (%.0f%%)",
                                 e_sp.area, inset_poly.area, inset_poly.area / e_sp.area * 100)
            volumes.append(_create_floor_volume(
                footprint=plinth_pts, y_bottom=0, y_top=plinth_height,
                building_id=f"plinth_{env_id}",
                building_label=f"Plinth ({env.get('zone_label', 'Mixed-Use')})",
                floor_index=0, floor_label="Commercial Plinth",
                use_type="retail", volume_type="plinth", confidence=0.75,
            ))
            plinth_heights[env_id] = plinth_height
            log.info("[Volumes] Plinth for '%s' (%.1fm)", env.get("zone_label", env_id), plinth_height)

        # ── Zone-level underground parking ──
        if parking_levels > 0:
            for pl in range(parking_levels):
                y_bottom = -(pl + 1) * DEFAULT_PARKING_DEPTH
                y_top = -pl * DEFAULT_PARKING_DEPTH
                volumes.append(_create_floor_volume(
                    footprint=env_pts, y_bottom=y_bottom, y_top=y_top,
                    building_id=f"parking_{env_id}",
                    building_label=f"Parking ({env.get('zone_label', '')})",
                    floor_index=-(pl + 1), floor_label=f"Parking B{pl + 1}",
                    use_type="underground_parking", volume_type="underground_parking",
                    confidence=0.70,
                ))
            log.info("[Volumes] %d parking levels for '%s'", parking_levels, env.get("zone_label", env_id))

    # ── Overlap detection: track processed footprint polygons per zone ──
    processed_polys: List[Any] = []  # list of shapely polys already extruded

    buildings_processed = 0
    prog_by_building = {p["building_id"]: p for p in programmes if "building_id" in p}

    for bid, bldg in buildings.items():
        footprint = bldg.get("points", [])
        if len(footprint) < 3:
            continue

        b_sp = bldg.get("_shapely")
        if not b_sp:
            b_sp = make_poly(footprint)
        if not b_sp:
            continue

        # ── Overlap check: skip if >20% overlap with any processed building ──
        skip = False
        for existing_sp in processed_polys:
            try:
                inter = b_sp.intersection(existing_sp)
                if inter.area > b_sp.area * 0.20:
                    log.info("[Volumes] Skipping '%s' — overlaps existing building (%.0f%%)",
                             bldg.get("zone_label", bid), inter.area / b_sp.area * 100)
                    skip = True
                    break
            except Exception:
                continue
        if skip:
            continue

        processed_polys.append(b_sp)

        # ── Get zone-level constraints from parent zone ──
        parent_zone = bldg_parent_zone.get(bid)
        parent_id = parent_zone.get("id", "") if parent_zone else ""
        zp = zp_by_zone_id.get(parent_id) if parent_id else None

        # Zone-level constraints
        zone_max_height = None
        zone_target_gfa = None
        if zp:
            zone_max_height = zp.get("max_height_m")
            zone_target_gfa = zp.get("target_gfa_m2")

        # Fall back to site-wide
        effective_max_height = zone_max_height or max_height_global
        log.info("[Volumes] Building '%s': zone_height=%s, global=%s → effective=%.1fm",
                 bldg.get("zone_label", bid),
                 f"{zone_max_height:.0f}m" if zone_max_height else "none",
                 f"{max_height_global:.0f}m" if max_height_global else "none",
                 effective_max_height or 0)

        # ── Determine base_y (plinth_tower: tower starts on top of plinth) ──
        base_y = 0.0
        if parent_id in plinth_heights:
            base_y = plinth_heights[parent_id]

        # ── Compute footprint area ──
        footprint_area = b_sp.area
        if footprint_area < 0.1:
            continue

        # ── GFA-driven floor count ──
        floor_height = DEFAULT_FLOOR_HEIGHT
        prog = prog_by_building.get(bid)
        is_derived = bldg.get("_derived", False)

        if prog:
            floors = prog.get("floors", 4)
            floor_height = prog.get("floor_height", DEFAULT_FLOOR_HEIGHT)
            uses = prog.get("uses", [])
            blabel = prog.get("building_label", bldg.get("zone_label", f"Building {buildings_processed + 1}"))
            confidence = prog.get("confidence", 0.7)
        else:
            # Determine building use for footprint cap
            zp_use = zp.get("use", "residential") if zp else "residential"
            max_fp = MAX_FOOTPRINT_BY_USE.get(zp_use, DEFAULT_MAX_FOOTPRINT)

            # Cap the effective footprint for floor calculation
            # (real buildings don't have 3000m² residential footprints)
            effective_fp = min(footprint_area, max_fp)
            if effective_fp < footprint_area:
                log.info("[Volumes] Capping footprint for '%s': %.0f → %.0fm² (use=%s)",
                         bldg.get("zone_label", bid), footprint_area, effective_fp, zp_use)

            # GFA-driven: floors based on proportional GFA split
            if zone_target_gfa and zone_target_gfa > 0 and effective_fp > 0:
                # Sum the effective footprints of all buildings in this zone
                total_eff_fp_in_zone = 0.0
                for b2id in buildings:
                    if bldg_parent_zone.get(b2id, {}).get("id") == parent_id:
                        b2_sp = buildings[b2id].get("_shapely")
                        if b2_sp:
                            total_eff_fp_in_zone += min(b2_sp.area, max_fp)
                
                total_eff_fp_in_zone = max(total_eff_fp_in_zone, 0.01)
                
                # Proportional GFA allocation
                gfa_for_this_building = zone_target_gfa * (effective_fp / total_eff_fp_in_zone)
                floors = max(1, math.ceil(gfa_for_this_building / effective_fp))
                log.info("[Volumes] GFA-driven: '%s' → %.0fm² zone GFA (proportional: %.0fm² for this bldg) / %.0fm² eff.fp = %d floors",
                         bldg.get("zone_label", bid), zone_target_gfa, gfa_for_this_building, effective_fp, floors)
            elif effective_max_height:
                available = effective_max_height - base_y
                floors = max(1, int(available / floor_height))
            else:
                floors = 4

            # Minimum 3 floors for any building (realistic minimum)
            if floors < 3 and zp_use not in ("industrial", "retail"):
                floors = 3

            uses = []
            blabel = bldg.get("zone_label", f"Building {buildings_processed + 1}")
            confidence = 0.55 if is_derived else 0.50

        # ── Cap by height constraint ──
        if effective_max_height:
            available_height = effective_max_height - base_y
            max_floors = max(1, int(available_height / floor_height))
            if floors > max_floors:
                log.info("[Volumes] Height cap: '%s' %d→%d floors (limit=%.1fm, base=%.1fm)",
                         blabel, floors, max_floors, effective_max_height, base_y)
                floors = max_floors

        # ── Generate above-ground floors ──
        for f in range(floors):
            y_bottom = base_y + f * floor_height
            y_top = base_y + (f + 1) * floor_height

            floor_use = "residential"
            floor_label = f"Floor {f}"
            if uses:
                for u in uses:
                    if u.get("floor") == f:
                        floor_use = u.get("use", "residential")
                        floor_label = u.get("label", f"Floor {f}")
                        break
                else:
                    floor_use = uses[-1].get("use", "residential") if uses else "residential"
                    floor_label = f"Floor {f}"

            volumes.append(_create_floor_volume(
                footprint=footprint, y_bottom=y_bottom, y_top=y_top,
                building_id=bid, building_label=blabel,
                floor_index=f, floor_label=floor_label,
                use_type=floor_use, volume_type="building_floor",
                confidence=confidence,
            ))

        buildings_processed += 1

    # ── Zone annotation tags (numbered labels at zone centroids) ──
    annotations: List[Dict[str, Any]] = []
    for idx, env in enumerate(buildable_envelopes):
        env_pts = env.get("points", [])
        if len(env_pts) < 3:
            continue
        is_3d = len(env_pts[0]) >= 3
        cx = sum(p[0] for p in env_pts) / len(env_pts)
        cz = sum(p[2] for p in env_pts) / len(env_pts) if is_3d else sum(p[1] for p in env_pts) / len(env_pts)
        zone_num = idx + 1
        zone_label = env.get("zone_label", f"Zone {zone_num}")
        centroid = [round(cx, 4), 0, round(cz, 4)] if is_3d else [round(cx, 4), round(cz, 4)]
        annotations.append({
            "id": f"annot_zone_{env.get('id', idx)}",
            "type": "annotation",
            "zone_type": "zone_annotation",
            "zone_label": f"Z{zone_num}",
            "annotation_text": f"Z{zone_num}: {zone_label}",
            "points": [centroid],
            "centroid": centroid,
            "closed": False,
            "filled": False,
            "color_hint": "#22c55e",
            "layer": "Zones",
            "sublayer": "Annotation",
            "confidence": 1.0,
            "zone_number": zone_num,
        })
    if annotations:
        log.info("[Volumes] Created %d zone annotation tags", len(annotations))

    # Summary
    summary = {
        "total": len(volumes),
        "buildings_processed": buildings_processed,
        "building_floors": len([v for v in volumes if v["zone_type"] == "building_floor"]),
        "plinths": len([v for v in volumes if v["zone_type"] == "plinth"]),
        "parking_levels": len([v for v in volumes if v["zone_type"] == "underground_parking"]),
        "derived_from_envelope": len(derived_footprints),
        "annotations": len(annotations),
    }

    log.info("[Volumes] Generated %d volumes (%d floors, %d plinths, %d parking) for %d buildings (%d derived)",
             summary["total"], summary["building_floors"],
             summary["plinths"], summary["parking_levels"],
             summary["buildings_processed"], summary["derived_from_envelope"])

    return {
        "volumes": volumes,
        "volume_summary": summary,
        "derived_footprints": derived_footprints,
        "annotations": annotations,
    }


