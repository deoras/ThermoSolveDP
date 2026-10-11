#!/bin/bash
# ThermoSolveDP launcher for macOS (double-click in Finder, or run ./run_mac.command).
# Creates a virtual environment on first run.
cd "$(dirname "$0")" || exit 1
if [ ! -d .venv ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv || exit 1
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.txt || exit 1
fi
exec .venv/bin/python main.py "$@"
