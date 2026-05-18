#!/bin/bash
echo "============================================================"
echo "  SpatialBrief — Installation"
echo "============================================================"
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python 3 is not installed."
    echo "        Install it via: https://www.python.org/downloads/"
    exit 1
fi
echo "[OK] Python found: $(python3 --version)"

# Check Node.js
if ! command -v node &> /dev/null; then
    echo "[ERROR] Node.js is not installed."
    echo "        Install it via: https://nodejs.org/"
    exit 1
fi
echo "[OK] Node.js found: $(node --version)"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Backend Setup ──
echo ""
echo "── Setting up Backend ──"
cd "$SCRIPT_DIR/backend"

if [ ! -d "venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv venv
fi

echo "Installing Python dependencies..."
source venv/bin/activate
pip install -r requirements.txt --quiet
deactivate

echo "[OK] Backend dependencies installed"

# ── Frontend Setup ──
echo ""
echo "── Setting up Frontend ──"
cd "$SCRIPT_DIR/frontend"

echo "Installing Node.js dependencies..."
npm install

echo "[OK] Frontend dependencies installed"

# ── Done ──
echo ""
echo "============================================================"
echo "  Installation complete!"
echo "  Run './run.sh' to start the application."
echo "============================================================"
