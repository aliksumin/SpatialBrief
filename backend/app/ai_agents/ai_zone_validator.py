"""
AI Zone Validator — Optional Gemini-based classification validation.
Falls back gracefully if no API key is configured.

Uses the new google-genai SDK (replaces deprecated google-generativeai).
"""
import json
from typing import List, Dict, Any, Optional


def validate_zones_with_ai(
    zones: List[Dict[str, Any]],
    text_blocks: List[Dict[str, Any]],
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Optionally validate zone classifications using Gemini AI.
    If no API key, returns zones unchanged.
    
    Sends polygon metadata (areas, colors, nesting, nearby labels)
    and asks AI to validate or correct zone_type assignments.
    """
    if not api_key or not zones:
        return zones
    
    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        
        # Prepare a compact summary for the AI
        zone_summaries = []
        for z in zones[:30]:  # Limit to avoid token overflow
            zone_summaries.append({
                "id": z["id"],
                "type": z["type"],
                "zone_type": z["zone_type"],
                "area": z.get("area_pdf_units", 0),
                "color": z.get("color_hint", ""),
                "filled": z.get("filled", False),
                "stroke_width": z.get("stroke_width", 0),
                "confidence": z.get("confidence", 0),
                "method": z.get("classification_method", ""),
            })
        
        nearby_text = [t["text"] for t in text_blocks[:20]]
        
        prompt = f"""You are a regulatory zoning document analyst. I extracted polygons from a Dutch zoning PDF.
Review these zone classifications and suggest corrections if needed.

Extracted zones:
{json.dumps(zone_summaries, indent=2)}

Nearby text labels found on the drawing:
{json.dumps(nearby_text, indent=2)}

Valid zone_type values: plot_boundary, buildable_envelope, infrastructure_zone, 
landscape_zone, restriction_line, zone_boundary, parcel_line, sub_zone, 
major_boundary, no_build_zone

Return ONLY a JSON array of corrections like:
[{{"id": "zone_xxx", "zone_type": "corrected_type", "confidence": 0.85, "reason": "..."}}]

If all classifications look correct, return an empty array: []
"""
        
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[prompt],
        )
        text = response.text.strip()
        
        # Extract JSON from response
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        
        corrections = json.loads(text)
        if not isinstance(corrections, list):
            return zones
        
        # Apply corrections
        correction_map = {c["id"]: c for c in corrections if "id" in c}
        for z in zones:
            if z["id"] in correction_map:
                corr = correction_map[z["id"]]
                z["zone_type"] = corr.get("zone_type", z["zone_type"])
                z["confidence"] = corr.get("confidence", z["confidence"])
                z["classification_method"] = f"ai_corrected ({corr.get('reason', 'AI')})"
        
        return zones
        
    except Exception as e:
        # AI validation failed — return zones unchanged
        print(f"[AI Validator] Skipped: {e}")
        return zones
