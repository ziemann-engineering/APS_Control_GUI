import logging

import sys
import random
from time import sleep
from pymeasure.display.Qt import QtWidgets
from pymeasure.display.windows.managed_dock_window import ManagedDockWindow
from pymeasure.experiment import Procedure
from pymeasure.experiment import IntegerParameter, FloatParameter, Parameter

from PyQt5.QtGui import QIcon

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class RandomProcedure(Procedure):
    name = 'Random Number Generator'
    iterations = IntegerParameter('Loop Iterations', default=10)
    delay = FloatParameter('Delay Time', units='s', default=0.2)
    seed = Parameter('Random Seed', default='12345')

    DATA_COLUMNS = ['Iteration', 'Random Number 1', 'Random Number 2', 'Random Number 3']

    def startup(self):
        log.info("Setting the seed of the random number generator")
        random.seed(self.seed)

    def execute(self):
        log.info("Starting the loop of %d iterations" % self.iterations)
        for i in range(self.iterations):
            data = {
                'Iteration': i,
                'Random Number 1': random.random(),
                'Random Number 2': random.random(),
                'Random Number 3': random.random()
            }
            self.emit('results', data)
            log.debug("Emitting results: %s" % data)
            self.emit('progress', 100 * i / self.iterations)
            sleep(self.delay)
            if self.should_stop():
                log.warning("Caught the stop flag in the procedure")
                break


class Keithley_gatecurrent_Procedure(Procedure):
    name = 'Keithley Gate Current Measurement'
    resource = Parameter('Instrument resource', default='')
    voltage = FloatParameter('Source voltage', units='V', default=20.0)
    compliance = FloatParameter('Compliance current', units='A', default=0.1)

    DATA_COLUMNS = ['Voltage (V)', 'Current (A)']

    def startup(self):
        """Open the instrument using pymeasure Keithley classes if available.

        If no resource is provided or opening fails, self.smu remains None and
        execute() will emit NaN values.
        """
        self.smu = None
        if not self.resource:
            log.warning('No instrument resource provided for Keithley2470Procedure')
            return

        try:
            from pymeasure.instruments import keithley as keithley_mod
            for cls_name in ('Keithley2470', 'Keithley2460', 'Keithley2450', 'Keithley2400'):
                cls = getattr(keithley_mod, cls_name, None)
                if cls is None:
                    continue
                try:
                    self.smu = cls(self.resource)
                    log.info(f'Opened instrument with {cls_name} via pymeasure')
                    break
                except Exception:
                    log.debug(f'Failed to open resource {self.resource} with {cls_name}')
                    self.smu = None
        except Exception:
            log.debug('pymeasure keithley module not available')

        if self.smu is None:
            log.warning('Could not open Keithley via pymeasure; no instrument available')

    def execute(self):
        """Set source voltage, enable output, measure current, then disable output.

        Emits a single results row with the voltage and measured current.
        """
        voltage = float(self.voltage)
        current = float('nan')

        if self.smu is not None:
            try:
                # Set voltage
                if hasattr(self.smu, 'apply_voltage'):
                    try:
                        self.smu.apply_voltage(voltage, float(self.compliance))
                    except Exception:
                        self.smu.apply_voltage(voltage)
                else:
                    # set attribute; let exceptions propagate to outer handler
                    setattr(self.smu, 'source_voltage', voltage)

                # Enable output
                if hasattr(self.smu, 'enable_source'):
                    self.smu.enable_source()
                elif hasattr(self.smu, 'enable_output'):
                    self.smu.enable_output()
                else:
                    if hasattr(self.smu, 'write'):
                        self.smu.write('OUTP ON')

                # wait for settling
                sleep(0.1)

                # Measure current
                if hasattr(self.smu, 'measure_current'):
                    val = self.smu.measure_current()
                    # handle single values or arrays
                    if hasattr(val, '__iter__'):
                        current = float(val[0])
                    else:
                        current = float(val)
                else:
                    if hasattr(self.smu, 'current'):
                        current = float(getattr(self.smu, 'current'))
                    if hasattr(self.smu, 'ask'):
                        resp = self.smu.ask('MEAS:CURR?')
                        current = float(resp)
                    elif hasattr(self.smu, 'query'):
                        resp = self.smu.query('MEAS:CURR?')
                        current = float(resp)

            except Exception as e:
                log.exception('Error during pymeasure instrument sequence: %s', e)
            finally:
                # Disable output
                if hasattr(self.smu, 'disable_source'):
                    try:
                        self.smu.disable_source()
                    except Exception:
                        log.debug('disable_source failed', exc_info=True)
                if hasattr(self.smu, 'disable_output'):
                    try:
                        self.smu.disable_output()
                    except Exception:
                        log.debug('disable_output failed', exc_info=True)
                if hasattr(self.smu, 'write'):
                    try:
                        self.smu.write('OUTP OFF')
                    except Exception:
                        log.debug('write OUTP OFF failed', exc_info=True)

        else:
            log.warning('No SMU available for measurement; emitting NaN')

        # Emit results
        self.emit('results', {'Voltage (V)': voltage, 'Current (A)': current})

    def shutdown(self):
        try:
            if self.smu is not None:
                if hasattr(self.smu, 'disable_source'):
                    try:
                        self.smu.disable_source()
                    except Exception:
                        log.debug('disable_source failed in shutdown', exc_info=True)
                if hasattr(self.smu, 'disable_output'):
                    try:
                        self.smu.disable_output()
                    except Exception:
                        log.debug('disable_output failed in shutdown', exc_info=True)
                if hasattr(self.smu, 'close'):
                    try:
                        self.smu.close()
                    except Exception:
                        log.debug('close failed in shutdown', exc_info=True)
                self.smu = None
        except Exception:
            log.debug('Exception in Keithley2470Procedure.shutdown', exc_info=True)


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
            # Map display name -> (procedure_class, inputs_list, x_axis_labels, y_axis_labels)
            self._procedure_infos = {
                'RandomProcedure': (
                    RandomProcedure,
                    ['iterations', 'delay'],
                    ['Iteration'],
                    ['Random Number 1', 'Random Number 2'],
                ),
                'Keithley Gate Current': (
                    Keithley_gatecurrent_Procedure,
                    ['resource', 'voltage', 'compliance'],
                    ['Voltage (V)'],
                    ['Current (A)'],
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
            if start_name:
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

        info = self._procedure_infos.get(text)
        if info is None:
            log.warning('Selected unknown procedure: %s', text)
            return
        cls, inputs_list, x_axis_labels, y_axis_labels = info
        if cls == self.procedure_class:
            return

        # Update the procedure class and current inputs list used by the window
        self.procedure_class = cls
        self._current_inputs_list = list(inputs_list)

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
            self._update_browser_for_procedure(self.procedure_class)
        except Exception:
            log.debug('Failed to update browser for new procedure', exc_info=True)

        # Rebuild plots/dock widget to reflect new procedure (x/y labels, columns)
        try:
            self._update_plots_for_procedure(self.procedure_class, x_axis_labels, y_axis_labels)
        except Exception:
            log.debug('Failed to update plots for new procedure', exc_info=True)

        log.info('Switched procedure to %s', text)


    def _update_browser_for_procedure(self, procedure_class):
        """Update the browser headers/columns to match the selected procedure.

        The Browser constructor sets header labels based on the procedure_class
        and a list of display_parameters. We update the browser's procedure_class
        and its header to reflect the new procedure.
        """
        try:
            browser = self.browser_widget.browser
            # Keep measured_quantities as-is, but update procedure_class and display_parameters
            browser.procedure_class = procedure_class
            display_parameters = list(getattr(procedure_class, 'DATA_COLUMNS', []))
            # The Browser expects display_parameters to be parameter names (not DATA_COLUMNS)
            # We'll try to reuse self.displays if provided; otherwise, try to use
            # any Parameter attributes on the procedure_class that match DATA_COLUMNS.
            # Fallback: keep existing display_parameters.
            if hasattr(self, 'displays') and self.displays:
                display_parameters = list(self.displays)

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
        except Exception:
            log.exception('Error updating browser headers for new procedure', exc_info=True)


    def _update_plots_for_procedure(self, procedure_class, x_axis_labels=None, y_axis_labels=None):
        """Rebuild or replace the DockWidget so plots reflect the chosen procedure.

        Strategy: create a new DockWidget instance with the selected procedure_class
        and replace the existing one in the window's widget_list and tabs.
        """
        try:
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

            # Update browser measured_quantities if necessary
            if hasattr(self, 'browser') and hasattr(new_dock, 'x_axis_labels'):
                self.browser.measured_quantities.update(new_dock.x_axis_labels + new_dock.y_axis_labels)

            log.info('Rebuilt DockWidget plots for procedure %s', procedure_class.__name__)
        except Exception:
            log.exception('Error rebuilding plots for new procedure', exc_info=True)



if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())