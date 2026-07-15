#!/bin/bash
# Flatfinder launcher for macOS and Linux.
#   macOS: double-click this file in Finder (first time: right-click -> Open to
#          get past the "unidentified developer" prompt).
#   Linux: double-click, or run:  bash "Launch Flatfinder.command"
# If double-click does nothing, the file may have lost its executable bit; run:
#          chmod +x "Launch Flatfinder.command"

cd "$(dirname "$0")" || exit 1
ROOT="$(pwd)"
PY="$ROOT/.venv/bin/python"

echo ""
echo "  Flatfinder"
echo ""

if [ ! -x "$PY" ]; then
  echo "  First-time setup - about a minute, and only happens once."
  echo ""
  BOOT=""
  if command -v python3 >/dev/null 2>&1; then BOOT="python3"
  elif command -v python  >/dev/null 2>&1; then BOOT="python"
  fi
  if [ -z "$BOOT" ]; then
    echo "  Flatfinder needs Python 3.11+ (a one-time install)."
    echo "    macOS:  https://www.python.org/downloads/   (or:  brew install python)"
    echo "    Linux:  sudo apt install python3 python3-venv   (Debian/Ubuntu)"
    echo ""
    read -r -p "  Press Enter to close. " _
    exit 1
  fi
  echo "  Creating a private environment..."
  "$BOOT" -m venv "$ROOT/.venv" || { echo "  ERROR: could not create .venv"; read -r _; exit 1; }
  echo "  Installing Flatfinder (downloading a few packages)..."
  "$PY" -m pip install -q -U pip
  "$PY" -m pip install -q -e "$ROOT" || { echo "  ERROR: install failed"; read -r _; exit 1; }
  echo "  Setup complete."
  echo ""
fi

# The app creates and manages config.yaml, .env and data/ itself — in your
# FLATFINDER_HOME folder if you've set one, otherwise here. Nothing to seed.

"$PY" -m flatfinder menu
CODE=$?
if [ "$CODE" != "0" ]; then
  echo ""
  echo "  Flatfinder exited with code $CODE."
  echo "  Logs: $ROOT/data/logs/latest.txt"
  read -r -p "  Press Enter to close. " _
fi
