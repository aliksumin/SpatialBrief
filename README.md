# OMRT App

An AI-powered application for extracting constraints, rules, and semantic elements from regulatory documents and vector geometries to generate structured, Rhino/Grasshopper-ready design inputs.

## Requirements

- Python 3.10+
- Node.js 18+

### Python Dependencies

```bash
cd backend
pip install -r requirements.txt
```

Key packages:
- **google-genai** — Google Gemini API (unified SDK) for AI vision classification
- **PyMuPDF** — PDF rendering and vector extraction
- **shapely** — Geometric analysis and spatial operations
- **ezdxf** — DXF/CAD file parsing

### Vector Conversion Dependency

> [!IMPORTANT]
> This application relies on the **ODA File Converter** for parsing and converting native DWG files into the DXF format required for processing.
>
> You must have the ODA File Converter installed locally on your system for DWG ingestion to work.
> 
> Download it here: [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter)

### API Key Configuration

> [!NOTE]
> This application uses **Gemini Vision AI** to perform intelligent classification of extracted geometry — distinguishing building outlines from zones, boundaries, and artifacts using visual understanding of the drawing.
>
> The AI features are **optional** — the app falls back to rule-based classification if no API key is provided.

To enable AI-powered features:
1. Navigate to the **Settings Panel** in the app's top ribbon (⚙ icon)
2. Select **Google** as the AI Provider
3. Enter your **Gemini API Key** (get one at [Google AI Studio](https://aistudio.google.com/apikey))
4. Click **Save Settings**

The API key is stored locally in your browser's `localStorage` and is sent to the backend via a secure header — it is **never stored on the server or committed to code**.

Alternatively, you can set environment variables or create a `.env` file in `backend/`:
```env
GEMINI_API_KEY="your-gemini-key"
OPENAI_API_KEY="your-openai-key"
```

## Running the Application

### Backend
```bash
cd backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```

The app will be available at `http://localhost:5173/`.

## Architecture

### Geometry Extraction Pipeline

The extraction pipeline follows a hierarchical approach:

1. **Boundaries** — Plot boundary (outermost closed polyline)
2. **Zones** — Functional areas inside the plot (buildable, landscape, traffic, etc.) identified by filled hatching colours
3. **Buildings** — Building footprint outlines within buildable zones (unfilled, compact shapes)

When a Gemini API key is configured, the pipeline uses **AI Vision classification**:
- Renders the PDF page as a high-resolution image
- Creates an annotated overlay with numbered polygon outlines
- Sends both images to Gemini Vision for semantic understanding
- The AI classifies each polygon based on visual context, not just geometric metrics

This enables the system to understand architectural intent — e.g., recognising that a thin elongated shape is an artifact (not a building), or that an unfilled rectangle inside a coloured zone is a building footprint.
