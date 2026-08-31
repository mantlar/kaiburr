#!/bin/bash

# Get the directory of this script
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
cd "$ROOT"

echo "=================================================="
echo " Kaiburr Bootstrapper"
echo "=================================================="

# Check Python version
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] python3 is not installed or not in PATH."
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
echo "[*] Detected Python version: $PYTHON_VERSION"

# Python version check logic (3.11+)
if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    echo "[*] Python version is acceptable."
else
    echo "[ERROR] Python 3.11+ is required but not found."
    echo "Please install Python 3.11 or higher and try again."
    exit 1
fi

# Create Virtual Environment if it doesn't exist
if [ ! -f "venv/bin/activate" ]; then
    echo "[*] Creating virtual environment..."
    python3 -m venv venv
    if [ $? -ne 0 ]; then
        echo "[ERROR] Failed to create virtual environment."
        exit 1
    fi
fi

# Launch core_launcher.py using the virtual environment's python
echo "[*] Launching Kaiburr Core..."
"venv/bin/python" core_launcher.py

if [ $? -ne 0 ]; then
    echo ""
    echo "[ERROR] Kaiburr exited with an error."
fi
