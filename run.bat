@echo off
echo ============================================================
echo   SpatialBrief — Starting Application
echo ============================================================
echo.

:: ── Start Backend ──
echo Starting Backend API on http://localhost:8000 ...
cd /d "%~dp0backend"
start "SpatialBrief Backend" cmd /k "call venv\Scripts\activate.bat && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"

:: Wait for backend to initialize
timeout /t 3 /nobreak >nul

:: ── Start Frontend ──
echo Starting Frontend on http://localhost:5173 ...
cd /d "%~dp0frontend"
start "SpatialBrief Frontend" cmd /k "npm run dev"

:: Wait for frontend to initialize
timeout /t 3 /nobreak >nul

:: ── Open browser ──
echo.
echo Opening browser...
start http://localhost:5173

echo.
echo ============================================================
echo   SpatialBrief is running!
echo.
echo   Frontend:  http://localhost:5173
echo   Backend:   http://localhost:8000
echo.
echo   Close the terminal windows to stop the servers.
echo ============================================================
pause
