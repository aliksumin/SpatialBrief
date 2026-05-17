"""
Adaptive Hierarchical Zone Extraction — Format-Agnostic
========================================================

Works on normalised intermediate geometry from ANY source (PDF, DXF, DWG).

Three-phase pipeline:
  Phase 1 — Hierarchy Discovery   (representation-agnostic)
  Phase 2 — Representation Analysis  (per hierarchy level)
  Phase 3 — Adaptive Extraction    (strategy per representation)

Input format  – list of path dicts, each with:
    points   : list[(x, y)]          2-D coordinates (page / model space)
    fill     : tuple|None            RGB fill colour
    stroke   : tuple|None            RGB stroke colour
    width    : float                 stroke width
    closed   : bool                  whether endpoints meet
    bbox     : (x0, y0, x1, y1)
    bbox_area: float
    _source  : 'pdf' | 'dxf' | 'dwg'   (optional, informational)
"""

from __future__ import annotations
import math, uuid, logging
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from shapely.geometry import Polygon, Point, MultiPolygon
from shapely.validation import make_valid

log = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────
# Shape Intelligence (shared with pdf_vector_extractor)
# ────────────────────────────────────────────────────────────────────

def _compute_shape_metrics(poly) -> dict:
    """Compute geometric shape metrics for a polygon.
    Returns dict with: aspect_ratio, compactness, circularity, elongation, min_dim
    """
    try:
        area = poly.area
        perimeter = poly.length
        hull = poly.convex_hull
        hull_area = hull.area

        minr = poly.minimum_rotated_rectangle
        coords = list(minr.exterior.coords)
        w = math.dist(coords[0], coords[1])
        h = math.dist(coords[1], coords[2])
        long_side = max(w, h)
        short_side = min(w, h)

        return {
            "aspect_ratio": long_side / max(short_side, 0.001),
            "compactness": area / max(hull_area, 0.001),
            "circularity": (4 * math.pi * area) / max(perimeter * perimeter, 0.001),
            "elongation": 1.0 - short_side / max(long_side, 0.001),
            "min_dim": short_side,
        }
    except Exception:
        return {
            "aspect_ratio": 1.0, "compactness": 1.0,
            "circularity": 0.5, "elongation": 0.0, "min_dim": 10.0,
        }

# ────────────────────────────────────────────────────────────────────
# Colour helpers
# ────────────────────────────────────────────────────────────────────

def _rgb_hex(rgb: Optional[Tuple]) -> Optional[str]:
    if rgb is None:
        return None
    return f"#{int(rgb[0]*255):02x}{int(rgb[1]*255):02x}{int(rgb[2]*255):02x}"


def _is_gray(rgb: Tuple, lo: float = 0.2, hi: float = 0.6) -> bool:
    """Check if a colour is neutral gray (R≈G≈B in the given range)."""
    r, g, b = rgb[:3]
    return abs(r - g) < 0.08 and abs(g - b) < 0.08 and lo < r < hi


# ────────────────────────────────────────────────────────────────────
# Phase 1 — Hierarchy Discovery
# ────────────────────────────────────────────────────────────────────

def _to_polygon(pts: list) -> Optional[Polygon]:
    """Build a valid Shapely Polygon from a coordinate ring."""
    ring = list(pts)
    if len(ring) < 3:
        return None
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    try:
        poly = Polygon(ring)
        if not poly.is_valid:
            poly = make_valid(poly)
        if isinstance(poly, MultiPolygon):
            poly = max(poly.geoms, key=lambda g: g.area)
        return poly if isinstance(poly, Polygon) and poly.area > 0 else None
    except Exception:
        return None


def discover_hierarchy(
    boundaries: List[Dict[str, Any]],
    hatching: List[Dict[str, Any]],
    page_area: float,
) -> Dict[str, Any]:
    """Phase 1: Build containment tree  Plot → Zones → SubzoneCandidates.

    Parameters
    ----------
    boundaries : closed polylines already collected (all sources)
    hatching   : filled elements (used for colour sampling only)
    page_area  : total page / model-space area

    Returns
    -------
    dict with keys: plot, zones, subzone_candidates, unclassified
    """
    # Attach Shapely polygon + area to every boundary if not present
    for b in boundaries:
        if "_shapely" not in b:
            pts = b["points"]
            if len(pts) >= 4:
                poly = _to_polygon(pts)
                if poly:
                    b["_shapely"] = poly
                    b["_area"] = poly.area

    # Sort by area descending
    with_area = [b for b in boundaries if "_area" in b and b["_area"] > 0]
    with_area.sort(key=lambda b: b["_area"], reverse=True)

    # --- Plot boundary: largest unfilled closed polygon (>5% page) ---
    plot = None
    for b in with_area:
        if b.get("fill") is None and b["_area"] > page_area * 0.05 and b.get("width", 0) >= 0.3:
            plot = b
            break
    if plot is None and with_area:
        plot = with_area[0]

    plot_poly = plot["_shapely"] if plot else None

    # --- Classify everything inside the plot ---
    zones: List[Dict[str, Any]] = []
    subzone_candidates: List[Dict[str, Any]] = []
    unclassified: List[Dict[str, Any]] = []

    for b in with_area:
        if b is plot:
            continue

        b_poly = b.get("_shapely")
        if b_poly is None:
            unclassified.append(b)
            continue

        # Check containment inside plot
        inside_plot = False
        if plot_poly:
            try:
                inside_plot = (
                    plot_poly.contains(b_poly) or
                    plot_poly.intersection(b_poly).area > b["_area"] * 0.80
                )
            except Exception:
                pass

        if not inside_plot:
            unclassified.append(b)
            continue

        # Decide zone vs. subzone candidate
        area_ratio = b["_area"] / page_area
        has_distinct_stroke = False
        if b.get("stroke"):
            sr, sg, sb = b["stroke"][:3]
            has_distinct_stroke = (
                not (sr < 0.15 and sg < 0.15 and sb < 0.15) and
                not _is_gray(b["stroke"][:3])
            )

        if area_ratio > 0.015 or has_distinct_stroke:
            zones.append(b)
        else:
            subzone_candidates.append(b)

    return {
        "plot": plot,
        "zones": zones,
        "subzone_candidates": subzone_candidates,
        "unclassified": unclassified,
    }


# ────────────────────────────────────────────────────────────────────
# Phase 2 — Representation Analysis
# ────────────────────────────────────────────────────────────────────

def analyze_representation(
    open_segments: List[Dict[str, Any]],
    existing_subzones: List[Dict[str, Any]],
    zone_poly: Optional[Polygon],
    page_area: float,
) -> str:
    """Phase 2: Detect how subzones are represented.

    Returns one of:
        'clean_outlines'     — already-closed polylines exist
        'fragmented_dashes'  — many short open segments needing joining
        'fill_only'          — only filled elements, no boundaries
        'mixed'              — both closed outlines and dash fragments
        'none'               — nothing meaningful found
    """
    inside_open = 0
    inside_closed = len(existing_subzones)

    if zone_poly:
        for seg in open_segments:
            pts = seg["points"]
            if len(pts) < 2:
                continue
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            try:
                if zone_poly.contains(Point(cx, cy)):
                    inside_open += 1
            except Exception:
                pass
    else:
        inside_open = len(open_segments)

    total = inside_open + inside_closed

    if total == 0:
        return "none"
    if inside_closed > 0 and inside_open < 10:
        return "clean_outlines"
    if inside_open > 20 and inside_closed == 0:
        return "fragmented_dashes"
    if inside_open > 20 and inside_closed > 0:
        return "mixed"
    return "clean_outlines" if inside_closed > 0 else "none"


# ────────────────────────────────────────────────────────────────────
# Phase 3 — Adaptive Extraction
# ────────────────────────────────────────────────────────────────────

# ............................................................
# Strategy B — Two-stage: Chain → Decompose large chains via
#              planar face extraction
# ............................................................

def _snap_point(pt: Tuple[float, float], grid: float) -> Tuple[float, float]:
    return (round(pt[0] / grid) * grid, round(pt[1] / grid) * grid)


def _edge_angle(frm: Tuple, to: Tuple) -> float:
    """Angle from node *frm* to node *to* in [-π, π]."""
    return math.atan2(to[1] - frm[1], to[0] - frm[0])


def _chain_join_segments(
    segments: List[Dict[str, Any]],
    max_gap: float = 8.0,
) -> List[List[Tuple[float, float]]]:
    """Greedy chain-join of open segments into polylines.

    Returns a list of polylines (coordinate lists).  Closed ones
    have first == last point (within tolerance).
    """
    if not segments:
        return []

    # Sort by length descending — start chains from longest pieces
    segs = list(segments)
    segs.sort(
        key=lambda s: sum(
            math.dist(s["points"][i], s["points"][i + 1])
            for i in range(len(s["points"]) - 1)
        ),
        reverse=True,
    )

    used: set = set()
    chains: List[List[Tuple[float, float]]] = []

    for start_idx, start_seg in enumerate(segs):
        if start_idx in used:
            continue
        chain = [tuple(p) for p in start_seg["points"]]
        used.add(start_idx)
        extended = True

        while extended:
            extended = False
            best_dist = max_gap
            best_idx = None
            best_mode = None

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
                sp = [tuple(p) for p in seg["points"]]
                if best_mode == "append":
                    chain.extend(sp[1:])
                elif best_mode == "append_rev":
                    chain.extend(list(reversed(sp))[1:])
                elif best_mode == "prepend":
                    chain = list(sp) + chain[1:]
                else:
                    chain = list(reversed(sp)) + chain[1:]

        if len(chain) >= 3:
            chains.append(chain)

    return chains


def _extract_faces_from_chains(
    chains: List[List[Tuple[float, float]]],
    page_area: float,
    snap_grid: float = 2.0,
    min_area_frac: float = 0.0001,
) -> List[List[Tuple[float, float]]]:
    """Extract closed faces from a set of polyline chains using
    planar face extraction.

    This is called on the CHAINED lines (not raw dashes), so
    each chain is a continuous polyline.  We decompose them into
    edges and build a planar graph.
    """
    min_area = page_area * min_area_frac

    node_map: Dict[Tuple[float, float], int] = {}
    node_coords: List[Tuple[float, float]] = []

    def get_node(pt):
        sp = _snap_point(pt, snap_grid)
        if sp not in node_map:
            node_map[sp] = len(node_coords)
            node_coords.append(sp)
        return node_map[sp]

    edges: set = set()
    for chain in chains:
        for i in range(len(chain) - 1):
            a = get_node(chain[i])
            b = get_node(chain[i + 1])
            if a != b:
                edges.add((min(a, b), max(a, b)))

    n_nodes = len(node_coords)
    n_edges = len(edges)
    log.info("  Face-extraction graph: %d nodes, %d edges", n_nodes, n_edges)

    if n_edges < 3:
        return []

    # Build adjacency
    adj: Dict[int, List[int]] = defaultdict(list)
    for a, b in edges:
        adj[a].append(b)
        adj[b].append(a)
    for u in adj:
        adj[u].sort(key=lambda v: _edge_angle(node_coords[u], node_coords[v]))

    # Build next-half-edge map
    next_he: Dict[Tuple[int, int], Tuple[int, int]] = {}
    for u in adj:
        for v in adj[u]:
            neighbours_v = adj[v]
            try:
                idx = neighbours_v.index(u)
            except ValueError:
                continue
            next_idx = (idx - 1) % len(neighbours_v)
            w = neighbours_v[next_idx]
            next_he[(u, v)] = (v, w)

    # Trace faces
    used_he: set = set()
    faces: List[List[int]] = []
    for he in next_he:
        if he in used_he:
            continue
        face = []
        cur = he
        safe = n_edges * 2 + 10
        while safe > 0:
            if cur in used_he:
                break
            used_he.add(cur)
            face.append(cur[0])
            nxt = next_he.get(cur)
            if nxt is None:
                break
            if nxt == he:
                face.append(nxt[0])
                break
            cur = nxt
            safe -= 1
        if len(face) >= 4 and face[0] == face[-1]:
            faces.append(face)

    log.info("  Traced %d raw faces", len(faces))

    # Filter by area, discard outer face
    result = []
    face_areas = []
    for face in faces:
        coords = [node_coords[nid] for nid in face]
        poly = _to_polygon(coords)
        if poly is None:
            continue
        area = poly.area
        if area < min_area:
            continue
        result.append(coords)
        face_areas.append(area)

    # Remove the outer (infinite) face — the largest one if it's
    # dramatically bigger than the rest
    if len(result) > 1 and face_areas:
        max_idx = face_areas.index(max(face_areas))
        sorted_areas = sorted(face_areas, reverse=True)
        if len(sorted_areas) >= 2 and sorted_areas[0] > sorted_areas[1] * 3:
            result.pop(max_idx)
            face_areas.pop(max_idx)

    log.info("  Kept %d meaningful faces (areas: %s)",
             len(result),
             [f"{a:.0f}" for a in sorted(face_areas, reverse=True)[:10]])
    return result


def _extract_fragmented_subzones(
    open_segments: List[Dict[str, Any]],
    plot_poly: Optional[Polygon],
    page_area: float,
    max_gap: float = 8.0,
) -> List[Dict[str, Any]]:
    """Strategy B: Extract subzones from fragmented dashed segments.

    Two-stage approach:
    1. Chain-join dashes into continuous polylines (greedy nearest-neighbour)
    2. For chains that close: use directly
    3. For the giant merged chain: decompose via planar face extraction

    The chain-joining correctly separates isolated buildings. Only the
    mega-chain (where buildings share walls) needs face decomposition.
    """
    if not open_segments:
        return []

    # Filter to segments inside the plot
    inside_segments = []
    if plot_poly:
        for seg in open_segments:
            pts = seg["points"]
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            try:
                if plot_poly.contains(Point(cx, cy)):
                    inside_segments.append(seg)
            except Exception:
                pass
    else:
        inside_segments = list(open_segments)

    # Group by colour+width
    style_groups: Dict[str, List[Dict[str, Any]]] = {}
    for seg in inside_segments:
        stroke = seg.get("stroke")
        if stroke is None:
            continue
        r, g, b = stroke[:3]
        w = round(seg.get("width", 0), 1)
        key = f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}|{w}"
        style_groups.setdefault(key, []).append(seg)

    new_subzones: List[Dict[str, Any]] = []

    for key, segs in style_groups.items():
        if len(segs) < 5:
            continue

        color_part = key.split("|")[0]
        width_part = float(key.split("|")[1])
        sr = int(color_part[1:3], 16) / 255
        sg = int(color_part[3:5], 16) / 255
        sb = int(color_part[5:7], 16) / 255

        log.info("Processing style group %s (%d segments)", key, len(segs))

        # Stage 1: Chain-join
        chains = _chain_join_segments(segs, max_gap)
        log.info("  Chain-join produced %d chains", len(chains))

        # Separate: small closed chains vs. the mega-chain
        closed_chains = []
        mega_chains = []  # Large non-closed or very large closed
        small_open = []

        for chain in chains:
            is_closed = len(chain) >= 4 and math.dist(chain[0], chain[-1]) < max_gap
            xs = [p[0] for p in chain]
            ys = [p[1] for p in chain]
            bbox_w = max(xs) - min(xs)
            bbox_h = max(ys) - min(ys)
            approx_area = bbox_w * bbox_h

            if is_closed and approx_area < page_area * 0.05:
                # Normal-sized closed chain — use directly as a subzone
                closed_chains.append(chain)
            elif len(chain) > 50 or approx_area > page_area * 0.05:
                # Very large chain — needs decomposition
                mega_chains.append(chain)
            else:
                small_open.append(chain)

        log.info("  Closed chains: %d, mega chains: %d, small open: %d",
                 len(closed_chains), len(mega_chains), len(small_open))

        # Convert closed chains to subzone dicts (with shape validation)
        for chain in closed_chains:
            # Close the loop
            if chain[0] != chain[-1]:
                chain.append(chain[0])
            poly = _to_polygon(chain)
            if poly is None or poly.area < page_area * 0.0001:
                continue

            # Shape validation — reject artifact shapes
            metrics = _compute_shape_metrics(poly)
            if metrics["aspect_ratio"] > 10.0 or metrics["elongation"] > 0.92:
                log.info("    Rejected chain (artifact shape): AR=%.1f, elong=%.2f",
                         metrics["aspect_ratio"], metrics["elongation"])
                continue
            if metrics["min_dim"] < 1.5:
                continue

            xs = [p[0] for p in chain]
            ys = [p[1] for p in chain]
            bbox = (min(xs), min(ys), max(xs), max(ys))
            new_subzones.append({
                "points": chain,
                "fill": None,
                "stroke": (sr, sg, sb),
                "width": width_part,
                "bbox": bbox,
                "bbox_area": (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]),
                "_reconstructed": True,
                "_shapely": poly,
                "_area": poly.area,
                "_extraction_strategy": "chain_join",
            })

        # Decompose mega-chains via planar face extraction
        if mega_chains:
            log.info("  Decomposing %d mega-chain(s) via planar face extraction",
                     len(mega_chains))
            faces = _extract_faces_from_chains(mega_chains, page_area)
            for face_coords in faces:
                poly = _to_polygon(face_coords)
                if poly is None or poly.area < page_area * 0.0001:
                    continue
                # Shape validation — reject artifact faces
                metrics = _compute_shape_metrics(poly)
                if metrics["aspect_ratio"] > 10.0 or metrics["elongation"] > 0.92:
                    log.info("    Rejected face (artifact shape): AR=%.1f, elong=%.2f",
                             metrics["aspect_ratio"], metrics["elongation"])
                    continue
                if metrics["min_dim"] < 1.5:
                    continue
                xs = [p[0] for p in face_coords]
                ys = [p[1] for p in face_coords]
                bbox = (min(xs), min(ys), max(xs), max(ys))
                new_subzones.append({
                    "points": list(face_coords),
                    "fill": None,
                    "stroke": (sr, sg, sb),
                    "width": width_part,
                    "bbox": bbox,
                    "bbox_area": (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]),
                    "_reconstructed": True,
                    "_shapely": poly,
                    "_area": poly.area,
                    "_extraction_strategy": "planar_face",
                })

    return new_subzones


# ............................................................
# Orchestrator
# ............................................................

def extract_subzones_adaptive(
    all_paths: List[Dict[str, Any]],
    boundaries: List[Dict[str, Any]],
    hatching: List[Dict[str, Any]],
    page_area: float,
) -> List[Dict[str, Any]]:
    """Orchestrate adaptive subzone extraction.

    1. Discover hierarchy from boundaries.
    2. Determine representation type.
    3. Extract subzone boundaries using the appropriate strategy.
    4. Return all new subzone boundary dicts.
    """
    hierarchy = discover_hierarchy(boundaries, hatching, page_area)

    plot = hierarchy["plot"]
    zones = hierarchy["zones"]
    existing_subzones = hierarchy["subzone_candidates"]
    plot_poly = plot["_shapely"] if plot and "_shapely" in plot else None

    log.info("Hierarchy: plot=%s, zones=%d, subzone_candidates=%d",
             f"{plot['_area']/page_area*100:.1f}%" if plot and "_area" in plot else "none",
             len(zones), len(existing_subzones))

    # Collect all open segments (unfilled, not closed)
    open_segments = []
    for p in all_paths:
        if p.get("fill") is not None:
            continue
        pts = p["points"]
        if len(pts) < 2:
            continue
        if len(pts) >= 4 and math.dist(pts[0], pts[-1]) < 5.0:
            continue
        seg_len = sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
        if seg_len < 0.5:
            continue
        open_segments.append(p)

    # Analyse overall representation
    rep_type = analyze_representation(open_segments, existing_subzones, plot_poly, page_area)
    log.info("Representation type: %s  (open_segs=%d, existing_subzones=%d)",
             rep_type, len(open_segments), len(existing_subzones))

    new_subzones: List[Dict[str, Any]] = []

    if rep_type in ("clean_outlines", "none"):
        # Strategy A: existing subzones are already extracted
        pass

    elif rep_type in ("fragmented_dashes", "mixed"):
        # Strategy B: chain + decompose
        new_subzones = _extract_fragmented_subzones(
            open_segments, plot_poly, page_area
        )

    log.info("Adaptive extraction produced %d new subzone boundaries", len(new_subzones))
    return new_subzones


# ────────────────────────────────────────────────────────────────────
# Fill-colour sampling  (shared utility)
# ────────────────────────────────────────────────────────────────────

def sample_fill_colours(
    boundaries: List[Dict[str, Any]],
    hatching: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Sample fill colours from hatching elements inside each boundary."""
    hatch_info = []
    for h in hatching:
        pts = h["points"]
        if len(pts) < 2 or h.get("fill") is None:
            continue
        cx = sum(pt[0] for pt in pts) / len(pts)
        cy = sum(pt[1] for pt in pts) / len(pts)
        rgb = h["fill"][:3]
        if _is_gray(rgb):
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
                    if r < 0.15 and g < 0.15 and bb < 0.15:
                        continue
                    if abs(r - g) < 0.1 and abs(g - bb) < 0.1 and r > 0.4:
                        continue
                    if key not in color_counts:
                        color_counts[key] = (0, hi["fill"])
                    color_counts[key] = (color_counts[key][0] + 1, hi["fill"])
            except Exception:
                continue

        if color_counts:
            best_key = max(color_counts, key=lambda k: color_counts[k][0])
            best_count, best_fill = color_counts[best_key]
            if best_count >= 2:
                b["fill"] = best_fill

    return boundaries
