import logging
from time import sleep
from pymeasure.experiment import Procedure
from pymeasure.experiment import FloatParameter, Parameter

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


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
