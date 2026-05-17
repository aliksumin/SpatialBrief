import requests

r = requests.post(
    'http://localhost:8000/api/v1/upload',
    files=[('files', ('test.pdf', open('uploads/Drakaterrein-A2_2022-04-26 versie 2.pdf', 'rb'), 'application/pdf'))]
)
d = r.json()
print(f"Status: {d['status']}")
g = d.get('geometry', {})
print(f"Extracted objects: {g.get('extracted_objects', 0)}")
vecs = g.get('raw_vector_objects', [])
print(f"Vectors: {len(vecs)}")
print(f"Zone summary: {g.get('zone_summary', {})}")
print(f"Text blocks: {len(g.get('extracted_text', []))}")
print("First 5 zones:")
for v in vecs[:5]:
    print(f"  {v['id'][:12]}: {v['zone_type']}, type={v['type']}, closed={v['closed']}, conf={v['confidence']}, color={v.get('color_hint','')}")
