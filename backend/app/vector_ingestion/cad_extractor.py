import ezdxf
import os
import math
import re
import uuid
from typing import List, Dict, Any

# --- Y-tier offsets removed — all geometry on flat ground plane ---

DEFAULT_COLORS = {
    "plot_boundary": "#3b82f6", "buildable_envelope": "#f97316",
    "infrastructure_zone": "#94a3b8", "landscape_zone": "#22c55e",
    "restriction_line": "#ef4444", "zone_boundary": "#f59e0b",
    "parcel_line": "#06b6d4", "sub_zone": "#8b5cf6",
    "major_boundary": "#e2e8f0", "context_line": "#64748b",
    "overlapping_zone": "#a855f7", "filled_zone": "#fb923c",
    "minor_context": "#475569", "cad_context": "#64748b",
    "no_build_zone": "#ef4444",
}


def _tessellate_arc(center, radius, start_angle, end_angle, scale=1.0, segments=16):
    points = []
    start_rad = math.radians(start_angle)
    end_rad = math.radians(end_angle)

    if end_rad < start_rad:
        end_rad += 2 * math.pi

    angle_step = (end_rad - start_rad) / segments
    for i in range(segments + 1):
        angle = start_rad + i * angle_step
        x = (center.x + radius * math.cos(angle)) * scale
        y = (center.y + radius * math.sin(angle)) * scale
        points.append([x, 0, y])
    return points


def _get_zone_type(layer_name: str) -> str:
    layer_lower = layer_name.lower()

    buildable_keywords = ["setback", "envelope", "max", "buildable", "max_height", "rooilijn"]
    if any(kw in layer_lower for kw in buildable_keywords):
        return "buildable_envelope"

    no_build_keywords = ["no-build", "exclusion", "clear", "vrij", "verboden"]
    if any(kw in layer_lower for kw in no_build_keywords):
        return "no_build_zone"

    plot_keywords = ["bound", "site", "limit", "kavel", "grens", "contour", "plot"]
    if any(kw in layer_lower for kw in plot_keywords):
        return "plot_boundary"

    return "cad_context"


def _is_essential_text(text: str) -> bool:
    keywords = ["height", "setback", "max", "min", "sqm", "m2", "limit", "grens", "kavel",
                "m²", "floor", "level", "area", "boundary", "plot", "zone", "regel"]
    text_lower = text.lower()
    return any(kw in text_lower for kw in keywords)


def _normalize_cad(vectors: list) -> list:
    """Post-process extracted CAD vectors to:
    1. Center geometry around origin
    2. Preserve real-world coordinates (meters)
    3. Orient correctly (CAD Y-up → Three.js XZ ground plane)
    4. Output consistent format matching pdf_vector_extractor
    """
    if not vectors:
        return []

    # Collect all raw XZ coordinates to compute bounding box
    all_x, all_z = [], []
    for v in vectors:
        for pt in v["points"]:
            all_x.append(pt[0])  # CAD x → world x
            all_z.append(pt[2])  # CAD y (mapped to z) → world z

    if not all_x:
        return []

    # Bounding box center
    min_x, max_x = min(all_x), max(all_x)
    min_z, max_z = min(all_z), max(all_z)
    cx = (min_x + max_x) / 2
    cz = (min_z + max_z) / 2

    # Preserve real-world coordinates (typically meters for architectural DWG/DXF).
    # Only center around origin — no rescaling.
    scale = 1.0

    out = []
    for v in vectors:
        zone_type = v.get("zone_type", "cad_context")

        # Transform points: center + scale, all at y=0
        pts3 = []
        for pt in v["points"]:
            nx = round((pt[0] - cx) * scale, 4)
            nz = round((pt[2] - cz) * scale, 4)
            pts3.append([nx, 0, nz])

        # Compute centroid
        if pts3:
            avg_x = sum(p[0] for p in pts3) / len(pts3)
            avg_z = sum(p[2] for p in pts3) / len(pts3)
            centroid = [round(avg_x, 4), 0, round(avg_z, 4)]
        else:
            centroid = [0, 0, 0]

        closed = v.get("closed", False)
        color = DEFAULT_COLORS.get(zone_type, "#94a3b8")
        layer_name = v.get("layer", "")

        # Compute area for closed polygons (coordinates in meters → area in m²)
        area_m2 = 0.0
        if closed and len(pts3) >= 3:
            try:
                from shapely.geometry import Polygon as SPoly
                poly_2d = [(p[0], p[2]) for p in pts3]
                area_m2 = round(SPoly(poly_2d).area, 1)
            except Exception:
                pass

        out.append({
            "id": f"cad_{uuid.uuid4().hex[:8]}",
            "type": "Polygon" if closed else "Polyline",
            "zone_type": zone_type,
            "points": pts3,
            "closed": closed,
            "area_pdf_units": 0,
            "area_m2": area_m2,
            "centroid": centroid,
            "color_hint": color,
            "stroke_width": 1.0,
            "confidence": 0.70,
            "classification_method": f"layer:{layer_name}",
            "filled": False,
            "source_layer": layer_name,
        })

    return out


def extract_from_dwg(cad_path: str) -> Dict[str, Any]:
    """
    Extracts essential vector lines, polylines, and text from a DWG or DXF file.
    Filters out noise (trees, hatches, irrelevant layers).
    """
    if not os.path.exists(cad_path):
        return {"error": f"File not found: {cad_path}"}
        
    extracted_vectors = []
    extracted_text = []
    
    try:
        doc = ezdxf.readfile(cad_path)
        msp = doc.modelspace()
        
        scale = 1.0 # Future: read units from drawing
        
        for entity in msp:
            layer = entity.dxf.layer
            
            if entity.dxftype() in ['TEXT', 'MTEXT']:
                text = entity.text if entity.dxftype() == 'MTEXT' else entity.dxf.text
                if _is_essential_text(text) or _get_zone_type(layer) != "cad_context":
                    insert = entity.dxf.insert
                    extracted_text.append({
                        "text": text,
                        "layer": layer,
                        "position": [insert.x * scale, 0, insert.y * scale]
                    })
                continue
                
            # GEOMETRY FILTERING
            zone_type = _get_zone_type(layer)
            if zone_type == "cad_context":
                continue
                
            if entity.dxftype() == 'LINE':
                p1 = entity.dxf.start
                p2 = entity.dxf.end
                extracted_vectors.append({
                    "type": "Line",
                    "points": [[p1.x * scale, 0, p1.y * scale], [p2.x * scale, 0, p2.y * scale]],
                    "layer": layer,
                    "zone_type": zone_type
                })
            elif entity.dxftype() in ['LWPOLYLINE', 'POLYLINE']:
                points = []
                for point in entity.get_points('xy'):
                    points.append([point[0] * scale, 0, point[1] * scale])
                
                if len(points) > 0:
                    closed = entity.closed
                    if closed and points[0] != points[-1]:
                        points.append(points[0])
                        
                    extracted_vectors.append({
                        "type": "Polyline",
                        "points": points,
                        "closed": closed,
                        "layer": layer,
                        "zone_type": zone_type
                    })
            elif entity.dxftype() == 'CIRCLE':
                points = _tessellate_arc(entity.dxf.center, entity.dxf.radius, 0, 360, scale, segments=32)
                extracted_vectors.append({
                    "type": "Polyline",
                    "points": points,
                    "closed": True,
                    "layer": layer,
                    "zone_type": zone_type
                })
            elif entity.dxftype() == 'ARC':
                points = _tessellate_arc(entity.dxf.center, entity.dxf.radius, entity.dxf.start_angle, entity.dxf.end_angle, scale, segments=16)
                extracted_vectors.append({
                    "type": "Polyline",
                    "points": points,
                    "closed": False,
                    "layer": layer,
                    "zone_type": zone_type
                })

        # Normalize: center, scale, orient, assign Y-tiers
        normalized = _normalize_cad(extracted_vectors)

        return {
            "extracted_objects": len(normalized),
            "vectors": normalized,
            "extracted_text": extracted_text
        }
    except Exception as e:
        return {"error": f"Failed to parse DWG/DXF: {str(e)}"}
