#!/bin/bash
echo "============================================================"
echo "  SpatialBrief — Starting Application"
echo "============================================================"
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Start Backend ──
echo "Starting Backend API on http://localhost:8000 ..."
cd "$SCRIPT_DIR/backend"
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!
deactivate 2>/dev/null

sleep 2

# ── Start Frontend ──
echo "Starting Frontend on http://localhost:5173 ..."
cd "$SCRIPT_DIR/frontend"
npm run dev &
FRONTEND_PID=$!

sleep 2

echo ""
echo "============================================================"
echo "  SpatialBrief is running!"
echo ""
echo "  Frontend:  http://localhost:5173"
echo "  Backend:   http://localhost:8000"
echo ""
echo "  Press Ctrl+C to stop both servers."
echo "============================================================"

# Open browser (cross-platform)
if command -v xdg-open &> /dev/null; then
    xdg-open http://localhost:5173
elif command -v open &> /dev/null; then
    open http://localhost:5173
fi

# Wait and cleanup on exit
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" SIGINT SIGTERM
wait
