import json, sys
sys.path.insert(0, '.')
from app.vector_ingestion.pdf_vector_extractor import extract_vectors_from_pdf

r = extract_vectors_from_pdf('uploads/Drakaterrein-A2_2022-04-26 versie 2.pdf')
print(f"Total zones: {r['extracted_objects']}")
print(f"Zone summary: {r.get('zone_summary', {})}")
print(f"Text blocks: {len(r.get('extracted_text', []))}")

# Show zone type distribution
types = {}
for v in r['vectors']:
    zt = v['zone_type']
    types.setdefault(zt, []).append(v)

for zt, items in sorted(types.items(), key=lambda x: -len(x[1])):
    areas = [i['area_pdf_units'] for i in items]
    print(f"\n  {zt}: {len(items)} zones")
    print(f"    Areas: min={min(areas):.0f}, max={max(areas):.0f}, median={sorted(areas)[len(areas)//2]:.0f}")
    print(f"    Confidence range: {min(i['confidence'] for i in items):.2f} - {max(i['confidence'] for i in items):.2f}")
    if len(items) <= 5:
        for i in items:
            print(f"      {i['id']}: area={i['area_pdf_units']:.0f}, conf={i['confidence']}, method={i['classification_method']}")
