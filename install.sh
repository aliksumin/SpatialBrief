#!/bin/bash
echo "============================================================"
echo "  SpatialBrief — Installation"
echo "============================================================"
echo ""

# Prefer Python 3.13 for rhino3dm compatibility (no wheels for 3.14+)
PYTHON_CMD=""
if command -v python3.13 &> /dev/null; then
    PYTHON_CMD="python3.13"
elif command -v python3 &> /dev/null; then
    PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    if [[ "$PY_VER" == "3.13" || "$PY_VER" < "3.14" ]]; then
        PYTHON_CMD="python3"
    else
        echo "[WARN] python3 is $PY_VER — rhino3dm has no pre-built wheels for 3.14+"
        echo "       Trying to find python3.13..."
        if command -v python3.13 &> /dev/null; then
            PYTHON_CMD="python3.13"
        else
            echo "[WARN] python3.13 not found — using python3 ($PY_VER)"
            echo "       .3dm export may not work. Install Python 3.13 for full support."
            PYTHON_CMD="python3"
        fi
    fi
fi

if [ -z "$PYTHON_CMD" ]; then
    echo "[ERROR] Python 3 is not installed."
    echo "        Install it via: https://www.python.org/downloads/"
    exit 1
fi
echo "[OK] Python found: $($PYTHON_CMD --version)"

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
    echo "Creating Python virtual environment with $PYTHON_CMD..."
    $PYTHON_CMD -m venv venv
else
    VENV_VER=$(venv/bin/python --version 2>&1 | awk '{print $2}')
    echo "Existing venv uses Python $VENV_VER"
    if [[ ! "$VENV_VER" == 3.13.* ]]; then
        echo "[WARN] Existing venv is not Python 3.13 — recreating..."
        rm -rf venv
        $PYTHON_CMD -m venv venv
    fi
fi

echo "Installing Python dependencies..."
source venv/bin/activate
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
deactivate

# Create uploads directory
mkdir -p uploads

echo "[OK] Backend dependencies installed"

# Verify rhino3dm
venv/bin/python -c "import rhino3dm; print('[OK] rhino3dm', rhino3dm.__version__, 'installed')" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "[WARN] rhino3dm failed to install — .3dm export will not be available"
    echo "       DXF export will still work."
fi

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
