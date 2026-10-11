#!/bin/bash
# Builds ThermoSolveDP.app on this Mac (one time, ~3-5 minutes). Run from Terminal with:
#     bash build_mac_app.command
set -e
cd "$(dirname "$0")"

echo "== ThermoSolveDP: building the Mac app =="
if [ ! -d .venv ]; then
    echo "Creating the Python environment..."
    python3 -m venv .venv
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.txt
fi
.venv/bin/python -m pip install --quiet -r requirements.txt "pyinstaller>=6.6"

echo "Packaging (this takes a few minutes)..."
rm -rf build dist
.venv/bin/python -m PyInstaller --noconfirm --clean --log-level WARN packaging/ThermoSolveDP.spec

echo "Checking the app works (solving all built-in examples)..."
if ! dist/ThermoSolveDP.app/Contents/MacOS/ThermoSolveDP --selftest; then
    echo "The self-test failed - please send the messages above."
    exit 1
fi

rm -rf ThermoSolveDP.app
ditto dist/ThermoSolveDP.app ThermoSolveDP.app
rm -rf build dist

# register the app (and the .tpf file type) with Finder
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f ThermoSolveDP.app >/dev/null 2>&1 || true

echo
echo "Done: ThermoSolveDP.app is in $(pwd)"
echo "Drag it into your Applications folder, then open it from Applications, Launchpad or Spotlight."
open -R ThermoSolveDP.app
