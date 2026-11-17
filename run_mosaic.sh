#!/usr/bin/env bash
set -e

# Move to the directory containing this script
cd "$(dirname "$0")"

if [ ! -f "pyproject.toml" ]; then
    echo "ERROR: pyproject.toml not found next to this script."
    echo "Extract the whole archive first, then run ./run_mosaic.sh from it."
    exit 1
fi

# ---- Keep the environment out of the project folder --------------------------
# The venv is thousands of files; outside the Mosaic folder it never syncs.
export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$HOME/.local/share/mosaic/venv}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$HOME/.local/share/mosaic/uv-cache}"
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"

# ---- Locate or install uv ----------------------------------------------------
if ! command -v uv &>/dev/null; then
    if [ -x "$HOME/.local/bin/uv" ]; then
        export PATH="$HOME/.local/bin:$PATH"
    else
        echo "uv not found. Installing now (one-time setup, requires internet)..."
        echo ""
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
        if ! command -v uv &>/dev/null; then
            echo ""
            echo "ERROR: uv could not be downloaded or is not reachable."
            echo "Download it manually from https://github.com/astral-sh/uv/releases,"
            echo "place it at \$HOME/.local/bin/uv, then run: ./run_mosaic.sh"
            exit 1
        fi
    fi
fi

echo "Starting Mosaic..."
if [ ! -d "$UV_PROJECT_ENVIRONMENT" ]; then
    echo "First launch: installing dependencies (2-3 minutes, about 500 MB)."
    echo "Subsequent launches are instant."
fi
echo ""

# ---- Keep the desktop responsive on a small server --------------------------
# The search is CPU-bound. Left alone, Numba takes every core and the window
# competes with it for time, which over remote desktop reads as a frozen app.
# Leave one core for the interface (never fewer than one worker), and run at a
# slightly lower priority so the desktop session wins any tie. Both can be
# overridden: set NUMBA_NUM_THREADS or MOSAIC_NICE before launching.
if [ -z "${NUMBA_NUM_THREADS:-}" ]; then
    cores=$(nproc 2>/dev/null || echo 2)
    workers=$((cores - 1))
    [ "$workers" -lt 1 ] && workers=1
    export NUMBA_NUM_THREADS="$workers"
fi
niceness="${MOSAIC_NICE:-5}"

# "|| ec=$?" stops set -e from aborting before the error message below.
ec=0
if command -v nice &>/dev/null; then
    nice -n "$niceness" uv run python -m mosaic || ec=$?
else
    uv run python -m mosaic || ec=$?
fi

if [ $ec -ne 0 ]; then
    echo ""
    echo "Mosaic exited with an error. The details are in the messages above."
fi
exit $ec
