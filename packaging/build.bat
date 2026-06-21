@echo off
setlocal enabledelayedexpansion

:: ---------------------------------------------------------------------------
:: Argument parsing
:: ---------------------------------------------------------------------------
set WITH_RERANKER=0
set ONEFILE_FLAG=--onefile
set PACKAGE_MODE=onefile

if "%~1"=="" goto args_done
if /i "%~1"=="--with-reranker" set WITH_RERANKER=1
if /i "%~1"=="--with-reranker" goto args_done
if /i "%~1"=="--onedir"        set ONEFILE_FLAG=--onedir
if /i "%~1"=="--onedir"        set PACKAGE_MODE=onedir
if /i "%~1"=="--onedir"        goto args_done
if /i "%~1"=="--onefile"       set ONEFILE_FLAG=--onefile
if /i "%~1"=="--onefile"       set PACKAGE_MODE=onefile
if /i "%~1"=="--onefile"       goto args_done
if /i "%~1"=="--help"          goto show_help
if /i "%~1"=="-h"              goto show_help
echo Unknown option: %~1  (use --with-reranker, --onedir, or --help) 1>&2
exit /b 2

:show_help
echo Usage:
echo   packaging\build.bat                  - lean onefile build (NO reranker / torch)
echo   packaging\build.bat --with-reranker  - full build (bundles sentence-transformers + torch)
echo   packaging\build.bat --onedir         - folder build (near-instant startup)
echo   packaging\build.bat --help
exit /b 0

:args_done

:: ---------------------------------------------------------------------------
:: Locate project root  (this script lives in packaging\)
:: ---------------------------------------------------------------------------
cd /d "%~dp0.."
set "ROOT_DIR=%CD%"

:: Prefer the project venv's tools if present.
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if defined PYTHON set "PY=%PYTHON%"

:: ---------------------------------------------------------------------------
:: Preflight checks
:: ---------------------------------------------------------------------------
"%PY%" -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo PyInstaller is not installed in the target environment. 1>&2
    echo Install it with:  %PY% -m pip install pyinstaller   ^(or: uv pip install pyinstaller^) 1>&2
    exit /b 1
)

"%PY%" -c "import mrag" >nul 2>&1
if errorlevel 1 (
    echo mrag is not importable by %PY%. Install it first:  uv pip install -e . 1>&2
    exit /b 1
)

:: ---------------------------------------------------------------------------
:: Resolve optional collect-all packages (inline, no subroutine)
:: ---------------------------------------------------------------------------
echo ==^> Resolving bundled packages
set COLLECT_ARGS=

"%PY%" -c "import fitz" >nul 2>&1 && set "COLLECT_ARGS=!COLLECT_ARGS! --collect-all fitz" || echo   [skip] fitz
"%PY%" -c "import pymupdf" >nul 2>&1 && set "COLLECT_ARGS=!COLLECT_ARGS! --collect-all pymupdf" || echo   [skip] pymupdf
"%PY%" -c "import qdrant_client" >nul 2>&1 && set "COLLECT_ARGS=!COLLECT_ARGS! --collect-all qdrant_client" || echo   [skip] qdrant_client
"%PY%" -c "import uvicorn" >nul 2>&1 && set "COLLECT_ARGS=!COLLECT_ARGS! --collect-all uvicorn" || echo   [skip] uvicorn
"%PY%" -c "import fastapi" >nul 2>&1 && set "COLLECT_ARGS=!COLLECT_ARGS! --collect-all fastapi" || echo   [skip] fastapi
"%PY%" -c "import pydantic" >nul 2>&1 && set "COLLECT_ARGS=!COLLECT_ARGS! --collect-all pydantic" || echo   [skip] pydantic
"%PY%" -c "import apsw" >nul 2>&1 && set "COLLECT_ARGS=!COLLECT_ARGS! --collect-all apsw" || echo   [skip] apsw

:: ---------------------------------------------------------------------------
:: Reranker mode toggle
:: ---------------------------------------------------------------------------
set "EXCLUDE_ARGS=--exclude-module torch --exclude-module sentence_transformers --exclude-module transformers"

if "%WITH_RERANKER%"=="1" goto reranker_full
echo ==^> Mode: LEAN [reranker excluded]
goto reranker_done

:reranker_full
echo ==^> Mode: FULL [bundling reranker -- sentence-transformers + torch]
"%PY%" -c "import sentence_transformers" >nul 2>&1
if errorlevel 1 (
    echo --with-reranker requested but sentence-transformers is not installed. 1>&2
    echo Install it first:  uv pip install -e ".[reranker]" 1>&2
    exit /b 1
)
"%PY%" -c "import sentence_transformers" >nul 2>&1 && set "COLLECT_ARGS=!COLLECT_ARGS! --collect-all sentence_transformers" || echo   [skip] sentence_transformers
"%PY%" -c "import transformers" >nul 2>&1 && set "COLLECT_ARGS=!COLLECT_ARGS! --collect-all transformers" || echo   [skip] transformers
"%PY%" -c "import torch" >nul 2>&1 && set "COLLECT_ARGS=!COLLECT_ARGS! --collect-all torch" || echo   [skip] torch
"%PY%" -c "import tokenizers" >nul 2>&1 && set "COLLECT_ARGS=!COLLECT_ARGS! --collect-all tokenizers" || echo   [skip] tokenizers
"%PY%" -c "import safetensors" >nul 2>&1 && set "COLLECT_ARGS=!COLLECT_ARGS! --collect-all safetensors" || echo   [skip] safetensors
echo     NOTE: large binary and slower startup.
echo           ~230 MB on CPU build; ^>1 GB with CUDA-enabled torch.
set "EXCLUDE_ARGS="

:reranker_done

:: ---------------------------------------------------------------------------
:: Clean previous artifacts
:: ---------------------------------------------------------------------------
echo ==^> Cleaning build\ dist\ *.spec(generated)
if exist build ( rd /s /q build )
if exist dist  ( rd /s /q dist )
if exist mrag.spec       del /f mrag.spec
if exist mrag_entry.spec del /f mrag_entry.spec

:: ---------------------------------------------------------------------------
:: Build
:: ---------------------------------------------------------------------------
echo ==^> Running PyInstaller (%PACKAGE_MODE%)
echo     ROOT_DIR: %ROOT_DIR%
echo     ENTRY:    %ROOT_DIR%\packaging\mrag_entry.py

"%PY%" -m PyInstaller %ONEFILE_FLAG% --name mrag --clean --noconfirm --copy-metadata mrag --collect-submodules mrag --collect-data mrag !COLLECT_ARGS! %EXCLUDE_ARGS% "%ROOT_DIR%\packaging\mrag_entry.py"

if errorlevel 1 (
    echo PyInstaller failed. 1>&2
    exit /b 1
)

:: ---------------------------------------------------------------------------
:: Report
:: ---------------------------------------------------------------------------
if "%PACKAGE_MODE%"=="onedir" (
    set "BIN=dist\mrag\mrag.exe"
) else (
    set "BIN=dist\mrag.exe"
)

if not exist "%BIN%" (
    echo Build finished but %BIN% was not found -- check PyInstaller output above. 1>&2
    exit /b 1
)

if "%PACKAGE_MODE%"=="onedir" (
    echo.
    echo ==^> Build complete ^(onedir^): dist\mrag\
    echo     Launch the inner executable:  %BIN% --version
) else (
    echo.
    echo ==^> Build complete ^(onefile^): %BIN%
    echo     Smoke test:  %BIN% --version
)
exit /b 0
