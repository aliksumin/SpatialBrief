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

# ── Default floor height if not specified ──
DEFAULT_FLOOR_HEIGHT = 3.0
DEFAULT_PLINTH_HEIGHT = 4.5
DEFAULT_PARKING_DEPTH = 3.0


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
    """Create a single floor volume as geometry for the viewport."""

    bottom_pts = _offset_points_y(footprint, y_bottom)
    top_pts = _offset_points_y(footprint, y_top)
    centroid = _compute_centroid(footprint)
    centroid_y = (y_bottom + y_top) / 2

    color = USE_COLORS.get(use_type, USE_COLORS.get(volume_type, "#94a3b8"))

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
        "source_layer": "volumes",
    }


def _derive_footprints_from_envelope(
    zones: List[Dict[str, Any]],
    constraints: List[Dict[str, Any]],
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
        # Scale setback into viewport coordinates
        coords = list(poly.exterior.coords)
        xs = [c[0] for c in coords]
        zs = [c[1] for c in coords]
        span = max(max(xs) - min(xs), max(zs) - min(zs), 1)
        viewport_offset = setback_dist * (span / 100.0)
        viewport_offset = max(viewport_offset, 0.3)
        inset = poly.buffer(-viewport_offset, join_style=2)
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

    if footprint_area and footprint_area < envelope_area * 0.95:
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
                "id": f"derived_bldg_1",
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
                "centroid": ct,
                "stroke_width": 1.0,
                "marker_labels": [],
                "_derived": True,
                "_target_floors": floors,
                "_target_gfa": target_gfa,
            })
            log.info("[Volumes/Derive] Created derived footprint: %.0f sq units (method=%s)",
                     scaled.area, method)
    else:
        # Use the full envelope as a single footprint
        coords = list(poly.exterior.coords)
        if is_3d:
            out_pts = [[round(c[0], 4), 0, round(c[1], 4)] for c in coords]
        else:
            out_pts = [[round(c[0], 4), round(c[1], 4)] for c in coords]

        centroid = poly.centroid
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
            "classification_method": f"derived_from_envelope ({method})",
            "filled": False,
            "area_pdf_units": round(poly.area, 1),
            "centroid": ct,
            "stroke_width": 1.0,
            "marker_labels": [],
            "_derived": True,
            "_target_floors": floors,
            "_target_gfa": target_gfa,
        })
        log.info("[Volumes/Derive] Using full envelope as footprint (%.0f sq units)", poly.area)

    return derived


def generate_volumes(
    zones: List[Dict[str, Any]],
    programmes: List[Dict[str, Any]],
    constraints: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Generate 3D floor-by-floor volumes from building footprints + programme.

    When no building footprints exist, derives them from the buildable
    envelope using constraints (target GFA, density, height).

    Returns:
        {
            "volumes": [...],           # List of volume geometry objects
            "volume_summary": {...},    # Stats about generation
            "derived_footprints": [...],# Footprints generated from envelope (if any)
        }
    """
    log.info("[Volumes] Starting volume generation: %d zones, %d programmes, %d constraints",
             len(zones), len(programmes), len(constraints))

    # Collect building footprints (sub_zone + plinth)
    buildings = {z["id"]: z for z in zones
                 if z.get("zone_type") in ("sub_zone", "plinth")}

    # ── If no buildings found, derive from envelope + constraints ──
    derived_footprints: List[Dict[str, Any]] = []
    if not buildings:
        log.info("[Volumes] No building footprints found — deriving from envelope + constraints")
        derived_footprints = _derive_footprints_from_envelope(zones, constraints)
        if derived_footprints:
            for df in derived_footprints:
                buildings[df["id"]] = df
                zones.append(df)  # Add to zones so downstream sees them
            log.info("[Volumes] Derived %d building footprint(s) from envelope", len(derived_footprints))
        else:
            log.info("[Volumes] Could not derive footprints — no volumes to generate")
            return {"volumes": [], "volume_summary": {"total": 0}, "derived_footprints": []}

    # Map programme by building_id
    prog_by_building = {p["building_id"]: p for p in programmes if "building_id" in p}

    # Get most restrictive height constraint (minimum value = strictest limit)
    max_height = None
    target_gfa = None
    for c in constraints:
        if c.get("category") == "height":
            if max_height is None or c["value"] < max_height:
                max_height = c["value"]
        elif c.get("category") == "gfa":
            if target_gfa is None or c["value"] > target_gfa:
                target_gfa = c["value"]

    volumes: List[Dict[str, Any]] = []
    buildings_processed = 0

    for bid, bldg in buildings.items():
        footprint = bldg.get("points", [])
        if len(footprint) < 3:
            continue

        prog = prog_by_building.get(bid)
        is_derived = bldg.get("_derived", False)

        if prog:
            floors = prog.get("floors", 4)
            floor_height = prog.get("floor_height", DEFAULT_FLOOR_HEIGHT)
            has_plinth = prog.get("has_plinth", False)
            has_parking = prog.get("has_underground_parking", False)
            parking_ratio = prog.get("parking_ratio", 0)
            uses = prog.get("uses", [])
            blabel = prog.get("building_label", bldg.get("zone_label", f"Building {buildings_processed + 1}"))
            confidence = prog.get("confidence", 0.7)
        elif is_derived:
            # Use floors derived from constraints
            floor_height = DEFAULT_FLOOR_HEIGHT
            floors = bldg.get("_target_floors", 4)
            has_plinth = False
            has_parking = False
            parking_ratio = 0
            uses = [{"floor": f, "use": "residential", "label": "Residential"} for f in range(floors)]
            blabel = bldg.get("zone_label", f"Derived Building {buildings_processed + 1}")
            confidence = 0.50
        else:
            # No programme data — use defaults
            floor_height = DEFAULT_FLOOR_HEIGHT
            if max_height:
                floors = max(1, int(max_height / floor_height))
            else:
                floors = 4
            has_plinth = False
            has_parking = False
            parking_ratio = 0
            uses = [{"floor": f, "use": "residential", "label": "Residential"} for f in range(floors)]
            blabel = bldg.get("zone_label", f"Building {buildings_processed + 1}")
            confidence = 0.45

        # Enforce height constraint (accounting for plinth if present)
        if max_height:
            if has_plinth and floors > 1:
                available_for_upper = max_height - DEFAULT_PLINTH_HEIGHT
                upper_floors = max(0, int(available_for_upper / floor_height))
                floors = 1 + upper_floors
            else:
                total_height = floors * floor_height
                if total_height > max_height:
                    floors = max(1, int(max_height / floor_height))
            total_height = floors * floor_height
            log.info("[Volumes] Height-capped '%s': %d floors, %.1fm (limit %.1fm)",
                     blabel, floors, total_height, max_height)

        # ── Generate underground parking ──
        if has_parking:
            parking_levels = 1
            if parking_ratio > 2.0:
                parking_levels = 2

            for pl in range(parking_levels):
                y_bottom = -(pl + 1) * DEFAULT_PARKING_DEPTH
                y_top = -pl * DEFAULT_PARKING_DEPTH

                volumes.append(_create_floor_volume(
                    footprint=footprint,
                    y_bottom=y_bottom,
                    y_top=y_top,
                    building_id=bid,
                    building_label=blabel,
                    floor_index=-(pl + 1),
                    floor_label=f"Parking B{pl + 1}",
                    use_type="underground_parking",
                    volume_type="underground_parking",
                    confidence=confidence * 0.8,
                ))

        # ── Generate above-ground floors ──
        for f in range(floors):
            y_bottom = f * floor_height
            y_top = (f + 1) * floor_height

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

            if f == 0 and has_plinth:
                y_top = DEFAULT_PLINTH_HEIGHT
                volume_type = "plinth"
                if floor_use == "residential":
                    floor_use = "retail"
                    floor_label = "Commercial Plinth"
            elif has_plinth and f > 0:
                y_bottom = DEFAULT_PLINTH_HEIGHT + (f - 1) * floor_height
                y_top = y_bottom + floor_height
                volume_type = "building_floor"
            else:
                volume_type = "building_floor"

            volumes.append(_create_floor_volume(
                footprint=footprint,
                y_bottom=y_bottom,
                y_top=y_top,
                building_id=bid,
                building_label=blabel,
                floor_index=f,
                floor_label=floor_label,
                use_type=floor_use,
                volume_type=volume_type,
                confidence=confidence,
            ))

        buildings_processed += 1

    # Summary
    summary = {
        "total": len(volumes),
        "buildings_processed": buildings_processed,
        "building_floors": len([v for v in volumes if v["zone_type"] == "building_floor"]),
        "plinths": len([v for v in volumes if v["zone_type"] == "plinth"]),
        "parking_levels": len([v for v in volumes if v["zone_type"] == "underground_parking"]),
        "derived_from_envelope": len(derived_footprints),
    }

    log.info("[Volumes] Generated %d volumes (%d floors, %d plinths, %d parking) for %d buildings (%d derived)",
             summary["total"], summary["building_floors"],
             summary["plinths"], summary["parking_levels"],
             summary["buildings_processed"], summary["derived_from_envelope"])

    return {
        "volumes": volumes,
        "volume_summary": summary,
        "derived_footprints": derived_footprints,
    }
