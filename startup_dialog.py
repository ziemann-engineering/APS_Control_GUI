"""
Startup Configuration Dialog for ZE APS Measurement GUI

This dialog allows users to:
1. Select which measurement procedure to run
2. Configure hardware connections for the selected procedure
3. Test connections before launching the main GUI
"""

import logging
import sys
import os
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, 
    QPushButton, QComboBox, QLineEdit, QGroupBox,
    QDoubleSpinBox,
    QApplication, QFrame
)
from PyQt5.QtCore import QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QIcon
import toml
from pathlib import Path

# Import procedure classes
from procedures.random import RandomProcedure
from procedures.HPPT import HighPowerPulseTest

log = logging.getLogger(__name__)


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
            elif self.device_type == 'keithley_2470':
                self._test_keithley_connection()
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
        port = self.connection_params.get('port', 'COM3')
        log.info(f"Testing APS controller connection on port: {port}")
        try:
            # Import and test APS controller
            sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from APS_controller import APSController
            
            aps = APSController(port)
            log.debug(f"Created APS controller instance for port {port}")
            if aps.connect():
                log.info(f"APS controller successfully connected on {port}")
                aps.disconnect()
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
        """Test Keithley 2470 connection."""
        resource = self.connection_params.get('resource', '')
        log.info(f"Testing Keithley 2470 connection with resource: {resource}")
        if not resource:
            log.warning("Keithley connection test failed: No resource address provided")
            self.connection_result.emit(
                "Keithley 2470", False, "No resource address"
            )
            return
        
        try:
            from pymeasure.instruments import keithley
            log.debug(f"Creating Keithley2470 instance for resource: {resource}")
            instrument = keithley.Keithley2470(resource)
            # Try a simple query
            idn = instrument.id
            log.info(f"Keithley 2470 successfully connected: {idn}")
            instrument.disconnect()
            log.debug(f"Keithley 2470 disconnected from {resource}")
            self.connection_result.emit(
                "Keithley 2470", True, f"Connected: {idn}"
            )
        except Exception as e:
            log.error(f"Keithley connection test error on {resource}: {e}")
            self.connection_result.emit(
                "Keithley 2470", False, "Connection error"
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
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Determine required hardware based on procedure
        if self.procedure_class == HighPowerPulseTest:
            self._add_aps_controller_config(layout)
            self._add_keithley_config(layout)
        elif self.procedure_class == RandomProcedure:
            # Random procedure doesn't need hardware
            info_label = QLabel("This procedure does not require hardware connections.")
            info_label.setStyleSheet("color: #666; font-style: italic;")
            layout.addWidget(info_label)
        
        layout.addStretch()
    
    def _add_aps_controller_config(self, layout):
        """Add APS controller configuration section."""
        group = QGroupBox("APS Controller")
        group_layout = QGridLayout(group)
        
        # Port selection
        group_layout.addWidget(QLabel("Serial Port:"), 0, 0)
        port_edit = QLineEdit("COM3")
        port_edit.setPlaceholderText("e.g., COM3, /dev/ttyUSB0")
        group_layout.addWidget(port_edit, 0, 1)
        
        # Test button
        test_btn = QPushButton("Test Connection")
        test_btn.clicked.connect(lambda: self._test_connection('aps_controller'))
        group_layout.addWidget(test_btn, 0, 2)
        
        # Status label - positioned next to test button
        status_label = QLabel("Not tested")
        status_label.setStyleSheet("color: #666;")
        status_label.setMinimumWidth(170) 
        group_layout.addWidget(status_label, 0, 3)
        
        # Store references
        self.connection_widgets['aps_controller'] = {'port': port_edit}
        self.status_labels['aps_controller'] = status_label
        self.test_buttons['aps_controller'] = test_btn
        
        layout.addWidget(group)
    
    def _add_keithley_config(self, layout):
        """Add Keithley 2470 configuration section."""
        group = QGroupBox("Keithley 2470 SMU")
        group_layout = QGridLayout(group)
        
        # Resource address
        group_layout.addWidget(QLabel("VISA Resource:"), 0, 0)
        resource_edit = QLineEdit("")
        resource_edit.setPlaceholderText("e.g., TCPIP::192.168.1.100::INSTR")
        group_layout.addWidget(resource_edit, 0, 1)
        
        # Test button
        test_btn = QPushButton("Test Connection")
        test_btn.clicked.connect(lambda: self._test_connection('keithley_2470'))
        group_layout.addWidget(test_btn, 0, 2)
        
        # Status label - positioned next to test button
        status_label = QLabel("Not tested")
        status_label.setStyleSheet("color: #666;")
        status_label.setMinimumWidth(170) 
        group_layout.addWidget(status_label, 0, 3)
        
        # Measurement voltage
        group_layout.addWidget(QLabel("Measurement Voltage (V):"), 1, 0)
        voltage_spin = QDoubleSpinBox()
        voltage_spin.setRange(0.0, 210.0)
        voltage_spin.setValue(20.0)
        voltage_spin.setSuffix(" V")
        group_layout.addWidget(voltage_spin, 1, 1)
        
        # Store references
        self.connection_widgets['keithley_2470'] = {
            'resource': resource_edit,
            'voltage': voltage_spin
        }
        self.status_labels['keithley_2470'] = status_label
        self.test_buttons['keithley_2470'] = test_btn
        
        layout.addWidget(group)
    
    def _test_connection(self, device_type):
        """Request connection test for specified device."""
        log.info(f"Connection test requested for device: {device_type}")
        widgets = self.connection_widgets.get(device_type, {})
        params = {}
        
        if device_type == 'aps_controller':
            params['port'] = widgets['port'].text()
            log.debug(f"APS controller test parameters: port={params['port']}")
        elif device_type == 'keithley_2470':
            params['resource'] = widgets['resource'].text()
            params['voltage'] = widgets['voltage'].value()
            log.debug(f"Keithley 2470 test parameters: resource={params['resource']}, voltage={params['voltage']}")
        
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
        elif "Keithley" in device_name:
            device_type = 'keithley_2470'
        
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
    
    def get_connection_parameters(self):
        """Get all connection parameters for this procedure."""
        params = {}
        
        for device_type, widgets in self.connection_widgets.items():
            device_params = {}
            for param_name, widget in widgets.items():
                if hasattr(widget, 'text'):
                    device_params[param_name] = widget.text()
                elif hasattr(widget, 'value'):
                    device_params[param_name] = widget.value()
            params[device_type] = device_params
        
        return params


class StartupDialog(QDialog):
    """Main startup dialog for procedure and hardware configuration."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        log.info("Initializing startup dialog")
        self.selected_procedure = None
        self.connection_parameters = {}
        self.connection_test_thread = None
        
        self.setWindowTitle("ZE APS Measurement Setup")
        self.setWindowIcon(QIcon('ZE.png'))
        self.setModal(True)
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
        layout = QVBoxLayout(self)
        
        # Header
        header = self._create_header()
        layout.addWidget(header)
        
        # Procedure selection
        procedure_group = QGroupBox("Select Measurement Procedure")
        procedure_group.setMinimumHeight(80)
        procedure_layout = QVBoxLayout(procedure_group)
        procedure_layout.setSpacing(10)
        
        self.procedure_combo = QComboBox()
        self.procedure_combo.setMinimumHeight(30)
        self.procedure_combo.addItem("Random Number Test", RandomProcedure)
        self.procedure_combo.addItem("High Power Pulse Test (HPPT)", HighPowerPulseTest)
        self.procedure_combo.currentTextChanged.connect(self._on_procedure_changed)
        
        procedure_layout.addWidget(self.procedure_combo)
        
        # Procedure description
        self.description_label = QLabel()
        self.description_label.setWordWrap(True)
        self.description_label.setMinimumHeight(30)
        self.description_label.setStyleSheet("color: #666; margin: 10px 0; padding: 5px;")
        procedure_layout.addWidget(self.description_label)
        
        layout.addWidget(procedure_group)
        
        # Add spacing between sections
        layout.addSpacing(10)
        
        # Hardware configuration (will be populated based on procedure)
        self.hardware_widget = None
        self.hardware_container = QFrame()
        self.hardware_container.setMinimumHeight(400)  # Ensure enough space for hardware config
        self.hardware_layout = QVBoxLayout(self.hardware_container)
        self.hardware_layout.setSpacing(10)
        layout.addWidget(self.hardware_container)
        
        # Add some spacing before buttons
        layout.addSpacing(10)
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        
        button_layout.addStretch()
        
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
        
        layout.addWidget(QFrame())  # Spacer
        layout.addLayout(button_layout)
        layout.setContentsMargins(10, 10, 10, 10)
        
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
        
        # Update description
        descriptions = {
            RandomProcedure: "A simple test procedure that generates random numbers. No hardware required.",
            HighPowerPulseTest: "High Power Pulse Test using APS controller and Keithley 2470 SMU for current measurements."
        }
        description = descriptions.get(procedure_class, "No description available.")
        self.description_label.setText(description)
        log.debug(f"Updated procedure description: {description}")
        
        # Remove existing hardware widget
        if self.hardware_widget:
            log.debug("Removing existing hardware configuration widget")
            self.hardware_layout.removeWidget(self.hardware_widget)
            self.hardware_widget.deleteLater()
        
        # Create new hardware configuration widget
        log.debug(f"Creating hardware configuration widget for {procedure_class.__name__}")
        self.hardware_widget = HardwareConfigWidget(procedure_class)
        self.hardware_widget.test_requested.connect(self._handle_test_request)
        self.hardware_layout.addWidget(self.hardware_widget)
        
        # Update button states
        test_all_visible = procedure_class != RandomProcedure
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
    
    def _test_all_connections(self):
        """Test all hardware connections for the selected procedure."""
        log.info("Testing all hardware connections")
        if not self.hardware_widget:
            log.warning("No hardware widget available for connection testing")
            return
        
        # Get all connection parameters and test each device
        params = self.hardware_widget.get_connection_parameters()
        log.info(f"Found {len(params)} devices to test: {list(params.keys())}")
        
        for device_type, device_params in params.items():
            # Small delay between tests to avoid overwhelming the UI
            delay = 100 * len([d for d in params.keys() if d <= device_type])
            log.debug(f"Scheduling connection test for {device_type} with {delay}ms delay")
            QTimer.singleShot(delay, 
                             lambda dt=device_type, dp=device_params: self._handle_test_request(dt, dp))
    
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
    
    def _load_saved_settings(self):
        """Load saved settings from previous session."""
        # Try to load last selected procedure from settings.toml
        try:
            settings_path = Path(__file__).parent / 'settings.toml'
            if not settings_path.exists():
                return
            with open(settings_path, 'r', encoding='utf-8') as f:
                settings = toml.load(f)
            last = None
            try:
                last = settings.get('gui', {}).get('last_procedure')
            except Exception:
                last = None
            if not last:
                return

            # Find a combo index whose associated class has the same __name__
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