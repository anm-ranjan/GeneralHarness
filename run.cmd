@echo off
REM Launch the MyHarness
REM
REM Usage:
REM   run.cmd               Start the Electron desktop shell
REM   run.cmd --dev         Start backend + Vite dev server (hot reload)
REM   run.cmd --prod        Start the web UI (serves built frontend only)
REM   run.cmd --electron    Start the Electron desktop shell
REM   run.cmd --tui         Start the Rust TUI (needs cargo; falls back to the
REM                         legacy Textual TUI when cargo is not installed)
REM   run.cmd --tui-legacy  Start the legacy Python Textual TUI explicitly
REM   run.cmd --cli         Start the CLI

setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "AGENT_DIR=%SCRIPT_DIR%backend\agent"
set "CONFIG_FILE=%AGENT_DIR%\agent_config.yaml"
set "CONFIG_READER=%SCRIPT_DIR%tui-rs\read_backend_url.py"
set "VENV_PYTHON=%SCRIPT_DIR%.venv\Scripts\python.exe"

REM Find a working Python, in order: %MYHARNESS_PYTHON%, the repo venv, system.
set "PYTHON="
if defined MYHARNESS_PYTHON (
    if exist "%MYHARNESS_PYTHON%" set "PYTHON=%MYHARNESS_PYTHON%"
)
if not defined PYTHON (
    if exist "%VENV_PYTHON%" set "PYTHON=%VENV_PYTHON%"
)
if not defined PYTHON (
    where python >nul 2>&1
    if !errorlevel! equ 0 set "PYTHON=python"
)
if not defined PYTHON (
    where python3 >nul 2>&1
    if !errorlevel! equ 0 set "PYTHON=python3"
)
if not defined PYTHON (
    echo Error: No Python interpreter found. Run 'npm run setup' or install Python 3.10+. >&2
    exit /b 1
)

if not defined MYHARNESS_WEB_DATA_DIR set "MYHARNESS_WEB_DATA_DIR=%SCRIPT_DIR%data"
if not defined MYHARNESS_WEB_STATIC_DIR set "MYHARNESS_WEB_STATIC_DIR=%SCRIPT_DIR%frontend\dist"
if not defined MYHARNESS_ELECTRON_LOG set "MYHARNESS_ELECTRON_LOG=%SCRIPT_DIR%logs\deploy_logs\electron.log"

REM Bind address precedence: MYHARNESS_WEB_HOST/PORT ^> server.* in the config ^>
REM loopback. 0.0.0.0 is never the default: the agent API is unauthenticated.
if not defined MYHARNESS_WEB_HOST (
    call :read_config server.host MYHARNESS_WEB_HOST
    if not defined MYHARNESS_WEB_HOST set "MYHARNESS_WEB_HOST=127.0.0.1"
)
if not defined MYHARNESS_WEB_PORT (
    call :read_config server.port MYHARNESS_WEB_PORT
    if not defined MYHARNESS_WEB_PORT set "MYHARNESS_WEB_PORT=8420"
)

set "LOGFILE=%SCRIPT_DIR%myharness.log"
set "PORT=%MYHARNESS_WEB_PORT%"
if not defined NPM_CACHE set "NPM_CACHE=%TEMP%\npm-cache"

if "%1"=="--tui" goto forward_launch
if "%1"=="--tui-legacy" goto forward_launch
if "%1"=="--cli" goto forward_launch

if "%1"=="--prod" (
    call :build_frontend_if_needed
    if !errorlevel! neq 0 exit /b !errorlevel!

    cd /d "%SCRIPT_DIR%backend"
    "%PYTHON%" -u web_app.py %* > "%LOGFILE%" 2>&1
    exit /b %errorlevel%
)

if "%1"=="" goto electron_launch
if "%1"=="--electron" goto electron_launch
if "%1"=="--desktop" goto electron_launch
if "%1"=="--dev" goto dev_launch

REM ── Dev mode: backend + Vite dev server with hot reload ──

:dev_launch

if not exist "%SCRIPT_DIR%frontend\node_modules" (
    echo Installing frontend dependencies...
    cd /d "%SCRIPT_DIR%frontend"
    call :install_frontend_deps
    if !errorlevel! neq 0 exit /b !errorlevel!
)

echo Starting backend...
cd /d "%SCRIPT_DIR%backend"
REM Launch with the absolute script path so :cleanup can kill only this repo's
REM backend rather than every python process running some web_app.py.
start "MyHarness Backend" /b "%PYTHON%" -u "%SCRIPT_DIR%backend\web_app.py" > "%LOGFILE%" 2>&1

REM Wait for backend to be ready (up to 30 seconds)
echo Waiting for backend on port %PORT%...
set /a TRIES=0
:wait_loop
if !TRIES! geq 30 (
    echo ERROR: Backend did not start within 30 seconds.
    goto cleanup
)
powershell -NoProfile -Command "try { $null = Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/api/health' -UseBasicParsing -TimeoutSec 1; exit 0 } catch { exit 1 }" >nul 2>&1
if !errorlevel! equ 0 goto backend_ready
set /a TRIES+=1
timeout /t 1 /nobreak >nul
goto wait_loop

:backend_ready
echo Backend ready.

echo Starting Vite dev server...
cd /d "%SCRIPT_DIR%frontend"
start "MyHarness Vite" /b npx vite --host 2>&1

echo.
echo   Backend:  http://127.0.0.1:%PORT%
echo   Frontend: http://localhost:5173  (hot reload)
echo   Press Ctrl+C to stop both.
echo.

REM Poll until backend exits (checks health every 3 seconds)
:poll_loop
timeout /t 3 /nobreak >nul
powershell -NoProfile -Command "try { $null = Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/api/health' -UseBasicParsing -TimeoutSec 2; exit 0 } catch { exit 1 }" >nul 2>&1
if !errorlevel! equ 0 goto poll_loop

:cleanup
echo.
echo Shutting down...
powershell -NoProfile -Command "$root = '%SCRIPT_DIR%backend\web_app.py'; Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -and $_.CommandLine.Contains($root) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*vite*' -and $_.Name -like 'node*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
echo Stopped.
exit /b 0

REM ── TUI / CLI launchers ─────────────────────────────────────────────
REM SHIFT does not update %*, and CALLed subroutines cannot see the script's
REM arguments, so remaining arguments are collected with a goto loop before
REM dispatching on the saved mode flag.

:forward_launch
set "LAUNCH_MODE=%~1"
shift
set "FORWARDED_ARGS="
:forward_args_loop
if "%~1"=="" goto forward_args_done
set "FORWARDED_ARGS=!FORWARDED_ARGS! ^"%~1^""
shift
goto forward_args_loop
:forward_args_done
if "%LAUNCH_MODE%"=="--tui" goto tui_launch
if "%LAUNCH_MODE%"=="--tui-legacy" goto run_legacy_tui
goto cli_launch

:tui_launch
where cargo >nul 2>&1
if !errorlevel! neq 0 (
    echo WARNING: cargo was not found on PATH. Install Rust from https://rustup.rs
    echo          ^(see README.md, "Running on Windows"^). Falling back to the
    echo          legacy Textual TUI.
    goto run_legacy_tui
)
REM Point the Rust TUI at the configured desktop.backend_url so it can reach a
REM remote backend instead of assuming the backend runs on this machine. An
REM explicit MYHARNESS_BACKEND_URL or a --backend-url flag still wins (clap
REM precedence: arg ^> env ^> default); localhost is the fallback.
call :resolve_tui_backend_url
echo Starting the Rust TUI ^(first build may take a few minutes^)...
cargo run --manifest-path "%SCRIPT_DIR%tui-rs\Cargo.toml" -- !FORWARDED_ARGS!
exit /b !errorlevel!

:resolve_tui_backend_url
if defined MYHARNESS_BACKEND_URL (
    echo Using backend URL from MYHARNESS_BACKEND_URL: %MYHARNESS_BACKEND_URL%
    goto :eof
)
if not exist "%CONFIG_FILE%" (
    echo No agent_config.yaml found; using TUI default http://127.0.0.1:8420.
    goto :eof
)
call :read_config desktop.backend_url MYHARNESS_BACKEND_URL
if defined MYHARNESS_BACKEND_URL (
    echo Using backend URL from desktop.backend_url: !MYHARNESS_BACKEND_URL!
) else (
    echo Could not read desktop.backend_url from config; using TUI default http://127.0.0.1:8420.
    echo Pass --backend-url http://HOST:8420 to override.
)
goto :eof

:run_legacy_tui
set "MYHARNESS_ORIGINAL_CWD=%CD%"
cd /d "%AGENT_DIR%"
"%PYTHON%" harness_agent.py --tui !FORWARDED_ARGS!
exit /b %errorlevel%

:cli_launch
set "MYHARNESS_ORIGINAL_CWD=%CD%"
cd /d "%AGENT_DIR%"
"%PYTHON%" harness_agent.py !FORWARDED_ARGS!
exit /b %errorlevel%

REM Read one dotted key from agent_config.yaml into a variable.
REM Usage: call :read_config ^<dotted.key^> ^<variable-name^>
REM Run the helper directly (redirect to a temp file), then read the file back
REM with for /f. Executing the helper *inside* for /f's backticks trips
REM cmd.exe's cmd /c multi-quote stripping when several quoted path arguments
REM are present and silently captures nothing; reading a plain file avoids that
REM and strips the trailing line ending cleanly.
:read_config
if not exist "%CONFIG_FILE%" goto :eof
if not exist "%CONFIG_READER%" goto :eof
set "_MH_CFG_FILE=%TEMP%\myharness_config_value.txt"
"%PYTHON%" "%CONFIG_READER%" "%CONFIG_FILE%" "%~1" > "%_MH_CFG_FILE%" 2>nul
for /f "usebackq delims=" %%V in ("%_MH_CFG_FILE%") do set "%~2=%%V"
del "%_MH_CFG_FILE%" >nul 2>&1
set "_MH_CFG_FILE="
goto :eof

:install_frontend_deps
if exist package-lock.json (
    call npm ci --cache "%NPM_CACHE%" --legacy-peer-deps
) else (
    call npm install --cache "%NPM_CACHE%" --legacy-peer-deps
)
exit /b %errorlevel%

:install_electron_deps
if exist package-lock.json (
    call npm ci --cache "%NPM_CACHE%"
) else (
    call npm install --cache "%NPM_CACHE%"
)
exit /b %errorlevel%

:electron_launch
call :build_frontend_if_needed
if !errorlevel! neq 0 exit /b !errorlevel!

if not exist "%SCRIPT_DIR%electron\node_modules" (
    echo Installing Electron dependencies...
    cd /d "%SCRIPT_DIR%electron"
    call :install_electron_deps
    if !errorlevel! neq 0 exit /b !errorlevel!
)

echo Starting Electron desktop shell...
cd /d "%SCRIPT_DIR%electron"
set "MYHARNESS_PYTHON=%PYTHON%"
call npm start
exit /b %errorlevel%

:build_frontend_if_needed
REM Build frontend if dist\ is missing or source is newer than the last build.
set "NEEDS_BUILD=false"
if exist "%SCRIPT_DIR%frontend\package.json" (
    if not exist "%SCRIPT_DIR%frontend\dist\index.html" (
        set "NEEDS_BUILD=true"
    ) else (
        powershell -NoProfile -Command "$dist = Get-Item '%SCRIPT_DIR%frontend\dist\index.html'; $src = Get-ChildItem '%SCRIPT_DIR%frontend\src' -File -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTimeUtc -gt $dist.LastWriteTimeUtc } | Select-Object -First 1; if ($src) { exit 0 } else { exit 1 }" >nul 2>&1
        if !errorlevel! equ 0 set "NEEDS_BUILD=true"
    )
)
if "!NEEDS_BUILD!"=="true" (
    echo Building frontend...
    cd /d "%SCRIPT_DIR%frontend"
    call :install_frontend_deps
    if !errorlevel! neq 0 exit /b !errorlevel!
    call npx vite build
    if !errorlevel! neq 0 exit /b !errorlevel!
)
exit /b 0
