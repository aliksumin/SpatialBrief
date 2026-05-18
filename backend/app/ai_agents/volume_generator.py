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


def generate_volumes(
    zones: List[Dict[str, Any]],
    programmes: List[Dict[str, Any]],
    constraints: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Generate 3D floor-by-floor volumes from building footprints + programme.

    Returns:
        {
            "volumes": [...],           # List of volume geometry objects
            "volume_summary": {...},    # Stats about generation
        }
    """
    log.info("[Volumes] Starting volume generation: %d zones, %d programmes, %d constraints",
             len(zones), len(programmes), len(constraints))

    # Collect building footprints (sub_zone)
    buildings = {z["id"]: z for z in zones if z.get("zone_type") == "sub_zone"}

    if not buildings:
        log.info("[Volumes] No building footprints (sub_zone) found — skipping")
        return {"volumes": [], "volume_summary": {"total": 0}}

    # Map programme by building_id
    prog_by_building = {p["building_id"]: p for p in programmes if "building_id" in p}

    # Get most restrictive height constraint (minimum value = strictest limit)
    max_height = None
    for c in constraints:
        if c.get("category") == "height":
            if max_height is None or c["value"] < max_height:
                max_height = c["value"]

    volumes: List[Dict[str, Any]] = []
    buildings_processed = 0

    for bid, bldg in buildings.items():
        footprint = bldg.get("points", [])
        if len(footprint) < 3:
            continue

        prog = prog_by_building.get(bid)

        if prog:
            floors = prog.get("floors", 4)
            floor_height = prog.get("floor_height", DEFAULT_FLOOR_HEIGHT)
            has_plinth = prog.get("has_plinth", False)
            has_parking = prog.get("has_underground_parking", False)
            parking_ratio = prog.get("parking_ratio", 0)
            uses = prog.get("uses", [])
            blabel = prog.get("building_label", bldg.get("zone_label", f"Building {buildings_processed + 1}"))
            confidence = prog.get("confidence", 0.7)
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
                # Plinth is taller (4.5m vs 3.0m), so available height
                # for upper floors is reduced
                available_for_upper = max_height - DEFAULT_PLINTH_HEIGHT
                upper_floors = max(0, int(available_for_upper / floor_height))
                floors = 1 + upper_floors  # 1 plinth + upper floors
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

            # Determine floor use
            floor_use = "residential"
            floor_label = f"Floor {f}"
            if uses:
                # Find matching floor use
                for u in uses:
                    if u.get("floor") == f:
                        floor_use = u.get("use", "residential")
                        floor_label = u.get("label", f"Floor {f}")
                        break
                else:
                    # No specific assignment — use the last defined use
                    floor_use = uses[-1].get("use", "residential") if uses else "residential"
                    floor_label = f"Floor {f}"

            # Plinth adjustments
            if f == 0 and has_plinth:
                y_top = DEFAULT_PLINTH_HEIGHT
                volume_type = "plinth"
                if floor_use == "residential":
                    floor_use = "retail"
                    floor_label = "Commercial Plinth"
            elif has_plinth and f > 0:
                # Shift upper floors to start above the taller plinth
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
    }

    log.info("[Volumes] Generated %d volumes (%d floors, %d plinths, %d parking) for %d buildings",
             summary["total"], summary["building_floors"],
             summary["plinths"], summary["parking_levels"],
             summary["buildings_processed"])

    return {
        "volumes": volumes,
        "volume_summary": summary,
    }
