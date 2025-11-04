import logging
import sys
import os
import time
import re
from pymeasure.experiment import Procedure
from pymeasure.experiment import FloatParameter, Parameter, BooleanParameter, IntegerParameter

#from datetime import datetime

# Add parent directory to Python path to import APS controller
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from APS_controller import APSController

log = logging.getLogger(__name__)


class HighPowerPulseTest(Procedure):
    # common properties of the procedure
    name = 'High Power Pulse Test (HPPT)' # For display
    internal_name = 'High_Power_Pulse_Test' # For internal use, no spaces or special chars
    short_name = 'HPPT' # For directory naming
    description = "High Power Pulse Test using APS controller and Keithley SMU for current measurements."
    #filename = f'{datetime.now():%Y-%m-%d_%H-%M-%S}' # Default filename pattern, can also use {date}, {time}, {measurement_voltage}, etc.

    # APS connection parameters
    aps_port = Parameter('APS Serial Port', default='COM3')
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
    
    # NGE103 PSU parameters
    nge103_ch1_voltage = FloatParameter('NGE103 Ch1 Voltage', units='V', default=24.0,
                                        minimum=0.0, maximum=32.0)
    nge103_ch1_current = FloatParameter('NGE103 Ch1 Current', units='A', default=0.1,
                                        minimum=0.0, maximum=3.0)
    nge103_ch2_voltage = FloatParameter('NGE103 Ch2 Voltage', units='V', default=5.0,
                                        minimum=0.0, maximum=32.0)
    nge103_ch2_current = FloatParameter('NGE103 Ch2 Current', units='A', default=0.1,
                                        minimum=0.0, maximum=3.0)
    nge103_ch3_voltage = FloatParameter('NGE103 Ch3 Voltage', units='V', default=15.0,
                                        minimum=0.0, maximum=32.0)
    nge103_ch3_current = FloatParameter('NGE103 Ch3 Current', units='A', default=0.1,
                                        minimum=0.0, maximum=3.0)
    
    # Keithley measurement parameters
    measurement_voltage = FloatParameter('Keithley Measurement Voltage', units='V', default=20.0)
    
    # General test parameters
    wait_for_completion = BooleanParameter('Wait for test completion', default=True)
    timeout = FloatParameter('Test timeout', units='s', default=300.0)

    DATA_COLUMNS = ['Timestamp', 'Event Type', 'Message', 'Current (A)', 'Burst Count']
    
    # GUI Configuration
    INPUTS = [
        'aps_port', 
        'keithley_resource', 
        'test_voltage',
        'dut_on_time',
        'pulse_period',
        'pulse_count',
        'gate_measurement',
        'measurement_voltage',
        'wait_for_completion', 
        'nge103_ch1_voltage',
        'nge103_ch1_current',
        'nge103_ch2_voltage',
        'nge103_ch2_current',
        'nge103_ch3_voltage',
        'nge103_ch3_current',
        'timeout'
    ]
    
    DISPLAYS = INPUTS  # Display same parameters as inputs
    
    X_AXIS = 'Timestamp'
    Y_AXIS = ['Current (A)', 'Burst Count']
    
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
            'display_name': 'R&S NGE103 Power Supply',
            'parameters': {
                'connection': {
                    'label': 'VISA Resource',
                    'default': '',
                    'placeholder': 'e.g., ASRL8::INSTR for COM8'
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
        """Connect to the APS controller and Keithley SMU.

        If no port is provided or connection fails, instruments remain None and
        execute() will emit error status.
        """
        self.aps = None
        self.keithley = None
        self.burst_count = 0  # Track total burst count
        
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
                from pymeasure.instruments import keithley
                self.keithley = keithley.KeithleySMU(self.keithley_resource)
                log.info(f'Connected to Keithley SMU on {self.keithley_resource}')
                
                # Configure Keithley for current measurement (voltage will be set by procedure when needed)
                self.keithley.reset()
                self.keithley.use_front_terminals()
                self.keithley.measure_current()
                self.keithley.compliance_current = 1.0  # 1A compliance
                self.keithley.disable_source()  # Start with output disabled
                
            except Exception as e:
                log.exception(f'Error initializing Keithley SMU: {e}')
                self.keithley = None

        if self.aps is None:
            log.warning('Could not connect to APS controller; no instrument available')
        if self.keithley is None:
            log.warning('Could not connect to Keithley SMU; current measurements will be skipped')

    def execute(self):
        """Execute HPPT test on APS controller with message monitoring.

        Monitors APS messages for 'burst n' and 'recharging' events.
        When 'recharging' is received, performs current measurement with Keithley.
        """
        if self.aps is None:
            log.warning('No APS controller available for HPPT test')
            self.emit('results', {
                'Timestamp': time.time(),
                'Event Type': 'Error',
                'Message': 'No APS controller available',
                'Current (A)': float('nan'),
                'Burst Count': self.burst_count
            })
            return

        try:
            # Check system safety before starting test
            if not self.aps.is_safe():
                log.error('APS system safety check failed')
                self.emit('results', {
                    'Timestamp': time.time(),
                    'Event Type': 'Safety Error',
                    'Message': 'APS system is not in safe state',
                    'Current (A)': float('nan'),
                    'Burst Count': self.burst_count
                })
                return

            log.info('Starting HPPT test...')
            log.info(f'Test parameters: Voltage={self.test_voltage}V, On-time={self.dut_on_time}ns, '
                    f'Period={self.pulse_period}ms, Pulses={self.pulse_count}, '
                    f'Gate measurement={self.gate_measurement}')
            
            # Convert pulse_period from ms to seconds for the command
            period_s = self.pulse_period / 1000.0
            
            # Start HPPT test with all required parameters
            start_response = self.aps.hppt_test(
                voltage_v=self.test_voltage,
                on_time_ns=self.dut_on_time,
                period_s=period_s,
                pulse_count=self.pulse_count,
                measurement=self.gate_measurement
            )
            log.info(f'HPPT test command response: {start_response}')
            
            if not start_response:
                log.error('Failed to start HPPT test')
                self.emit('results', {
                    'Timestamp': time.time(),
                    'Event Type': 'Start Failed',
                    'Message': 'Failed to start HPPT test',
                    'Current (A)': float('nan'),
                    'Burst Count': self.burst_count
                })
                return

            # Emit test start event
            self.emit('results', {
                'Timestamp': time.time(),
                'Event Type': 'Test Started',
                'Message': f'HPPT test: {self.test_voltage}V, {self.dut_on_time}ns, {self.pulse_count} pulses',
                'Current (A)': float('nan'),
                'Burst Count': self.burst_count
            })

            # Monitor messages from APS controller
            log.info('Monitoring APS controller messages...')
            self._monitor_aps_messages()

        except Exception as e:
            log.exception('Error during HPPT test execution: %s', e)
            self.emit('results', {
                'Timestamp': time.time(),
                'Event Type': 'Exception',
                'Message': f'Error during HPPT test: {str(e)}',
                'Current (A)': float('nan'),
                'Burst Count': self.burst_count
            })

    def _monitor_aps_messages(self):
        """Monitor APS controller messages for burst and recharging events.
        
        This method continuously monitors the APS serial communication for:
        - 'burst n' messages (logged)
        - 'recharging' messages (triggers current measurement)
        """
        start_time = time.time()
        
        while time.time() - start_time < self.timeout:
            try:
                # Check if test is still running
                status = self.aps.get_status()
                if not status.test_running:
                    log.info('HPPT test completed')
                    self.emit('results', {
                        'Timestamp': time.time(),
                        'Event Type': 'Test Completed',
                        'Message': 'HPPT test completed normally',
                        'Current (A)': float('nan'),
                        'Burst Count': self.burst_count
                    })
                    break

                # Read any available messages from APS controller
                # This is a custom method we'll need to implement to monitor raw serial data
                messages = self._read_aps_messages()
                
                for message in messages:
                    self._process_aps_message(message)
                
                # Small delay to prevent excessive CPU usage
                time.sleep(0.01)
                
            except Exception as e:
                log.debug(f'Error during message monitoring: {e}')
                time.sleep(0.1)
        
        # Timeout occurred
        if time.time() - start_time >= self.timeout:
            log.warning(f'HPPT monitoring timed out after {self.timeout}s')
            self.emit('results', {
                'Timestamp': time.time(),
                'Event Type': 'Timeout',
                'Message': f'HPPT monitoring timed out after {self.timeout}s',
                'Current (A)': float('nan'),
                'Burst Count': self.burst_count
            })

    def _read_aps_messages(self):
        """Read available messages from APS controller serial buffer.
        
        Returns:
            List of message strings
        """
        messages = []
        
        if not self.aps or not self.aps.serial_conn:
            return messages
            
        try:
            # Check if data is available
            if self.aps.serial_conn.in_waiting > 0:
                # Read all available data
                raw_data = self.aps.serial_conn.read(self.aps.serial_conn.in_waiting)
                data_str = raw_data.decode('ascii', errors='ignore')
                
                # Split into lines and filter out empty ones
                lines = [line.strip() for line in data_str.split('\n') if line.strip()]
                messages.extend(lines)
                
        except Exception as e:
            log.debug(f'Error reading APS messages: {e}')
            
        return messages

    def _process_aps_message(self, message):
        """Process a single APS controller message.
        
        Args:
            message: Message string from APS controller
        """
        log.info(f'APS Message: {message}')
        
        # Check for burst messages
        burst_match = re.match(r'burst\s+(\d+)', message, re.IGNORECASE)
        if burst_match:
            burst_number = int(burst_match.group(1))
            self.burst_count += 1  # Increment total burst count
            log.info(f'Detected burst {burst_number} (total: {self.burst_count})')
            
            self.emit('results', {
                'Timestamp': time.time(),
                'Event Type': 'Burst',
                'Message': f'burst {burst_number}',
                'Current (A)': float('nan'),
                'Burst Count': self.burst_count
            })
            return

        # Check for recharging message
        if 'recharging' in message.lower():
            log.info('Detected recharging event - performing current measurement')
            
            current = self._measure_current_with_keithley()
            
            self.emit('results', {
                'Timestamp': time.time(),
                'Event Type': 'Recharging',
                'Message': message,
                'Current (A)': current,
                'Burst Count': self.burst_count
            })
            return

        # Log other messages
        self.emit('results', {
            'Timestamp': time.time(),
            'Event Type': 'Message',
            'Message': message,
            'Current (A)': float('nan'),
            'Burst Count': self.burst_count
        })

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
            log.info(f'Measured current: {current:.6f} A')
            
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
        """Clean shutdown - stop any running tests and disconnect from APS."""
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
