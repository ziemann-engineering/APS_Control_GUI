# ZE / APS Measurement GUI
# main file
# TODO: change keithley_gatecurrent to HPPT
# TODO: implement AUX PSU control (voltage, current, enable/disable)
# TODO: implement oscilloscope control (trigger, timebase, channels, data saving)
# TODO: implement all APS control functions (from C code)
# TODO: new setup window to select test and connect devices

import logging
import os
import json
import base64
import sys
from pymeasure.display.Qt import QtWidgets, QtCore
from pymeasure.display.windows.managed_dock_window import ManagedDockWindow
from procedures.random_procedure import RandomProcedure
from procedures.keithley_gatecurrent import Keithley_gatecurrent_Procedure

from PyQt5.QtGui import QIcon
from datetime import datetime

log = logging.getLogger(__name__)
 
logging.basicConfig(filename=f"./logs/{datetime.now():%Y-%m-%d_%H-%M-%S}.log", encoding="utf-8", filemode="a", format='%(asctime)s - %(levelname)s - %(message)s')


class MainWindow(ManagedDockWindow):

    def __init__(self):
        # Preserve the inputs-list so we can recreate InputsWidget when switching procedures
        self._inputs_list = ['iterations', 'delay']
        # Track the current inputs list (changes when user switches procedures)
        self._current_inputs_list = list(self._inputs_list)
        super().__init__(
            procedure_class=RandomProcedure,
            inputs=self._inputs_list,
            displays=['iterations', 'delay'],
            x_axis=['Iteration'],
            y_axis=['Random Number 1','Random Number 2']
        )
        self.setWindowTitle('ZE APS Measurement GUI')
        self.setWindowIcon(QIcon('ze.png'))

        try:
            self._enable_all_grids()
        except Exception:
            # Never let grid helper break the main window
            log.exception('Failed to enable plot grids')

        # Procedure selector combobox: allow the user to switch the procedure class
        try:
            # Map display name -> (procedure_class, inputs_list, x_axis_labels, y_axis_labels, displays)
            self._procedure_infos = {
                'RandomProcedure': (
                    RandomProcedure,
                    ['iterations', 'delay'],
                    ['Iteration'],
                    ['Random Number 1', 'Random Number 2'],
                    ['iterations', 'delay'],
                ),
                'Keithley Gate Current': (
                    Keithley_gatecurrent_Procedure,
                    ['resource', 'voltage', 'compliance'],
                    ['Voltage (V)'],
                    ['Current (A)'],
                    ['resource', 'voltage', 'compliance'],
                ),
            }
            self.procedure_selector = QtWidgets.QComboBox(parent=self)
            # display user-friendly names; keep mapping to classes above
            for name in self._procedure_infos.keys():
                self.procedure_selector.addItem(name)

            # try to set the current index to the class we started with
            start_name = None
            for k, v in self._procedure_infos.items():
                cls = v[0]
                if cls is type(self.procedure_class) or cls == self.procedure_class:
                    start_name = k
                    break
            # Restore last selection from settings, else use current class
            settings = QtCore.QSettings('ZE', 'APS Measurement GUI')
            saved = settings.value('last_procedure', type=str)
            if saved and saved in self._procedure_infos:
                self.procedure_selector.setCurrentText(saved)
            elif start_name:
                self.procedure_selector.setCurrentText(start_name)

            self.procedure_selector.currentTextChanged.connect(self._on_procedure_selected)

            # Insert the selector into the existing inputs dock layout (if present)
            try:
                for dock in self.findChildren(QtWidgets.QDockWidget):
                    if dock.windowTitle() == 'Input Parameters':
                        inputs_dock = dock.widget()
                        inputs_layout = inputs_dock.layout()
                        # Use a QGroupBox so the selector has its own visible
                        # widget area above the inputs. Parent to inputs_dock so
                        # it's laid out together with the inputs.
                        selector_container = QtWidgets.QGroupBox('Test type', parent=inputs_dock)
                        selector_container.setObjectName('procedure_selector_container')
                        selector_layout = QtWidgets.QHBoxLayout(selector_container)
                        selector_layout.setContentsMargins(6, 6, 6, 6)
                        selector_layout.addWidget(self.procedure_selector)
                        # Add the groupbox above the existing inputs widget
                        inputs_layout.insertWidget(0, selector_container)
                        break
            except Exception:
                log.debug('Could not insert procedure selector into inputs dock', exc_info=True)
        except Exception:
            log.debug('Failed to create procedure selector', exc_info=True)

        # Disable selector while running or queue non-empty
        try:
            self.manager.queued.connect(lambda _e: self._refresh_selector_enabled())
            self.manager.running.connect(lambda _e: self._refresh_selector_enabled())
            self.manager.finished.connect(lambda _e: self._refresh_selector_enabled())
            self.manager.abort_returned.connect(lambda _e: self._refresh_selector_enabled())
            self._refresh_selector_enabled()
        except Exception:
            log.debug('Failed to hook manager signals for selector enable/disable', exc_info=True)

        # Apply saved dock layout and per-procedure data directory
        try:
            self._restore_layout()
        except Exception:
            log.debug('Failed to restore window layout', exc_info=True)
        try:
            if hasattr(self, 'procedure_selector'):
                self._apply_procedure_directory(self.procedure_selector.currentText())
        except Exception:
            log.debug('Failed to apply initial procedure directory', exc_info=True)
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
                self._restore_dock_layout_for_proc(self._current_proc_name(), dock)
        except Exception:
            log.debug('Failed to restore initial dock layout', exc_info=True)

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


    def _on_procedure_selected(self, text):
        """Handle the user selecting a different procedure.

        This will update self.procedure_class and recreate the InputsWidget so
        that the input fields match the newly selected procedure.
        """

        # Safety: prevent switching while experiments are queued/running unless confirmed
        # Also persist selection immediately
        try:
            QtCore.QSettings('ZE', 'APS Measurement GUI').setValue('last_procedure', text)
        except Exception:
            pass

        # If selector is disabled (running/queued), revert change (safety)
        try:
            if not self.procedure_selector.isEnabled():
                for name, (cls0, *_rest) in self._procedure_infos.items():
                    if cls0 == self.procedure_class:
                        self.procedure_selector.blockSignals(True)
                        self.procedure_selector.setCurrentText(name)
                        self.procedure_selector.blockSignals(False)
                        break
                return
        except Exception:
            log.debug('Selector revert check failed', exc_info=True)

        info = self._procedure_infos.get(text)
        if info is None:
            log.warning('Selected unknown procedure: %s', text)
            return
        cls, inputs_list, x_axis_labels, y_axis_labels, displays = info
        if cls == self.procedure_class:
            return

        # Update the procedure class and current inputs list used by the window
        self.procedure_class = cls
        self._current_inputs_list = list(inputs_list)
        # Update displays list used by browser
        self.displays = list(displays)

        # Find the inputs dock and its layout so we can replace the InputsWidget
        for dock in self.findChildren(QtWidgets.QDockWidget):
            if dock.windowTitle() == 'Input Parameters':
                inputs_dock = dock.widget()
                inputs_layout = inputs_dock.layout()
                break
        else:
            log.debug('Inputs dock not found; cannot replace InputsWidget')
            return

        # Locate the current InputsWidget index in the layout
        old_index = None
        for i in range(inputs_layout.count()):
            item = inputs_layout.itemAt(i)
            w = item.widget() if item is not None else None
            if w is self.inputs:
                old_index = i
                break

        if old_index is None:
            # Fallback: append at top
            insert_pos = 0
        else:
            insert_pos = old_index

        # Remove the old InputsWidget safely (but keep the selector container)
        try:
            # remove the old inputs widget wherever it sits
            inputs_layout.removeWidget(self.inputs)

            try:
                self.inputs.setParent(None)
                self.inputs.deleteLater()
            except Exception:
                pass
        except Exception:
            log.debug('Failed to remove old InputsWidget', exc_info=True)

        # Create a new InputsWidget for the selected procedure
        try:
            from pymeasure.display.widgets.inputs_widget import InputsWidget
            new_inputs = InputsWidget(
                self.procedure_class,
                self._current_inputs_list,
                parent=self,
                hide_groups=self.hide_groups,
                inputs_in_scrollarea=self.inputs_in_scrollarea,
            )
            # If selector is present at 0 and we're inserting at 0,
            # shift to 1 so selector remains on top.
            if inputs_layout.count() > 0:
                first_item = inputs_layout.itemAt(0)
                if first_item is not None and first_item.widget() is not None:
                    if first_item.widget().objectName() == 'procedure_selector_container' and insert_pos == 0:
                        insert_pos = 1

            inputs_layout.insertWidget(insert_pos, new_inputs)
            self.inputs = new_inputs
        except Exception:
            log.exception('Failed to create new InputsWidget for %s', text)
            return

        # Update the BrowserWidget to use the selected procedure's display parameters
        try:
            self._update_browser_for_procedure(self.procedure_class, x_axis_labels, y_axis_labels)
        except Exception:
            log.debug('Failed to update browser for new procedure', exc_info=True)

        # Rebuild plots/dock widget to reflect new procedure (x/y labels, columns)
        try:
            self._update_plots_for_procedure(self.procedure_class, x_axis_labels, y_axis_labels)
        except Exception:
            log.debug('Failed to update plots for new procedure', exc_info=True)

        log.info('Switched procedure to %s', text)
        # Apply directory for this procedure after switching
        try:
            self._apply_procedure_directory(text)
        except Exception:
            log.debug('Failed to apply procedure directory on switch', exc_info=True)


    def _update_browser_for_procedure(self, procedure_class, x_axis_labels=None, y_axis_labels=None):
        """Update the browser headers/columns to match the selected procedure.

        The Browser constructor sets header labels based on the procedure_class
        and a list of display_parameters. We update the browser's procedure_class
        and its header to reflect the new procedure.
        """
        try:
            browser = self.browser_widget.browser
            # Keep measured_quantities as-is, but update procedure_class and display_parameters
            browser.procedure_class = procedure_class
            # Browser expects parameter attribute names for displays
            display_parameters = list(self.displays) if getattr(self, 'displays', None) else list(browser.display_parameters)
            browser.display_parameters = list(display_parameters)

            # Build header labels similar to Browser.__init__
            header_labels = ["Graph", "Filename", "Progress", "Status"]
            for parameter in display_parameters:
                # If the attribute exists on the procedure_class, use its .name
                if hasattr(procedure_class, parameter):
                    header_labels.append(getattr(procedure_class, parameter).name)
                else:
                    header_labels.append(str(parameter))

            browser.setColumnCount(len(header_labels))
            browser.setHeaderLabels(header_labels)
            # Reset measured quantities to only those relevant to current plots
            labels = []
            if x_axis_labels:
                labels += list(x_axis_labels)
            if y_axis_labels:
                labels += list(y_axis_labels)
            if labels:
                browser.measured_quantities = set(labels)
        except Exception:
            log.exception('Error updating browser headers for new procedure', exc_info=True)


    def _update_plots_for_procedure(self, procedure_class, x_axis_labels=None, y_axis_labels=None):
        """Rebuild or replace the DockWidget so plots reflect the chosen procedure.

        Strategy: create a new DockWidget instance with the selected procedure_class
        and replace the existing one in the window's widget_list and tabs.
        """
        try:
            # Save current procedure's dock layout before switching
            try:
                self._save_dock_layout_for_proc(self._current_proc_name())
            except Exception:
                pass
            # Find existing DockWidget in widget_list
            old_dock = None
            for w in list(self.widget_list):
                # DockWidget is from pymeasure.display.widgets.dock_widget.DockWidget
                if getattr(w, 'plot_frames', None) is not None:
                    old_dock = w
                    break

            # Determine x_axis and y_axis defaults using provided labels or fallbacks
            x_axis_labels = x_axis_labels or getattr(self, 'x_axis', None) or getattr(self, 'x_axis_labels', None) or []
            y_axis_labels = y_axis_labels or getattr(self, 'y_axis', None) or getattr(self, 'y_axis_labels', None) or []

            # Create new DockWidget with the same labels but new procedure class
            from pymeasure.display.widgets.dock_widget import DockWidget
            new_dock = DockWidget('Dock Tab', procedure_class, x_axis_labels, y_axis_labels,
                                  linewidth=getattr(old_dock, 'linewidth', 1) if old_dock else 1)

            # Replace in widget_list
            if old_dock is not None:
                idx = list(self.widget_list).index(old_dock)
                widget_list = list(self.widget_list)
                widget_list[idx] = new_dock
                self.widget_list = tuple(widget_list)

                # Replace tab widget in tabs if present
                for t in range(self.tabs.count()):
                    if self.tabs.widget(t) is old_dock:
                        self.tabs.removeTab(t)
                        self.tabs.insertTab(t, new_dock, new_dock.name)
                        break
            else:
                # If no old dock found, append
                widget_list = list(self.widget_list)
                widget_list.insert(0, new_dock)
                self.widget_list = tuple(widget_list)
                self.tabs.insertTab(0, new_dock, new_dock.name)

            # Restore dock layout for the (now) current procedure, if saved
            try:
                self._restore_dock_layout_for_proc(self._current_proc_name(), new_dock)
            except Exception:
                pass

            # Update window-level axes and browser measured_quantities to match new labels exactly
            try:
                self.x_axis = list(new_dock.x_axis_labels)
                self.y_axis = list(new_dock.y_axis_labels)
            except Exception:
                pass
            try:
                browser = self.browser_widget.browser
                browser.measured_quantities = set(new_dock.x_axis_labels + new_dock.y_axis_labels)
            except Exception:
                pass

            log.info('Rebuilt DockWidget plots for procedure %s', procedure_class.__name__)
        except Exception:
            log.exception('Error rebuilding plots for new procedure', exc_info=True)

    def _refresh_selector_enabled(self):
        try:
            running = self.manager.is_running()
            queued = getattr(self.manager.experiments, 'has_next', lambda: False)()
            self.procedure_selector.setEnabled(not (running or queued))
        except Exception:
            pass

    # ----- Layout and data directory persistence -----
    def _settings(self):
        return QtCore.QSettings('ZE', 'APS Measurement GUI')

    def _restore_layout(self):
        s = self._settings()
        geom = s.value('window/geometry', type=QtCore.QByteArray)
        state = s.value('window/state', type=QtCore.QByteArray)
        try:
            if geom is not None:
                self.restoreGeometry(geom)
        except Exception:
            log.debug('restoreGeometry failed', exc_info=True)
        try:
            if state is not None:
                self.restoreState(state)
        except Exception:
            log.debug('restoreState failed', exc_info=True)

    def _save_layout(self):
        s = self._settings()
        try:
            s.setValue('window/geometry', self.saveGeometry())
        except Exception:
            log.debug('saveGeometry failed', exc_info=True)
        try:
            s.setValue('window/state', self.saveState())
        except Exception:
            log.debug('saveState failed', exc_info=True)

    def _current_proc_name(self):
        try:
            return self.procedure_selector.currentText()
        except Exception:
            return getattr(self.procedure_class, 'name', self.procedure_class.__name__)

    def _default_data_dir_for(self, proc_display_name: str) -> str:
        # Default to ./data/<sanitized-name>
        app_dir = os.path.dirname(os.path.abspath(__file__))
        base = os.path.join(app_dir, 'data')
        safe = proc_display_name.strip().replace(' ', '_')
        return os.path.join(base, safe)

    def _apply_procedure_directory(self, proc_display_name: str):
        s = self._settings()
        key = f'proc_dir_abs/{proc_display_name}'
        directory = s.value(key, type=str)
        if not directory:
            directory = self._default_data_dir_for(proc_display_name)
        try:
            os.makedirs(directory, exist_ok=True)
        except Exception:
            log.debug('Failed to create directory %s', directory, exc_info=True)
        # Update the window's directory used for new Results files
        try:
            self.directory = directory
            if hasattr(self, 'directory_input') and self.directory_input is not None:
                if self.directory_input.text() != directory:
                    self.directory_input.setText(directory)
            self._last_saved_dir = directory
        except Exception:
            log.debug('Failed to set window directory to %s', directory, exc_info=True)

    def _persist_current_directory(self):
        try:
            proc_name = self._current_proc_name()
            directory = getattr(self, 'directory', None)
            if directory:
                self._settings().setValue(f'proc_dir_abs/{proc_name}', directory)
        except Exception:
            log.debug('Failed to persist current directory', exc_info=True)

    def closeEvent(self, event):
        # Persist layout and current procedure directory on close
        try:
            self._save_layout()
        except Exception:
            pass
        try:
            # Save current dock layout for current procedure
            self._save_dock_layout_for_proc(self._current_proc_name())
        except Exception:
            pass
        try:
            self._persist_current_directory()
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

    def _save_dock_layout_for_proc(self, proc_display_name: str):
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
                self._settings().setValue(f'dock_layout/{proc_display_name}', payload)
                self._last_saved_layout_payload = payload
        except Exception:
            log.debug('Failed to persist dock layout state', exc_info=True)

    def _restore_dock_layout_for_proc(self, proc_display_name: str, dock):
        try:
            payload = self._settings().value(f'dock_layout/{proc_display_name}', type=str)
            if not payload:
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
        except Exception:
            log.debug('Failed to restore dock layout state', exc_info=True)

    def _attach_directory_input(self):
        # Prefer attribute if present
        if hasattr(self, 'directory_input') and self.directory_input is not None:
            try:
                self.directory_input.textChanged.connect(lambda _t: self._persist_current_directory())
                self.directory_input.editingFinished.connect(self._persist_current_directory)
                return
            except Exception:
                pass
        # Fallback: scan for a QLineEdit that matches our current directory
        try:
            current_dir = getattr(self, 'directory', '')
            for le in self.findChildren(QtWidgets.QLineEdit):
                try:
                    if le.text() == current_dir or 'directory' in le.objectName().lower():
                        le.textChanged.connect(lambda _t: self._persist_current_directory())
                        le.editingFinished.connect(self._persist_current_directory)
                        self.directory_input = le
                        break
                except Exception:
                    continue
        except Exception:
            pass

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
            self._save_dock_layout_for_proc(self._current_proc_name())
        except Exception:
            pass



if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())