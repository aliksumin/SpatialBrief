"""
PDF Vector Geometry Extractor — 6-Stage Pipeline
Stage 1: Path Reconstruction
Stage 2: Polygon Assembly (Shapely)
Stage 3: Color/Width Clustering
Stage 4: Semantic Classification (color + containment + text labels)
Stage 5: Coordinate Normalization
Stage 6: Text-based Label Enrichment
"""
import fitz, os, math, uuid, re, logging
from typing import List, Dict, Any, Tuple, Optional
from shapely.geometry import Polygon, LineString, MultiPolygon, Point
from shapely.validation import make_valid

from app.vector_ingestion.hierarchy_builder import (
    extract_subzones_adaptive, sample_fill_colours, discover_hierarchy,
)
from app.vector_ingestion.ai_vision_classifier import classify_polygons_with_vision

log = logging.getLogger(__name__)

# --- Stage 1: Path Reconstruction ---

def _tess_cubic(p1, p2, p3, p4, n=10):
    pts = []
    for i in range(n + 1):
        t = i / n; u = 1 - t
        x = u**3*p1.x + 3*u**2*t*p2.x + 3*u*t**2*p3.x + t**3*p4.x
        y = u**3*p1.y + 3*u**2*t*p2.y + 3*u*t**2*p3.y + t**3*p4.y
        pts.append((x, y))
    return pts

def _tess_quad(p1, p2, p3, n=10):
    pts = []
    for i in range(n + 1):
        t = i / n; u = 1 - t
        x = u**2*p1.x + 2*u*t*p2.x + t**2*p3.x
        y = u**2*p1.y + 2*u*t*p2.y + t**2*p3.y
        pts.append((x, y))
    return pts

def _reconstruct_paths(page):
    raw = page.get_drawings()
    result = []
    for pd in raw:
        pts = []
        for item in pd["items"]:
            k = item[0]
            if k == "l":
                p1, p2 = item[1], item[2]
                if not pts or abs(pts[-1][0]-p1.x)>0.5 or abs(pts[-1][1]-p1.y)>0.5:
                    pts.append((p1.x, p1.y))
                pts.append((p2.x, p2.y))
            elif k == "c":
                p1,p2,p3,p4 = item[1],item[2],item[3],item[4]
                if not pts or abs(pts[-1][0]-p1.x)>0.5 or abs(pts[-1][1]-p1.y)>0.5:
                    pts.append((p1.x, p1.y))
                pts.extend(_tess_cubic(p1,p2,p3,p4)[1:])
            elif k == "qu":
                try:
                    p1,p2,p3 = item[1],item[2],item[3]
                    if not pts or abs(pts[-1][0]-p1.x)>0.5 or abs(pts[-1][1]-p1.y)>0.5:
                        pts.append((p1.x, p1.y))
                    pts.extend(_tess_quad(p1,p2,p3)[1:])
                except: pass
            elif k == "re":
                r = item[1]
                pts.extend([(r.x0,r.y0),(r.x1,r.y0),(r.x1,r.y1),(r.x0,r.y1),(r.x0,r.y0)])
        if len(pts) < 2: continue
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        bbox = (min(xs),min(ys),max(xs),max(ys))
        result.append({
            "points": pts, "fill": pd.get("fill"), "stroke": pd.get("color"),
            "width": pd.get("width",0), "bbox": bbox,
            "bbox_area": (bbox[2]-bbox[0])*(bbox[3]-bbox[1]),
        })
    return result

# --- Stage 2: Boundary-First Zone Extraction ---
# Instead of merging hatching triangles (bottom-up), we:
# 1. Reconstruct fragmented dashed boundaries into closed polylines
# 2. Use those closed polylines as the actual zone shapes
# 3. Sample fill colors from hatching elements contained within each boundary


def _is_gray_background(rgb: Tuple) -> bool:
    """Check if a color is the gray background fill (#646464 or similar)."""
    r, g, b = rgb[:3]
    return abs(r - g) < 0.08 and abs(g - b) < 0.08 and 0.2 < r < 0.6


def _join_dashed_segments(paths: List[Dict[str, Any]], max_gap: float = 8.0) -> List[Dict[str, Any]]:
    """Join fragmented dashed line segments into continuous polylines.

    In many PDFs, dashed boundaries are stored as hundreds of short,
    disconnected line segments.  This function greedily chains them by
    nearest-endpoint matching (within *max_gap* PDF units) and returns
    any newly-formed closed polylines as synthetic path dicts.
    """
    # Group open, unfilled paths by (stroke_color_hex, rounded_width)
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for p in paths:
        if p.get("fill") is not None:
            continue
        pts = p["points"]
        if len(pts) < 2:
            continue
        # Already closed — skip
        if len(pts) >= 4 and math.dist(pts[0], pts[-1]) < 5.0:
            continue
        seg_len = sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
        if seg_len < 0.5:
            continue
        stroke = p.get("stroke")
        if stroke is None:
            continue
        r, g, b = stroke[:3]
        w = round(p["width"], 1)
        key = f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}|{w}"
        groups.setdefault(key, []).append(p)

    new_paths: List[Dict[str, Any]] = []
    for key, segs in groups.items():
        if len(segs) < 5:
            continue  # Not enough fragments to form a boundary

        # Sort by segment length descending — start chains from longer pieces
        segs.sort(
            key=lambda s: sum(
                math.dist(s["points"][i], s["points"][i + 1])
                for i in range(len(s["points"]) - 1)
            ),
            reverse=True,
        )

        used: set = set()
        for start_idx, start_seg in enumerate(segs):
            if start_idx in used:
                continue
            chain = list(start_seg["points"])
            used.add(start_idx)
            extended = True

            while extended:
                extended = False
                best_dist = max_gap
                best_idx: Optional[int] = None
                best_mode: Optional[str] = None

                for idx, seg in enumerate(segs):
                    if idx in used:
                        continue
                    sp = seg["points"]
                    d1 = math.dist(chain[-1], sp[0])
                    d2 = math.dist(chain[-1], sp[-1])
                    d3 = math.dist(chain[0], sp[-1])
                    d4 = math.dist(chain[0], sp[0])
                    min_d = min(d1, d2, d3, d4)
                    if min_d < best_dist:
                        best_dist = min_d
                        best_idx = idx
                        if min_d == d1:
                            best_mode = "append"
                        elif min_d == d2:
                            best_mode = "append_rev"
                        elif min_d == d3:
                            best_mode = "prepend"
                        else:
                            best_mode = "prepend_rev"

                if best_idx is not None:
                    seg = segs[best_idx]
                    used.add(best_idx)
                    extended = True
                    sp = seg["points"]
                    if best_mode == "append":
                        chain.extend(sp[1:])
                    elif best_mode == "append_rev":
                        chain.extend(list(reversed(sp))[1:])
                    elif best_mode == "prepend":
                        chain = list(sp) + chain[1:]
                    else:
                        chain = list(reversed(sp)) + chain[1:]

            # Check if the chain closes
            if len(chain) >= 4 and math.dist(chain[0], chain[-1]) < max_gap:
                xs = [pt[0] for pt in chain]
                ys = [pt[1] for pt in chain]
                bbox = (min(xs), min(ys), max(xs), max(ys))
                color_part = key.split("|")[0]
                r = int(color_part[1:3], 16) / 255
                g = int(color_part[3:5], 16) / 255
                b = int(color_part[5:7], 16) / 255
                new_paths.append({
                    "points": chain,
                    "fill": None,
                    "stroke": (r, g, b),
                    "width": float(key.split("|")[1]),
                    "bbox": bbox,
                    "bbox_area": (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]),
                    "_reconstructed": True,
                })

    return new_paths


def _collect_boundary_zones(
    paths: List[Dict[str, Any]],
    page_area: float,
    min_area_frac: float = 0.00003,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Collect all closed polyline boundaries and separate hatching fills.

    Returns (boundary_paths, hatching_fills) where boundary_paths are
    the unfilled closed polylines that define zone shapes, and
    hatching_fills are the small filled triangles used only for color
    sampling.
    """
    min_area = page_area * min_area_frac

    boundaries: List[Dict[str, Any]] = []
    hatching: List[Dict[str, Any]] = []
    other: List[Dict[str, Any]] = []

    for p in paths:
        pts = p["points"]
        is_filled = p.get("fill") is not None

        if is_filled:
            # Classify as hatching element (small filled triangles)
            hatching.append(p)
            continue

        # Unfilled path — check if it's a closed boundary
        if len(pts) < 4:
            other.append(p)
            continue

        closed = math.dist(pts[0], pts[-1]) < 5.0
        if not closed:
            other.append(p)
            continue

        ring = list(pts)
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        try:
            poly = Polygon(ring)
            if not poly.is_valid:
                poly = make_valid(poly)
            if isinstance(poly, MultiPolygon):
                poly = max(poly.geoms, key=lambda g: g.area)
            if not isinstance(poly, Polygon):
                other.append(p)
                continue
            if poly.area < min_area:
                other.append(p)
                continue
            boundaries.append({**p, "points": ring, "_shapely": poly, "_area": poly.area})
        except Exception:
            other.append(p)

    return boundaries, hatching, other


def _assign_fill_colors(
    boundaries: List[Dict[str, Any]],
    hatching: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Sample fill colors from hatching elements inside each boundary.

    For each boundary polygon, count the hatching triangles whose
    centroid falls inside.  The dominant *non-gray* color determines
    the boundary's fill, giving it a semantic zone type.
    """
    # Pre-compute hatching centroids
    hatch_info = []
    for h in hatching:
        pts = h["points"]
        if len(pts) < 2 or h.get("fill") is None:
            continue
        cx = sum(pt[0] for pt in pts) / len(pts)
        cy = sum(pt[1] for pt in pts) / len(pts)
        rgb = h["fill"][:3]
        # Skip gray background fills
        if _is_gray_background(rgb):
            continue
        hatch_info.append({"point": Point(cx, cy), "fill": h["fill"]})

    for b in boundaries:
        poly = b.get("_shapely")
        if poly is None:
            continue

        color_counts: Dict[str, Tuple[int, Tuple]] = {}
        for hi in hatch_info:
            try:
                if poly.contains(hi["point"]):
                    r, g, bb = hi["fill"][:3]
                    key = f"#{int(r*255):02x}{int(g*255):02x}{int(bb*255):02x}"
                    # Skip near-black fills
                    if r < 0.15 and g < 0.15 and bb < 0.15:
                        continue
                    # Skip gray-ish fills (R≈G≈B, above 0.4)
                    if abs(r - g) < 0.1 and abs(g - bb) < 0.1 and r > 0.4:
                        continue
                    if key not in color_counts:
                        color_counts[key] = (0, hi["fill"])
                    color_counts[key] = (color_counts[key][0] + 1, hi["fill"])
            except Exception:
                continue

        # Pick the dominant color
        if color_counts:
            best_key = max(color_counts, key=lambda k: color_counts[k][0])
            best_count, best_fill = color_counts[best_key]
            if best_count >= 2:
                b["fill"] = best_fill

    return boundaries


def _deduplicate_boundaries(boundaries: List[Dict[str, Any]], page_area: float) -> List[Dict[str, Any]]:
    """Remove duplicate boundaries that overlap significantly (same shape drawn with different stroke colors)."""
    if not boundaries:
        return boundaries

    # Sort by area descending
    boundaries.sort(key=lambda b: b.get("_area", 0), reverse=True)

    keep = []
    for b in boundaries:
        is_dup = False
        for k in keep:
            # Check area similarity (within 2%)
            if k.get("_area", 0) > 0:
                ratio = min(b["_area"], k["_area"]) / max(b["_area"], k["_area"])
                if ratio > 0.98:
                    # Check bbox overlap
                    b1 = b["bbox"]
                    b2 = k["bbox"]
                    if all(abs(b1[i] - b2[i]) < 10 for i in range(4)):
                        # Duplicate — keep the one with fill, or the one with larger width
                        if b.get("fill") and not k.get("fill"):
                            # This one has fill, replace
                            k["fill"] = b["fill"]
                        is_dup = True
                        break
        if not is_dup:
            keep.append(b)

    return keep

# --- Stage 3: Polygon Assembly ---

def _assemble_polygons(paths, page_area, min_frac=0.00003):
    min_area = page_area * min_frac
    polys = []
    for p in paths:
        pts = p["points"]
        if len(pts) < 3: continue
        # Check for closed or near-closed polycurves.
        # Buildings are often drawn as polycurves with a small gap at the
        # closure point — treat anything with gap < 5.0 PDF units as closed.
        gap = math.dist(pts[0], pts[-1])
        closed = gap < 5.0

        # Second chance: promote near-closed polylines (gap < 15 PDF units)
        # to closed if they have building-like shape (compact, rectangular).
        if not closed and gap < 15.0 and len(pts) >= 4:
            # Temporarily close and check shape metrics
            trial_ring = list(pts) + [pts[0]]
            try:
                trial_poly = Polygon(trial_ring)
                if not trial_poly.is_valid:
                    trial_poly = make_valid(trial_poly)
                if isinstance(trial_poly, MultiPolygon):
                    trial_poly = max(trial_poly.geoms, key=lambda g: g.area)
                if isinstance(trial_poly, Polygon) and trial_poly.area >= min_area:
                    metrics = _compute_shape_metrics(trial_poly)
                    if _is_building_shape(metrics):
                        closed = True
                        log.debug("Promoted near-closed polycurve (gap=%.1f) to closed polygon (building shape)", gap)
            except Exception:
                pass

        if closed and len(pts) >= 4:
            ring = list(pts)
            if ring[0] != ring[-1]: ring.append(ring[0])
            try:
                poly = Polygon(ring)
                if not poly.is_valid: poly = make_valid(poly)
                if isinstance(poly, MultiPolygon): poly = max(poly.geoms, key=lambda g: g.area)
                if not isinstance(poly, Polygon): continue
                if poly.area < min_area: continue
                c = poly.centroid
                polys.append({
                    "id": f"zone_{uuid.uuid4().hex[:8]}", "shapely_poly": poly,
                    "points": ring, "area": poly.area, "centroid": (c.x, c.y),
                    "closed": True, **{k: p[k] for k in ("fill","stroke","width","bbox","bbox_area")},
                })
            except: continue
        else:
            try:
                ls = LineString(pts)
                if ls.length < math.sqrt(min_area): continue
                c = ls.centroid
                polys.append({
                    "id": f"line_{uuid.uuid4().hex[:8]}", "shapely_poly": None,
                    "points": pts, "area": 0, "centroid": (c.x, c.y),
                    "closed": False, **{k: p[k] for k in ("fill","stroke","width","bbox","bbox_area")},
                })
            except: continue
    return polys


def _identify_label_markers(polys, page_area):
    """Identify small circular shapes that are label markers, not zone geometry.
    Returns (markers, non_markers).
    Circles have isoperimetric quotient Q = 4*pi*area/perimeter^2 close to 1."""
    max_label_area = page_area * 0.003  # Labels are typically < 0.3% of page
    markers = []
    non_markers = []
    for p in polys:
        is_marker = False
        if p["closed"] and p["area"] < max_label_area:
            pts = p["points"]
            perimeter = sum(math.dist(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts)))
            if perimeter > 0:
                q = 4 * math.pi * p["area"] / (perimeter ** 2)
                if q > 0.65:  # Lowered threshold to catch Bézier-approximated circles
                    is_marker = True
            # Also catch small near-square shapes (bbox aspect ratio close to 1)
            if not is_marker:
                bw = p["bbox"][2] - p["bbox"][0]
                bh = p["bbox"][3] - p["bbox"][1]
                if bw > 0 and bh > 0:
                    aspect = min(bw, bh) / max(bw, bh)
                    if aspect > 0.85 and max(bw, bh) < 30:
                        is_marker = True
        if is_marker:
            markers.append(p)
        else:
            non_markers.append(p)
    return markers, non_markers


def _extract_marker_labels(markers, polys, page):
    """Extract text from inside label marker circles and attach to nearest zone polygon.
    The circles typically contain height values (e.g. '60', '45') or annotation codes."""
    if not markers or not page:
        return polys

    text_blocks = page.get_text("blocks")
    if not text_blocks:
        return polys

    # For each marker, find text inside it
    marker_data = []  # (centroid_x, centroid_y, text)
    for m in markers:
        cx = (m["bbox"][0] + m["bbox"][2]) / 2
        cy = (m["bbox"][1] + m["bbox"][3]) / 2
        radius = max(m["bbox"][2] - m["bbox"][0], m["bbox"][3] - m["bbox"][1]) / 2
        search_r = radius * 1.5  # Slightly larger search area

        for tb in text_blocks:
            tx = (tb[0] + tb[2]) / 2
            ty = (tb[1] + tb[3]) / 2
            if math.dist((cx, cy), (tx, ty)) < search_r:
                text = tb[4].strip()
                if text:
                    marker_data.append((cx, cy, text))
                    break

    if not marker_data:
        return polys

    # Attach each marker's text to the smallest containing polygon
    closed_polys = sorted(
        [p for p in polys if p["closed"] and p.get("shapely_poly")],
        key=lambda p: p["area"]
    )

    for mx, my, text in marker_data:
        pt = Point(mx, my)
        for p in closed_polys:
            try:
                if p["shapely_poly"].contains(pt):
                    existing = p.get("marker_labels", [])
                    existing.append(text)
                    p["marker_labels"] = existing
                    break
            except:
                continue

    return polys


def _filter_page_borders(polys, page_area, pw, ph):
    """Remove polygons that are likely page/drawing borders rather than
    meaningful geometry.  A page border typically covers >80% of the page
    and is a simple rectangle (4-6 vertices after tessellation).
    Also removes tall/narrow or wide/flat drawing-frame rectangles."""
    result = []
    for p in polys:
        bw = p["bbox"][2] - p["bbox"][0]
        bh = p["bbox"][3] - p["bbox"][1]
        if p["closed"] and p["area"] > page_area * 0.70:
            # Check if bbox covers most of the page
            bbox_coverage = (bw * bh) / page_area
            if bbox_coverage > 0.80:
                # Skip this — it's a page/drawing border
                continue
            # Also catch near-full-width or near-full-height rectangles
            if (bw > pw * 0.90 or bh > ph * 0.90) and len(p["points"]) <= 8:
                continue

        # Filter tall/narrow or wide/flat rectangles that span most of one
        # page dimension — these are drawing frame elements (title blocks,
        # margin lines) rather than meaningful zone boundaries.
        if p["closed"] and len(p["points"]) <= 8 and bw > 0 and bh > 0:
            aspect = max(bw, bh) / min(bw, bh)
            spans_page = bw > pw * 0.70 or bh > ph * 0.70
            if aspect > 5.0 and spans_page:
                continue

        result.append(p)
    return result


def _simplify_polygons(polys):
    """Simplify polygon outlines to reduce vertex count and triangulation artifacts.
    Uses Shapely's simplify with a small tolerance to remove near-collinear points."""
    for p in polys:
        if not p["closed"] or not p.get("shapely_poly"):
            continue
        sp = p["shapely_poly"]
        # Use tolerance proportional to the polygon's size
        # Higher tolerance = fewer points = cleaner frontend fills
        tol = math.sqrt(sp.area) * 0.05
        simplified = sp.simplify(tol, preserve_topology=True)
        if isinstance(simplified, Polygon) and simplified.is_valid and len(simplified.exterior.coords) >= 4:
            new_coords = list(simplified.exterior.coords)
            p["points"] = new_coords
            p["shapely_poly"] = simplified
    return polys

# --- Stage 3: Color Clustering ---

def _rgb_hex(rgb):
    if rgb is None: return None
    return f"#{int(rgb[0]*255):02x}{int(rgb[1]*255):02x}{int(rgb[2]*255):02x}"

# --- Shape Intelligence ---

def _compute_shape_metrics(poly):
    """Compute geometric shape metrics for a polygon.
    Returns dict with: aspect_ratio, compactness, circularity, elongation, min_dim
    """
    try:
        area = poly.area
        perimeter = poly.length
        hull = poly.convex_hull
        hull_area = hull.area

        # Oriented bounding box dimensions
        minr = poly.minimum_rotated_rectangle
        coords = list(minr.exterior.coords)
        w = math.dist(coords[0], coords[1])
        h = math.dist(coords[1], coords[2])
        long_side = max(w, h)
        short_side = min(w, h)

        aspect_ratio = long_side / max(short_side, 0.001)
        compactness = area / max(hull_area, 0.001)  # 1.0 = perfectly convex
        circularity = (4 * math.pi * area) / max(perimeter * perimeter, 0.001)
        elongation = 1.0 - short_side / max(long_side, 0.001)
        min_dim = short_side

        return {
            "aspect_ratio": aspect_ratio,
            "compactness": compactness,
            "circularity": circularity,
            "elongation": elongation,
            "min_dim": min_dim,
        }
    except Exception:
        return {
            "aspect_ratio": 1.0,
            "compactness": 1.0,
            "circularity": 0.5,
            "elongation": 0.0,
            "min_dim": 10.0,
        }


def _is_building_shape(metrics: dict) -> bool:
    """Does this polygon look like a building footprint?
    Buildings are roughly rectangular, compact, not extremely elongated.
    Relaxed thresholds to catch small auxiliary structures (sheds, carports)
    and irregular L/T/U-shaped footprints.
    """
    return (
        metrics["aspect_ratio"] < 6.0 and
        metrics["compactness"] > 0.35 and
        metrics["elongation"] < 0.85 and
        metrics["circularity"] > 0.08 and
        metrics["min_dim"] > 2.0
    )


def _is_artifact_shape(metrics: dict) -> bool:
    """Is this polygon clearly an artifact (not a real zone or building)?
    Artifacts are extremely elongated, thin, or spiky.
    """
    return (
        metrics["aspect_ratio"] > 10.0 or
        metrics["elongation"] > 0.92 or
        metrics["min_dim"] < 1.5 or
        metrics["compactness"] < 0.15
    )


def _cdist(c1, c2):
    return math.sqrt(sum((a-b)**2 for a,b in zip(c1[:3],c2[:3])))

COLOR_FAMILIES = {
    "orange_fill": ((1.0,0.75,0.53), "buildable_envelope"),
    "gray_fill": ((0.80,0.80,0.80), "infrastructure_zone"),
    "green_fill": ((0.16,0.78,0.27), "landscape_zone"),
    "red_stroke": ((0.87,0.0,0.0), "restriction_line"),
    "orange_stroke": ((1.0,0.61,0.0), "zone_boundary"),
    "cyan_stroke": ((0.0,1.0,1.0), "parcel_line"),
    "dark_gray": ((0.39,0.39,0.39), "context_line"),
}

def _classify_color(color):
    if color is None: return ("none","unknown")
    best_d, best_f, best_z = float("inf"), "unknown", "unknown"
    for fn,(rc,zt) in COLOR_FAMILIES.items():
        d = _cdist(color, rc)
        if d < best_d: best_d, best_f, best_z = d, fn, zt
    return (best_f, best_z) if best_d <= 0.35 else ("unknown","unknown")

# --- Stage 4: Semantic Classification (shape-aware) ---

def _classify_zones(polygons, page_area):
    """Classify polygons using colour, shape metrics, and hierarchy rules.

    Classification hierarchy:
      1. Plot boundary — largest unfilled polygon (>5% page)
      2. Zones — any polygon with a coloured fill (hatching)
      3. Buildings (sub_zone) — unfilled, building-shaped, inside a zone
      4. Boundaries — unfilled, non-building shape (thick/medium stroke)
      5. Artifacts — extreme shape metrics → discarded
    """
    if not polygons:
        return []

    closed = sorted(
        [p for p in polygons if p["closed"]],
        key=lambda p: p["area"], reverse=True,
    )
    opens = [p for p in polygons if not p["closed"]]

    # ── Pass 1: Identify plot boundary ──
    plot = None
    for p in closed:
        if p["fill"] is None and p["area"] > page_area * 0.05 and p["width"] >= 0.5:
            if plot is None or p["area"] > plot["area"]:
                plot = p
    if plot is None and closed:
        plot = closed[0]

    plot_poly = plot["shapely_poly"] if plot and plot.get("shapely_poly") else None

    # ── Pass 2: Classify each closed polygon ──
    out = []
    for p in closed:
        poly = p.get("shapely_poly")
        metrics = _compute_shape_metrics(poly) if poly else None

        # --- Plot boundary ---
        if plot and p["id"] == plot["id"]:
            zt, conf, meth = "plot_boundary", 0.95, "area_rank"

        # --- Has fill → always a ZONE (never a building) ---
        elif p["fill"] is not None:
            ff, fz = _classify_color(p["fill"])
            if fz != "unknown":
                zt, conf, meth = fz, 0.85, f"fill:{ff}"
            else:
                zt, conf, meth = "filled_zone", 0.6, "fill_unknown"

        # --- No fill: use shape + containment + stroke to decide ---
        else:
            # Check if inside the plot
            inside_plot = False
            if plot_poly and poly:
                try:
                    inside_plot = (
                        plot_poly.contains(poly) or
                        plot_poly.intersection(poly).area > p["area"] * 0.80
                    )
                except Exception:
                    pass

            # Colour-based classification
            sf, sz = _classify_color(p["stroke"])

            if metrics and _is_artifact_shape(metrics):
                # Artifact — extreme shape, discard
                zt, conf, meth = "minor_context", 0.20, "artifact_shape"

            elif sz != "unknown":
                # Known stroke colour (cyan = parcel, orange = zone boundary, etc.)
                zt, conf, meth = sz, 0.80, f"stroke:{sf}"

            elif inside_plot and metrics and _is_building_shape(metrics):
                # Building-shaped, inside plot → building (sub_zone)
                # BUT protect very large polygons — they are zones, not buildings
                plot_area = plot_poly.area if plot_poly else page_area
                area_ratio_of_plot = p["area"] / plot_area if plot_area > 0 else 0
                # Extraction strategy override: chain_join/planar_face are
                # almost certainly buildings regardless of size
                strategy = p.get("_extraction_strategy", "direct")
                if strategy in ("chain_join", "planar_face"):
                    zt, conf, meth = "sub_zone", 0.80, f"building_shape+{strategy}"
                elif area_ratio_of_plot > 0.10:
                    # Too large to be a building — classify as zone
                    zt, conf, meth = "uncategorized_zone", 0.70, "large_unfilled_inside_plot"
                else:
                    zt, conf, meth = "sub_zone", 0.75, "building_shape"

            elif p["width"] >= 1.0:
                zt, conf, meth = "major_boundary", 0.75, "thick_stroke"

            elif p["width"] >= 0.5 and inside_plot:
                # Medium stroke inside plot — could be building or boundary
                if metrics and _is_building_shape(metrics):
                    plot_area = plot_poly.area if plot_poly else page_area
                    area_ratio_of_plot = p["area"] / plot_area if plot_area > 0 else 0
                    strategy = p.get("_extraction_strategy", "direct")
                    if strategy in ("chain_join", "planar_face"):
                        zt, conf, meth = "sub_zone", 0.80, f"medium_stroke+{strategy}"
                    elif area_ratio_of_plot > 0.10:
                        zt, conf, meth = "uncategorized_zone", 0.65, "large_unfilled_medium_stroke"
                    else:
                        zt, conf, meth = "sub_zone", 0.70, "medium_stroke+building_shape"
                else:
                    zt, conf, meth = "major_boundary", 0.60, "medium_stroke"

            elif inside_plot:
                # Inside plot, thin stroke — check if it covers significant area
                plot_area = plot_poly.area if plot_poly else page_area
                area_ratio_of_plot = p["area"] / plot_area if plot_area > 0 else 0
                if area_ratio_of_plot > 0.01:
                    # Significant area inside plot — must be a zone, not noise
                    zt, conf, meth = "uncategorized_zone", 0.55, "thin_stroke_significant_area"
                else:
                    zt, conf, meth = "minor_context", 0.40, "thin_stroke"

            else:
                zt, conf, meth = "minor_context", 0.40, "thin_stroke"

            # Add containment tag
            if inside_plot and zt not in ("minor_context",):
                meth = f"{meth}+containment"
                conf = max(conf, 0.75)

        out.append({
            **p,
            "zone_type": zt,
            "confidence": conf,
            "classification_method": meth,
            "color_hex": _rgb_hex(p["fill"] or p["stroke"]),
            "_shape_metrics": metrics,
        })

    # ── Open lines ──
    for ln in opens:
        sf, sz = _classify_color(ln["stroke"])
        zt = sz if sz != "unknown" else "context_line"
        out.append({
            **ln,
            "zone_type": zt,
            "confidence": 0.50,
            "classification_method": f"open:{sf}",
            "color_hex": _rgb_hex(ln["stroke"]),
        })

    return out


def _resolve_zone_building_overlap(zones):
    """Resolve zone/building ambiguity for filled subzones.

    When ALL boundaries (including subzones from adaptive extraction) get
    hatching fill, some building outlines inherit the parent zone's fill
    colour and are misclassified as zones.

    This function identifies such cases by checking:
    - Is this polygon CONTAINED inside a larger zone?
    - Does it have the SAME fill colour as that larger zone?

    If both are true → the fill is inherited → reclassify as building (sub_zone).
    If the fill is different or the polygon isn't contained → keep as zone.
    """
    if not zones:
        return zones

    # Collect all filled closed zones sorted by area (largest first)
    filled_zones = [
        z for z in zones
        if z["closed"] and z.get("fill") is not None and z.get("shapely_poly")
        and z["zone_type"] not in ("plot_boundary", "sub_zone", "minor_context")
    ]
    filled_zones.sort(key=lambda z: z["area"], reverse=True)

    def _fill_similar(f1, f2, threshold=0.12):
        """Check if two RGB fill tuples are similar."""
        if f1 is None or f2 is None:
            return False
        return all(abs(f1[i] - f2[i]) < threshold for i in range(3))

    # Compute plot area for size guard
    plot_zone = next((z for z in zones if z["zone_type"] == "plot_boundary" and z.get("shapely_poly")), None)
    plot_area = plot_zone["area"] if plot_zone else max((z["area"] for z in zones if z["closed"]), default=1)

    reclassified = 0
    for z in zones:
        if not z["closed"] or z.get("fill") is None or not z.get("shapely_poly"):
            continue
        if z["zone_type"] in ("plot_boundary", "minor_context"):
            continue

        # Size guard: never demote large polygons (>10% of plot) to sub_zone
        if z["area"] > plot_area * 0.10:
            continue

        z_poly = z["shapely_poly"]
        z_fill = z["fill"]

        # Check if contained inside a larger zone with the same fill
        for parent in filled_zones:
            if parent["id"] == z["id"]:
                continue
            if parent["area"] <= z["area"]:
                continue  # Only check LARGER zones

            parent_poly = parent["shapely_poly"]
            try:
                inter = parent_poly.intersection(z_poly).area
                containment = inter / z["area"] if z["area"] > 0 else 0

                if containment > 0.80:
                    # This polygon is inside the parent
                    if _fill_similar(z_fill, parent["fill"]):
                        # Same fill colour → inherited hatching → building
                        z["zone_type"] = "sub_zone"
                        z["fill"] = None  # Strip inherited fill
                        z["confidence"] = max(z.get("confidence", 0), 0.75)
                        z["classification_method"] = (
                            z.get("classification_method", "") +
                            "+inherited_fill→building"
                        )
                        reclassified += 1
                    break  # Found the parent, stop searching
            except Exception:
                continue

    if reclassified:
        log.info("Resolved %d zone/building overlaps (inherited fill → building)",
                 reclassified)

    return zones


def _promote_nested_subzones(zones):
    """Reclassify nested polygons based on the hierarchy rule:
    - A filled zone inside a larger zone of the same type → stays a ZONE
      (it's a sub-area with different regulation, not a building)
    - An unfilled polygon inside a zone → stays as classified (building/boundary)

    This function NEVER demotes a filled polygon to sub_zone.
    """
    # Group closed zones by zone_type
    type_groups: Dict[str, List[Dict[str, Any]]] = {}
    for z in zones:
        if not z["closed"] or not z.get("shapely_poly"):
            continue
        zt = z["zone_type"]
        type_groups.setdefault(zt, []).append(z)

    # For each zone type with multiple members, apply hierarchy rules
    for zt, members in type_groups.items():
        if zt in ("plot_boundary", "sub_zone", "minor_context", "context_line"):
            continue
        if len(members) <= 1:
            continue

        # Sort by area descending — largest is the "parent"
        members.sort(key=lambda z: z["area"], reverse=True)
        parent = members[0]
        parent_poly = parent.get("shapely_poly")
        if parent_poly is None:
            continue

        for child in members[1:]:
            child_poly = child.get("shapely_poly")
            if child_poly is None:
                continue
            try:
                intersection = parent_poly.intersection(child_poly)
                if intersection.area <= child["area"] * 0.80:
                    continue  # Not really nested

                # Child is nested inside parent of same type.
                # If child has NO fill → it might be a building outline
                if child.get("fill") is None:
                    metrics = child.get("_shape_metrics")
                    if metrics and _is_building_shape(metrics):
                        child["zone_type"] = "sub_zone"
                        child["classification_method"] += "+nested_building"
                        child["confidence"] = max(child["confidence"], 0.80)

                # If child HAS fill → keep as same zone type (sub-area)
                # Add a tag so it's still distinguishable
                else:
                    child["classification_method"] += "+nested_subzone"

            except Exception:
                continue

    return zones


def _classify_gap_by_neighbors(gap_poly, zones, page_area):
    """Classify a gap polygon by looking at what zones surround it.

    Returns (zone_type, confidence, color_hex).
    """
    # Find zones that share a border with this gap
    neighbor_types = []
    neighbor_fills = []
    for z in zones:
        if not z["closed"] or not z.get("shapely_poly"):
            continue
        if z["zone_type"] in ("plot_boundary", "minor_context", "sub_zone"):
            continue
        try:
            if z["shapely_poly"].intersects(gap_poly):
                shared_boundary = z["shapely_poly"].intersection(gap_poly.boundary)
                # Only count as neighbor if they share a meaningful edge (not just a point)
                if hasattr(shared_boundary, 'length') and shared_boundary.length > 1.0:
                    neighbor_types.append(z["zone_type"])
                    if z.get("fill"):
                        neighbor_fills.append(z["fill"])
                    elif z.get("color_hex"):
                        neighbor_fills.append(z["color_hex"])
        except Exception:
            continue

    if not neighbor_types:
        # No neighbors found — use default
        return ("buildable_envelope", 0.45, "#f97316")

    # Most common neighbor type
    from collections import Counter
    type_counts = Counter(neighbor_types)
    dominant_type = type_counts.most_common(1)[0][0]

    # Assign color from the dominant neighbor type
    color_map = {
        "buildable_envelope": "#f97316",
        "landscape_zone": "#22c55e",
        "infrastructure_zone": "#94a3b8",
        "traffic_zone": "#64748b",
        "filled_zone": "#fb923c",
        "uncategorized_zone": "#f97316",
    }
    color = color_map.get(dominant_type, "#f97316")

    return (dominant_type, 0.55, color)


def _fill_plot_gaps(zones, page_area):
    """Fill empty areas inside the plot boundary with zone polygons.

    Rule: The entire area within the plot boundary must be divided by zones
    without empty spots.

    Algorithm:
    1. Find the plot boundary polygon
    2. Collect all zone polygons (excluding sub_zone, minor_context, open lines)
    3. Compute: gap_area = plot - union(all zones)
    4. For each gap polygon of meaningful size:
       a. Try to match it to a filtered/discarded polygon
       b. If no match: create a synthetic zone
       c. Classify using neighboring zone context
    """
    # Find plot boundary
    plot = None
    for z in zones:
        if z["zone_type"] == "plot_boundary" and z.get("shapely_poly"):
            plot = z
            break
    if plot is None:
        return zones

    plot_poly = plot["shapely_poly"]
    plot_area = plot_poly.area

    # Collect all zone polygons that should tile the plot
    # (exclude sub_zone/building footprints, minor_context, open lines)
    zone_polys = []
    for z in zones:
        if not z["closed"] or not z.get("shapely_poly"):
            continue
        if z["zone_type"] in ("plot_boundary", "minor_context", "sub_zone", "context_line"):
            continue
        zone_polys.append(z["shapely_poly"])

    if not zone_polys:
        log.info("[Gap fill] No zone polygons found — skipping")
        return zones

    # Compute uncovered area
    try:
        from shapely.ops import unary_union
        zone_union = unary_union(zone_polys)
        gap_geometry = plot_poly.difference(zone_union)
    except Exception as e:
        log.warning("[Gap fill] Failed to compute gaps: %s", e)
        return zones

    if gap_geometry.is_empty:
        log.info("[Gap fill] No gaps found — plot is fully covered")
        return zones

    # Extract individual gap polygons
    gap_polys = []
    if gap_geometry.geom_type == 'Polygon':
        gap_polys = [gap_geometry]
    elif gap_geometry.geom_type == 'MultiPolygon':
        gap_polys = list(gap_geometry.geoms)
    elif gap_geometry.geom_type == 'GeometryCollection':
        gap_polys = [g for g in gap_geometry.geoms if g.geom_type == 'Polygon']

    min_gap_area = plot_area * 0.0001  # Ignore tiny slivers (< 0.01% of plot)
    meaningful_gaps = [g for g in gap_polys if g.area > min_gap_area]

    if not meaningful_gaps:
        log.info("[Gap fill] Only tiny slivers found (%d) — skipping", len(gap_polys))
        return zones

    log.info("[Gap fill] Found %d meaningful gaps (out of %d total)",
             len(meaningful_gaps), len(gap_polys))

    # Create zone polygons for each gap
    new_zones = []
    for gap in meaningful_gaps:
        gap_type, gap_conf, gap_color = _classify_gap_by_neighbors(gap, zones, page_area)

        coords = list(gap.exterior.coords)
        c = gap.centroid
        xs = [p[0] for p in coords]
        ys = [p[1] for p in coords]
        bbox = (min(xs), min(ys), max(xs), max(ys))

        new_zone = {
            "id": f"zone_{uuid.uuid4().hex[:8]}",
            "shapely_poly": gap,
            "points": coords,
            "area": gap.area,
            "centroid": (c.x, c.y),
            "closed": True,
            "fill": None,
            "stroke": None,
            "width": 0.3,
            "bbox": bbox,
            "bbox_area": (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]),
            "zone_type": gap_type,
            "confidence": gap_conf,
            "classification_method": f"gap_fill+neighbor_context({gap_type})",
            "color_hex": gap_color,
            "_shape_metrics": None,
            "zone_label": "",
            "marker_labels": [],
        }
        new_zones.append(new_zone)
        log.info("  Gap zone: %.0f units² → %s (%.0f%% conf)",
                 gap.area, gap_type, gap_conf * 100)

    zones.extend(new_zones)
    log.info("[Gap fill] Added %d gap-fill zones", len(new_zones))
    return zones


# --- Deduplication ---

def _dedup(zones):
    """Deduplicate zones. Include fill/stroke color in key to avoid
    merging semantically different polygons that share the same bbox."""
    if not zones: return []
    groups = {}
    for z in zones:
        # Include fill and stroke in key so differently-colored same-shape polys survive
        fh = _rgb_hex(z.get("fill")) or "nf"
        sh = _rgb_hex(z.get("stroke")) or "ns"
        k = f"{z['bbox'][0]:.0f}_{z['bbox'][1]:.0f}_{z['bbox'][2]:.0f}_{z['bbox'][3]:.0f}_{len(z['points'])}_{fh}_{sh}"
        groups.setdefault(k, []).append(z)
    result = []
    for g in groups.values():
        if len(g) == 1: result.append(g[0])
        else:
            best = max(g, key=lambda z: (1 if z["fill"] else 0, z["width"], z["confidence"]))
            best_c = max(g, key=lambda z: z["confidence"])
            if best_c["confidence"] > best["confidence"]:
                best["zone_type"] = best_c["zone_type"]
                best["confidence"] = best_c["confidence"]
                best["classification_method"] = best_c["classification_method"]
            if best["fill"]: best["color_hex"] = _rgb_hex(best["fill"])
            else:
                for z in g:
                    if z["stroke"] and z["stroke"] != (0,0,0):
                        best["color_hex"] = _rgb_hex(z["stroke"]); break
            result.append(best)
    return result

# --- Stage 5: Coordinate Normalization & Auto-Orientation ---

DEFAULT_COLORS = {
    "plot_boundary": "#3b82f6", "buildable_envelope": "#f97316",
    "infrastructure_zone": "#94a3b8", "landscape_zone": "#22c55e",
    "restriction_line": "#ef4444", "zone_boundary": "#f59e0b",
    "parcel_line": "#06b6d4", "sub_zone": "#8b5cf6",
    "major_boundary": "#e2e8f0", "context_line": "#64748b",
    "overlapping_zone": "#a855f7", "filled_zone": "#fb923c",
    "minor_context": "#475569", "uncategorized_zone": "#f97316",
    "traffic_zone": "#64748b", "no_build_zone": "#ef4444",
}

# No Y-tier offsets — all geometry on flat ground plane (y=0)


def _signed_area_2d(pts):
    """Signed area of a 2D polygon. Positive = CCW, Negative = CW."""
    n = len(pts)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += pts[i][0] * pts[j][1]
        area -= pts[j][0] * pts[i][1]
    return area / 2.0


def _min_area_bbox_angle(pts_2d):
    """Find the rotation angle that produces the minimum-area bounding box.
    Uses rotating calipers on the convex hull edges."""
    from shapely.geometry import MultiPoint
    if len(pts_2d) < 3:
        return 0.0
    try:
        hull = MultiPoint(pts_2d).convex_hull
        if hull.geom_type != "Polygon":
            return 0.0
        coords = list(hull.exterior.coords)
    except Exception:
        return 0.0

    best_angle = 0.0
    best_area = float("inf")

    for i in range(len(coords) - 1):
        dx = coords[i + 1][0] - coords[i][0]
        dy = coords[i + 1][1] - coords[i][1]
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            continue
        angle = math.atan2(dy, dx)
        cos_a = math.cos(-angle)
        sin_a = math.sin(-angle)
        xs = [cos_a * x + sin_a * y for x, y in coords]
        ys = [-sin_a * x + cos_a * y for x, y in coords]
        w = max(xs) - min(xs)
        h = max(ys) - min(ys)
        area = w * h
        if area < best_area:
            best_area = area
            best_angle = angle

    # Ensure landscape orientation (wider than tall)
    cos_a = math.cos(-best_angle)
    sin_a = math.sin(-best_angle)
    xs = [cos_a * x + sin_a * y for x, y in coords]
    ys = [-sin_a * x + cos_a * y for x, y in coords]
    w = max(xs) - min(xs)
    h = max(ys) - min(ys)
    if h > w:
        best_angle += math.pi / 2

    return best_angle


def _auto_orient(zones, pw, ph, scale=0.1):
    """Auto-orient all geometry:
    1. Center on page midpoint
    2. Map directly: X = pdf_x (rightward), Z = pdf_y (downward in PDF)
       The camera's up-vector handles screen orientation so the viewport
       matches the source PDF layout exactly.
    3. Re-center around geometry bounding box
    4. Fix winding per polygon
    """
    cx, cy = pw / 2, ph / 2

    # Step 1: Center and map directly (no Y-flip).
    # PDF: origin top-left, Y increases downward.
    # World: X = (pdf_x - cx), Z = (pdf_y - cy)
    # The top-down camera with up=[0,0,-1] makes negative-Z "up" on screen,
    # so PDF top (small Y → negative Z) appears at screen top. Exact match.
    for z in zones:
        z["_rot2d"] = [((px - cx) * scale, (py - cy) * scale) for px, py in z["points"]]
        z["_cen_rot"] = ((z["centroid"][0] - cx) * scale, (z["centroid"][1] - cy) * scale)

    # Step 2: Re-center so bounding box midpoint is at origin
    all_x = []
    all_z = []
    for z in zones:
        for rx, rz in z["_rot2d"]:
            all_x.append(rx)
            all_z.append(rz)
    if all_x:
        off_x = (max(all_x) + min(all_x)) / 2
        off_z = (max(all_z) + min(all_z)) / 2
    else:
        off_x = off_z = 0.0

    for z in zones:
        z["_rot2d"] = [(rx - off_x, rz - off_z) for rx, rz in z["_rot2d"]]
        z["_cen_rot"] = (z["_cen_rot"][0] - off_x, z["_cen_rot"][1] - off_z)

    # Step 3: Fix winding PER POLYGON
    for z in zones:
        if z["closed"] and len(z["_rot2d"]) >= 3:
            sa = _signed_area_2d(z["_rot2d"])
            if sa < 0:
                z["_rot2d"] = list(reversed(z["_rot2d"]))

    return zones, 0.0


def _normalize(zones, pw, ph, scale=0.1):
    zones, orient_angle = _auto_orient(zones, pw, ph, scale)

    out = []
    for z in zones:
        # All geometry on flat ground plane — y = 0
        pts3 = [[round(rx, 4), 0, round(rz, 4)] for rx, rz in z["_rot2d"]]
        ct3 = [round(z["_cen_rot"][0], 4), 0, round(z["_cen_rot"][1], 4)]

        color = z.get("color_hex") or DEFAULT_COLORS.get(z["zone_type"], "#94a3b8")
        out.append({
            "id": z["id"], "type": "Polygon" if z["closed"] else "Polyline",
            "zone_type": z["zone_type"], "points": pts3, "closed": z["closed"],
            "area_pdf_units": round(z["area"], 1), "centroid": ct3,
            "color_hint": color, "stroke_width": z["width"],
            "confidence": round(z["confidence"], 2),
            "classification_method": z["classification_method"],
            "filled": z["fill"] is not None,
            "zone_label": z.get("zone_label", ""),
            "marker_labels": z.get("marker_labels", []),
        })

    # Cleanup temp keys
    for z in zones:
        for k in ("_pts2d", "_cen2d", "_rot2d", "_cen_rot"):
            z.pop(k, None)

    return out

# --- Stage 6: Text-based Label Enrichment ---

# Map of known label patterns from Dutch zoning plans to semantic labels
TEXT_LABEL_MAP = {
    "GD": "Gemengd (Mixed-Use)",
    "V": "Verkeer (Traffic)",
    "G": "Groen (Green)",
    "WR-A": "Waarde - Archeologie",
}

# Regex patterns for functional zone tags
_SGD_RE = re.compile(r'\(sgd-(\d+)\)')  # specifieke vorm van gemengd
_SBA_RE = re.compile(r'\[sba-([^\]]+)\]')  # specifieke bouwaanduiding
_HEIGHT_RE = re.compile(r'^(\d+(?:,\d+)?)$')  # building height values


def _enrich_with_text(zones, page):
    """Stage 6: Enrich zone classification using text labels from the source document.
    Each text block is assigned to the SMALLEST polygon that contains it,
    so that labels go to the most specific sub-zone, not the large outer boundary."""
    all_text = page.get_text("blocks")
    if not all_text:
        return zones

    # Sort closed zones by area ascending — smaller polygons get priority
    closed_zones = sorted(
        [z for z in zones if z["closed"] and z.get("shapely_poly")],
        key=lambda z: z["area"]
    )

    # For each text block, find the smallest polygon containing it
    text_assignments = {}  # zone_id -> list of text lines
    for b in all_text:
        t = b[4].strip()
        if not t:
            continue
        tc = Point((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)

        # Find smallest containing polygon
        for z in closed_zones:
            try:
                if z["shapely_poly"].contains(tc):
                    text_assignments.setdefault(z["id"], []).append(t)
                    break  # Assigned to smallest — stop
            except:
                continue

    # Now process assignments for each zone
    for z in zones:
        texts = text_assignments.get(z["id"], [])
        if not texts:
            continue

        labels = []
        heights = []
        tags = []

        for t in texts:
            for line in t.split('\n'):
                line = line.strip()
                if not line:
                    continue

                if line in TEXT_LABEL_MAP:
                    if TEXT_LABEL_MAP[line] not in labels:
                        labels.append(TEXT_LABEL_MAP[line])
                    continue

                sgd = _SGD_RE.match(line)
                if sgd:
                    tag = f"sgd-{sgd.group(1)}"
                    if tag not in tags:
                        tags.append(tag)
                    continue

                sba = _SBA_RE.match(line)
                if sba:
                    tag = f"sba-{sba.group(1)}"
                    if tag not in tags:
                        tags.append(tag)
                    continue

                hm = _HEIGHT_RE.match(line)
                if hm:
                    val = hm.group(1)
                    if val not in heights:
                        heights.append(val)
                    continue

                if line == "(m)":
                    if "maatschappelijk" not in tags:
                        tags.append("maatschappelijk")

        # Build composite label
        parts = []
        if labels:
            parts.append(labels[0])
        if tags:
            parts.append(" · ".join(tags))
        if heights:
            parts.append(f"h={','.join(heights)}m")

        if parts:
            z["zone_label"] = " | ".join(parts)
            # Upgrade classification if we got a clear text label
            if (labels or tags) and z["zone_type"] in ("minor_context", "unknown", "sub_zone", "context_line"):
                if "sub_zone" not in z["zone_type"]:
                    z["zone_type"] = "sub_zone"
                z["confidence"] = max(z["confidence"], 0.85)
                z["classification_method"] += "+text_label"

    return zones


# --- Public API ---

def extract_vectors_from_pdf(
    pdf_path: str,
    gemini_api_key: Optional[str] = None,
    gemini_model: Optional[str] = None,
) -> Dict[str, Any]:
    if not os.path.exists(pdf_path):
        return {"error": f"File not found: {pdf_path}"}
    doc = fitz.open(pdf_path)
    vectors, texts = [], []
    if len(doc) > 0:
        page = doc[0]
        pw, ph = page.rect.width, page.rect.height
        pa = pw * ph

        # ── Stage 1: Raw path reconstruction ──
        raw = _reconstruct_paths(page)
        log.info("Stage 1: %d raw paths", len(raw))

        # ── Stage 2a: Collect already-closed boundaries + hatching ──
        boundaries, hatching, other_paths = _collect_boundary_zones(raw, pa)
        log.info("Stage 2a: %d closed boundaries, %d hatching, %d other",
                 len(boundaries), len(hatching), len(other_paths))

        # ── Stage 2b: Adaptive subzone extraction (hierarchy-aware) ──
        # This analyses the representation (clean outlines vs fragmented
        # dashes) and applies the appropriate strategy.
        new_subzones = extract_subzones_adaptive(raw, boundaries, hatching, pa)
        log.info("Stage 2b: adaptive extraction found %d new subzones", len(new_subzones))

        # Tag subzone boundaries so we can identify them later
        for sz in new_subzones:
            sz["_from_adaptive"] = True

        # Merge all boundaries and assign fill to ALL of them
        # (both zone-level and subzone-level get hatching fill)
        boundaries = boundaries + new_subzones
        boundaries = _assign_fill_colors(boundaries, hatching)

        # ── Stage 2d: Deduplicate overlapping boundaries ──
        boundaries = _deduplicate_boundaries(boundaries, pa)
        log.info("Stage 2d: %d boundaries after dedup", len(boundaries))

        # Convert boundaries to polygon dicts for downstream stages
        polys = []
        for b in boundaries:
            poly = b.get("_shapely")
            if poly is None:
                continue
            c = poly.centroid
            polys.append({
                "id": f"zone_{uuid.uuid4().hex[:8]}",
                "shapely_poly": poly,
                "points": b["points"],
                "area": b["_area"],
                "centroid": (c.x, c.y),
                "closed": True,
                "fill": b.get("fill"),
                "stroke": b.get("stroke"),
                "width": b.get("width", 0),
                "bbox": b["bbox"],
                "bbox_area": b["bbox_area"],
                "_extraction_strategy": b.get("_extraction_strategy", "direct"),
            })

        polys = _filter_page_borders(polys, pa, pw, ph)
        markers, polys = _identify_label_markers(polys, pa)
        polys = _extract_marker_labels(markers, polys, page)
        polys = _simplify_polygons(polys)

        # ── AI Vision Classification (when API key available) ──
        classification_mode = "rule_based"
        ai_error_detail = None
        if gemini_api_key:
            log.info("Stage AI: Running vision-based classification...")
            log.info("Stage AI: API key present (first 8 chars: %s...)", gemini_api_key[:8])
            try:
                polys = classify_polygons_with_vision(
                    polys, page, pa, gemini_api_key,
                    model_name=gemini_model or "gemini-2.5-flash",
                )
                # Check if SDK was missing (not an API error, just not installed)
                if any(p.get("_ai_sdk_missing") for p in polys):
                    classification_mode = "rule_based"
                    log.info("AI Vision SDK not installed — using rule-based classification")
                    for p in polys:
                        p.pop("_ai_sdk_missing", None)
                # Check for API errors surfaced from the classifier
                elif any(p.get("_ai_error") for p in polys):
                    classification_mode = "rule_based_fallback"
                    ai_error_detail = next(
                        (p["_ai_error"] for p in polys if p.get("_ai_error")), None
                    )
                    log.error("AI Vision API error: %s", ai_error_detail)
                    for p in polys:
                        p.pop("_ai_error", None)
                # Check if any polygon got AI tags
                elif any(p.get("_ai_zone_type") or p.get("_is_artifact") for p in polys):
                    classification_mode = "ai_vision"
                else:
                    classification_mode = "rule_based_fallback"
                    log.warning("AI Vision returned no classifications — falling back")
            except Exception as e:
                classification_mode = "rule_based_fallback"
                ai_error_detail = str(e)
                log.error("AI Vision failed: %s — falling back to rules", e)
        else:
            classification_mode = "rule_based_no_key"

        # ── Rule-based classification (baseline / fallback) ──
        classified = _classify_zones(polys, pa)

        # Apply AI overrides if vision classification was run
        if classification_mode == "ai_vision":
            for z in classified:
                ai_type = z.get("_ai_zone_type")
                if ai_type:
                    z["zone_type"] = ai_type
                    z["confidence"] = z.get("_ai_confidence", 0.9)
                    z["classification_method"] = (
                        f"ai_vision ({z.get('_ai_reason', '')})"
                    )
                if z.get("_is_artifact"):
                    z["zone_type"] = "minor_context"
                    z["confidence"] = 0.1
                    z["classification_method"] = "ai_vision_artifact"

        deduped = _dedup(classified)

        # ── Resolve zone/building overlap ──
        # For filled subzones: if contained inside a larger zone with
        # the same fill colour → it inherited the parent's hatching
        # → reclassify as building (sub_zone), strip the fill.
        deduped = _resolve_zone_building_overlap(deduped)

        # Promote nested subzones (inner same-type → sub_zone)
        deduped = _promote_nested_subzones(deduped)

        # Stage 6: text-based enrichment before filtering
        deduped = _enrich_with_text(deduped, page)

        # ── Gap-fill: ensure plot boundary is fully covered ──
        deduped = _fill_plot_gaps(deduped, pa)

        log.info("After classification + enrichment + gap-fill: %d zones (mode=%s)",
                 len(deduped), classification_mode)

        # Filter noise — keep only clean, meaningful zones
        meaningful = []
        for z in deduped:
            zt = z["zone_type"]
            area = z["area"]
            closed = z["closed"]

            # Always keep: plot boundary, zone boundary, parcel line
            if zt in ("plot_boundary", "zone_boundary", "parcel_line", "uncategorized_zone"):
                meaningful.append(z)
            # Keep any zone that has a text label from the source doc
            elif z.get("zone_label"):
                meaningful.append(z)
            # Key zone types — but require minimum area to avoid tiny noise fills
            elif zt in ("buildable_envelope", "landscape_zone",
                        "infrastructure_zone", "restriction_line"):
                if closed and area > pa * 0.0005:
                    meaningful.append(z)
                elif not closed and (z["width"] >= 0.5 or z["bbox_area"] > pa * 0.005):
                    meaningful.append(z)
            # Sub-zones and major boundaries inside the plot
            elif closed and area > pa * 0.00008:
                if zt not in ("unknown",):
                    meaningful.append(z)
            # Open lines: keep restriction, major boundary, zone boundary
            elif not closed and zt in ("restriction_line", "major_boundary", "zone_boundary"):
                if z["width"] >= 0.3 or z["bbox_area"] > pa * 0.005:
                    meaningful.append(z)
            # Large unknown areas
            elif area > pa * 0.005:
                meaningful.append(z)

        # Deduplicate plot boundaries — keep only the most complex one
        plot_boundaries = [z for z in meaningful if z["zone_type"] == "plot_boundary"]
        if len(plot_boundaries) > 1:
            plot_boundaries.sort(key=lambda z: len(z.get("points", [])), reverse=True)
            rect_ids = set()
            for pb in plot_boundaries[1:]:
                pts = pb.get("points", [])
                if len(pts) <= 6:
                    rect_ids.add(pb["id"])
            if rect_ids:
                meaningful = [z for z in meaningful if z["id"] not in rect_ids]

        vectors = _normalize(meaningful, pw, ph)

        # Text extraction for metadata panel
        scale = 0.1
        plot_bbox = None
        for v in vectors:
            if v["zone_type"] == "plot_boundary":
                xs = [p[0] for p in v["points"]]; zs = [p[2] for p in v["points"]]
                plot_bbox = fitz.Rect(min(xs)/scale+pw/2, min(zs)/scale+ph/2,
                                      max(xs)/scale+pw/2, max(zs)/scale+ph/2)
                break
        if plot_bbox is None:
            mx, my = pw*0.10, ph*0.10
            plot_bbox = fitz.Rect(mx, my, pw-mx, ph-my)

        kw = ["height","setback","max","min","sqm","m2","limit","grens","kavel",
              "m²","floor","level","area","boundary","plot","zone","regel",
              "bouw","hoogte","goot","nok","bebouw","rooilijn","meter"]
        for b in page.get_text("blocks"):
            br = fitz.Rect(b[:4])
            if not plot_bbox.intersects(br): continue
            t = b[4].strip()
            if t:
                tl = t.lower()
                if any(k in tl for k in kw) or (any(c.isdigit() for c in t) and len(t)<20):
                    texts.append({"text": t, "bbox": [
                        round((b[0]-pw/2)*scale,4), round((b[1]-ph/2)*scale,4),
                        round((b[2]-pw/2)*scale,4), round((b[3]-ph/2)*scale,4)]})

    doc.close()
    zt = {}
    for v in vectors: zt[v["zone_type"]] = zt.get(v["zone_type"],0)+1
    result = {
        "extracted_objects": len(vectors),
        "vectors": vectors,
        "extracted_text": texts,
        "zone_summary": zt,
        "classification_mode": classification_mode if vectors else "no_pages",
    }
    if ai_error_detail:
        result["ai_error_detail"] = ai_error_detail
    return result
