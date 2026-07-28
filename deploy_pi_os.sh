#!/usr/bin/env bash

set -euo pipefail

REPO_URL='https://github.com/veloyage/Python-Software.git'
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
MODE=${1:-setup}

usage() {
  cat <<'EOF'
Usage: ./deploy_pi_os.sh [setup|update]

  setup   Prepare a fresh downloaded folder or installation, install Linux and
          USB prerequisites, update project files when Git is available, and
          install Python dependencies. This is the default.
  update  Update an existing Git installation and its Python dependencies only.
EOF
}

case "$MODE" in
  setup|update) ;;
  -h|--help|help)
    usage
    exit 0
    ;;
  *)
    echo "ERROR: Unknown mode '$MODE'."
    usage >&2
    exit 2
    ;;
esac

cd "$SCRIPT_DIR"

# ---------------------------------------------------------------------------
# 1. Identify whether the project source can be updated
# ---------------------------------------------------------------------------
if [ -d .git ]; then
  if ! command -v git >/dev/null 2>&1; then
    if [ "$MODE" = 'update' ]; then
      echo 'ERROR: This installation is a Git checkout, but git is not installed.'
      exit 1
    fi
    echo 'Git checkout found; setup mode will install git before updating it.'
  fi
  HAS_GIT_CHECKOUT=true
elif [ "$MODE" = 'update' ]; then
  echo 'ERROR: Update mode requires an existing Git installation (.git directory not found).'
  exit 1
else
  HAS_GIT_CHECKOUT=false
  echo 'No Git metadata found; using the downloaded project files in this folder.'
fi

# ---------------------------------------------------------------------------
# 2. Check for sudo and install initial system prerequisites
# ---------------------------------------------------------------------------
if [ "$MODE" = 'setup' ]; then
  if ! command -v sudo >/dev/null 2>&1; then
    echo 'ERROR: sudo is required for initial system setup.'
    exit 1
  fi

  sudo apt-get update
  sudo apt-get install -y \
    git \
    python3-venv \
    python3-pip \
    python3-pyqt5 \
    dfu-util \
    libusb-1.0-0 \
    libusb-1.0-0-dev \
    python3-usb

  # Allow the logged-in user to open USB instruments through libusb/PyUSB.
  sudo install -d -m 0755 /etc/udev/rules.d
  printf '%s\n' 'SUBSYSTEM=="usb", MODE="0666"' | sudo tee /etc/udev/rules.d/99-aps-usb-access.rules >/dev/null
  sudo udevadm control --reload-rules
  sudo udevadm trigger --subsystem-match=usb

  echo "USB access rule installed for $USER. Reconnect USB instruments or reboot before using them."

  if [ "$HAS_GIT_CHECKOUT" = false ]; then
    echo 'Checking the downloaded project files against the repository...'
    git init
    git remote add origin "$REPO_URL"
    if git fetch origin main; then
      git reset --hard origin/main
    else
      echo 'WARNING: Could not reach the repository; continuing with the downloaded project files.'
    fi
  else
    echo 'Git repository found - pulling latest changes...'
    git pull --ff-only origin main
  fi
elif [ "$HAS_GIT_CHECKOUT" = true ]; then
  echo 'Git repository found - pulling latest changes...'
  git pull --ff-only origin main
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
# 4. Create or reuse the virtual environment
# ---------------------------------------------------------------------------
if [ ! -x ".venv/bin/python" ]; then
  echo 'Creating virtual environment...'
  python3 -m venv --system-site-packages .venv
fi

. .venv/bin/activate

# ---------------------------------------------------------------------------
# 5. Install / update Python dependencies
# ---------------------------------------------------------------------------
if [ ! -f requirements.txt ]; then
  echo 'ERROR: requirements.txt not found.'
  exit 1
fi

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# ---------------------------------------------------------------------------
# 6. Install a per-user desktop launcher so the Linux panel can identify the app
# ---------------------------------------------------------------------------
INSTALL_USER=${SUDO_USER:-$USER}
INSTALL_HOME=$(getent passwd "$INSTALL_USER" | cut -d: -f6)
if [ -n "$INSTALL_HOME" ] && [ -d "$INSTALL_HOME" ]; then
  APPLICATIONS_DIR="$INSTALL_HOME/.local/share/applications"
  mkdir -p "$APPLICATIONS_DIR"
  cat > "$APPLICATIONS_DIR/ze-aps-gui.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=ZE APS Measurement GUI
Comment=ZE automated power semiconductor measurement system
Exec=env QT_AUTO_SCREEN_SCALE_FACTOR=0 QT_ENABLE_HIGHDPI_SCALING=0 QT_SCALE_FACTOR=1 QT_FONT_DPI=96 "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/APS GUI.py"
Icon=$SCRIPT_DIR/ZE.png
Terminal=false
Categories=Science;Engineering;
StartupWMClass=ze-aps-gui
EOF
  chmod 0644 "$APPLICATIONS_DIR/ze-aps-gui.desktop"
  echo "Desktop launcher installed for $INSTALL_USER."
else
  echo "WARNING: Could not determine a home directory for $INSTALL_USER; desktop launcher was not installed."
fi

# ---------------------------------------------------------------------------
# 7. Smoke-test key imports. If any fail, check if system packages are accessible in the virtual environment.
# ---------------------------------------------------------------------------
echo 'Verifying key packages...'
python -c 'import PyQt5; import pyqtgraph; import pymeasure; import pyvisa'

echo ''
echo "Linux $MODE complete."
echo "Activate the environment with: source .venv/bin/activate"
echo "Launch with:                  python 'APS GUI.py'"