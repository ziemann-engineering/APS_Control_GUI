"""
Startup Configuration Dialog for ZE APS Measurement GUI

This dialog allows users to:
1. Select which measurement procedure to run (auto-discovered from procedures folder)
2. Configure hardware connections for the selected procedure
3. Test connections before launching the main GUI
"""

import logging
import sys
import importlib
import inspect
from pathlib import Path
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, 
    QPushButton, QComboBox, QLineEdit, QGroupBox,
    QApplication, QFrame, QCheckBox, QMessageBox, QProgressDialog
)
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtGui import QFont, QIcon
import toml

import pyvisa

from pymeasure.experiment import Procedure

log = logging.getLogger(__name__)


def discover_procedures():
    """
    Automatically discover all procedure classes in the procedures folder.
    
    Returns:
        List of tuples: [(procedure_class, display_name), ...]
    """
    procedures = []
    procedures_dir = Path(__file__).parent / 'procedures'
    
    if not procedures_dir.exists():
        log.warning(f"Procedures directory not found: {procedures_dir}")
        return procedures
    
    log.info(f"Discovering procedures in: {procedures_dir}")
    
    # Find all Python files in procedures directory (excluding __init__ and __pycache__)
    for py_file in procedures_dir.glob('*.py'):
        if py_file.name.startswith('__'):
            continue
        
        module_name = py_file.stem
        log.debug(f"Checking module: {module_name}")
        
        try:
            # Import the module
            module = importlib.import_module(f'procedures.{module_name}')
            
            # Find all Procedure subclasses in the module
            for name, obj in inspect.getmembers(module, inspect.isclass):
                # Check if it's a Procedure subclass (but not Procedure itself)
                if issubclass(obj, Procedure) and obj is not Procedure:
                    display_name = getattr(obj, 'name', name)
                    log.info(f"Found procedure: {name} -> {display_name}")
                    procedures.append((obj, display_name))
        
        except Exception as e:
            log.error(f"Failed to load procedure from {module_name}: {e}")
    
    # Sort by display name
    procedures.sort(key=lambda x: x[1])
    log.info(f"Discovered {len(procedures)} procedures")
    
    return procedures


class ConnectionTestThread(QThread):
    """Thread for testing hardware connections without blocking the UI."""
    
    connection_result = pyqtSignal(str, bool, str)  # device_name, success, message
    
    def __init__(self, device_type, connection_params):
        super().__init__()
        self.device_type = device_type
        self.connection_params = connection_params
    
    def run(self):
        """Test connection in background thread."""
        log.info(f"Starting connection test for {self.device_type} with params: {self.connection_params}")
        try:
            if self.device_type == 'aps_controller':
                self._test_aps_connection()
            elif self.device_type == 'keithley_smu':
                self._test_keithley_connection()
            elif self.device_type == 'nge103_psu':
                self._test_nge103_connection()
            elif self.device_type == 'hmc8043_psu':
                self._test_hmc8043_connection()
            elif self.device_type == 'keysight_oscilloscope':
                self._test_oscilloscope_connection()
            else:
                log.error(f"Unknown device type requested: {self.device_type}")
                self.connection_result.emit(
                    self.device_type, False, f"Unknown device type: {self.device_type}"
                )
        except Exception as e:
            log.error(f"Connection test for {self.device_type} failed with exception: {str(e)}")
            self.connection_result.emit(
                self.device_type, False, f"Connection test failed: {str(e)}"
            )
    
    def _test_aps_connection(self):
        """Test APS controller connection."""
        # Accept multiple possible parameter names (connection, port, resource)
        port = None
        for key in ('port', 'connection', 'resource', 'address'):
            if key in self.connection_params and self.connection_params.get(key):
                port = self.connection_params.get(key)
                break
        if not port:
            port = 'COM3'
        log.info(f"Testing APS controller connection on port: {port}")
        try:
            self._ensure_project_path()
            from hardware.APS_controller import APSController
            
            aps = APSController(port)
            log.debug(f"Created APS controller instance for port {port}")
            if aps.connect():
                log.info(f"APS controller successfully connected on {port}")
                disconnect_method = getattr(aps, 'disconnect', None) or getattr(aps, 'close', None)
                if callable(disconnect_method):
                    disconnect_method()
                    log.debug(f"APS controller disconnected from {port}")
                self.connection_result.emit(
                    "APS Controller", True, f"Connected on {port}"
                )
            else:
                log.warning(f"APS controller failed to connect on {port}")
                self.connection_result.emit(
                    "APS Controller", False, "Failed to connect"
                )
        except Exception as e:
            log.error(f"APS connection test error on {port}: {e}")
            self.connection_result.emit(
                "APS Controller", False, "Connection error."
            )
    
    def _test_keithley_connection(self):
        """Test Keithley SMU connection."""
        resource = ''
        for key in ('connection', 'resource', 'address', 'port'):
            if key in self.connection_params and self.connection_params.get(key):
                resource = self.connection_params.get(key)
                break
        log.info(f"Testing Keithley SMU connection with resource: {resource}")
        if not resource:
            log.warning("Keithley connection test failed: No resource address provided")
            self.connection_result.emit(
                "Keithley SMU", False, "No resource address"
            )
            return
        
        try:
            from pymeasure.instruments.keithley import Keithley2400
            log.debug(f"Creating Keithley2400 instance for resource: {resource}")
            instrument = Keithley2400(resource)
            instrument.reset()
            instrument.use_front_terminals()
            instrument.line_frequency = 50
            instrument.wires = 2
            # Try a simple query
            idn = instrument.id
            log.info(f"Keithley SMU successfully connected: {idn}")
            instrument.shutdown()
            self.connection_result.emit(
                "Keithley SMU", True, f"Connected: {idn}"
            )
        except Exception as e:
            log.error(f"Keithley connection test error on {resource}: {e}")
            self.connection_result.emit(
                "Keithley SMU", False, "Connection error"
            )
    
    def _ensure_project_path(self):
        """Ensure the workspace root directory is on sys.path."""
        try:
            root = str(Path(__file__).resolve().parent)
            if root not in sys.path:
                sys.path.insert(0, root)
        except Exception:
            log.exception('Failed to ensure project root on sys.path')

    def _test_nge103_connection(self):
        """Test NGE103 power supply connection."""
        resource = self.connection_params.get('connection', '')
        log.info(f"Testing NGE103 PSU connection with resource: {resource}")
        if not resource:
            log.warning("NGE103 connection test failed: No resource address provided")
            self.connection_result.emit(
                "NGE103 PSU", False, "No resource address"
            )
            return
        
        try:
            self._ensure_project_path()
            from hardware.rs_nge103 import NGE100
            log.debug(f"Creating NGE100 instance for resource: {resource}")
            psu = NGE100(resource, channels=3)
            if psu.connect():
                idn = psu.ID()
                log.info(f"NGE103 PSU successfully connected: {idn}")
                disconnect_method = getattr(psu, 'disconnect', None) or getattr(psu, 'close', None)
                if callable(disconnect_method):
                    disconnect_method()
                    log.debug(f"NGE103 PSU disconnected from {resource}")
                self.connection_result.emit(
                    "NGE103 PSU", True, f"Connected: {idn}"
                )
            else:
                log.warning(f"NGE103 PSU failed to connect on {resource}")
                self.connection_result.emit(
                    "NGE103 PSU", False, "Failed to connect"
                )
        except Exception as e:
            log.error(f"NGE103 connection test error on {resource}: {e}")
            self.connection_result.emit(
                "NGE103 PSU", False, "Connection error"
            )
    
    def _test_oscilloscope_connection(self):
        """Test Keysight oscilloscope connection."""
        resource = self.connection_params.get('connection', '')
        log.info(f"Testing Keysight oscilloscope connection with resource: {resource}")
        if not resource:
            log.warning("Oscilloscope connection test failed: No resource address provided")
            self.connection_result.emit(
                "Keysight Oscilloscope", False, "No resource address"
            )
            return
        
        try:
            self._ensure_project_path()
            from hardware.keysight_dso_s import KeysightDSOSController
            log.debug(f"Creating KeysightDSOSController instance for resource: {resource}")
            scope = KeysightDSOSController(resource)
            if scope.connect():
                # Get IDN by querying the scope directly
                idn = scope.scope.query('*IDN?').strip() if scope.scope else "Unknown"
                log.info(f"Keysight oscilloscope successfully connected: {idn}")
                disconnect_method = getattr(scope, 'disconnect', None) or getattr(scope, 'close', None)
                if callable(disconnect_method):
                    disconnect_method()
                log.debug(f"Keysight oscilloscope disconnected from {resource}")
                log.debug(f"Keysight oscilloscope disconnected from {resource}")
                self.connection_result.emit(
                    "Keysight Oscilloscope", True, f"Connected: {idn}"
                )
            else:
                log.warning(f"Keysight oscilloscope failed to connect on {resource}")
                self.connection_result.emit(
                    "Keysight Oscilloscope", False, "Failed to connect"
                )
        except Exception as e:
            log.error(f"Oscilloscope connection test error on {resource}: {e}", exc_info=True)
            self.connection_result.emit(
                "Keysight Oscilloscope", False, f"Error: {str(e)}"
            )

    def _test_hmc8043_connection(self):
        """Test R&S HMC8043 PSU connection."""
        resource = self.connection_params.get('connection', '')
        log.info(f"Testing HMC8043 PSU connection with resource: {resource}")
        if not resource:
            log.warning("HMC8043 connection test failed: No resource address provided")
            self.connection_result.emit(
                "R&S HMC8043 Power Supply", False, "No resource address"
            )
            return
        
        try:
            self._ensure_project_path()
            from hardware.rs_hmc8043 import RSHMC8043Controller
            log.debug(f"Creating RSHMC8043Controller instance for resource: {resource}")
            controller = RSHMC8043Controller(resource)
            if controller.connect():
                idn = controller.psu.query('*IDN?').strip() if controller.psu else "Unknown"
                log.info(f"HMC8043 PSU successfully connected: {idn}")
                controller.disconnect()
                self.connection_result.emit(
                    "R&S HMC8043 Power Supply", True, f"Connected: {idn}"
                )
            else:
                log.warning(f"HMC8043 PSU failed to connect on {resource}")
                self.connection_result.emit(
                    "R&S HMC8043 Power Supply", False, "Failed to connect"
                )
        except Exception as e:
            log.error(f"HMC8043 connection test error on {resource}: {e}", exc_info=True)
            self.connection_result.emit(
                "R&S HMC8043 Power Supply", False, "Connection error"
            )


class HardwareConfigWidget(QGroupBox):
    """Widget for configuring hardware connections for a specific procedure."""
    
    test_requested = pyqtSignal(str, dict)  # device_type, connection_params
    
    def __init__(self, procedure_class, parent=None):
        super().__init__(f"{procedure_class.name} - Hardware Configuration", parent)
        self.procedure_class = procedure_class
        self.connection_widgets = {}
        self.status_labels = {}
        self.test_buttons = {}
        self.enable_checkboxes = {}  # Store enable/disable checkboxes
        self.aux_psu_types = ('nge103_psu', 'hmc8043_psu')
        self._setup_ui()
        active_aux = self._get_active_aux_psu_type()
        if active_aux:
            self._enforce_aux_psu_exclusivity(active_aux)
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Get hardware configuration from procedure class
        hardware_config = getattr(self.procedure_class, 'HARDWARE', {})
        
        if not hardware_config:
            # No hardware needed
            info_label = QLabel("This procedure does not require hardware connections.")
            info_label.setStyleSheet("color: #666; font-style: italic;")
            layout.addWidget(info_label)
        else:
            # Create configuration sections for each hardware device
            for device_type, device_info in hardware_config.items():
                self._add_hardware_section(layout, device_type, device_info)
        
        layout.addStretch()
    
    def _add_hardware_section(self, layout, device_type, device_info):
        """Add a hardware configuration section based on device info."""
        display_name = device_info.get('display_name', device_type)
        parameters = device_info.get('parameters', {})
        
        group = QGroupBox(display_name)
        group_layout = QGridLayout(group)
        
        row = 0
        device_widgets = {}
        
        # Add enable/disable checkbox on the first parameter row
        enable_checkbox = QCheckBox("Enable")
        enable_checkbox.setChecked(True)  # Enabled by default
        enable_checkbox.stateChanged.connect(
            lambda state: self._toggle_device_enabled(device_type, state)
        )
        self.enable_checkboxes[device_type] = enable_checkbox
        
        first_param = True
        for param_name, param_config in parameters.items():
            label = param_config.get('label', param_name)
            default = param_config.get('default', '')
            placeholder = param_config.get('placeholder', '')
            
            col = 0
            # Add Enable checkbox on first row
            if first_param:
                group_layout.addWidget(enable_checkbox, row, col)
                col += 1
            
            # Parameter label
            param_label = QLabel(f"{label}:")
            group_layout.addWidget(param_label, row, col)
            col += 1
            
            # Parameter input - use an editable combobox for connection-like fields
            is_resource_field = (
                param_name.lower() in ('connection', 'resource', 'visa', 'address', 'addr')
                or 'visa' in label.lower()
                or 'serial' in label.lower()
                or 'port' in label.lower()
            )

            if is_resource_field:
                param_combo = QComboBox()
                param_combo.setEditable(True)
                # Make per-device resource combobox wider for easier selection
                try:
                    param_combo.setMinimumWidth(300)
                except Exception:
                    pass
                if default:
                    try:
                        param_combo.addItem(str(default))
                        param_combo.setCurrentText(str(default))
                    except Exception:
                        pass
                param_combo.setEditable(True)
                param_combo.setToolTip(placeholder)
                group_layout.addWidget(param_combo, row, col)
                col += 1
                device_widgets[param_name] = param_combo
            else:
                # Regular text entry
                param_edit = QLineEdit(str(default))
                param_edit.setPlaceholderText(placeholder)
                group_layout.addWidget(param_edit, row, col)
                col += 1
                device_widgets[param_name] = param_edit
            
            # Test button and status on first row
            if first_param:
                test_btn = QPushButton("Test Connection")
                test_btn.clicked.connect(lambda: self._test_connection(device_type))
                group_layout.addWidget(test_btn, row, col)
                col += 1
                
                status_label = QLabel("Not tested")
                status_label.setStyleSheet("color: #666;")
                status_label.setMinimumWidth(170)
                group_layout.addWidget(status_label, row, col)
                
                self.test_buttons[device_type] = test_btn
                self.status_labels[device_type] = status_label
                first_param = False
            
            # device_widgets assignment handled above
            row += 1
        
        # Store references
        self.connection_widgets[device_type] = device_widgets
        
        layout.addWidget(group)

    def apply_enabled_states(self, enabled_map: dict):
        """Apply enabled/disabled state per device.

        enabled_map is expected to be: { device_type: bool, ... }
        """
        try:
            if not enabled_map:
                return
            for dev_type, enabled in enabled_map.items():
                cb = self.enable_checkboxes.get(dev_type)
                if cb is None:
                    continue
                try:
                    cb.setChecked(bool(enabled))
                    # Ensure widgets reflect the new state
                    self._toggle_device_enabled(dev_type, 2 if enabled else 0)
                except Exception:
                    log.debug(f"Failed to apply enabled state for {dev_type}", exc_info=True)
        except Exception:
            log.exception("Error applying enabled states")

    def apply_saved_connections(self, saved_map: dict):
        """Apply saved connection strings to widgets.

        saved_map is expected to be a mapping: { device_type: { param_name: value, ... }, ... }
        but in this context we expect saved_map for this procedure: { device_type: { param: value }}
        """
        try:
            if not saved_map:
                return
            for dev_type, widgets in self.connection_widgets.items():
                dev_saved = saved_map.get(dev_type, {}) if isinstance(saved_map, dict) else {}
                for pname, w in widgets.items():
                    val = dev_saved.get(pname)
                    if val is None:
                        continue
                    try:
                        if hasattr(w, 'setCurrentText'):
                            w.setCurrentText(str(val))
                        elif hasattr(w, 'setText'):
                            w.setText(str(val))
                    except Exception:
                        log.debug(f"Failed to set saved value for {dev_type}.{pname}", exc_info=True)
        except Exception:
            log.exception("Error applying saved connections")
    
    def _toggle_device_enabled(self, device_type, state):
        """Enable or disable all widgets for a specific device."""
        enabled = (state == Qt.Checked)
        self._set_device_enabled_state(device_type, enabled)
        if enabled and device_type in self.aux_psu_types:
            self._enforce_aux_psu_exclusivity(device_type)

    def _set_device_enabled_state(self, device_type, enabled):
        """Apply enable/disable state without affecting other devices."""
        if device_type in self.connection_widgets:
            for widget in self.connection_widgets[device_type].values():
                widget.setEnabled(enabled)

        if device_type in self.test_buttons:
            self.test_buttons[device_type].setEnabled(enabled)

        if device_type in self.status_labels:
            label = self.status_labels[device_type]
            if not enabled:
                label.setText("Disabled")
                label.setStyleSheet("color: #999;")
            else:
                label.setText("Not tested")
                label.setStyleSheet("color: #666;")

    def _enforce_aux_psu_exclusivity(self, active_device):
        """Disable other auxiliary PSUs when one is enabled."""
        for aux_device in self.aux_psu_types:
            if aux_device == active_device:
                continue
            checkbox = self.enable_checkboxes.get(aux_device)
            if checkbox is None or not checkbox.isChecked():
                continue
            checkbox.blockSignals(True)
            checkbox.setChecked(False)
            checkbox.blockSignals(False)
            self._set_device_enabled_state(aux_device, False)
            label = self.status_labels.get(aux_device)
            if label is not None:
                label.setText("Disabled (other PSU active)")
                label.setStyleSheet("color: #999;")
    
    def _test_connection(self, device_type):
        """Request connection test for specified device."""
        log.info(f"Connection test requested for device: {device_type}")
        widgets = self.connection_widgets.get(device_type, {})
        params = {}
        for key, widget in widgets.items():
            if hasattr(widget, 'currentText'):
                params[key] = widget.currentText()
            elif hasattr(widget, 'text'):
                params[key] = widget.text()
            elif hasattr(widget, 'value'):
                params[key] = widget.value()
            else:
                params[key] = str(widget)
        
        log.debug(f"{device_type} test parameters: {params}")

        # Disable test button and show testing status
        self.test_buttons[device_type].setEnabled(False)
        self.status_labels[device_type].setText("Testing connection...")
        self.status_labels[device_type].setStyleSheet("color: #f39c12;")
        
        # Emit test request
        self.test_requested.emit(device_type, params)
    
    def update_connection_status(self, device_name, success, message):
        """Update connection status display."""
        log.info(f"Updating connection status for {device_name}: {'SUCCESS' if success else 'FAILED'} - {message}")
        # Find the device type from the display name
        device_type = None
        if "APS" in device_name:
            device_type = 'aps_controller'
        elif "Keithley" in device_name and "SMU" in device_name:
            device_type = 'keithley_smu'
        elif "NGE103" in device_name or "NGE" in device_name:
            device_type = 'nge103_psu'
        elif "HMC8043" in device_name or "HMC" in device_name:
            device_type = 'hmc8043_psu'
        elif "Oscilloscope" in device_name or "Keysight" in device_name:
            device_type = 'keysight_oscilloscope'
        
        if device_type and device_type in self.status_labels:
            label = self.status_labels[device_type]
            button = self.test_buttons[device_type]
            
            if success:
                label.setText(f"✓ {message}")
                label.setStyleSheet("color: #27ae60;")
                log.debug(f"Set success status for {device_type}: {message}")
            else:
                label.setText(f"✗ {message}")
                label.setStyleSheet("color: #e74c3c;")
                log.debug(f"Set failure status for {device_type}: {message}")
            
            button.setEnabled(True)
            log.debug(f"Re-enabled test button for {device_type}")
        else:
            log.warning(f"Could not find device type for status update: {device_name}")
    
    def get_connection_parameters(self, only_enabled=True):
        """Get all connection parameters for this procedure.
        
        Args:
            only_enabled: If True (default), only return enabled devices.
                         If False, return all devices regardless of enabled state.
        """
        params = {}
        
        for device_type, widgets in self.connection_widgets.items():
            # Check if device is enabled
            is_enabled = True
            if device_type in self.enable_checkboxes:
                is_enabled = self.enable_checkboxes[device_type].isChecked()
                if only_enabled and not is_enabled:
                    # Device is disabled and we only want enabled, skip it
                    continue
            
            device_params = {}
            for param_name, widget in widgets.items():
                if hasattr(widget, 'currentText'):
                    device_params[param_name] = widget.currentText()
                elif hasattr(widget, 'text'):
                    device_params[param_name] = widget.text()
                elif hasattr(widget, 'value'):
                    device_params[param_name] = widget.value()
            params[device_type] = device_params
        
        return params

    def _get_active_aux_psu_type(self):
        """Return active auxiliary PSU device type if one is enabled."""
        for device_type in self.aux_psu_types:
            checkbox = self.enable_checkboxes.get(device_type)
            if checkbox and checkbox.isChecked():
                return device_type
        return None


class StartupDialog(QDialog):
    """Main startup dialog for procedure and hardware configuration."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        log.info("Initializing startup dialog")
        self.selected_procedure = None
        self.connection_parameters = {}
        self.connection_test_thread = None
        self.saved_connections = {}
        
        self.setWindowTitle("ZE / APS Measurement Setup")
        self.setWindowIcon(QIcon('ZE.png'))
        self.setModal(True)
        self._should_maximize = False
        # Default dialog size is 1024x768 unless that is the system fullscreen
        try:
            screen = QApplication.primaryScreen().size()
            if screen.width() < 1200 or screen.height() < 800:
                # If system resolution is small, maximize on show
                self._should_maximize = True
            else:
                self.resize(1024, 768)
        except Exception:
            # Fallback to fixed size if primary screen cannot be queried
            self.resize(1024, 768)
        log.debug("Startup dialog window properties set")
        
        # Center on screen after setup
        self._setup_ui()
        self._load_saved_settings()
        
        # Adjust size to content and center on screen
        #self.adjustSize()
        self._center_on_screen()
        log.info("Startup dialog initialization complete")
    
    def _setup_ui(self):
        """Setup the user interface."""
        self.main_layout = QVBoxLayout(self)
        
        # Header
        header = self._create_header()
        self.main_layout.addWidget(header)
        
        # Procedure selection
        procedure_group = QGroupBox("Select Measurement Procedure")
        procedure_group.setMinimumHeight(80)
        procedure_layout = QVBoxLayout(procedure_group)
        procedure_layout.setSpacing(10)
        
        self.procedure_combo = QComboBox()
        self.procedure_combo.setMinimumHeight(30)
        
        # Discover and populate procedures
        discovered_procedures = discover_procedures()
        for procedure_class, display_name in discovered_procedures:
            self.procedure_combo.addItem(display_name, procedure_class)
        
        if self.procedure_combo.count() == 0:
            log.error("No procedures found! Adding a placeholder.")
            self.procedure_combo.addItem("No procedures found", None)
        
        self.procedure_combo.currentTextChanged.connect(self._on_procedure_changed)
        
        procedure_layout.addWidget(self.procedure_combo)
        
        # Procedure description
        self.description_label = QLabel()
        self.description_label.setWordWrap(True)
        self.description_label.setMinimumHeight(30)
        self.description_label.setStyleSheet("color: #666; margin: 10px 0; padding: 5px;")
        procedure_layout.addWidget(self.description_label)
        
        self.main_layout.addWidget(procedure_group)
        
        # Add spacing between sections
        self.main_layout.addSpacing(10)
        
        # Hardware configuration (will be populated based on procedure)
        self.hardware_widget = None
        self.hardware_widget_index = self.main_layout.count()  # Remember position for replacement
        
        # Add some spacing before buttons
        self.main_layout.addSpacing(10)
        
    # (Removed top-level global VISA resource input - per-device editable comboboxes are used)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        button_layout.addStretch()

        # Scan button: placed at bottom with other action buttons
        self.scan_visa_btn = QPushButton("Scan VISA Resources")
        self.scan_visa_btn.setToolTip("Scan for connected VISA instruments and populate the list")
        self.scan_visa_btn.setMinimumHeight(28)
        self.scan_visa_btn.clicked.connect(self._scan_visa_resources)
        button_layout.addWidget(self.scan_visa_btn)

        self.test_all_btn = QPushButton("Test All Connections")
        self.test_all_btn.clicked.connect(self._test_all_connections)
        self.test_all_btn.setMinimumHeight(35)
        button_layout.addWidget(self.test_all_btn)

        self.start_btn = QPushButton("Start Measurement System")
        self.start_btn.setDefault(True)
        self.start_btn.clicked.connect(self.accept)
        self.start_btn.setMinimumHeight(35)
        self.start_btn.setMinimumWidth(150)
        button_layout.addWidget(self.start_btn)

        self.main_layout.addWidget(QFrame())  # Spacer
        self.main_layout.addLayout(button_layout)
        self.main_layout.setContentsMargins(10, 10, 10, 10)
        
        # Initialize with first procedure
        self._on_procedure_changed()
    
    def _create_header(self):
        """Create the dialog header."""
        frame = QFrame()
        frame.setStyleSheet("background-color: #f8f9fa; border-bottom: 1px solid #dee2e6;")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # Title and subtitle
        text_layout = QVBoxLayout()
        text_layout.setSpacing(5)
        
        title = QLabel("ZE APS Measurement System")
        title_font = QFont()
        title_font.setPointSize(16)  # Slightly smaller title
        title_font.setBold(True)
        title.setFont(title_font)
        text_layout.addWidget(title)
        
        subtitle = QLabel("Configure your measurement procedure and hardware connections")
        subtitle.setStyleSheet("color: #666;")
        text_layout.addWidget(subtitle)
        
        layout.addLayout(text_layout)
        layout.addStretch()
        
        return frame
    
    def _on_procedure_changed(self):
        """Handle procedure selection change."""
        procedure_class = self.procedure_combo.currentData()
        if procedure_class is None:
            log.warning("Procedure changed but no data available")
            return
        
        log.info(f"Procedure changed to: {procedure_class.__name__}")
        self.selected_procedure = procedure_class

        description = procedure_class.description if hasattr(procedure_class, 'description') else "No description available."
        self.description_label.setText(description)
        log.debug(f"Updated procedure description: {description}")
        
        # Remove existing hardware widget
        if self.hardware_widget:
            log.debug("Removing existing hardware configuration widget")
            self.main_layout.removeWidget(self.hardware_widget)
            self.hardware_widget.deleteLater()
        
        # Create new hardware configuration widget
        log.debug(f"Creating hardware configuration widget for {procedure_class.__name__}")
        self.hardware_widget = HardwareConfigWidget(procedure_class)
        self.hardware_widget.test_requested.connect(self._handle_test_request)
        self.main_layout.insertWidget(self.hardware_widget_index, self.hardware_widget)
        # Apply any saved connection strings for this procedure
        try:
            saved_for_proc = self.saved_connections.get(procedure_class.__name__, {}) if hasattr(self, 'saved_connections') else {}
            if saved_for_proc:
                self.hardware_widget.apply_saved_connections(saved_for_proc)
        except Exception:
            log.debug('Failed to apply saved connections', exc_info=True)
        # Apply any saved enabled/disabled states for devices
        try:
            saved_enabled_for_proc = self.saved_enabled.get(procedure_class.__name__, {}) if hasattr(self, 'saved_enabled') else {}
            if saved_enabled_for_proc:
                self.hardware_widget.apply_enabled_states(saved_enabled_for_proc)
        except Exception:
            log.debug('Failed to apply saved enabled states', exc_info=True)
        
        # Update button states
        hardware_config = getattr(procedure_class, 'HARDWARE', {})
        test_all_visible = bool(hardware_config)  # Show if there's any hardware to test
        self.test_all_btn.setVisible(test_all_visible)
        log.debug(f"Test all button visibility set to: {test_all_visible}")
    
    def _handle_test_request(self, device_type, params):
        """Handle hardware connection test request."""
        if self.connection_test_thread and self.connection_test_thread.isRunning():
            return  # Don't start multiple tests
        
        self.connection_test_thread = ConnectionTestThread(device_type, params)
        self.connection_test_thread.connection_result.connect(self._handle_test_result)
        self.connection_test_thread.start()
    
    def _handle_test_result(self, device_name, success, message):
        """Handle connection test result."""
        if self.hardware_widget:
            self.hardware_widget.update_connection_status(device_name, success, message)
        
        # Process next queued test if any
        self._process_test_queue()
    
    def _process_test_queue(self):
        """Process the next test in the queue."""
        if not hasattr(self, '_test_queue') or not self._test_queue:
            return
        
        # Check if a test is still running
        if self.connection_test_thread and self.connection_test_thread.isRunning():
            return
        
        # Get next test from queue
        device_type, device_params = self._test_queue.pop(0)
        log.debug(f"Processing queued test for {device_type}")
        self._handle_test_request(device_type, device_params)
    
    def _test_all_connections(self):
        """Test all enabled hardware connections for the selected procedure."""
        log.info("Testing all enabled hardware connections")
        if not self.hardware_widget:
            log.warning("No hardware widget available for connection testing")
            return
        
        # Get connection parameters for enabled devices only
        params = self.hardware_widget.get_connection_parameters(only_enabled=True)
        log.info(f"Found {len(params)} enabled devices to test: {list(params.keys())}")
        
        if not params:
            log.info("No enabled devices to test")
            return
        
        # Queue all tests
        self._test_queue = list(params.items())
        
        # Start processing the queue
        self._process_test_queue()

    def _scan_visa_resources(self):
        """Scan for VISA resources in a background thread and populate per-device comboboxes.

        Shows a modal progress dialog while scanning. Adds found resources as options to
        each per-device editable combobox but does not change the current selection.
        """
        log.info("Starting VISA scan (background thread)")

        progress = QProgressDialog("Scanning for VISA resources...", None, 0, 0, self)
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setWindowTitle("VISA Scan")
        progress.setCancelButtonText(None)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        progress.show()

        def on_finished(resources):
            try:
                progress.close()
                if not resources:
                    QMessageBox.information(self, "VISA Scan", "No VISA resources found.")
                    return

                hw_widget = getattr(self, 'hardware_widget', None)
                added = 0
                if hw_widget and hasattr(hw_widget, 'connection_widgets'):
                    for dev_type, widgets in hw_widget.connection_widgets.items():
                        for pname, w in widgets.items():
                            if isinstance(w, QComboBox):
                                try:
                                    # Add scanned resources without removing existing items or changing selection
                                    existing = [w.itemText(i) for i in range(w.count())]
                                    for r in resources:
                                        if r not in existing:
                                            w.addItem(r)
                                            added += 1
                                except Exception:
                                    log.debug(f"Failed to update widget {dev_type}.{pname}", exc_info=True)

                QMessageBox.information(self, "VISA Scan", f"Found {len(resources)} resource(s). Added {added} new option(s) to device lists.")
            except Exception:
                log.exception("Error handling scan results")

        def on_error(msg):
            progress.close()
            log.error(f"VISA scan failed: {msg}")
            QMessageBox.warning(self, "VISA Scan Error", f"Failed to scan VISA resources:\n{msg}")

        try:
            # Perform scan synchronously while showing modal progress dialog
            rm = pyvisa.ResourceManager()
            # Let the UI update the dialog before scanning
            QApplication.processEvents()
            resources = list(rm.list_resources())
            on_finished(resources)
        except Exception as e:
            on_error(str(e))
    
    def _center_on_screen(self):
        """Center the dialog on the screen."""
        try:
            from PyQt5.QtWidgets import QDesktopWidget
            screen = QDesktopWidget().screenGeometry()
            dialog = self.geometry()
            x = (screen.width() - dialog.width()) // 2
            y = (screen.height() - dialog.height()) // 2
            self.move(x, y)
        except Exception:
            # Fallback: let the system position the window
            pass

    def showEvent(self, event):
        """Handle show event to maximize if needed."""
        super().showEvent(event)
        if getattr(self, '_should_maximize', False):
            self.showMaximized()
            self._should_maximize = False

    def _load_saved_settings(self):
        """Load saved settings from previous session."""
        # Try to load settings.toml and restore GUI state (last procedure and saved VISA resource)
        try:
            settings_path = Path(__file__).parent / 'settings.toml'
            if not settings_path.exists():
                return
            with open(settings_path, 'r', encoding='utf-8') as f:
                settings = toml.load(f) or {}

            gui_settings = settings.get('gui', {}) if isinstance(settings, dict) else {}

            # Load saved connections and enabled states FIRST (before restoring procedure)
            self.saved_connections = gui_settings.get('connections', {}) if isinstance(gui_settings, dict) else {}
            self.saved_enabled = gui_settings.get('enabled', {}) if isinstance(gui_settings, dict) else {}

            # Restore last selected procedure if present
            # This triggers _on_procedure_changed which uses saved_connections
            last = gui_settings.get('last_procedure')
            if last:
                for idx in range(self.procedure_combo.count()):
                    data = self.procedure_combo.itemData(idx)
                    try:
                        name = getattr(data, '__name__', None)
                    except Exception:
                        name = None
                    if name == last:
                        # This will trigger _on_procedure_changed via the connected signal
                        self.procedure_combo.setCurrentIndex(idx)
                        log.info(f"Restored last selected procedure: {last}")
                        break

        except Exception:
            log.debug('Failed to load saved settings', exc_info=True)
    
    def get_configuration(self):
        """Get the complete configuration selected by the user."""
        log.info("Getting final configuration from startup dialog")
        
        # Create a procedure object instance to pass to the main application
        procedure_instance = None
        if self.selected_procedure:
            try:
                procedure_instance = self.selected_procedure()
                log.debug(f"Created procedure instance: {procedure_instance.__class__.__name__}")
            except Exception as e:
                log.error(f"Failed to create procedure instance: {e}")
                procedure_instance = None
        
        config = {
            'procedure': procedure_instance,  # Pass the procedure object instead of class
            'connection_parameters': {}
        }
        
        if self.hardware_widget:
            config['connection_parameters'] = self.hardware_widget.get_connection_parameters()
            aux_type = self.hardware_widget._get_active_aux_psu_type()
            if aux_type and aux_type in config['connection_parameters']:
                aux_params = dict(config['connection_parameters'][aux_type])
                aux_params.setdefault('type', aux_type)
                config['connection_parameters']['aux_psu'] = aux_params
        
        procedure_name = getattr(procedure_instance, 'name', 'Unknown') if procedure_instance else 'None'
        log.info(f"Final configuration: procedure={procedure_name}, "
                f"connections={list(config['connection_parameters'].keys())}")

        # Persist the last selected procedure to settings.toml so it can be restored
        try:
            settings_path = Path(__file__).parent / 'settings.toml'
            settings = {}
            if settings_path.exists():
                try:
                    with open(settings_path, 'r', encoding='utf-8') as f:
                        settings = toml.load(f) or {}
                except Exception:
                    settings = {}
            if 'gui' not in settings or not isinstance(settings['gui'], dict):
                settings['gui'] = {}
            # Save the class name of the selected procedure (e.g. RandomProcedure)
            sel = self.selected_procedure
            if sel is not None:
                settings['gui']['last_procedure'] = getattr(sel, '__name__', str(sel))
            # Save per-procedure device connection selections
            try:
                if self.hardware_widget:
                    proc_name = getattr(self.selected_procedure, '__name__', None)
                    if proc_name:
                        if 'connections' not in settings['gui'] or not isinstance(settings['gui']['connections'], dict):
                            settings['gui']['connections'] = {}
                        # Save ALL device connections (not just enabled) so they persist
                        settings['gui']['connections'][proc_name] = self.hardware_widget.get_connection_parameters(only_enabled=False)
                        # Also save enabled/disabled state for each device
                        if 'enabled' not in settings['gui'] or not isinstance(settings['gui']['enabled'], dict):
                            settings['gui']['enabled'] = {}
                        enabled_map = {}
                        for dev_type, checkbox in getattr(self.hardware_widget, 'enable_checkboxes', {}).items():
                            try:
                                enabled_map[dev_type] = bool(checkbox.isChecked())
                            except Exception:
                                enabled_map[dev_type] = True
                        settings['gui']['enabled'][proc_name] = enabled_map
            except Exception:
                log.debug('Failed to save per-device connections or enabled states', exc_info=True)
            with open(settings_path, 'w', encoding='utf-8') as f:
                toml.dump(settings, f)
            log.debug(f"Saved last_procedure = {settings['gui'].get('last_procedure')} to {settings_path}")
        except Exception:
            log.debug('Failed to save last procedure to settings.toml', exc_info=True)
        return config


def show_startup_dialog():
    """Show the startup dialog and return the configuration."""
    log.info("Starting ZE APS Measurement System startup dialog")
    app = QApplication.instance()
    if app is None:
        log.debug("Creating new QApplication instance")
        app = QApplication(sys.argv)
    else:
        log.debug("Using existing QApplication instance")
    
    dialog = StartupDialog()
    log.info("Showing startup dialog to user")
    
    if dialog.exec_() == QDialog.Accepted:
        log.info("User accepted startup dialog - proceeding with measurement system")
        return dialog.get_configuration()
    else:
        log.info("User cancelled startup dialog - measurement system will not start")
        return None


if __name__ == "__main__":
    # Test the startup dialog standalone
    config = show_startup_dialog()
    if config:
        print("Selected configuration:")
        procedure = config.get('procedure')
        if procedure:
            print(f"Procedure: {procedure.name}")
            print(f"Internal Name: {procedure.internal_name}")
            print(f"Short Name: {procedure.short_name}")
        print(f"Parameters: {config['connection_parameters']}")
    else:
        print("Cancelled")