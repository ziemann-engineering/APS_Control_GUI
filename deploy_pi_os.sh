#!/usr/bin/env bash

set -euo pipefail

REPO_URL='https://github.com/veloyage/Python-Software.git'

# ---------------------------------------------------------------------------
# 1. Clone or update the repository
# ---------------------------------------------------------------------------
if command -v git >/dev/null 2>&1; then
  if [ -d .git ]; then
    echo 'Git repository found — pulling latest changes...'
    git pull --ff-only
  else
    echo 'Cloning repository...'
    git clone "$REPO_URL" .
  fi
else
  echo 'ERROR: git is not installed. Install it with: sudo apt-get install -y git'
  exit 1
fi

# ---------------------------------------------------------------------------
# 2. Check for sudo
# ---------------------------------------------------------------------------
if ! command -v sudo >/dev/null 2>&1; then
  echo 'ERROR: sudo is required to install system packages.'
  exit 1
fi

# ---------------------------------------------------------------------------
# 3. Python version check (requires 3.8+)
# ---------------------------------------------------------------------------
py_version=$(python3 -c 'import sys; print("{}.{}".format(*sys.version_info[:2]))')
py_minor=$(python3 -c 'import sys; print(sys.version_info[1])')
if [ "$(python3 -c 'import sys; print(sys.version_info[0])')" -lt 3 ] || [ "$py_minor" -lt 8 ]; then
  echo "ERROR: Python 3.8 or later is required (found: $py_version)."
  exit 1
fi
echo "Python $py_version detected."

# ---------------------------------------------------------------------------
# 4. Install system packages (PyQt5 and dfu-util from apt)
# ---------------------------------------------------------------------------
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip python3-pyqt5 dfu-util

# ---------------------------------------------------------------------------
# 5. Create or reuse the virtual environment
# ---------------------------------------------------------------------------
if [ ! -x ".venv/bin/python" ]; then
  echo 'Creating virtual environment...'
  python3 -m venv --system-site-packages .venv
fi

. .venv/bin/activate

# ---------------------------------------------------------------------------
# 6. Install / update Python dependencies
# ---------------------------------------------------------------------------
if [ ! -f requirements.txt ]; then
  echo 'ERROR: requirements.txt not found.'
  exit 1
fi

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# ---------------------------------------------------------------------------
# 7. Smoke-test key imports. If any fail, check if system packages are accessible in the virtual environment.
# ---------------------------------------------------------------------------
echo 'Verifying key packages...'
python -c 'import PyQt5; import pyqtgraph; import pymeasure; import pyvisa'

echo ''
echo 'Linux deployment complete.'
echo "Activate the environment with: source .venv/bin/activate"
echo "Launch with:                  python 'APS GUI.py'"