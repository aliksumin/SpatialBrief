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
        # Apply a default setback inset (never use the full zone as-is)
        default_inset = max(poly.length * 0.03, 2.0)  # ~3% of perimeter or 2 units
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
            "centroid": ct,
            "stroke_width": 1.0,
            "marker_labels": [],
            "_derived": True,
            "_target_floors": floors,
            "_target_gfa": target_gfa,
        })
        log.info("[Volumes/Derive] Inset envelope to 60%% coverage as footprint (%.0f sq units)",
                 use_poly.area)

    return derived


def generate_volumes(
    zones: List[Dict[str, Any]],
    programmes: List[Dict[str, Any]],
    constraints: List[Dict[str, Any]],
    site_brief: Optional[Dict[str, Any]] = None,
    zone_rules: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Generate 3D floor-by-floor volumes from building footprints + programme.

    When no building footprints exist, derives them from the buildable
    envelope using per-zone constraints (target GFA, density, height).

    Args:
        site_brief: Site brief with per-zone expectations
        zone_rules: Per-zone constraint rules (height, GFA, setback per zone)

    Returns:
        {
            "volumes": [...],           # List of volume geometry objects
            "volume_summary": {...},    # Stats about generation
            "derived_footprints": [...],# Footprints generated from envelope (if any)
        }
    """
    log.info("[Volumes] Starting volume generation: %d zones, %d programmes, %d constraints",
             len(zones), len(programmes), len(constraints))

    # ── Build per-zone rules lookup ──
    # zone_rules come from Extract Constraints (which merged site_brief + extracted data)
    zr_list = zone_rules or (site_brief.get("zone_rules", []) if site_brief else [])
    # We'll match zone_rules to actual geometry zones later by label similarity
    log.info("[Volumes] %d zone_rules available for per-zone constraints", len(zr_list))

    # Collect building footprints (sub_zone + plinth)
    # Filter out oversized sub_zones — they're likely misclassified envelopes
    plot_area = 0
    for z in zones:
        if z.get("zone_type") == "plot_boundary" and z.get("area_pdf_units", 0) > 0:
            plot_area = max(plot_area, z["area_pdf_units"])
    # If no plot found, estimate from max zone area
    if not plot_area:
        plot_area = max((z.get("area_pdf_units", 0) for z in zones), default=1)

    buildings = {}
    for z in zones:
        zt = z.get("zone_type", "")
        if zt not in ("sub_zone", "plinth"):
            continue
        z_area = z.get("area_pdf_units", 0)
        # Skip sub_zones larger than 40% of plot — they're zone boundaries, not buildings
        if zt == "sub_zone" and plot_area > 0 and z_area > plot_area * 0.4:
            log.info("[Volumes] Skipping oversized sub_zone '%s' (%.0f units² = %.0f%% of plot)",
                     z.get("zone_label", z["id"]), z_area, z_area / plot_area * 100)
            continue
        buildings[z["id"]] = z

    log.info("[Volumes] %d building footprints collected (filtered from %d sub_zone/plinth)",
             len(buildings), sum(1 for z in zones if z.get('zone_type') in ('sub_zone', 'plinth')))

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
    else:
        # ── Check for empty buildable zones (zones with no buildings inside) ──
        from shapely.geometry import Polygon as ShapelyPoly
        from shapely import affinity

        # Include: explicit buildable types + oversized sub_zones we filtered out
        buildable_zones = []
        for z in zones:
            if not z.get("closed"):
                continue
            zt = z.get("zone_type", "")
            zid = z.get("id", "")
            if zt in ("buildable_envelope", "filled_zone", "uncategorized_zone"):
                buildable_zones.append(z)
            elif zt == "sub_zone" and zid not in buildings:
                # Oversized sub_zone that was filtered from buildings — treat as zone
                buildable_zones.append(z)
        building_list = list(buildings.values())
        log.info("[Volumes] Checking %d zones for empty buildable areas", len(buildable_zones))

        for bz in buildable_zones:
            bz_sp = bz.get("shapely_poly")
            if not bz_sp:
                pts = bz.get("points", [])
                if len(pts) < 3:
                    continue
                is_3d = len(pts[0]) == 3
                pts_2d = [(p[0], p[2]) if is_3d else (p[0], p[1]) for p in pts]
                try:
                    bz_sp = ShapelyPoly(pts_2d)
                    if not bz_sp.is_valid:
                        from shapely.validation import make_valid
                        bz_sp = make_valid(bz_sp)
                except Exception:
                    continue

            # Check if ANY building is inside this zone
            has_building_inside = False
            for b in building_list:
                b_sp = b.get("shapely_poly")
                if not b_sp:
                    b_pts = b.get("points", [])
                    if len(b_pts) < 3:
                        continue
                    b_is_3d = len(b_pts[0]) == 3
                    b_pts_2d = [(p[0], p[2]) if b_is_3d else (p[0], p[1]) for p in b_pts]
                    try:
                        b_sp = ShapelyPoly(b_pts_2d)
                    except Exception:
                        continue
                try:
                    if bz_sp.contains(b_sp.centroid) or (
                        bz_sp.intersection(b_sp).area > b_sp.area * 0.5
                    ):
                        has_building_inside = True
                        break
                except Exception:
                    continue

            if not has_building_inside:
                # Find matching zone_rule for this zone
                bz_label = (bz.get("zone_label") or "").lower()
                matched_rule = None
                for zr in zr_list:
                    zr_label = (zr.get("zone_label") or "").lower()
                    if zr_label and bz_label and (
                        zr_label in bz_label or bz_label in zr_label
                    ):
                        matched_rule = zr
                        break
                # If no match by label, use the next unmatched rule
                if not matched_rule and zr_list:
                    matched_rule = zr_list[0]

                # Build per-zone constraints for derivation
                zone_constraints = list(constraints)  # start with site-wide
                if matched_rule:
                    # Add zone-specific overrides as constraints
                    if matched_rule.get("max_height_m"):
                        zone_constraints.append({
                            "category": "height", "value": matched_rule["max_height_m"],
                            "unit": "m", "name": f"Height ({matched_rule.get('zone_label', '')})",
                        })
                    if matched_rule.get("target_gfa_m2"):
                        zone_constraints.append({
                            "category": "gfa", "value": matched_rule["target_gfa_m2"],
                            "unit": "m²", "name": f"GFA ({matched_rule.get('zone_label', '')})",
                        })

                log.info("[Volumes] Empty zone: %s (%s) — deriving footprint (rule: %s)",
                         bz.get("id", "?"), bz_label or bz.get("zone_type"),
                         matched_rule.get("zone_label") if matched_rule else "none")

                single_zone_derived = _derive_footprints_from_envelope(
                    [bz] + [z for z in zones if z.get("zone_type") == "plot_boundary"],
                    zone_constraints,
                )
                for df in single_zone_derived:
                    df["id"] = f"derived_{bz['id']}"
                    df["zone_label"] = f"Derived ({bz.get('zone_label', 'Mixed-Use')})"
                    if matched_rule:
                        df["_zone_rule"] = matched_rule
                    buildings[df["id"]] = df
                    zones.append(df)
                    derived_footprints.append(df)
                    log.info("[Volumes] Derived building for empty zone: %s", df["id"])

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

    # ── Detect plinth-building relationships ──
    # A plinth is a large footprint containing smaller building footprints.
    # Buildings on a plinth should start from the plinth top, not ground.
    plinths = {bid: b for bid, b in buildings.items()
               if b.get("zone_type") == "plinth"}
    towers = {bid: b for bid, b in buildings.items()
              if b.get("zone_type") == "sub_zone"}

    # Map each tower to the plinth it sits on (if any)
    tower_plinth_map: Dict[str, str] = {}
    for tid, tower in towers.items():
        t_sp = tower.get("shapely_poly")
        if not t_sp:
            continue
        for pid, plinth in plinths.items():
            p_sp = plinth.get("shapely_poly")
            if not p_sp:
                continue
            try:
                if p_sp.contains(t_sp) or (
                    p_sp.intersection(t_sp).area > t_sp.area * 0.7
                ):
                    tower_plinth_map[tid] = pid
                    break
            except Exception:
                continue

    if tower_plinth_map:
        log.info("[Volumes] Plinth-tower relationships: %s",
                 {tid: pid for tid, pid in tower_plinth_map.items()})

    volumes: List[Dict[str, Any]] = []
    buildings_processed = 0

    for bid, bldg in buildings.items():
        footprint = bldg.get("points", [])
        if len(footprint) < 3:
            continue

        is_plinth = bldg.get("zone_type") == "plinth"
        prog = prog_by_building.get(bid)
        is_derived = bldg.get("_derived", False)

        # ── Per-zone height/GFA lookup ──
        # Check if this building has a zone_rule (from derivation) or find one by containment
        bldg_zone_rule = bldg.get("_zone_rule")
        bldg_max_height = max_height  # default to site-wide

        if not bldg_zone_rule and zr_list:
            # Try to find a zone_rule matching this building's label
            bl = (bldg.get("zone_label") or "").lower()
            for zr in zr_list:
                zl = (zr.get("zone_label") or "").lower()
                if zl and bl and (zl in bl or bl in zl):
                    bldg_zone_rule = zr
                    break

        if bldg_zone_rule:
            zr_height = bldg_zone_rule.get("max_height_m")
            if zr_height:
                bldg_max_height = zr_height
                log.debug("[Volumes] Using per-zone height %.1fm for '%s'",
                          bldg_max_height, bldg.get("zone_label", bid))

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
            floor_height = DEFAULT_FLOOR_HEIGHT
            floors = bldg.get("_target_floors", 4)
            # Override with zone_rule if available
            if bldg_zone_rule and bldg_zone_rule.get("max_height_m"):
                floors = max(1, int(bldg_zone_rule["max_height_m"] / floor_height))
            has_plinth = False
            has_parking = False
            parking_ratio = 0
            uses = [{"floor": f, "use": "residential", "label": "Residential"} for f in range(floors)]
            blabel = bldg.get("zone_label", f"Derived Building {buildings_processed + 1}")
            confidence = 0.50
        else:
            floor_height = DEFAULT_FLOOR_HEIGHT
            if bldg_max_height:
                floors = max(1, int(bldg_max_height / floor_height))
            else:
                floors = 4
            has_plinth = False
            has_parking = False
            parking_ratio = 0
            uses = [{"floor": f, "use": "residential", "label": "Residential"} for f in range(floors)]
            blabel = bldg.get("zone_label", f"Building {buildings_processed + 1}")
            confidence = 0.45

        # ── If this IS a plinth, generate only 1 plinth floor ──
        if is_plinth:
            plinth_height = DEFAULT_PLINTH_HEIGHT
            volumes.append(_create_floor_volume(
                footprint=footprint,
                y_bottom=0,
                y_top=plinth_height,
                building_id=bid,
                building_label=blabel or "Plinth",
                floor_index=0,
                floor_label="Commercial Plinth",
                use_type="retail",
                volume_type="plinth",
                confidence=confidence,
            ))
            # Underground parking under plinth
            if has_parking:
                parking_levels = 2 if parking_ratio > 2.0 else 1
                for pl in range(parking_levels):
                    y_bottom = -(pl + 1) * DEFAULT_PARKING_DEPTH
                    y_top = -pl * DEFAULT_PARKING_DEPTH
                    volumes.append(_create_floor_volume(
                        footprint=footprint,
                        y_bottom=y_bottom,
                        y_top=y_top,
                        building_id=bid,
                        building_label=blabel or "Plinth",
                        floor_index=-(pl + 1),
                        floor_label=f"Parking B{pl + 1}",
                        use_type="underground_parking",
                        volume_type="underground_parking",
                        confidence=confidence * 0.8,
                    ))
            buildings_processed += 1
            continue

        # ── Regular building (tower) — check if sits on a plinth ──
        parent_plinth_id = tower_plinth_map.get(bid)
        base_y = DEFAULT_PLINTH_HEIGHT if parent_plinth_id else 0

        # Enforce height constraint (per-zone or site-wide)
        effective_height = bldg_max_height or max_height
        if effective_height:
            available_height = effective_height - base_y
            max_floors = max(1, int(available_height / floor_height))
            if floors > max_floors:
                floors = max_floors
            total_height = base_y + floors * floor_height
            log.info("[Volumes] Height-capped '%s': %d floors, %.1fm (base=%.1fm, limit=%.1fm%s)",
                     blabel, floors, total_height, base_y, effective_height,
                     " [per-zone]" if bldg_zone_rule else "")

        # Underground parking (only if building is NOT on a plinth)
        if has_parking and not parent_plinth_id:
            parking_levels = 2 if parking_ratio > 2.0 else 1
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

        # Above-ground floors
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

            # Ground floor of a building ON a plinth doesn't get a plinth label
            # (the plinth is already a separate volume)
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
