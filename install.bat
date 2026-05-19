@echo off
echo ============================================================
echo   SpatialBrief - Installation
echo ============================================================
echo.

:: Check Python 3.13 (required for rhino3dm binary wheels)
:: rhino3dm does not have pre-built wheels for Python 3.14+
:: so we explicitly require Python 3.13 via the py launcher.
py -3.13 --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python 3.13 is not installed.
    echo         rhino3dm requires Python 3.13 ^(no pre-built wheels for 3.14+^).
    echo         Download from: https://www.python.org/downloads/release/python-3130/
    echo.
    echo         If Python 3.13 is installed but 'py -3.13' fails,
    echo         ensure the Python Launcher for Windows is installed.
    pause
    exit /b 1
)
echo [OK] Python 3.13 found

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
    echo Creating Python 3.13 virtual environment...
    py -3.13 -m venv venv
) else (
    :: Verify the existing venv uses Python 3.13
    for /f "tokens=2 delims= " %%v in ('venv\Scripts\python.exe --version 2^>^&1') do set VENV_VER=%%v
    echo Existing venv uses Python %VENV_VER%
    echo %VENV_VER% | findstr /b "3.13" >nul
    if %errorlevel% neq 0 (
        echo [WARN] Existing venv is not Python 3.13 — recreating...
        rmdir /s /q venv
        py -3.13 -m venv venv
    )
)

echo Installing Python dependencies...
call venv\Scripts\activate.bat
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
call deactivate

:: Create uploads directory (gitignored, needed at runtime)
if not exist "uploads" mkdir uploads

echo [OK] Backend dependencies installed

:: Verify rhino3dm is importable
venv\Scripts\python.exe -c "import rhino3dm; print('[OK] rhino3dm', rhino3dm.__version__, 'installed')" 2>nul
if %errorlevel% neq 0 (
    echo [WARN] rhino3dm failed to install — .3dm export will not be available
    echo        DXF export will still work.
)

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
