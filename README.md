# SpatialBrief

An AI-powered application for extracting constraints, rules, and semantic elements from regulatory documents and vector geometries to generate structured, Rhino/Grasshopper-ready design inputs.

SpatialBrief transforms zoning PDFs and CAD drawings into a structured analytical pipeline — automatically identifying plot boundaries, building footprints, zones, regulatory constraints, building programmes, and 3D volume models.

## Requirements

- **Python** 3.10+
- **Node.js** 18+

### Quick Install

```bash
# Windows
install.bat

# macOS / Linux
chmod +x install.sh && ./install.sh
```

Or install manually:

```bash
# Backend
cd backend
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # macOS/Linux
pip install -r requirements.txt

# Frontend
cd frontend
npm install
```

### Python Dependencies

Key packages:
- **google-genai** — Google Gemini API (unified SDK) for AI vision classification and constraint extraction
- **PyMuPDF** — PDF rendering and vector extraction
- **shapely** — Geometric analysis and spatial operations
- **ezdxf** — DXF/CAD file parsing
- **fastapi** + **uvicorn** — Backend API server
- **pydantic** — Data validation and schemas

### Vector Conversion Dependency

> [!IMPORTANT]
> This application relies on the **ODA File Converter** for parsing and converting native DWG files into the DXF format required for processing.
>
> You must have the ODA File Converter installed locally on your system for DWG ingestion to work.
> 
> Download it here: [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter)

### API Key Configuration

> [!NOTE]
> This application uses **Gemini Vision AI** for intelligent classification of extracted geometry, regulatory constraint extraction, building programme analysis, and zone validation. The AI features are **optional** — the app falls back to rule-based processing if no API key is provided.

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

### Quick Start

```bash
# Windows
run.bat

# macOS / Linux
chmod +x run.sh && ./run.sh
```

### Manual Start

**Backend:**
```bash
cd backend
venv\Scripts\activate        # Windows
source venv/bin/activate     # macOS/Linux
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Frontend:**
```bash
cd frontend
npm run dev
```

The app will be available at `http://localhost:5173/`.

## Architecture

### 11-Node Analytical Pipeline

SpatialBrief processes documents through an 11-node pipeline:

| # | Node | Description |
|---|------|-------------|
| 1 | **Load Input Bundle** | Upload PDF, DWG, and DXF documents |
| 2 | **Classify Documents** | Determine file roles (zoning map vs. regulatory text) via AI |
| 3 | **Extract Metadata** | Parse text blocks, tables, and annotations |
| 4 | **Detect Units & Coordinates** | Detect drawing scale and coordinate origin |
| 5 | **Separate Drawing Areas** | Analyse plot boundaries, envelopes, and no-build zones |
| 6 | **Extract Vector Geometry** | Reconstruct clean 2D polygons from PDF paths and CAD entities |
| 7 | **Extract Constraints** | AI-powered setback, height, density & parking rule extraction with geometry generation |
| 8 | **Extract Programme** | GFA, uses, floor counts per building — AI fills gaps from regional defaults |
| 9 | **Generate Volumes** | Floor-by-floor 3D massing with plinths and underground parking |
| 10 | **Validation Report** | Summary and cross-validation of all pipeline outputs |
| 11 | **Export Package** | Rhino / Grasshopper handoff |

### Geometry Extraction Pipeline

The extraction pipeline follows a hierarchical approach:

1. **Boundaries** — Plot boundary (outermost closed polyline)
2. **Zones** — Functional areas inside the plot (buildable, landscape, traffic, etc.) identified by filled hatching colours
3. **Buildings** — Building footprint outlines within buildable zones (unfilled, compact shapes)

When a Gemini API key is configured, the pipeline uses **AI Vision classification**:
- Renders the PDF page as a high-resolution image (300 DPI)
- Creates an annotated overlay with numbered, colour-coded polygon outlines
- Generates zoomed detail crops of dense building clusters (400 DPI)
- Sends images + polygon metadata to Gemini Vision for semantic understanding
- The AI classifies each polygon based on visual context, not just geometric metrics

This enables the system to understand architectural intent — e.g., recognising that a thin elongated shape is an artifact (not a building), or that an unfilled rectangle inside a coloured zone is a building footprint.

### AI Agent Modules

| Module | Purpose |
|--------|---------|
| `ai_vision_classifier` | Two-pass Gemini Vision classification of zones and buildings |
| `ai_zone_validator` | Post-extraction AI validation of zone type assignments |
| `constraint_extractor` | Regex + AI extraction of regulatory constraints with setback geometry generation |
| `programme_extractor` | Building programme extraction (GFA, uses, floors) with AI gap-fill |
| `volume_generator` | Floor-by-floor 3D volume extrusion from footprints + programme |

### Project Structure

```
SpatialBrief/
├── backend/
│   ├── app/
│   │   ├── main.py                    # FastAPI app entry point
│   │   ├── config.py                  # Environment settings
│   │   ├── routers/
│   │   │   └── upload.py              # API endpoints (/upload, /process)
│   │   ├── ai_agents/                 # AI-powered analysis modules
│   │   │   ├── ai_zone_validator.py
│   │   │   ├── constraint_extractor.py
│   │   │   ├── programme_extractor.py
│   │   │   └── volume_generator.py
│   │   ├── vector_ingestion/          # Geometry extraction pipeline
│   │   │   ├── pdf_vector_extractor.py
│   │   │   ├── ai_vision_classifier.py
│   │   │   ├── hierarchy_builder.py
│   │   │   ├── cad_extractor.py
│   │   │   └── zone_classifier.py
│   │   ├── schemas/                   # Pydantic data models
│   │   └── semantic_mapping/          # Semantic layer analysis
│   ├── requirements.txt
│   └── uploads/                       # Uploaded files (gitignored)
├── frontend/
│   ├── src/
│   │   ├── App.tsx                    # Main application
│   │   ├── components/
│   │   │   ├── layout/TopRibbon.tsx   # App header with project controls
│   │   │   ├── workflow/NodeGraph.tsx  # Pipeline node graph sidebar
│   │   │   ├── viewer/                # 3D viewport (Three.js/R3F)
│   │   │   ├── settings/             # API & extraction settings panels
│   │   │   └── vectorReview/         # Geometry review tools
│   │   ├── index.css                  # Design system & global styles
│   │   └── App.css                    # Component styles
│   ├── package.json
│   └── index.html
├── install.bat / install.sh           # One-command installation
├── run.bat / run.sh                   # One-command startup
├── .gitignore
├── LICENSE
└── README.md
```

### Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 19 + TypeScript + Vite |
| 3D Viewport | Three.js + React Three Fiber + Drei |
| Backend | FastAPI + Uvicorn (Python) |
| AI | Google Gemini (vision + text) via `google-genai` SDK |
| Geometry | Shapely + PyMuPDF + ezdxf |
| Styling | Vanilla CSS with dark-mode design system |

## License

See [LICENSE](LICENSE) for details.
