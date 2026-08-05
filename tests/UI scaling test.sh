cd /path/to/APS_Control_GUI

whoami
pwd
git log -1 --oneline
git status --short

echo "--- launcher ---"
cat ~/.local/share/applications/ze-aps-gui.desktop

echo "--- desktop scaling environment ---"
env | sort | grep -Ei '^(QT|GDK|XDG_CURRENT_DESKTOP|WAYLAND|DISPLAY|XDG_SESSION)'

echo "--- X11 font DPI, if applicable ---"
xrdb -query 2>/dev/null | grep -i 'Xft\.dpi' || true

echo "--- Qt, normal user ---"
QT_QPA_PLATFORM=offscreen .venv/bin/python - <<'PY'
import os
from PyQt5 import QtCore, QtWidgets

print("python:", os.sys.executable)
print("PyQt:", QtCore.PYQT_VERSION_STR, "Qt:", QtCore.QT_VERSION_STR)
print("QT variables:")
for key in sorted(key for key in os.environ if key.startswith("QT_")):
    print(f"  {key}={os.environ[key]}")

QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_DisableHighDpiScaling)
QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_Use96Dpi)
app = QtWidgets.QApplication([])
screen = app.primaryScreen()
print("logical DPI:", screen.logicalDotsPerInch())
print("physical DPI:", screen.physicalDotsPerInch())
print("pixel ratio:", screen.devicePixelRatio())
print("application font:", app.font().family(), app.font().pointSizeF())
PY