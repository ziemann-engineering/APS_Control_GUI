import logging
import sys
import os
import time
import re
from pymeasure.experiment import Procedure
from pymeasure.experiment import FloatParameter, Parameter, BooleanParameter

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
    #filename = f'{datetime.now():%Y-%m-%d_%H-%M-%S}' # Default filename pattern, can also use {date}, {time}, {measurement_voltage}, etc.

    # parameters for the procedure
    aps_port = Parameter('APS Serial Port', default='COM3')
    keithley_resource = Parameter('Keithley 2470 Resource', default='')
    measurement_voltage = FloatParameter('Measurement voltage', units='V', default=20.0)
    wait_for_completion = BooleanParameter('Wait for test completion', default=True)
    timeout = FloatParameter('Test timeout', units='s', default=30.0)

    DATA_COLUMNS = ['Timestamp', 'Event Type', 'Message', 'Current (A)', 'Burst Count']

    def startup(self):
        """Connect to the APS controller and Keithley 2470.

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

        # Initialize Keithley 2470
        if not self.keithley_resource:
            log.warning('No Keithley resource provided - current measurements will be skipped')
        else:
            try:
                from pymeasure.instruments import keithley
                self.keithley = keithley.Keithley2470(self.keithley_resource)
                log.info(f'Connected to Keithley 2470 on {self.keithley_resource}')
                
                # Configure Keithley for voltage source, current measurement
                self.keithley.reset()
                self.keithley.use_front_terminals()
                self.keithley.measure_current()
                self.keithley.source_voltage = 0  # Start with 0V
                self.keithley.compliance_current = 1.0  # 1A compliance
                
            except Exception as e:
                log.exception(f'Error initializing Keithley 2470: {e}')
                self.keithley = None

        if self.aps is None:
            log.warning('Could not connect to APS controller; no instrument available')
        if self.keithley is None:
            log.warning('Could not connect to Keithley 2470; current measurements will be skipped')

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
            
            # Start HPPT test
            start_response = self.aps.hppt_test()
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
                'Message': 'HPPT test initiated successfully',
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
        """Perform current measurement with Keithley 2470.
        
        Returns:
            Measured current in Amperes, or NaN if measurement failed
        """
        if self.keithley is None:
            log.warning('No Keithley available for current measurement')
            return float('nan')

        try:
            log.info(f'Enabling Keithley output at {self.measurement_voltage}V for current measurement')
            
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
