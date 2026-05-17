"""
Semantic Zone Classifier
Assigns zone_type and confidence to extracted polygons using
color analysis, spatial containment, and area ranking.
"""
from typing import List, Dict, Any, Optional, Tuple
import math

def classify_zone_by_color(color: Optional[tuple], is_fill: bool = False) -> Tuple[str, float]:
    """Classify a zone based on its color. Returns (zone_type, confidence)."""
    if color is None:
        return ("unknown", 0.3)
    
    families = {
        "buildable_envelope": [(1.0,0.75,0.53), (1.0,0.65,0.40), (0.95,0.70,0.50)],
        "infrastructure_zone": [(0.80,0.80,0.80), (0.75,0.75,0.75), (0.85,0.85,0.85)],
        "landscape_zone": [(0.16,0.78,0.27), (0.0,0.80,0.20), (0.20,0.70,0.30)],
        "restriction_line": [(0.87,0.0,0.0), (1.0,0.0,0.0), (0.80,0.10,0.10)],
        "zone_boundary": [(1.0,0.61,0.0), (0.95,0.55,0.0), (1.0,0.50,0.0)],
        "parcel_line": [(0.0,1.0,1.0), (0.0,0.90,0.90), (0.0,0.80,1.0)],
    }
    
    best_type, best_dist = "unknown", float("inf")
    for zt, refs in families.items():
        for ref in refs:
            d = math.sqrt(sum((a-b)**2 for a,b in zip(color[:3], ref)))
            if d < best_dist:
                best_dist = d
                best_type = zt
    
    if best_dist > 0.35:
        return ("unknown", 0.3)
    
    conf = max(0.5, 1.0 - best_dist * 2)
    if is_fill:
        conf = min(conf + 0.1, 1.0)
    
    return (best_type, round(conf, 2))


def classify_by_containment(poly_data: Dict, plot_boundary: Optional[Dict]) -> Tuple[str, float]:
    """Classify based on spatial relationship to the plot boundary."""
    if not plot_boundary or not poly_data.get("shapely_poly") or not plot_boundary.get("shapely_poly"):
        return ("unknown", 0.3)
    
    try:
        plot_poly = plot_boundary["shapely_poly"]
        test_poly = poly_data["shapely_poly"]
        
        if plot_poly.contains(test_poly):
            # Nested inside plot → sub-zone
            area_ratio = test_poly.area / plot_poly.area
            if area_ratio > 0.5:
                return ("major_sub_zone", 0.85)
            elif area_ratio > 0.1:
                return ("sub_zone", 0.75)
            else:
                return ("minor_sub_zone", 0.60)
        elif plot_poly.intersects(test_poly):
            return ("overlapping_zone", 0.55)
        else:
            return ("external_context", 0.40)
    except Exception:
        return ("unknown", 0.3)


def merge_classifications(*classifications: Tuple[str, float]) -> Tuple[str, float]:
    """Merge multiple classification results, preferring higher confidence."""
    best = max(classifications, key=lambda c: c[1])
    return best
