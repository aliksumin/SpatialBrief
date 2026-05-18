@echo off
echo ============================================================
echo   SpatialBrief - Installation
echo ============================================================
echo.

:: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    echo         Download from: https://www.python.org/downloads/
    pause
    exit /b 1
)
echo [OK] Python found

:: Check Node.js
node --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Node.js is not installed or not in PATH.
    echo         Download from: https://nodejs.org/
    pause
    exit /b 1
)
echo [OK] Node.js found

:: -- Backend Setup --
echo.
echo -- Setting up Backend --
cd /d "%~dp0backend"

if not exist "venv" (
    echo Creating Python virtual environment...
    python -m venv venv
)

echo Installing Python dependencies...
call venv\Scripts\activate.bat
pip install -r requirements.txt --quiet
call deactivate

:: Create uploads directory (gitignored, needed at runtime)
if not exist "uploads" mkdir uploads

echo [OK] Backend dependencies installed

:: -- Frontend Setup --
echo.
echo -- Setting up Frontend --
cd /d "%~dp0frontend"

echo Installing Node.js dependencies...
call npm install

echo [OK] Frontend dependencies installed

:: -- Done --
echo.
echo ============================================================
echo   Installation complete!
echo   Run 'run.bat' to start the application.
echo ============================================================
pause
