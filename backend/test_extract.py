import json
import sys
sys.path.insert(0, '.')
from app.vector_ingestion.pdf_vector_extractor import extract_vectors_from_pdf

for fname in ['uploads/Drakaterrein-A2_2022-04-26 versie 2.pdf', 'uploads/Draka Terrein Hamerkwartier_Regels.pdf']:
    r = extract_vectors_from_pdf(fname)
    print(f"\n=== {fname} ===")
    print(f"Extracted objects: {r.get('extracted_objects', 0)}")
    print(f"Vectors: {len(r.get('vectors', []))}")
    print(f"Text blocks: {len(r.get('extracted_text', []))}")
    if r.get('vectors'):
        for v in r['vectors'][:3]:
            print(f"  Zone: {v.get('zone_type')}, Points: {len(v.get('points',[]))}, Closed: {v.get('closed')}")
    
    # Also show raw drawing stats
    import fitz
    doc = fitz.open(fname)
    if len(doc) > 0:
        page = doc[0]
        paths = page.get_drawings()
        print(f"\nRaw stats for page 0:")
        print(f"  Total paths: {len(paths)}")
        item_types = {}
        for p in paths:
            for item in p['items']:
                t = item[0]
                item_types[t] = item_types.get(t, 0) + 1
        print(f"  Item types: {item_types}")
        
        # Count filled vs stroked
        filled = sum(1 for p in paths if p.get('fill') is not None)
        stroked = sum(1 for p in paths if p.get('color') is not None)
        print(f"  Filled paths: {filled}, Stroked paths: {stroked}")
        
        # Page dimensions
        print(f"  Page size: {page.rect.width:.0f} x {page.rect.height:.0f}")
        
        # Show areas of all rectangles
        rects = []
        for p in paths:
            for item in p['items']:
                if item[0] == 're':
                    rect = item[1]
                    rects.append((rect.width * rect.height, rect.width, rect.height, p.get('fill'), p.get('color')))
        rects.sort(key=lambda x: x[0], reverse=True)
        print(f"  Rectangles found: {len(rects)}")
        for area, w, h, fill, color in rects[:10]:
            print(f"    Area={area:.0f}, Size={w:.0f}x{h:.0f}, fill={fill}, stroke={color}")
        
    doc.close()
