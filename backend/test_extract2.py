import json, sys, math
sys.path.insert(0, '.')
import fitz
from shapely.geometry import Polygon, LineString, MultiLineString
from shapely.ops import polygonize, unary_union

fname = 'uploads/Drakaterrein-A2_2022-04-26 versie 2.pdf'
doc = fitz.open(fname)
page = doc[0]
paths = page.get_drawings()

page_width = page.rect.width
page_height = page.rect.height
page_area = page_width * page_height

print(f"Page: {page_width:.0f} x {page_height:.0f}")
print(f"Total paths: {len(paths)}")

# Analyze path sizes and characteristics
filled_paths = []
large_closed_paths = []
all_lines = []

for i, path in enumerate(paths):
    rect = path.get('rect')
    if not rect:
        continue
    
    area = rect.width * rect.height
    fill = path.get('fill')
    color = path.get('color')
    width = path.get('width', 0)
    
    # Build polyline from items
    points = []
    for item in path['items']:
        if item[0] == 'l':  # line
            p1, p2 = item[1], item[2]
            if not points or (points[-1] != (p1.x, p1.y)):
                points.append((p1.x, p1.y))
            points.append((p2.x, p2.y))
        elif item[0] == 'c':  # cubic bezier
            p1, p2, p3, p4 = item[1], item[2], item[3], item[4]
            if not points or (points[-1] != (p1.x, p1.y)):
                points.append((p1.x, p1.y))
            # tessellate
            for t_i in range(1, 11):
                t = t_i / 10
                inv_t = 1 - t
                x = (inv_t**3)*p1.x + 3*(inv_t**2)*t*p2.x + 3*inv_t*(t**2)*p3.x + (t**3)*p4.x
                y = (inv_t**3)*p1.y + 3*(inv_t**2)*t*p2.y + 3*inv_t*(t**2)*p3.y + (t**3)*p4.y
                points.append((x, y))
        elif item[0] == 'qu':  # quadratic bezier
            try:
                p1, p2, p3 = item[1], item[2], item[3]
                if not points or (points[-1] != (p1.x, p1.y)):
                    points.append((p1.x, p1.y))
                for t_i in range(1, 11):
                    t = t_i / 10
                    inv_t = 1 - t
                    x = (inv_t**2)*p1.x + 2*inv_t*t*p2.x + (t**2)*p3.x
                    y = (inv_t**2)*p1.y + 2*inv_t*t*p2.y + (t**2)*p3.y
                    points.append((x, y))
            except (IndexError, AttributeError):
                pass
    
    if len(points) < 2:
        continue
    
    # Check if path is closed
    dist = math.dist(points[0], points[-1])
    is_closed = dist < 5  # tolerance
    
    # Compute actual path length
    path_length = sum(math.dist(points[j], points[j+1]) for j in range(len(points)-1))
    
    # Large, filled and closed are likely zones
    if fill is not None and is_closed and area > page_area * 0.001:
        filled_paths.append({
            'index': i,
            'area': area,
            'bbox_area': area,
            'fill': fill,
            'color': color,
            'width': width,
            'n_points': len(points),
            'path_length': path_length,
            'closed': is_closed,
            'rect': (rect.x0, rect.y0, rect.x1, rect.y1),
        })
    
    if is_closed and len(points) >= 3 and area > page_area * 0.005:
        large_closed_paths.append({
            'index': i,
            'area': area,
            'fill': fill,
            'color': color,
            'width': width,
            'n_points': len(points),
            'closed': is_closed,
        })

print(f"\nFilled + closed paths (area > 0.1% page): {len(filled_paths)}")
for p in sorted(filled_paths, key=lambda x: x['area'], reverse=True)[:20]:
    print(f"  idx={p['index']}, area={p['area']:.0f} ({p['area']/page_area*100:.1f}%), fill={p['fill']}, color={p['color']}, pts={p['n_points']}, w={p['width']}")

print(f"\nLarge closed paths (area > 0.5% page): {len(large_closed_paths)}")
for p in sorted(large_closed_paths, key=lambda x: x['area'], reverse=True)[:20]:
    print(f"  idx={p['index']}, area={p['area']:.0f} ({p['area']/page_area*100:.1f}%), fill={p['fill']}, color={p['color']}, pts={p['n_points']}, w={p['width']}")

# Analyze line widths distribution
widths = {}
colors_dist = {}
for path in paths:
    w = round(path.get('width', 0), 2)
    widths[w] = widths.get(w, 0) + 1
    c = path.get('color')
    if c:
        c_key = tuple(round(x, 2) for x in c)
        colors_dist[c_key] = colors_dist.get(c_key, 0) + 1

print(f"\nLine width distribution:")
for w, count in sorted(widths.items(), key=lambda x: x[1], reverse=True)[:15]:
    print(f"  width={w}: {count} paths")

print(f"\nColor distribution (top 15):")
for c, count in sorted(colors_dist.items(), key=lambda x: x[1], reverse=True)[:15]:
    print(f"  color={c}: {count} paths")

# Analyze text blocks
print(f"\n=== TEXT ANALYSIS ===")
blocks = page.get_text("blocks")
print(f"Total text blocks: {len(blocks)}")
for b in blocks[:20]:
    text = b[4].strip()[:80]
    print(f"  [{b[0]:.0f},{b[1]:.0f},{b[2]:.0f},{b[3]:.0f}] '{text}'")

doc.close()
