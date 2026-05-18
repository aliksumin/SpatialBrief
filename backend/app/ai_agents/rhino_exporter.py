"""
Rhino/DXF Exporter
==================

Exports all extracted geometry, volumes, and constraints into a layered
DXF file (Rhino-compatible) with full metadata as XDATA/extended data.

Optionally exports as .3dm (OpenNURBS) if the rhino3dm package is available.

Layer hierarchy mirrors the viewer:
  00_Boundaries/
      Plot_Boundary, Zone_Boundary, Parcel_Line, ...
  01_Zones/
      Buildable_Envelope, Landscape_Zone, ...
  02_Buildings/
      Sub_Zone
  03_Constraints/
      Setback_Line, Height_Limit, ...
  04_Generated_Volumes/
      Building_Floor, Plinth, Underground_Parking
  05_Infrastructure/
      Context_Line, CAD_Context, ...
"""
from __future__ import annotations

import io
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

import ezdxf
from ezdxf.enums import TextEntityAlignment

log = logging.getLogger(__name__)

# ── Zone type → layer category mapping (matches frontend ThreeSceneManager) ──

BOUNDARY_TYPES = {
    "plot_boundary", "zone_boundary", "parcel_line",
    "major_boundary", "restriction_line",
}
ZONE_TYPES = {
    "buildable_envelope", "landscape_zone", "infrastructure_zone",
    "filled_zone", "no_build_zone", "uncategorized_zone", "traffic_zone",
}
BUILDING_TYPES = {"sub_zone"}
CONSTRAINT_TYPES = {"setback_line", "height_limit", "constraint_zone"}
VOLUME_TYPES = {"building_floor", "plinth", "underground_parking"}
INFRA_TYPES = {"context_line", "minor_context", "cad_context"}

CATEGORY_MAP = {
    "BOUNDARIES": BOUNDARY_TYPES,
    "ZONES": ZONE_TYPES,
    "BUILDINGS": BUILDING_TYPES,
    "CONSTRAINTS": CONSTRAINT_TYPES,
    "VOLUMES": VOLUME_TYPES,
    "INFRASTRUCTURE": INFRA_TYPES,
}

CATEGORY_PREFIX = {
    "BOUNDARIES": "00_Boundaries",
    "ZONES": "01_Zones",
    "BUILDINGS": "02_Buildings",
    "CONSTRAINTS": "03_Constraints",
    "VOLUMES": "04_Generated_Volumes",
    "INFRASTRUCTURE": "05_Infrastructure",
}

# ── Colors (AutoCAD color indices approximating the viewer palette) ──
# ACI colors: 1=red, 2=yellow, 3=green, 4=cyan, 5=blue, 6=magenta, 7=white
ZONE_ACI_COLORS: Dict[str, int] = {
    "plot_boundary": 5,       # blue
    "zone_boundary": 2,       # yellow
    "parcel_line": 4,         # cyan
    "major_boundary": 7,      # white
    "restriction_line": 1,    # red
    "buildable_envelope": 30, # orange
    "landscape_zone": 3,      # green
    "infrastructure_zone": 9, # grey
    "filled_zone": 30,        # orange
    "no_build_zone": 1,       # red
    "uncategorized_zone": 30, # orange
    "traffic_zone": 9,        # grey
    "sub_zone": 6,            # magenta/purple
    "setback_line": 1,        # red
    "height_limit": 2,        # yellow
    "constraint_zone": 30,    # orange
    "building_floor": 6,      # purple
    "plinth": 30,             # orange
    "underground_parking": 9, # grey
    "context_line": 9,        # grey
    "minor_context": 8,       # dark grey
    "cad_context": 9,         # grey
}

# ── Human-readable layer names ──
ZONE_LAYER_NAMES: Dict[str, str] = {
    "plot_boundary": "Plot_Boundary",
    "zone_boundary": "Zone_Boundary",
    "parcel_line": "Parcel_Line",
    "major_boundary": "Major_Boundary",
    "restriction_line": "Restriction_Line",
    "buildable_envelope": "Buildable_Envelope",
    "landscape_zone": "Landscape_Zone",
    "infrastructure_zone": "Infrastructure_Zone",
    "filled_zone": "Filled_Zone",
    "no_build_zone": "No_Build_Zone",
    "uncategorized_zone": "Uncategorized_Zone",
    "traffic_zone": "Traffic_Zone",
    "sub_zone": "Building_Footprint",
    "setback_line": "Setback_Line",
    "height_limit": "Height_Limit",
    "constraint_zone": "Constraint_Zone",
    "building_floor": "Building_Floor",
    "plinth": "Plinth",
    "underground_parking": "Underground_Parking",
    "context_line": "Context_Line",
    "minor_context": "Minor_Context",
    "cad_context": "CAD_Context",
}


def _categorize(zone_type: str) -> str:
    """Return the category key for a zone_type."""
    for cat, types in CATEGORY_MAP.items():
        if zone_type in types:
            return cat
    return "ZONES"  # fallback


def _layer_name(zone_type: str) -> str:
    """Build the full hierarchical layer name: '00_Boundaries::Plot_Boundary'."""
    cat = _categorize(zone_type)
    prefix = CATEGORY_PREFIX[cat]
    sublayer = ZONE_LAYER_NAMES.get(zone_type, zone_type.replace(" ", "_"))
    return f"{prefix}-{sublayer}"


def _attach_metadata(entity: Any, obj: Dict[str, Any]) -> None:
    """Attach all relevant metadata fields as XDATA on the DXF entity."""
    # Use a registered application name for XDATA
    app_name = "OMRT_SPATIAL"

    xdata_tags = [(1000, f"zone_type={obj.get('zone_type', '')}")]

    for key in [
        "id", "zone_label", "confidence", "classification_method",
        "source_layer", "area_pdf_units", "building_id", "building_label",
        "floor_index", "floor_label", "use_type", "volume_type",
        "y_bottom", "y_top", "constraint_name", "constraint_value",
        "constraint_unit", "constraint_category",
    ]:
        val = obj.get(key)
        if val is not None:
            xdata_tags.append((1000, f"{key}={val}"))

    try:
        entity.set_xdata(app_name, xdata_tags)
    except Exception:
        pass  # XDATA may fail on some entity types


def export_to_dxf(
    geometry: List[Dict[str, Any]],
    constraints: Optional[List[Dict[str, Any]]] = None,
    project_name: str = "SpatialBrief Export",
) -> bytes:
    """
    Export all geometry objects to a DXF file with layered hierarchy and metadata.

    Args:
        geometry: List of geometry objects (zones, buildings, volumes, constraints)
        constraints: Optional list of constraint objects for metadata
        project_name: Project name for the file header

    Returns:
        DXF file content as bytes
    """
    doc = ezdxf.new("R2013")  # AutoCAD 2013 format for broad compatibility
    msp = doc.modelspace()

    # Register XDATA application
    doc.appids.new("OMRT_SPATIAL")

    # Pre-create all parent layers (categories) with default colors
    for cat, prefix in CATEGORY_PREFIX.items():
        try:
            doc.layers.add(prefix, color=7)
        except ezdxf.DXFTableEntryError:
            pass

    # Track created sublayers
    created_layers: set = set()

    # Count objects per category for logging
    counts: Dict[str, int] = {}

    for obj in geometry:
        zone_type = obj.get("zone_type", "unknown")
        points = obj.get("points", [])
        if not points or len(points) < 2:
            continue

        # Determine layer
        layer = _layer_name(zone_type)

        # Create sublayer if needed
        if layer not in created_layers:
            aci_color = ZONE_ACI_COLORS.get(zone_type, 7)
            try:
                doc.layers.add(layer, color=aci_color)
            except ezdxf.DXFTableEntryError:
                pass
            created_layers.add(layer)

        cat = _categorize(zone_type)
        counts[cat] = counts.get(cat, 0) + 1

        # Check if this is a 3D volume (has y_bottom / y_top)
        y_bottom = obj.get("y_bottom")
        y_top = obj.get("y_top")
        is_volume = y_bottom is not None and y_top is not None

        if is_volume:
            _add_volume_geometry(msp, obj, layer, points, y_bottom, y_top)
        else:
            _add_2d_geometry(msp, obj, layer, points)

    # Add constraint annotation text if constraints provided
    if constraints:
        _add_constraint_annotations(doc, msp, constraints, created_layers)

    log.info(
        "[DXF Export] Created %d layers, %d total objects: %s",
        len(created_layers),
        sum(counts.values()),
        ", ".join(f"{k}={v}" for k, v in sorted(counts.items())),
    )

    # Write to string buffer then encode to bytes
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")


def _add_2d_geometry(
    msp: Any, obj: Dict[str, Any], layer: str, points: List[List[float]]
) -> None:
    """Add a 2D polyline to the modelspace."""
    # Convert [x, y, z] to (x, z) for plan view (Y is up in the viewer, Z in DXF)
    dxf_pts = []
    for p in points:
        if len(p) >= 3:
            dxf_pts.append((p[0], p[2], 0))  # x, z → x, y in DXF plan
        elif len(p) >= 2:
            dxf_pts.append((p[0], p[1], 0))

    if len(dxf_pts) < 2:
        return

    is_closed = obj.get("closed", False)

    # Close the polyline if needed
    if is_closed and dxf_pts[0] != dxf_pts[-1]:
        dxf_pts.append(dxf_pts[0])

    entity = msp.add_lwpolyline(
        dxf_pts,
        dxfattribs={"layer": layer},
        close=is_closed,
    )
    _attach_metadata(entity, obj)


def _add_volume_geometry(
    msp: Any,
    obj: Dict[str, Any],
    layer: str,
    points: List[List[float]],
    y_bottom: float,
    y_top: float,
) -> None:
    """Add a 3D extruded volume as a 3DFACE or MESH to the modelspace.

    Creates:
    - Bottom face polyline at y_bottom
    - Top face polyline at y_top
    - Vertical edge lines connecting them
    - A closed 3D polyline for the extrusion outline
    """
    # Convert footprint points to plan coordinates
    footprint = []
    for p in points:
        if len(p) >= 3:
            footprint.append((p[0], p[2]))  # x, z from viewer → x, y in DXF
        elif len(p) >= 2:
            footprint.append((p[0], p[1]))

    if len(footprint) < 3:
        return

    # Deduplicate
    unique_pts = []
    seen = set()
    for pt in footprint:
        key = (round(pt[0], 4), round(pt[1], 4))
        if key not in seen:
            seen.add(key)
            unique_pts.append(pt)
    footprint = unique_pts

    if len(footprint) < 3:
        return

    # Close the footprint
    if footprint[0] != footprint[-1]:
        footprint.append(footprint[0])

    # Bottom ring polyline (in DXF: z = y_bottom)
    bottom_pts = [(x, y, y_bottom) for x, y in footprint]
    bottom = msp.add_polyline3d(bottom_pts, dxfattribs={"layer": layer})
    bottom.close()
    _attach_metadata(bottom, {**obj, "_ring": "bottom"})

    # Top ring polyline
    top_pts = [(x, y, y_top) for x, y in footprint]
    top = msp.add_polyline3d(top_pts, dxfattribs={"layer": layer})
    top.close()
    _attach_metadata(top, {**obj, "_ring": "top"})

    # Vertical edges
    for x, y in footprint[:-1]:  # Skip last (duplicate of first)
        line = msp.add_line(
            (x, y, y_bottom),
            (x, y, y_top),
            dxfattribs={"layer": layer},
        )
        _attach_metadata(line, {**obj, "_edge": "vertical"})

    # Create 3DFACE elements for the walls (pairs of adjacent footprint vertices)
    for i in range(len(footprint) - 1):
        x1, y1 = footprint[i]
        x2, y2 = footprint[i + 1]
        face = msp.add_3dface(
            [
                (x1, y1, y_bottom),
                (x2, y2, y_bottom),
                (x2, y2, y_top),
                (x1, y1, y_top),
            ],
            dxfattribs={"layer": layer},
        )
        _attach_metadata(face, obj)


def _add_constraint_annotations(
    doc: Any,
    msp: Any,
    constraints: List[Dict[str, Any]],
    created_layers: set,
) -> None:
    """Add text annotations for constraints."""
    layer = "03_Constraints-Annotations"
    if layer not in created_layers:
        try:
            doc.layers.add(layer, color=2)  # yellow
        except ezdxf.DXFTableEntryError:
            pass
        created_layers.add(layer)

    y_offset = 0
    for c in constraints:
        name = c.get("name", "Constraint")
        value = c.get("value", "")
        unit = c.get("unit", "")
        category = c.get("category", "")
        text = f"{name}: {value} {unit} [{category}]"

        # Place constraint text annotations in a column to the side
        msp.add_text(
            text,
            height=0.5,
            dxfattribs={
                "layer": layer,
                "insert": (-20, y_offset, 0),
            },
        )
        y_offset -= 1.5


# ── Optional .3dm export (requires rhino3dm) ──

def _try_rhino3dm_export(
    geometry: List[Dict[str, Any]],
    constraints: Optional[List[Dict[str, Any]]] = None,
) -> Optional[bytes]:
    """
    Attempt to export as .3dm using rhino3dm.
    Returns bytes if successful, None if rhino3dm is not available.
    """
    try:
        import rhino3dm
    except ImportError:
        log.info("[3DM Export] rhino3dm not available — skipping .3dm export")
        return None

    try:
        model = rhino3dm.File3dm()

        # Create layer hierarchy
        layer_indices: Dict[str, int] = {}

        for obj in geometry:
            zone_type = obj.get("zone_type", "unknown")
            layer = _layer_name(zone_type)

            if layer not in layer_indices:
                rhino_layer = rhino3dm.Layer()
                rhino_layer.Name = layer
                aci = ZONE_ACI_COLORS.get(zone_type, 7)
                # Map ACI to approximate RGB
                rgb = _aci_to_rgb(aci)
                rhino_layer.Color = (rgb[0], rgb[1], rgb[2], 255)
                layer_indices[layer] = model.Layers.Add(rhino_layer)

            points = obj.get("points", [])
            if len(points) < 2:
                continue

            attrs = rhino3dm.ObjectAttributes()
            attrs.LayerIndex = layer_indices[layer]

            # Attach metadata as UserStrings
            for key in [
                "id", "zone_type", "zone_label", "confidence",
                "classification_method", "building_id", "floor_index",
                "use_type", "volume_type", "y_bottom", "y_top",
            ]:
                val = obj.get(key)
                if val is not None:
                    attrs.SetUserString(key, str(val))

            y_bottom = obj.get("y_bottom")
            y_top = obj.get("y_top")

            if y_bottom is not None and y_top is not None:
                # 3D extrusion
                _add_rhino_extrusion(model, obj, attrs, points, y_bottom, y_top)
            else:
                # 2D polyline
                _add_rhino_polyline(model, attrs, points, obj.get("closed", False))

        # Write to buffer
        buf = io.BytesIO()
        model.Write(buf)
        return buf.getvalue()

    except Exception as e:
        log.error("[3DM Export] Failed: %s", e)
        return None


def _add_rhino_polyline(model: Any, attrs: Any, points: List, closed: bool) -> None:
    """Add a polyline to the rhino3dm model."""
    import rhino3dm

    rhino_pts = []
    for p in points:
        if len(p) >= 3:
            rhino_pts.append(rhino3dm.Point3d(p[0], p[2], 0))  # viewer Y→Rhino Z
        elif len(p) >= 2:
            rhino_pts.append(rhino3dm.Point3d(p[0], p[1], 0))

    if len(rhino_pts) < 2:
        return

    if closed and rhino_pts[0] != rhino_pts[-1]:
        rhino_pts.append(rhino_pts[0])

    polyline = rhino3dm.Polyline(rhino_pts)
    model.Objects.AddPolyline(polyline, attrs)


def _add_rhino_extrusion(
    model: Any, obj: Dict, attrs: Any,
    points: List, y_bottom: float, y_top: float,
) -> None:
    """Add an extrusion to the rhino3dm model."""
    import rhino3dm

    # Build footprint curve in XY plane at Z=y_bottom
    rhino_pts = []
    for p in points:
        if len(p) >= 3:
            rhino_pts.append(rhino3dm.Point3d(p[0], p[2], y_bottom))
        elif len(p) >= 2:
            rhino_pts.append(rhino3dm.Point3d(p[0], p[1], y_bottom))

    if len(rhino_pts) < 3:
        return

    # Close curve
    if rhino_pts[0] != rhino_pts[-1]:
        rhino_pts.append(rhino_pts[0])

    polyline = rhino3dm.Polyline(rhino_pts)
    curve = rhino3dm.PolylineCurve(polyline)

    height = y_top - y_bottom
    if height <= 0:
        return

    try:
        extrusion = rhino3dm.Extrusion.Create(curve, height, True)
        if extrusion:
            model.Objects.AddExtrusion(extrusion, attrs)
    except Exception as e:
        log.debug("[3DM] Extrusion failed for %s: %s", obj.get("id", "?"), e)
        # Fallback: just add the base polyline
        model.Objects.AddPolyline(polyline, attrs)


def _aci_to_rgb(aci: int) -> Tuple[int, int, int]:
    """Map AutoCAD Color Index to approximate RGB."""
    aci_map = {
        1: (255, 0, 0),      # red
        2: (255, 255, 0),    # yellow
        3: (0, 255, 0),      # green
        4: (0, 255, 255),    # cyan
        5: (0, 0, 255),      # blue
        6: (255, 0, 255),    # magenta
        7: (255, 255, 255),  # white
        8: (128, 128, 128),  # dark grey
        9: (192, 192, 192),  # light grey
        30: (255, 127, 0),   # orange
    }
    return aci_map.get(aci, (200, 200, 200))
