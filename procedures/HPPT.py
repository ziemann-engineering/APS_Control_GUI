import logging
import time
import re
from pymeasure.experiment import Procedure
from pymeasure.experiment import FloatParameter, Parameter, BooleanParameter, IntegerParameter

#from datetime import datetime

from hardware.APS_controller import APSController

log = logging.getLogger(__name__)


class HighPowerPulseTest(Procedure):
    # common properties of the procedure
    name = 'High Power Pulse Test (HPPT)' # For display
    internal_name = 'High_Power_Pulse_Test' # For internal use, no spaces or special chars
    short_name = 'HPPT' # For directory naming
    description = "High Power Pulse Test using APS controller and Keithley SMU for current measurements."
    #filename = f'{datetime.now():%Y-%m-%d_%H-%M-%S}' # Default filename pattern, can also use {date}, {time}, {measurement_voltage}, etc.

    # APS connection parameters
    aps_port = Parameter('APS Serial Port', default='COM0')
    keithley_resource = Parameter('Keithley SMU Resource', default='')
    aux_psu_resource = Parameter('AUX PSU Resource', default='')

    # Device identification strings (populated from connection tests or startup)
    aps_id = Parameter('APS Firmware Info', default='')
    keithley_id = Parameter('Keithley SMU ID', default='')
    aux_psu_id = Parameter('AUX PSU ID', default='')
    
    # HPPT test parameters (from firmware: voltage_v, on_time_ns, period_s, pulse_count, measurement)
    test_voltage = FloatParameter('Test Voltage', units='V', default=100.0, 
                                  minimum=0.0, maximum=2000.0)
    dut_on_time = IntegerParameter('DUT On-Time', units='ns', default=100, 
                                    minimum=14, maximum=3000)
    pulse_period = FloatParameter('Pulse Period', units='ms', default=1.0, 
                                   minimum=0.001, maximum=1000.0)
    pulse_count = IntegerParameter('Pulse Count', default=1000, minimum=1, maximum=1000000)
    gate_measurement = BooleanParameter('Wait for Gate Measurement', default=False)
    
    # Auxiliary PSU channel parameters (format: "voltage, current" e.g. "24.0, 0.1")
    aux_psu_ch1 = Parameter('AUX PSU Ch1 (V, A)', default='24.0, 0.5')
    aux_psu_ch2 = Parameter('AUX PSU Ch2 (V, A)', default='5.0, 0.1')
    aux_psu_ch3 = Parameter('AUX PSU Ch3 (V, A)', default='15.0, 0.1')
    
    # Keithley measurement parameters
    measurement_voltage = FloatParameter('Keithley Measurement Voltage', units='V', default=20.0)
    
    # General test parameters
    wait_for_completion = BooleanParameter('Wait for test completion', default=True)
    timeout = FloatParameter('Test timeout', units='s', default=300.0)

    DATA_COLUMNS = ['Timestamp', 'Burst', 'Current (A)', 'Voltage (V)']
    
    # GUI Configuration
    INPUTS = [
        'test_voltage',
        'dut_on_time',
        'pulse_period',
        'pulse_count',
        'gate_measurement',
        'measurement_voltage',
        'wait_for_completion', 
        'aux_psu_ch1',
        'aux_psu_ch2',
        'aux_psu_ch3',
        'timeout'
    ]
    
    DISPLAYS = INPUTS  # Display same parameters as inputs
    
    X_AXIS = 'Burst'
    Y_AXIS = 'Current (A)'
    
    # Hardware Configuration for Startup Dialog
    HARDWARE = {
        'nge103_psu': {
            'display_name': 'R&S NGE103 Power Supply (Auxiliary PSU)',
            'parameters': {
                'connection': {
                    'label': 'VISA Resource',
                    'default': '',
                    'placeholder': 'e.g., ASRL8::INSTR for COM8'
                }
            }
        },
        'hmc8043_psu': {
            'display_name': 'R&S HMC8043 Power Supply',
            'parameters': {
                'connection': {
                    'label': 'VISA Resource',
                    'default': '',
                    'placeholder': 'e.g., USB0::0x0957::0x8B18::INSTR'
                }
            }
        },
        'aps_controller': {
            'display_name': 'APS Controller',
            'parameters': {
                'connection': {
                    'label': 'Serial Port',
                    'default': 'COM5',
                    'placeholder': 'e.g., COM5, /dev/ttyUSB0'
                }
            }
        },
        'keithley_smu': {
            'display_name': 'Keithley SMU',
            'parameters': {
                'connection': {
                    'label': 'VISA Resource',
                    'default': 'GPIB::24',
                    'placeholder': 'e.g., GPIB::24'
                }
            }
        },
        'keysight_oscilloscope': {
            'display_name': 'Keysight Oscilloscope',
            'parameters': {
                'connection': {
                    'label': 'VISA Resource',
                    'default': 'USB0::0x2A8D::0x904A::MY58150189::INSTR',
                    'placeholder': 'e.g., USB0::0x2A8D::0x904A::MY58150189::INSTR'
                }
            }
        }
    }

    def startup(self):
        """Connect to the APS controller, Keithley SMU, and auxiliary PSU.

        If no port is provided or connection fails, instruments remain None and
        execute() will emit error status.
        """
        self.aps = None
        self.keithley = None
        self.aux_psu = None
        self.aux_psu_type = None
        self.burst_count = 0  # Track total burst count

        # Get connection parameters from startup config
        self._apply_connection_parameters()
        
        # Configure auxiliary PSU FIRST (before other instruments)
        aux_type, aux_resource = self._get_aux_psu_configuration()
        if aux_resource:
            self.aux_psu_type = aux_type or 'nge103_psu'
            try:
                controller = None
                if self.aux_psu_type == 'hmc8043_psu':
                    from hardware.rs_hmc8043 import RSHMC8043Controller
                    controller = RSHMC8043Controller(aux_resource)
                else:
                    from hardware.rs_nge103 import NGE100
                    controller = NGE100(aux_resource)

                if controller and controller.connect():
                    self.aux_psu = controller
                    log.info(f'Connected to auxiliary PSU ({self.aux_psu_type}) on {aux_resource}')
                    # Capture IDN
                    try:
                        if not self.aux_psu_id:
                            if hasattr(controller, 'ID'):
                                self.aux_psu_id = controller.ID()
                            elif hasattr(controller, 'psu') and controller.psu:
                                self.aux_psu_id = controller.psu.query('*IDN?').strip()
                    except Exception:
                        pass
                    self._configure_aux_psu_channels()
                else:
                    log.error('Failed to connect to auxiliary PSU')
                    self.aux_psu = None
            except Exception as e:
                log.exception(f'Error initializing auxiliary PSU: {e}')
                self.aux_psu = None
        else:
            log.warning('No auxiliary PSU resource provided - power supply control will be skipped')
        
        # Initialize APS controller
        if not self.aps_port:
            log.warning('No APS port provided for HPPT procedure')
        else:
            try:
                self.aps = APSController(self.aps_port)
                if self.aps.connect():
                    log.info(f'Connected to APS controller on {self.aps_port}')
                    # Capture firmware info
                    try:
                        info_data = self.aps.info(print_response=False)
                        if info_data and not self.aps_id:
                            board = info_data.get('Board', info_data.get('board', ''))
                            build_time = info_data.get('Build time', info_data.get('build_time', ''))
                            self.aps_id = f"{board}, Built: {build_time}".strip(', ')
                    except Exception:
                        pass
                    
                    # Check if system is safe
                    if not self.aps.is_safe():
                        log.error('APS system is not in safe state - check safety cover and emergency button')
                        self.aps.disconnect()
                        self.aps = None
                        return
                        
                else:
                    log.error(f'Failed to connect to APS controller on {self.aps_port}')
                    self.aps = None
            except Exception as e:
                log.exception(f'Error initializing APS controller: {e}')
                self.aps = None

        # Initialize Keithley SMU
        if not self.keithley_resource:
            log.warning('No Keithley resource provided - current measurements will be skipped')
        else:
            try:
                from pymeasure.instruments.keithley import Keithley2400
                self.keithley = Keithley2400(self.keithley_resource)
                log.info(f'Connected to Keithley SMU on {self.keithley_resource}')
                # Capture IDN
                try:
                    if not self.keithley_id:
                        self.keithley_id = self.keithley.id
                except Exception:
                    pass
                self.keithley.reset()
                self.keithley.line_frequency = 50
                self.keithley.wires = 2
                self.keithley.use_front_terminals()
                # Configure Keithley for current measurement (voltage will be set by procedure when needed)
                self.keithley.measure_current(nplc=10, current=0.1, auto_range=True) # Set to autorange, 100 mA max will be ignored
                self.keithley.compliance_current = 0.1  # 0.1A compliance
                self.keithley.disable_source()  # Start with output disabled
            except Exception as e:
                err_str = str(e)
                # gpib_ctypes raises GpibError "dev() error: Errno 0" when no GPIB hardware is
                # present.  That is a normal "device not found" situation when running without
                # instruments, so log it as a warning (no traceback) rather than an exception.
                if 'dev()' in err_str or 'GpibError' in type(e).__name__:
                    log.warning(f'Keithley SMU not found on {self.keithley_resource} '
                                f'(GPIB device not present) — current measurements will be skipped')
                else:
                    log.exception(f'Error initializing Keithley SMU: {e}')
                self.keithley = None

        if self.aps is None:
            log.warning('Could not connect to APS controller; no instrument available')
        if self.keithley is None:
            log.warning('Could not connect to Keithley SMU; current measurements will be skipped')
        if self.aux_psu is None:
            log.warning('Could not connect to auxiliary PSU; power supply control will be skipped')

    def _apply_connection_parameters(self):
        """Apply connection parameters from startup dialog to procedure attributes."""
        # Try instance attribute first, then class attribute (set by main.py)
        params = getattr(self, 'connection_parameters', None)
        if not params:
            params = getattr(self.__class__, '_startup_connection_parameters', None)
        if not params or not isinstance(params, dict):
            return
        
        # APS Controller
        aps_params = params.get('aps_controller', {})
        if isinstance(aps_params, dict):
            aps_port = aps_params.get('connection') or aps_params.get('port') or aps_params.get('resource')
            if aps_port:
                self.aps_port = aps_port
        
        # Keithley SMU
        keithley_params = params.get('keithley_smu', {})
        if isinstance(keithley_params, dict):
            keithley_res = keithley_params.get('connection') or keithley_params.get('resource')
            if keithley_res:
                self.keithley_resource = keithley_res

        # AUX PSU resource (for CSV traceability)
        aux_info = params.get('aux_psu', {})
        if isinstance(aux_info, dict):
            aux_res = aux_info.get('connection') or aux_info.get('resource', '')
            if aux_res:
                self.aux_psu_resource = aux_res

    def _apply_device_ids(self, device_ids):
        """Apply device ID strings from connection tests to procedure parameters."""
        if not isinstance(device_ids, dict):
            return
        if device_ids.get('aps_controller'):
            self.aps_id = device_ids['aps_controller']
        if device_ids.get('keithley_smu'):
            self.keithley_id = device_ids['keithley_smu']
        # Accept either active aux PSU type
        for key in ('aux_psu', 'nge103_psu', 'hmc8043_psu'):
            if device_ids.get(key):
                self.aux_psu_id = device_ids[key]
                break

    def _get_aux_psu_configuration(self):
        """Get AUX PSU type and resource from connection_parameters."""
        params = getattr(self, 'connection_parameters', None)
        if not params:
            params = getattr(self.__class__, '_startup_connection_parameters', None)
        if not params or not isinstance(params, dict):
            params = {}
        aux_info = params.get('aux_psu') if isinstance(params, dict) else {}
        if not isinstance(aux_info, dict):
            aux_info = {}
        resource = aux_info.get('connection') or aux_info.get('resource') or ''
        aux_type = aux_info.get('type')
        if resource and not aux_type:
            aux_type = 'nge103_psu'
        return aux_type, resource

    def _parse_aux_psu_channel(self, value):
        """Parse 'voltage, current' string into (voltage, current) floats."""
        try:
            parts = [p.strip() for p in str(value).split(',')]
            if len(parts) >= 2:
                return float(parts[0]), float(parts[1])
            elif len(parts) == 1:
                return float(parts[0]), 0.1  # Default current
        except (ValueError, TypeError):
            pass
        return 0.0, 0.1  # Safe defaults

    def _configure_aux_psu_channels(self):
        """Configure all 3 AUX PSU channels with voltage and current limits.
        
        Only enables Ch1 initially. Ch2 and Ch3 are enabled when measurement starts.
        """
        if self.aux_psu is None:
            return
        channel_params = [
            (1, self.aux_psu_ch1),
            (2, self.aux_psu_ch2),
            (3, self.aux_psu_ch3),
        ]
        try:
            for channel, param_value in channel_params:
                voltage, current = self._parse_aux_psu_channel(param_value)
                self.aux_psu.set_voltage(channel, voltage)
                self.aux_psu.set_current(channel, current)
                # Only enable Ch1 on startup; Ch2/Ch3 enabled when measurement starts
                if channel == 1:
                    self.aux_psu.enable_output(channel, True)
                    log.info(f'AUX PSU Ch{channel} configured and enabled: {voltage}V, {current}A')
                else:
                    self.aux_psu.enable_output(channel, False)
                    log.info(f'AUX PSU Ch{channel} configured (disabled until measurement): {voltage}V, {current}A')
        except Exception as e:
            log.exception(f'Failed to configure AUX PSU channels: {e}')

    def _enable_aux_psu_measurement_channels(self):
        """Enable AUX PSU Ch2 and Ch3 for measurement with current parameter values."""
        if self.aux_psu is None:
            return
        try:
            channel_params = [
                (2, self.aux_psu_ch2),
                (3, self.aux_psu_ch3),
            ]
            for channel, param_value in channel_params:
                voltage, current = self._parse_aux_psu_channel(param_value)
                self.aux_psu.set_voltage(channel, voltage)
                self.aux_psu.set_current(channel, current)
                self.aux_psu.enable_output(channel, True)
                log.info(f'AUX PSU Ch{channel} enabled for measurement: {voltage}V, {current}A')
        except Exception as e:
            log.exception(f'Failed to enable AUX PSU measurement channels: {e}')

    def _disable_aux_psu_measurement_channels(self):
        """Disable AUX PSU Ch2 and Ch3 after measurement."""
        if self.aux_psu is None:
            return
        try:
            for channel in (2, 3):
                self.aux_psu.enable_output(channel, False)
                log.info(f'AUX PSU Ch{channel} disabled after measurement')
        except Exception as e:
            log.exception(f'Failed to disable AUX PSU measurement channels: {e}')

    def _cycle_aux_psu_ch1(self):
        """Turn off all AUX PSU channels for 1 second, then turn Ch1 back on.
        
        Used during abort to reset the DUT power.
        """
        if self.aux_psu is None:
            return
        try:
            log.info('Cycling AUX PSU: turning off all channels for 1 second')
            for channel in (1, 2, 3):
                self.aux_psu.enable_output(channel, False)
            time.sleep(1.0)
            self.aux_psu.enable_output(1, True)
            log.info('AUX PSU Ch1 turned back on')
        except Exception as e:
            log.exception(f'Failed to cycle AUX PSU: {e}')

    def execute(self):
        """Execute HPPT test on APS controller with message monitoring.

        Test sequence:
        1. Get initial SMU current measurement (Burst 0)
        2. Send HPPT_test command
        3. Send start
        4. Each time "recharging" is received, start another current measurement
        5. When measurement done, send "Ig measurement done"
        6. Repeat 4+5 until "measurement complete" is received
        """
        if self.aps is None:
            log.error('No APS controller available for HPPT test')
            return

        self.measurement_count = 0

        try:
            # Check if abort was requested before we even start
            if self.should_stop():
                log.info('HPPT test aborted before start')
                return

            # Check system safety before starting test
            if not self.aps.is_safe():
                log.error('APS system safety check failed')
                return

            log.info('Starting HPPT test...')
            log.info(f'Test parameters: Voltage={self.test_voltage}V, On-time={self.dut_on_time}ns, '
                    f'Period={self.pulse_period}ms, Pulses={self.pulse_count}, '
                    f'Gate measurement={self.gate_measurement}')

            # Enable AUX PSU Ch2 and Ch3 for measurement
            self._enable_aux_psu_measurement_channels()

            self.timeout = self.pulse_period * self.pulse_count / 1000.0 + 30.0  # Estimate timeout based on test duration + 30s buffer
            
            # Step 1: Send HPPT_test command
            log.info('Step 1: Sending HPPT_test command')
            period_s = self.pulse_period / 1000.0
            
            start_response = self.aps.hppt_test(
                voltage_v=self.test_voltage,
                on_time_ns=self.dut_on_time,
                period_s=period_s,
                pulse_count=self.pulse_count,
                measurement=self.gate_measurement
            )
            log.info(f'HPPT test command response: {start_response}')
            
            if not start_response:
                log.error('Failed to send HPPT test command')
                return

            # Step 2: If gate measurement enabled, do pre-test measurement
            if self.gate_measurement:
                log.info('Step 2: Performing pre-test gate measurement (gate_measurement enabled)')
                pre_test_current = self._measure_current_with_keithley()
                self.emit('results', {
                    'Timestamp': time.time(),
                    'Burst': 0,
                    'Current (A)': pre_test_current,
                    'Voltage (V)': self.measurement_voltage
                })

            # Step 3: Send start command
            log.info('Step 3: Sending start command')
            self.aps.start()
            log.info(f'HPPT test started: {self.test_voltage}V, {self.dut_on_time}ns, {self.pulse_count} pulses')

            # Monitor messages from APS controller (Steps 4-6)
            log.info('Steps 4-6: Monitoring APS controller messages...')
            
            # Use the new monitor_messages method from APS controller
            def handle_message(msg):
                """Process each message from the controller."""
                # Log at appropriate level based on message content
                msg_lower = msg.lower()
                if 'error' in msg_lower:
                    log.error(f'APS: {msg}')
                elif 'warning' in msg_lower:
                    log.warning(f'APS: {msg}')
                else:
                    log.info(f'APS: {msg}')
                
                # Check if abort was requested
                if self.should_stop():
                    log.info('HPPT test aborted by user')
                    try:
                        self.aps.stop_test()
                    except Exception:
                        pass
                    return False  # Stop monitoring

                # Check for measurement complete
                if 'Test sequence completed' in msg_lower or msg.endswith('>'):
                    log.info('HPPT test completed')
                    return False  # Stop monitoring
                
                # Check for burst messages
                burst_match = re.match(r'burst\s+(\d+)', msg, re.IGNORECASE)
                if burst_match:
                    burst_number = int(burst_match.group(1))
                    self.burst_count += 1  # Increment total burst count
                    log.info(f'Detected burst {burst_number} (total: {self.burst_count})')
                
                # Check for measuring message
                if 'measuring' in msg.lower():
                    log.info('Detected measuring event - performing current measurement')
                    
                    # Measure current
                    current = self._measure_current_with_keithley()
                    
                    self.measurement_count += 1

                    # Emit measurement data. use measurement_count for burst numbering
                    # (more predictable, and allows final post-measurement to be burst N+1)
                    self.emit('results', {
                        'Timestamp': time.time(),
                        'Burst': self.measurement_count,
                        'Current (A)': current,
                        'Voltage (V)': self.measurement_voltage
                    })
                    
                    # Send "Ig measured" message to APS controller
                    log.debug('Sending "Ig done" to APS controller')
                    try:
                        if self.aps and self.aps.serial_conn:
                            self.aps.serial_conn.write(b'Ig done\r\n')
                            self.aps.serial_conn.flush()
                            log.debug('"Ig done" sent successfully')
                    except Exception as e:
                        log.error(f'Failed to send "Ig measurement done": {e}')
                
                return True  # Continue monitoring
            
            # Monitor with timeout, also checking should_stop
            self.aps.monitor_messages(handle_message, stop_condition=self.should_stop, timeout=self.timeout)

            # If aborted, stop the test on the controller and cycle Ch1
            if self.should_stop():
                log.info('Stopping APS test due to abort')
                try:
                    self.aps.stop_test()
                except Exception:
                    pass
                # Cycle Ch1 off for 1s then back on to reset DUT
                self._cycle_aux_psu_ch1()

            # Disable AUX PSU Ch2 and Ch3 after measurement completes
            self._disable_aux_psu_measurement_channels()

        except Exception as e:
            # On any unexpected error: try to stop the APS controller, then cycle AUX PSU Ch1
            # to reset the DUT power (in case firmware doesn't respond to stop while running).
            if self.aps is not None:
                try:
                    self.aps.stop_test()
                    log.info('Sent stop_test to APS controller after error')
                except Exception:
                    log.debug('stop_test failed or not acknowledged; cycling AUX PSU Ch1 instead')
                # Always cycle Ch1 to ensure DUT is power-cycled after an error
                self._cycle_aux_psu_ch1()
            # Ensure measurement channels are disabled even on error
            self._disable_aux_psu_measurement_channels()
            log.exception('Error during HPPT test execution: %s', e)

    def _reinitialize_keithley(self) -> bool:
        """Close and reopen the Keithley VISA session to recover from persistent VI_ERROR_SYSTEM_ERROR.

        VI_ERROR_SYSTEM_ERROR (-1073807360) can repeat many times in a row when the NI-VISA
        session itself becomes corrupted (e.g. after a USB-GPIB adapter mini-disconnect caused
        by EMI from HV pulses, or after the GPIB bus gets stuck).  A plain retry on the same
        resource object will always fail in that case.  This method:
          1. Sends a GPIB Device Clear (viClear / SDC) to the instrument to wake it if it is
             stuck in a trigger-wait state.
          2. Closes the existing VISA session so NI-VISA releases its internal state.
          3. Waits 1 s for the NI-488.2 driver and USB adapter to settle.
          4. Opens a fresh VISA session and reconfigures the instrument.

        Returns True if the new session is working, False otherwise (self.keithley is set to
        None in that case so further measurement attempts are skipped for the rest of the run).
        """
        log.warning('Keithley VI_ERROR_SYSTEM_ERROR persisted across retries — '
                    'reinitializing VISA session to recover')

        # Step 1: GPIB Device Clear on the existing (possibly broken) connection.
        # This resets the instrument's input/output buffers and aborts any pending operation.
        try:
            self.keithley.adapter.connection.clear()
            log.debug('Keithley GPIB Device Clear sent')
            time.sleep(0.2)
        except Exception as e:
            log.debug(f'GPIB Device Clear skipped (session already corrupt): {e}')

        # Step 2: Close the existing VISA session.
        try:
            self.keithley.adapter.connection.close()
            log.debug('Keithley VISA session closed')
        except Exception as e:
            log.debug(f'Error closing Keithley VISA session (ignoring): {e}')

        self.keithley = None

        # Step 3: Wait for NI-VISA / USB adapter to fully reset.
        time.sleep(1.0)

        # Step 4: Open a fresh session and reconfigure.
        try:
            from pymeasure.instruments.keithley import Keithley2400
            self.keithley = Keithley2400(self.keithley_resource)
            self.keithley.reset()
            self.keithley.line_frequency = 50
            self.keithley.wires = 2
            self.keithley.use_front_terminals()
            self.keithley.measure_current(nplc=10, current=0.1, auto_range=True)
            self.keithley.compliance_current = 0.1
            self.keithley.disable_source()
            log.info('Keithley VISA session reinitialized successfully')
            return True
        except Exception as e:
            log.error(f'Keithley VISA reinitialization failed — current measurements will be '
                      f'skipped for the rest of this run: {e}')
            self.keithley = None
            return False

    def _measure_current_with_keithley(self):
        """Perform current measurement with Keithley SMU.

        Uses an escalating recovery strategy for VI_ERROR_SYSTEM_ERROR (-1073807360):
          Attempt 1 — normal measurement.
          Attempt 2 — 0.5 s wait, then retry (handles genuine single-shot transients).
          Attempt 3 — full VISA session teardown + rebuild via _reinitialize_keithley(),
                      then one final attempt.

        VI_ERROR_SYSTEM_ERROR often repeats several times in a row because the NI-VISA
        session object itself is in a bad state after the first failure.  Simply retrying
        on the same session cannot fix that; a fresh VISA open_resource() call is required.

        Returns:
            Measured current in Amperes, or NaN if all recovery attempts failed.
        """
        if self.keithley is None:
            log.warning('No Keithley SMU available for current measurement')
            return float('nan')

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                log.info(f'Enabling Keithley SMU output at {self.measurement_voltage}V for current measurement'
                         + (f' (attempt {attempt}/{max_attempts})' if attempt > 1 else ''))

                self.keithley.source_voltage = self.measurement_voltage
                self.keithley.enable_source()
                time.sleep(0.01)
                current = self.keithley.current
                log.info(f'Measured current: {current*1e6:.6f} uA')
                self.keithley.disable_source()
                self.keithley.source_voltage = 0
                return float(current)

            except Exception as e:
                err_str = str(e)
                is_visa_system_error = (
                    'VI_ERROR_SYSTEM_ERROR' in err_str
                    or '-1073807360' in err_str
                    or 'system error' in err_str.lower()
                )

                # Always try to disable the source before any recovery step.
                try:
                    self.keithley.disable_source()
                    self.keithley.source_voltage = 0
                except Exception:
                    pass

                if not is_visa_system_error or attempt == max_attempts:
                    log.error(f'Error during Keithley current measurement: {e}')
                    return float('nan')

                if attempt == 1:
                    # Possibly a genuine one-off transient — simple wait and retry.
                    log.warning(f'Keithley VI_ERROR_SYSTEM_ERROR on attempt {attempt}; '
                                f'waiting 0.5 s before retry: {e}')
                    time.sleep(0.5)

                elif attempt == 2:
                    # Still failing — the VISA session is likely corrupt.  Rebuild it.
                    log.warning(f'Keithley VI_ERROR_SYSTEM_ERROR still present after retry (attempt {attempt}): {e}')
                    if not self._reinitialize_keithley():
                        # Reinit failed; no point attempting a third measurement.
                        return float('nan')
                    time.sleep(0.5)

        return float('nan')

    def shutdown(self):
        """Clean shutdown - stop any running tests and disconnect from all instruments."""
        try:
            if self.aps is not None:
                # Stop any running test for safety
                try:
                    self.aps.stop_test()
                    log.info('Stopped any running tests during shutdown')
                except Exception as e:
                    log.debug('Failed to stop test during shutdown: %s', e)
                
                # Disconnect from APS controller
                try:
                    self.aps.disconnect()
                    log.info('Disconnected from APS controller')
                except Exception as e:
                    log.debug('Failed to disconnect from APS: %s', e)
                
                self.aps = None

            # Disconnect from auxiliary PSU (leave outputs enabled)
            if self.aux_psu is not None:
                try:
                    self.aux_psu.disconnect()
                    log.info('Disconnected from auxiliary PSU')
                except Exception as e:
                    log.debug('Failed to disconnect from auxiliary PSU: %s', e)
                self.aux_psu = None

        except Exception:
            log.debug('Exception in HPPT procedure shutdown', exc_info=True)
    
    def set_hppt_parameter(self, parameter: str, value: float):
        """Set an HPPT parameter on the APS controller.
        
        Args:
            parameter: Parameter name
            value: Parameter value
            
        Returns:
            True if successful, False otherwise
        """
        if self.aps is not None:
            try:
                result = self.aps.hppt_parameter(parameter, value)
                log.info(f'Set HPPT parameter {parameter} = {value}')
                return result
            except Exception as e:
                log.error(f'Failed to set HPPT parameter {parameter}: {e}')
                return False
        else:
            log.warning('No APS controller available to set parameters')
            return False
    
    def get_hppt_parameter(self, parameter: str):
        """Get an HPPT parameter from the APS controller.
        
        Args:
            parameter: Parameter name
            
        Returns:
            Parameter value or None if error
        """
        if self.aps is not None:
            try:
                value = self.aps.hppt_parameter(parameter)
                log.info(f'Got HPPT parameter {parameter} = {value}')
                return value
            except Exception as e:
                log.error(f'Failed to get HPPT parameter {parameter}: {e}')
                return None
        else:
            log.warning('No APS controller available to get parameters')
            return None
