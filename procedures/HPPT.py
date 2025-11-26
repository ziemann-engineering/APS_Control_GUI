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
                self.keithley.reset()
                self.keithley.line_frequency = 50
                self.keithley.wires = 2
                self.keithley.use_front_terminals()
                # Configure Keithley for current measurement (voltage will be set by procedure when needed)
                self.keithley.measure_current(nplc=10, current=0.1, auto_range=True) # Set to autorange, 100 mA max will be ignored
                self.keithley.compliance_current = 0.1  # 0.1A compliance
                self.keithley.disable_source()  # Start with output disabled
            except Exception as e:
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
        """Configure all 3 AUX PSU channels with voltage and current limits."""
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
                self.aux_psu.enable_output(channel, True)
                log.info(f'AUX PSU Ch{channel} configured and enabled: {voltage}V, {current}A')
        except Exception as e:
            log.exception(f'Failed to configure AUX PSU channels: {e}')

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
            # Check system safety before starting test
            if not self.aps.is_safe():
                log.error('APS system safety check failed')
                return

            log.info('Starting HPPT test...')
            log.info(f'Test parameters: Voltage={self.test_voltage}V, On-time={self.dut_on_time}ns, '
                    f'Period={self.pulse_period}ms, Pulses={self.pulse_count}, '
                    f'Gate measurement={self.gate_measurement}')

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
            
            # Monitor with timeout
            self.aps.monitor_messages(handle_message, timeout=self.timeout)

        except Exception as e:
            log.exception('Error during HPPT test execution: %s', e)

    def _measure_current_with_keithley(self):
        """Perform current measurement with Keithley SMU.
        
        Returns:
            Measured current in Amperes, or NaN if measurement failed
        """
        if self.keithley is None:
            log.warning('No Keithley SMU available for current measurement')
            return float('nan')

        try:
            log.info(f'Enabling Keithley SMU output at {self.measurement_voltage}V for current measurement')

            # Set voltage and enable output
            self.keithley.source_voltage = self.measurement_voltage
            self.keithley.enable_source()
            
            # Wait for settling
            time.sleep(0.01)
            
            # Measure current
            current = self.keithley.current
            log.info(f'Measured current: {current*1e6:.6f} uA')
            
            # Disable output
            self.keithley.disable_source()
            self.keithley.source_voltage = 0
            
            return float(current)
            
        except Exception as e:
            log.error(f'Error during Keithley current measurement: {e}')
            
            # Ensure output is disabled even on error
            try:
                if self.keithley:
                    self.keithley.disable_source()
                    self.keithley.source_voltage = 0
            except Exception:
                pass
                
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

            # Disconnect from auxiliary PSU
            if self.aux_psu is not None:
                try:
                    # Disable all outputs before disconnecting
                    for ch in (1, 2, 3):
                        try:
                            self.aux_psu.enable_output(ch, False)
                        except Exception:
                            pass
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
