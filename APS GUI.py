# ZE / APS Measurement GUI
# main file
# TO TEST: implement oscilloscope control (trigger, timebase, channels, data saving)
# TO TEST: implement all APS control functions (from C code)
# DONE: new setup window to select test and connect devices

import logging
import os
import json
import base64
import sys
import toml
from pathlib import Path

# Keep Qt's scale independent of the desktop account's inherited Qt settings.
os.environ['QT_AUTO_SCREEN_SCALE_FACTOR'] = '0'
os.environ['QT_ENABLE_HIGHDPI_SCALING'] = '0'
os.environ['QT_SCALE_FACTOR'] = os.environ.get('APS_QT_SCALE_FACTOR', '1')
os.environ['QT_FONT_DPI'] = '96'

from pymeasure.display.Qt import QtWidgets, QtCore
from pymeasure.display.windows.managed_dock_window import ManagedDockWindow
from pymeasure.experiment import unique_filename

from PyQt5.QtGui import QFont, QIcon
from datetime import datetime

from procedures.random import RandomProcedure

log = logging.getLogger(__name__)
APPLICATION_ICON = Path(__file__).resolve().with_name('ZE.png')
# Ensure logs directory exists and configure file-based logging when possible.
logs_dir = Path('./logs')
log_filename = logs_dir / f"{datetime.now():%Y-%m-%d_%H-%M-%S}.log"

try:
    # Try to create the logs directory (no-op if it already exists)
    logs_dir.mkdir(parents=True, exist_ok=True)
    # Test that we can open the log file for appending. This will raise if the
    # directory is not writable or the path is invalid.
    with open(log_filename, 'a', encoding='utf-8'):
        pass
    # If we get here, file logging is possible.
    logging.basicConfig(level=logging.INFO, filename=str(log_filename), encoding='utf-8', filemode='a', format='%(asctime)s - %(levelname)s - %(message)s')
except Exception:
    # Fall back to console logging (stderr) if any part of file setup fails.
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class SettingsManager:
    """TOML-based settings manager for persistent application configuration."""
    
    def __init__(self, settings_file='settings.toml'):
        self.settings_file = Path(settings_file)
        self._settings = {}
        self._load_settings()
    
    def _load_settings(self):
        """Load settings from TOML file."""
        try:
            if self.settings_file.exists():
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    self._settings = toml.load(f)
            else:
                # Create default settings structure
                self._settings = {
                    'gui': {'last_procedure': 'RandomProcedure'},
                    'window': {'geometry': '', 'state': ''},
                    'directories': {},
                    'docks': {}
                }
                self._save_settings()
        except Exception as e:
            log.error(f'Failed to load settings from {self.settings_file}: {e}')
            # Fallback to default settings
            self._settings = {
                'gui': {'last_procedure': 'RandomProcedure'},
                'window': {'geometry': '', 'state': ''},
                'directories': {},
                'docks': {}
            }
    
    def _save_settings(self):
        """Save current settings to TOML file."""
        try:
            # Ensure directory exists
            self.settings_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                toml.dump(self._settings, f)
        except Exception as e:
            log.error(f'Failed to save settings to {self.settings_file}: {e}')
    
    def get_value(self, key_path, default=None):
        """Get a value using dot notation (e.g., 'gui.last_procedure')."""
        try:
            keys = key_path.split('.')
            value = self._settings
            for key in keys:
                if isinstance(value, dict) and key in value:
                    value = value[key]
                else:
                    return default
            return value
        except Exception:
            return default
    
    def set_value(self, key_path, value):
        """Set a value using dot notation and save immediately."""
        try:
            keys = key_path.split('.')
            current = self._settings
            
            # Navigate to the parent dictionary
            for key in keys[:-1]:
                if key not in current:
                    current[key] = {}
                current = current[key]
            
            # Set the final value
            current[keys[-1]] = value
            self._save_settings()
        except Exception as e:
            log.error(f'Failed to set value {key_path}={value}: {e}')
    
    def get_byte_array(self, key_path, default=None):
        """Get a base64 encoded byte array and decode it."""
        try:
            encoded = self.get_value(key_path, '')
            if encoded:
                return QtCore.QByteArray(base64.b64decode(encoded.encode('ascii')))
            return default
        except Exception:
            return default
    
    def set_byte_array(self, key_path, byte_array):
        """Encode a QByteArray as base64 and store it."""
        try:
            if byte_array is not None:
                encoded = base64.b64encode(bytes(byte_array.data())).decode('ascii')
                self.set_value(key_path, encoded)
        except Exception as e:
            log.error(f'Failed to set byte array {key_path}: {e}')


class MainWindow(ManagedDockWindow):

    def __init__(self, startup_config=None):

        # Initialize settings manager
        self.settings_manager = SettingsManager()
        
        # Store startup configuration
        self.startup_config = startup_config or {}
        
        # Get procedure object from startup
        self.procedure = self.startup_config.get('procedure')
        if self.procedure is None:
            # Fallback to RandomProcedure if no procedure provided
            self.procedure = RandomProcedure()
            log.warning("No procedure provided in startup config, using RandomProcedure as fallback")
        
        procedure_class = self.procedure.__class__
        log.info(f"Using procedure: {self.procedure.name} (internal: {self.procedure.internal_name}, short: {self.procedure.short_name})")
        startup_connections = self.startup_config.get('connection_parameters', {})

        # If GSS procedure: update ListParameter choices BEFORE super().__init__ creates
        # the INPUTS form, so the discovered serial-number dropdowns are already populated.
        discovered = startup_connections.get('gss_discovered_devices', [])
        if discovered:
            try:
                from procedures.GSS import update_device_choices
                update_device_choices(discovered)
                log.info(f'Updated GSS device choices: {len(discovered)} device(s)')
            except Exception:
                log.debug('Could not update GSS device choices', exc_info=True)

        try:
            # Set on CLASS so new instances created by pymeasure also get it
            procedure_class._startup_connection_parameters = startup_connections
            self.procedure.connection_parameters = startup_connections
        except Exception:
            log.debug('Failed to attach connection parameters to procedure', exc_info=True)
        
        # Get GUI configuration from procedure class if available, otherwise use defaults
        inputs_list = getattr(procedure_class, 'INPUTS', [])
        displays = getattr(procedure_class, 'DISPLAYS', inputs_list)
        x_axis_attr = getattr(procedure_class, 'X_AXIS', None)
        y_axis_attr = getattr(procedure_class, 'Y_AXIS', [])
        
        # Convert to list format if needed
        x_axis = [x_axis_attr] if isinstance(x_axis_attr, str) else (x_axis_attr or [])
        y_axis = y_axis_attr if isinstance(y_axis_attr, list) else [y_axis_attr]
        
        # Preserve the inputs-list for compatibility
        self._inputs_list = inputs_list
        self._current_inputs_list = list(inputs_list)
        
        super().__init__(
            procedure_class=procedure_class,
            inputs=inputs_list,
            displays=displays,
            x_axis=x_axis,
            y_axis=y_axis
        )

        if getattr(procedure_class, 'internal_name', '') == 'Gate_Switching_Stress':
            from gss_parallel_manager import GSSParallelManager

            old_manager = self.manager
            self.manager = GSSParallelManager(
                self.widget_list,
                self.browser,
                log_level=self.log_level,
                parent=self,
            )
            self.manager.abort_returned.connect(self.abort_returned)
            self.manager.queued.connect(self.queued)
            self.manager.running.connect(self.running)
            self.manager.finished.connect(self.finished)
            self.manager.log.connect(self.log.handle)
            old_manager.deleteLater()
            log.info('GSS parallel experiment manager enabled')

        # Re-layout the parameter inputs so each label sits inline with its
        # entry field (instead of stacked above it), which is much more
        # compact for procedures with many parameters.
        try:
            self._make_inputs_compact()
        except Exception:
            log.debug('Failed to compact input layout', exc_info=True)

        # Clarify which of the several "directory" fields does what, since
        # the toolbar's Results Directory is otherwise easily confused with
        # the procedure's own Local Log / NAS Backup directory parameters.
        try:
            self._clarify_directory_labels()
        except Exception:
            log.debug('Failed to clarify directory labels', exc_info=True)

        # Set objectName for all QDockWidgets to avoid saveState warnings
        try:
            from PyQt5.QtWidgets import QDockWidget
            for dock in self.findChildren(QDockWidget):
                if not dock.objectName():
                    # Use the window title as the object name, or a generic name
                    title = dock.windowTitle()
                    if title:
                        # Clean the title to make it a valid object name
                        obj_name = title.replace(' ', '_').replace(';', '')
                        dock.setObjectName(obj_name)
                    else:
                        # Fallback to a generic name
                        dock.setObjectName(f'DockWidget_{id(dock)}')
        except Exception:
            log.debug('Failed to set QDockWidget objectNames', exc_info=True)

        self.filename = f'{datetime.now():%Y-%m-%d_%H-%M-%S}' # self.procedure.filename   # Sets default filename
        self.store_measurement = True                             # Controls the 'Save data' toggle
        self.file_input.extensions = ["csv", "txt", "data"]         # Sets recognized extensions, first entry is the default extension
        self.file_input.filename_fixed = False                      # Controls whether the filename-field is frozen (but still displayed)

        self.setWindowTitle('ZE APS Measurement GUI')
        self.setWindowIcon(QIcon(str(APPLICATION_ICON)))

        # Delay plot customization to ensure widgets are created
        QtCore.QTimer.singleShot(100, self._customize_plots)

        # Pre-populate connection parameters from startup configuration
        self._populate_connection_parameters()

        # Update window title to show selected procedure
        self.setWindowTitle(f'ZE APS Measurement GUI - {self.procedure.name}')
        
        # Apply saved dock layout and per-procedure data directory
        try:
            self._restore_layout()
        except Exception:
            log.debug('Failed to restore window layout', exc_info=True)
        try:
            self._apply_procedure_directory()
            log.debug(f'Current directory after application: {getattr(self, "directory", "NOT SET")}')
        except Exception:
            log.error('Failed to apply initial procedure directory', exc_info=True)
        # Persist directory changes whenever user edits the directory input
        try:
            self._attach_directory_input()
        except Exception:
            log.debug('Failed to hook directory input changes', exc_info=True)

        # Periodically persist dock layouts and directory as a fallback
        try:
            self._last_saved_dir = None
            self._last_saved_layout_payload = None
            self._persist_timer = QtCore.QTimer(self)
            self._persist_timer.setInterval(2000)
            self._persist_timer.timeout.connect(self._periodic_persist)
            self._persist_timer.start()
        except Exception:
            log.debug('Failed to start persistence timer', exc_info=True)

        # Restore dock layout for current (initial) procedure
        try:
            dock = self._get_current_dock()
            if dock is not None:
                self._restore_dock_layout_for_proc(dock)
        except Exception:
            log.debug('Failed to restore initial dock layout', exc_info=True)

        # Initialize AUX PSU Ch1 immediately (Ch2/Ch3 enabled on measurement start)
        QtCore.QTimer.singleShot(500, self._initialize_aux_psu)
        
        # Restore saved input parameters after UI is fully ready
        QtCore.QTimer.singleShot(200, self._restore_input_parameters)

    def new_curve(self, wdg, results, color=None, **kwargs):
        """Create one GSS plot curve for each DUT instead of one mixed trace."""
        if (
            getattr(results.procedure, 'internal_name', '') == 'Gate_Switching_Stress'
            and wdg is self.plot_widget
        ):
            from gss_plotting import new_dut_curves
            return new_dut_curves(wdg, results, results.procedure.num_duts)
        return super().new_curve(wdg, results, color=color, **kwargs)

    def _make_inputs_compact(self):
        """Re-layout pymeasure's auto-generated InputsWidget so each label and
        its entry field sit on the same row ("Label: [field]") instead of the
        default stacked layout (label above, field below). This roughly
        halves the vertical space used by the parameter panel, which matters
        for procedures with many inputs on smaller screens.

        Directory-type parameters (any input whose name contains
        "directory") are kept in the original stacked (label above, field
        below) layout -- since paths can be long -- and are grouped together
        at the bottom of the panel, after all other (compact) inputs.

        If the procedure class defines ``INPUT_SECTIONS`` (a dict mapping an
        input name to a section title), a small bold headline is inserted
        above that input's row, e.g. to group inputs under their associated
        hardware selector.
        """
        inputs = getattr(self, 'inputs', None)
        if inputs is None or getattr(inputs, '_compact_layout_applied', False):
            return  # nothing to do, or already compacted

        old_layout = inputs.layout()
        if old_layout is None:
            return

        names = list(getattr(inputs, '_inputs', self._inputs_list))
        labels = getattr(inputs, 'labels', {})

        directory_names = [n for n in names if 'directory' in n.lower()]
        compact_names = [n for n in names if n not in directory_names]

        # Detach every label/field from the old (vertical) layout without
        # destroying the widgets themselves, then discard the empty layout.
        for name in names:
            widget = getattr(inputs, name, None)
            if widget is not None:
                old_layout.removeWidget(widget)
            label = labels.get(name)
            if label is not None:
                old_layout.removeWidget(label)
        QtWidgets.QWidget().setLayout(old_layout)  # orphans + frees the now-empty layout

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        form.setFormAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(3)

        # Optional per-procedure section headlines: a dict of
        # {input_name: 'Section Title'} shown above that input's row, e.g.
        # to group inputs under their associated hardware selector.
        procedure_class = getattr(inputs, '_procedure_class', None)
        sections = getattr(procedure_class, 'INPUT_SECTIONS', {}) or {}

        first_row = True
        for name in compact_names:
            widget = getattr(inputs, name, None)
            if widget is None:
                continue
            section_title = sections.get(name)
            if section_title:
                headline = QtWidgets.QLabel(section_title)
                headline.setStyleSheet(
                    'font-weight: bold;' if first_row else 'font-weight: bold; margin-top: 8px;'
                )
                form.addRow(headline)
            label = labels.get(name)
            if label is not None:
                form.addRow(label, widget)
            else:
                form.addRow(widget)
            first_row = False

        outer = QtWidgets.QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(form)

        if directory_names:
            outer.addSpacing(8)
            for name in directory_names:
                widget = getattr(inputs, name, None)
                if widget is None:
                    continue
                label = labels.get(name)
                if label is not None:
                    outer.addWidget(label)
                outer.addWidget(widget)

        inputs.setLayout(outer)
        inputs._compact_layout_applied = True

    def _clarify_directory_labels(self):
        """Make it obvious what each of the two "directory" fields does:

        - Local Directory (toolbar, built into pymeasure): where this run's
          Results file (the CSV backing the browser/plot) is written. This
          is the single authoritative local copy of the run's data.
        - NAS Backup Directory (procedure parameter, GSS only): optional
          network path that this run's Results file (and checkpoint) is
          mirrored to, for off-machine backup.

        Renames the generic toolbar "Directory" label and adds tooltips to
        both so their purposes are clear at a glance.
        """
        local_tip = (
            "Directory for this run's Results file (the CSV shown in the "
            "browser/plot above).\nThis is the single local copy of the run's data."
        )
        nas_tip = (
            "Optional network directory that mirrors the Results file above, "
            "for off-machine backup.\nLeave empty to disable."
        )

        file_input = getattr(self, 'file_input', None)
        if file_input is not None:
            for label in file_input.findChildren(QtWidgets.QLabel):
                if label.text().strip() == 'Directory':
                    label.setText('Local Directory')
                    label.setToolTip(local_tip)
            directory_input = getattr(file_input, 'directory_input', None)
            if directory_input is not None:
                directory_input.setToolTip(local_tip)

        inputs = getattr(self, 'inputs', None)
        labels = getattr(inputs, 'labels', {}) if inputs is not None else {}
        widget = getattr(inputs, 'nas_directory', None) if inputs is not None else None
        if widget is not None:
            widget.setToolTip(nas_tip)
        label = labels.get('nas_directory')
        if label is not None:
            label.setToolTip(nas_tip)

        self._add_directory_browse_button(widget)

    def _add_directory_browse_button(self, line_edit):
        """Add a trailing browse action to a plain QLineEdit, matching the
        folder-picker button pymeasure's own Local Directory field has.
        """
        if line_edit is None:
            return
        try:
            from PyQt5.QtWidgets import QAction
            browse_action = QAction(line_edit)
            browse_action.setIcon(line_edit.style().standardIcon(
                getattr(QtWidgets.QStyle.StandardPixmap, 'SP_DialogOpenButton')))
            browse_action.setToolTip('Browse…')
            browse_action.triggered.connect(lambda: self._browse_for_directory(line_edit))
            line_edit.addAction(browse_action, QtWidgets.QLineEdit.ActionPosition.TrailingPosition)
        except Exception:
            log.debug('Failed to add directory browse button', exc_info=True)

    def _browse_for_directory(self, line_edit):
        current = line_edit.text().strip()
        start = current if current and os.path.isdir(current) else os.path.dirname(os.path.abspath(__file__))
        chosen = QtWidgets.QFileDialog.getExistingDirectory(self, 'Directory', start)
        if chosen:
            line_edit.setText(chosen)

    def _customize_plots(self):
        """Customize plot appearance after widgets are created."""
        try:
            self._enable_all_grids()
        except Exception:
            log.debug('Failed to enable plot grids', exc_info=True)
        try:
            self._restrict_plot_axis_choices()
        except Exception:
            log.debug('Failed to restrict plot axis choices', exc_info=True)

    def _restrict_plot_axis_choices(self):
        """Remove string-valued columns (e.g. 'Controller', 'Status') from the
        plot's X/Y axis dropdowns, since selecting them as an axis errors out
        -- they can't be plotted as numeric data.

        Only applies if the procedure class defines a ``NON_PLOTTABLE_COLUMNS``
        list; other procedures are left untouched.
        """
        non_plottable = getattr(self.procedure.__class__, 'NON_PLOTTABLE_COLUMNS', None)
        if not non_plottable:
            return
        dock = self._get_current_dock()
        if dock is None:
            return
        for plot_widget in getattr(dock, 'plot_frames', []):
            for combo in (getattr(plot_widget, 'columns_x', None), getattr(plot_widget, 'columns_y', None)):
                if combo is None:
                    continue
                for name in non_plottable:
                    idx = combo.findText(name)
                    if idx >= 0:
                        combo.removeItem(idx)

    def _initialize_aux_psu(self):
        """Initialize AUX PSU Ch1 when main window loads.
        
        This enables the power supply channel immediately so the DUT has power
        before any measurement is started. Ch2/Ch3 are enabled when measurement starts.
        """
        try:
            connection_params = self.startup_config.get('connection_parameters', {})
            aux_info = connection_params.get('aux_psu', {})
            if not aux_info:
                log.debug('No AUX PSU configured - skipping initialization')
                return
            
            aux_resource = aux_info.get('connection') or aux_info.get('resource')
            aux_type = aux_info.get('type', 'nge103_psu')
            
            if not aux_resource:
                log.debug('No AUX PSU resource - skipping initialization')
                return
            
            log.info(f'Initializing AUX PSU ({aux_type}) on {aux_resource}')
            
            # Create and connect to PSU
            controller = None
            if aux_type == 'hmc8043_psu':
                from hardware.rs_hmc8043 import RSHMC8043Controller
                controller = RSHMC8043Controller(aux_resource)
            else:
                from hardware.rs_nge103 import NGE100
                controller = NGE100(aux_resource)
            
            if controller and controller.connect():
                # Store reference for later use
                self._aux_psu = controller
                self._aux_psu_type = aux_type
                
                # Get channel parameters from procedure class
                proc_class = self.procedure.__class__
                ch1_param = getattr(proc_class, 'aux_psu_ch1', None)
                if ch1_param is None:
                    ch1_param = '24.0, 0.5'  # Default
                
                # Parse V, I from parameter
                try:
                    parts = [p.strip() for p in str(ch1_param.default if hasattr(ch1_param, 'default') else ch1_param).split(',')]
                    voltage = float(parts[0]) if len(parts) >= 1 else 24.0
                    current = float(parts[1]) if len(parts) >= 2 else 0.5
                except Exception:
                    voltage, current = 24.0, 0.5
                
                # Configure and enable Ch1 only
                controller.set_voltage(1, voltage)
                controller.set_current(1, current)
                controller.enable_output(1, True)
                log.info(f'AUX PSU Ch1 enabled: {voltage}V, {current}A')
                
                # Configure Ch2/Ch3 but leave disabled
                for ch in (2, 3):
                    ch_param = getattr(proc_class, f'aux_psu_ch{ch}', None)
                    if ch_param:
                        try:
                            parts = [p.strip() for p in str(ch_param.default if hasattr(ch_param, 'default') else ch_param).split(',')]
                            v = float(parts[0]) if len(parts) >= 1 else 5.0
                            i = float(parts[1]) if len(parts) >= 2 else 0.1
                            controller.set_voltage(ch, v)
                            controller.set_current(ch, i)
                            controller.enable_output(ch, False)
                            log.info(f'AUX PSU Ch{ch} configured (disabled): {v}V, {i}A')
                        except Exception:
                            pass
            else:
                log.warning('Failed to connect to AUX PSU for initialization')
        except Exception as e:
            log.exception(f'Error initializing AUX PSU: {e}')
        try:
            self._limit_crosshairs_precision()
        except Exception:
            log.debug('Failed to limit crosshairs precision', exc_info=True)

    def _enable_all_grids(self):
        """Helper to enable grids on all pyqtgraph plots, if pyqtgraph is
        installed and used in the GUI.
        """
        try:
            import pyqtgraph as pg
            # pg.PlotWidget is a QWidget that may be discoverable
            PlotWidget = getattr(pg, 'PlotWidget', None)
            if PlotWidget is not None:
                for pw in self.findChildren(PlotWidget):
                    try:
                        # PlotWidget exposes showGrid, and the lower-level
                        # plotItem also has showGrid
                        if hasattr(pw, 'showGrid'):
                            pw.showGrid(x=True, y=True)
                        else:
                            plotitem = getattr(pw, 'plotItem', None)
                            if plotitem is not None and hasattr(plotitem, 'showGrid'):
                                plotitem.showGrid(x=True, y=True)
                    except Exception:
                        log.debug('Failed to set grid on a PlotWidget', exc_info=True)
        except Exception:
            log.debug('pyqtgraph not available or error while enabling grids', exc_info=True)

    def _limit_crosshairs_precision(self):
        """Limit the cursor coordinate display to 3 significant digits."""
        try:
            from pymeasure.display.widgets.plot_frame import PlotFrame
            plot_frames = self.findChildren(PlotFrame)
            log.info(f"Found {len(plot_frames)} PlotFrame widgets for crosshairs customization")
            for plot_frame in plot_frames:
                # Disconnect the original signal and connect our custom handler
                try:
                    plot_frame.crosshairs.coordinates.disconnect(plot_frame.update_coordinates)
                except Exception:
                    pass  # May already be disconnected or not connected
                
                # Create a new handler with limited precision
                def make_limited_update(pf):
                    def limited_update_coordinates(x, y):
                        pf.coordinates.setText(f"({x:.3g}, {y:.3g})")
                    return limited_update_coordinates
                
                # Connect our custom handler
                plot_frame.crosshairs.coordinates.connect(make_limited_update(plot_frame))
        except Exception:
            log.exception('Failed to limit crosshairs precision')

    def _populate_connection_parameters(self):
        """Pre-populate input fields with connection parameters from startup configuration."""
        connection_params = self.startup_config.get('connection_parameters', {})
        
        if not connection_params:
            return
        
        # Wait a bit for the inputs widget to be fully initialized
        QtCore.QTimer.singleShot(100, lambda: self._do_populate_parameters(connection_params))
    
    def _do_populate_parameters(self, connection_params):
        """Actually populate the parameters after UI is ready."""
        try:
            # APS Controller parameters
            aps_params = connection_params.get('aps_controller', {})
            if aps_params and hasattr(self.inputs, 'aps_port'):
                port = ''
                for key in ('port', 'connection', 'resource', 'address'):
                    candidate = aps_params.get(key)
                    if candidate:
                        port = candidate
                        break
                if port:
                    self.inputs.aps_port.setText(port)
            
            # Keithley parameters
            keithley_params = connection_params.get('keithley_smu', {})
            if keithley_params and hasattr(self.inputs, 'keithley_resource'):
                resource = ''
                for key in ('connection', 'resource', 'address', 'port'):
                    candidate = keithley_params.get(key)
                    if candidate:
                        resource = candidate
                        break
                if resource:
                    self.inputs.keithley_resource.setText(resource)

            aux_psu_params = connection_params.get('aux_psu', {})
            if aux_psu_params and hasattr(self.inputs, 'aux_psu_resource'):
                aux_resource = ''
                for key in ('connection', 'resource', 'address', 'port'):
                    candidate = aux_psu_params.get(key)
                    if candidate:
                        aux_resource = candidate
                        break
                if aux_resource:
                    self.inputs.aux_psu_resource.setText(aux_resource)
            
            log.info("Pre-populated connection parameters from startup configuration")
        except Exception as e:
            log.debug(f"Failed to populate connection parameters: {e}")

    # ----- Layout and data directory persistence -----
    def _restore_layout(self):
        try:
            geom = self.settings_manager.get_byte_array('window.geometry')
            if geom is not None:
                self.restoreGeometry(geom)
        except Exception:
            log.debug('restoreGeometry failed', exc_info=True)
        try:
            state = self.settings_manager.get_byte_array('window.state')
            if state is not None:
                self.restoreState(state)
        except Exception:
            log.debug('restoreState failed', exc_info=True)

    def _save_layout(self):
        try:
            self.settings_manager.set_byte_array('window.geometry', self.saveGeometry())
        except Exception:
            log.debug('saveGeometry failed', exc_info=True)
        try:
            self.settings_manager.set_byte_array('window.state', self.saveState())
        except Exception:
            log.debug('saveState failed', exc_info=True)

    def _default_data_dir_for(self) -> str:
        # Default to ./data/<procedure_short_name>
        app_dir = os.path.dirname(os.path.abspath(__file__))
        short = self.procedure.short_name
        return os.path.join(app_dir, 'data', short)

    def _apply_procedure_directory(self):
        # Get the dir from saved settings
        directory = self.settings_manager.get_value(f"directories.{self.procedure.internal_name}")
        if directory is None:
            directory = self._default_data_dir_for()
            log.info(f'No saved directory found, using default: {directory}')   
              
        # Always ensure the directory exists when loading a procedure
        try:
            os.makedirs(directory, exist_ok=True)
            log.info(f'Data directory for "{self.procedure.short_name}": {directory}')
        except Exception as e:
            log.error(f'Failed to create directory {directory}: {e}')
            
        # Update the window's directory used for new Results files
        try:
            self.directory = directory
            log.debug(f'Set self.directory to: {directory}')
            if hasattr(self, 'directory_input') and self.directory_input is not None:
                if self.directory_input.text() != directory:
                    self.directory_input.setText(directory)
                    log.debug(f'Updated directory input to: {directory}')
                else:
                    log.debug(f'Directory input already matches: {directory}')
            self._last_saved_dir = directory
        except Exception:
            log.debug('Failed to set window directory to %s', directory, exc_info=True)

    def _on_directory_edited(self):
        """Called when the user edits the directory QLineEdit manually."""
        try:
            new_dir = self.directory_input.text().strip()
            if new_dir:
                # Ensure path exists or attempt to create
                try:
                    os.makedirs(new_dir, exist_ok=True)
                except Exception:
                    log.debug(f'Could not create directory: {new_dir}', exc_info=True)
                self.directory = new_dir
                # Persist immediately
                self._persist_current_directory()
                log.info(f'User changed data directory to: {new_dir}')
        except Exception:
            log.debug('Error handling directory edit', exc_info=True)

    def _on_directory_browse(self):
        """Open a folder selection dialog to choose the directory."""
        try:
            from PyQt5.QtWidgets import QFileDialog
            start = getattr(self, 'directory', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'))
            chosen = QFileDialog.getExistingDirectory(self, 'Select Data Directory', start)
            if chosen:
                self.directory_input.setText(chosen)
                # Trigger same logic as manual edit
                self._on_directory_edited()
        except Exception:
            log.debug('Directory browse failed', exc_info=True)

    def _attach_directory_input(self):
        """Ensure directory_input signals are connected (if toolbar created)."""
        try:
            if hasattr(self, 'directory_input') and self.directory_input is not None:
                # Connect if not already connected
                try:
                    self.directory_input.editingFinished.disconnect()
                except Exception:
                    pass
                self.directory_input.editingFinished.connect(lambda: self._on_directory_edited())
        except Exception:
            log.debug('Failed to attach directory_input handlers', exc_info=True)

    def _persist_current_directory(self):
        try:
            directory = getattr(self, 'directory', None)
            if directory:
                # Use the internal_name from the procedure class for consistent key usage
                self.settings_manager.set_value((f"directories.{self.procedure.internal_name}"), directory)
                log.debug(f'Persisted directory "{directory}"')
        except Exception:
            log.debug('Failed to persist current directory', exc_info=True)

    # ----- Input parameter persistence -----
    def _restore_input_parameters(self):
        """Restore saved input parameter values for the current procedure."""
        try:
            proc_name = self.procedure.internal_name
            saved_params = self.settings_manager.get_value(f"parameters.{proc_name}", {})
            if not saved_params:
                log.debug(f'No saved parameters found for {proc_name}')
                return
            
            for param_name, value in saved_params.items():
                if hasattr(self.inputs, param_name):
                    widget = getattr(self.inputs, param_name)
                    self._set_widget_value(widget, value)
                    log.debug(f'Restored {param_name} = {value}')
            
            log.info(f'Restored {len(saved_params)} saved parameters for {proc_name}')
        except Exception:
            log.debug('Failed to restore input parameters', exc_info=True)
    
    def _save_input_parameters(self):
        """Save current input parameter values for the current procedure."""
        try:
            proc_name = self.procedure.internal_name
            params = {}
            
            for param_name in self._inputs_list:
                if hasattr(self.inputs, param_name):
                    widget = getattr(self.inputs, param_name)
                    value = self._get_widget_value(widget)
                    if value is not None:
                        params[param_name] = value
            
            if params:
                self.settings_manager.set_value(f"parameters.{proc_name}", params)
                log.debug(f'Saved {len(params)} parameters for {proc_name}')
        except Exception:
            log.debug('Failed to save input parameters', exc_info=True)
    
    def _get_widget_value(self, widget):
        """Get the current value from an input widget."""
        from PyQt5.QtWidgets import QCheckBox, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox
        
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        elif isinstance(widget, QSpinBox):
            return widget.value()
        elif isinstance(widget, QDoubleSpinBox):
            return widget.value()
        elif isinstance(widget, QLineEdit):
            return widget.text()
        elif isinstance(widget, QComboBox):
            return widget.currentText()
        return None
    
    def _set_widget_value(self, widget, value):
        """Set a value on an input widget."""
        from PyQt5.QtWidgets import QCheckBox, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox
        
        try:
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QSpinBox):
                widget.setValue(int(value))
            elif isinstance(widget, QDoubleSpinBox):
                widget.setValue(float(value))
            elif isinstance(widget, QLineEdit):
                widget.setText(str(value))
            elif isinstance(widget, QComboBox):
                idx = widget.findText(str(value))
                if idx >= 0:
                    widget.setCurrentIndex(idx)
        except Exception:
            log.debug(f'Failed to set widget value: {value}', exc_info=True)

    def closeEvent(self, event):
        # Persist layout and current procedure directory on close
        try:
            self._save_layout()
        except Exception:
            pass
        try:
            # Save current dock layout for current procedure
            self._save_dock_layout_for_proc()
        except Exception:
            pass
        try:
            self._persist_current_directory()
        except Exception:
            pass
        try:
            self._save_input_parameters()
        except Exception:
            pass
        super().closeEvent(event)

    # ---- Dock layouts (internal DockArea) per procedure ----
    def _get_current_dock(self):
        try:
            for w in list(self.widget_list):
                if getattr(w, 'plot_frames', None) is not None:
                    return w
        except Exception:
            pass
        return None

    def _save_dock_layout_for_proc(self):
        dock = self._get_current_dock()
        if dock is None:
            return
        state = None
        try:
            area = getattr(dock, 'area', None)
            if area is None:
                # Try alternative attribute names
                area = getattr(dock, 'dock_area', None)
            if area is None:
                area = getattr(dock, 'dockArea', None)
            if area is not None and hasattr(area, 'saveState'):
                state = area.saveState()
            elif hasattr(dock, 'saveState'):
                state = dock.saveState()
        except Exception:
            log.debug('Failed to capture dock layout state', exc_info=True)
        if state is None:
            return
        # Serialize state
        try:
            if isinstance(state, (bytes, bytearray)):
                payload = 'b64:' + base64.b64encode(bytes(state)).decode('ascii')
            elif hasattr(state, 'data'):
                # QByteArray like
                payload = 'b64:' + base64.b64encode(bytes(state.data())).decode('ascii')
            else:
                payload = 'json:' + json.dumps(state)
            if payload != self._last_saved_layout_payload:
                # Use the internal_name from the procedure class for consistent key usage
                self.settings_manager.set_value(f"docks.{self.procedure.internal_name}", payload)
                self._last_saved_layout_payload = payload
                log.debug('Saved dock layout with key')
        except Exception:
            log.debug('Failed to persist dock layout state', exc_info=True)

    def _restore_dock_layout_for_proc(self, dock):
        try:
            # Use the internal_name from the procedure class for consistent key usage
            payload = self.settings_manager.get_value(f"docks.{self.procedure.internal_name}")
            if not payload:
                log.debug('No dock layout found')
                return
            state = None
            if payload.startswith('b64:'):
                state = base64.b64decode(payload[4:].encode('ascii'))
            elif payload.startswith('json:'):
                state = json.loads(payload[5:])
            area = getattr(dock, 'area', None)
            if area is None:
                area = getattr(dock, 'dock_area', None)
            if area is None:
                area = getattr(dock, 'dockArea', None)
            if area is not None and hasattr(area, 'restoreState'):
                area.restoreState(state)
            elif hasattr(dock, 'restoreState'):
                dock.restoreState(state)
            log.debug(f'Successfully restored dock layout for {self.procedure.internal_name}')
        except Exception:
            log.debug('Failed to restore dock layout state', exc_info=True)

    def _periodic_persist(self):
        # Persist directory if changed
        try:
            dir_now = getattr(self, 'directory', None)
            if dir_now and dir_now != self._last_saved_dir:
                self._persist_current_directory()
                self._last_saved_dir = dir_now
        except Exception:
            pass
        # Persist current dock layout payload if changed
        try:
            self._save_dock_layout_for_proc()
        except Exception:
            pass
        # Persist input parameters periodically
        try:
            self._save_input_parameters()
        except Exception:
            pass

    # ---- Queue override: pre-apply connection parameters so they appear in CSV ----
    def queue(self, procedure=None):
        """Queue a new experiment, pre-applying connection parameters before CSV creation.

        This ensures that connection strings (APS port, Keithley resource, AUX PSU resource)
        appear correctly in the CSV header rather than showing their default empty values.

        Also auto-resumes the manager if it was left in a paused state after an abort,
        so the new experiment starts immediately without requiring a manual Resume click.
        """
        try:
            if procedure is None:
                procedure = self.make_procedure()
            # Apply connection parameters BEFORE pymeasure creates the Results/CSV file
            params = getattr(procedure.__class__, '_startup_connection_parameters', None) or {}
            if not params:
                params = self.startup_config.get('connection_parameters', {})
            if params and hasattr(procedure, '_apply_connection_parameters'):
                procedure.connection_parameters = params
                procedure._apply_connection_parameters()
                # Also set the AUX PSU resource on the procedure parameter if present
                aux_info = params.get('aux_psu', {})
                aux_res = aux_info.get('connection') or aux_info.get('resource', '')
                if aux_res and hasattr(procedure, 'aux_psu_resource'):
                    procedure.aux_psu_resource = aux_res
                log.info("Pre-applied connection parameters to procedure before CSV creation")
            # Pre-apply device IDs captured during connection tests
            device_ids = self.startup_config.get('device_ids', {})
            if device_ids and hasattr(procedure, '_apply_device_ids'):
                procedure._apply_device_ids(device_ids)
                log.info(f"Pre-applied device IDs to procedure: {list(device_ids.keys())}")
            # Let the procedure know exactly where pymeasure will save this run's
            # Results file, so procedures that mirror it elsewhere (e.g. GSS's
            # nas_directory) can find it without duplicating file-writing logic.
            # This must use the exact same arguments as the base class's queue()
            # so the filename it computes here matches the one pymeasure ends
            # up using.
            if self.enable_file_input and self.store_measurement:
                try:
                    procedure.results_filepath = unique_filename(
                        self.directory,
                        prefix=self.file_input.filename_base,
                        datetimeformat="",
                        procedure=procedure,
                        ext=self.file_input.filename_extension,
                    )
                except Exception:
                    log.debug('Could not pre-compute Results filepath', exc_info=True)
        except Exception:
            log.debug('Failed to pre-apply connection parameters in queue()', exc_info=True)
        super().queue(procedure)
        # If the manager is paused after an abort, auto-resume so the new experiment
        # starts immediately without the user needing to click Resume.
        try:
            if (hasattr(self, 'abort_button')
                    and self.abort_button.text() == "Resume"
                    and self.manager.experiments.has_next()):
                log.info("Auto-resuming manager after queue (post-abort state)")
                self.resume()
        except Exception:
            log.debug('Failed to auto-resume after queue', exc_info=True)

    def abort_returned(self, experiment):
        """Keep controls enabled until every parallel run has returned."""
        if self.manager.is_running():
            return
        super().abort_returned(experiment)

    def finished(self, experiment):
        """Do not mark a parallel GSS window idle while another run is active."""
        if self.manager.is_running() or self.manager.experiments.has_next():
            return
        super().finished(experiment)


if __name__ == "__main__":
    QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_DisableHighDpiScaling)
    QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_Use96Dpi)
    app = QtWidgets.QApplication(sys.argv)
    application_font = QFont(app.font())
    application_font.setPointSize(10)
    app.setFont(application_font)
    app.setDesktopFileName('ze-aps-gui')
    app.setWindowIcon(QIcon(str(APPLICATION_ICON)))
    
    # Show startup dialog first
    from startup_dialog import show_startup_dialog
    
    config = show_startup_dialog()
    if config is None:
        # User cancelled startup dialog
        sys.exit(0)
    
    # Create main window with selected configuration
    window = MainWindow(startup_config=config)
    window.show()
    sys.exit(app.exec())