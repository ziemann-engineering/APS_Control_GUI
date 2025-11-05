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


class DoublePulseTest(Procedure):
    # common properties of the procedure
    name = 'Double Pulse Test (DPT)' # For display
    internal_name = 'Double_Pulse_Test' # For internal use, no spaces or special chars
    short_name = 'DPT' # For directory naming
    description = "Double Pulse Switching Test using APS controller, NGE100 PSU, and oscilloscope."
    #filename = f'{datetime.now():%Y-%m-%d_%H-%M-%S}' # Default filename pattern, can also use {date}, {time}, {measurement_voltage}, etc.

    # APS connection parameters
    aps_port = Parameter('APS Serial Port', default='COM3')
    nge100_resource = Parameter('NGE100 PSU Resource', default='')
    oscilloscope_resource = Parameter('Oscilloscope Resource', default='USB0::0x2A8D::0x904A::MY58150189::INSTR')
    
    # DPT test parameters (from firmware: current_a, voltage_v)
    test_current = FloatParameter('Test Current', units='A', default=10.0, 
                                  minimum=0.0, maximum=100.0)
    test_voltage = FloatParameter('Test Voltage', units='V', default=400.0, 
                                  minimum=0.0, maximum=2000.0)
    
    # NGE100 PSU parameters
    nge100_ch1_voltage = FloatParameter('NGE100 Ch1 Voltage', units='V', default=24.0,
                                        minimum=0.0, maximum=32.0)
    nge100_ch1_current = FloatParameter('NGE100 Ch1 Current', units='A', default=0.5,
                                        minimum=0.0, maximum=3.0)
    nge100_ch2_voltage = FloatParameter('NGE100 Ch2 Voltage', units='V', default=5.0,
                                        minimum=0.0, maximum=32.0)
    nge100_ch2_current = FloatParameter('NGE100 Ch2 Current', units='A', default=0.1,
                                        minimum=0.0, maximum=3.0)
    nge100_ch3_voltage = FloatParameter('NGE100 Ch3 Voltage', units='V', default=20.0,
                                        minimum=0.0, maximum=32.0)
    nge100_ch3_current = FloatParameter('NGE100 Ch3 Current', units='A', default=0.1,
                                        minimum=0.0, maximum=3.0)

    # General test parameters
    wait_for_completion = BooleanParameter('Wait for test completion', default=True)
    timeout = FloatParameter('Test timeout', units='s', default=60.0)

    DATA_COLUMNS = ['Timestamp', 'Pulse', 'Voltage (V)', 'Current (A)']
    
    # GUI Configuration
    INPUTS = [
        'aps_port', 
        'nge100_resource',
        'oscilloscope_resource',
        'test_current',
        'test_voltage',
        'nge100_ch1_voltage',
        'nge100_ch1_current',
        'nge100_ch2_voltage',
        'nge100_ch2_current',
        'nge100_ch3_voltage',
        'nge100_ch3_current',
        'wait_for_completion', 
        'timeout'
    ]
    
    DISPLAYS = INPUTS  # Display same parameters as inputs
    
    X_AXIS = 'Timestamp'
    Y_AXIS = ['Voltage (V)', 'Current (A)']
    
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
        'nge100_psu': {
            'display_name': 'R&S NGE100 Power Supply',
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
        """Connect to the APS controller, NGE100 PSU, and oscilloscope.

        If no port is provided or connection fails, instruments remain None and
        execute() will emit error status.
        """
        self.aps = None
        self.nge100 = None
        self.oscilloscope = None
        self.pulse_count = 0  # Track total pulse count
        self.pulse_duration_us = None  # Will be set from APS response
        
        # Initialize NGE100 PSU FIRST (before APS controller)
        # This ensures Ch1 is always on before connecting to APS
        if not self.nge100_resource:
            log.warning('No NGE100 resource provided - power supply control will be skipped')
        else:
            try:
                from rs_nge103 import RS_NGE103
                self.nge100 = RS_NGE103(self.nge100_resource)
                if self.nge100.connect():
                    log.info(f'Connected to NGE100 PSU on {self.nge100_resource}')
                    
                    # Configure all three channels
                    self.nge100.set_voltage(1, self.nge100_ch1_voltage)
                    self.nge100.set_current(1, self.nge100_ch1_current)
                    self.nge100.enable_output(1, True)
                    log.info(f'NGE100 Ch1 configured and enabled: {self.nge100_ch1_voltage}V, {self.nge100_ch1_current}A')
                    
                    self.nge100.set_voltage(2, self.nge100_ch2_voltage)
                    self.nge100.set_current(2, self.nge100_ch2_current)
                    self.nge100.enable_output(2, True)
                    log.info(f'NGE100 Ch2 configured: {self.nge100_ch2_voltage}V, {self.nge100_ch2_current}A')
                    
                    self.nge100.set_voltage(3, self.nge100_ch3_voltage)
                    self.nge100.set_current(3, self.nge100_ch3_current)
                    self.nge100.enable_output(3, True)
                    log.info(f'NGE100 Ch3 configured: {self.nge100_ch3_voltage}V, {self.nge100_ch3_current}A')
                else:
                    log.error('Failed to connect to NGE100 PSU')
                    self.nge100 = None
            except Exception as e:
                log.exception(f'Error initializing NGE100 PSU: {e}')
                self.nge100 = None
        
        # Initialize APS controller (AFTER PSU is configured)
        if not self.aps_port:
            log.warning('No APS port provided for DPT procedure')
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

        # Initialize oscilloscope
        if not self.oscilloscope_resource:
            log.warning('No oscilloscope resource provided - waveform capture will be skipped')
        else:
            try:
                from keysight_dso_s import KeysightDSOS
                self.oscilloscope = KeysightDSOS(self.oscilloscope_resource)
                if self.oscilloscope.connect():
                    log.info(f'Connected to oscilloscope on {self.oscilloscope_resource}')
                    
                    # Configure trigger (timebase will be set later based on pulse duration)
                    self.oscilloscope.setup_trigger(
                        source='EXT',
                        level=1,
                        mode='EDGE',
                        slope='POSitive'
                    )
                    
                    # Configure channel 3 vertical scaling (always 10V/div)
                    self.oscilloscope.setup_channel(3, enabled=True, scale=10.0)
                    log.info('Oscilloscope Ch3 configured: 10V/div')
                    
                    # Configure channel 2 external probe for current measurement
                    self.oscilloscope.configure_external_probe(2, gain=0.05, units='A')
                    self.oscilloscope.set_channel_invert(2, True)
                    log.info('Oscilloscope Ch2 configured: external probe 0.05 scaling, unit=Ampere, inverted')
                    
                    log.info('Oscilloscope configured: EXT trigger, Ch2=current probe (inverted), Ch3=10V/div')
                else:
                    log.error('Failed to connect to oscilloscope')
                    self.oscilloscope = None
            except Exception as e:
                log.exception(f'Error initializing oscilloscope: {e}')
                self.oscilloscope = None

        if self.aps is None:
            log.warning('Could not connect to APS controller; no instrument available')
        if self.nge100 is None:
            log.warning('Could not connect to NGE100 PSU; power supply control will be skipped')
        if self.oscilloscope is None:
            log.warning('Could not connect to oscilloscope; waveform capture will be skipped')

    def execute(self):
        """Execute DPT test on APS controller with message monitoring.

        Test sequence:
        1. Send DPT_test command and parse pulse duration
        2. Configure oscilloscope (timebase from duration, Ch1 from voltage, Ch2 from current, Ch3=10V/div)
        3. Send start
        4. Monitor for test completion
        5. Capture oscilloscope waveform and screenshot
        """
        if self.aps is None:
            log.error('No APS controller available for DPT test')
            return

        try:
            # Check system safety before starting test
            if not self.aps.is_safe():
                log.error('APS system safety check failed')
                return

            log.info('Starting DPT test...')
            log.info(f'Test parameters: Current={self.test_current}A, Voltage={self.test_voltage}V')
            
            # Step 1: Send DPT_test command
            log.info('Step 1: Sending DPT_test command')
            start_response = self.aps.dpt_test(
                current_a=self.test_current,
                voltage_v=self.test_voltage
            )
            log.info(f'DPT test command response: {start_response}')
            
            if not start_response:
                log.error('Failed to send DPT test command')
                return
            
            # Parse pulse duration from response
            # Expected format: "For XA and YV the first pulse needs to last Z.ZZZ microseconds with U V charging voltage."
            duration_match = re.search(r'(\d+\.?\d*)\s*microseconds', start_response)
            if duration_match:
                self.pulse_duration_us = float(duration_match.group(1))
                log.info(f'Parsed pulse duration: {self.pulse_duration_us} µs')
            else:
                log.warning('Could not parse pulse duration from APS response')
                self.pulse_duration_us = 100.0  # Default fallback
            
            # Step 2: Configure oscilloscope based on pulse duration and test parameters
            if self.oscilloscope:
                log.info('Step 2: Configuring oscilloscope based on test parameters')
                
                # Set timebase based on pulse duration (show ~10 divisions worth)
                # Total time = pulse duration + some margin for second pulse
                total_time_us = self.pulse_duration_us * 3  # 3x duration to show both pulses
                timebase_us_per_div = total_time_us / 10.0  # 10 divisions
                self.oscilloscope.setup_timebase(scale=timebase_us_per_div * 1e-6)  # Convert to seconds
                log.info(f'Oscilloscope timebase: {timebase_us_per_div:.3f} µs/div')
                
                # Configure Ch1 vertical scaling based on test voltage
                # Use 1/8 of test voltage per division (to show full range)
                ch1_scale = self.test_voltage / 8.0
                self.oscilloscope.setup_channel(1, enabled=True, scale=ch1_scale)
                log.info(f'Oscilloscope Ch1: {ch1_scale:.1f} V/div')
                
                # Configure Ch2 vertical scaling based on test current
                # External probe is already configured (0.05 gain)
                # Scale to show current range (1/8 of test current per division)
                ch2_scale = self.test_current / 8.0
                self.oscilloscope.setup_channel(2, enabled=True, scale=ch2_scale)
                log.info(f'Oscilloscope Ch2: {ch2_scale:.2f} A/div (via external probe)')
                
                # Arm oscilloscope for single acquisition
                self.oscilloscope.set_acquisition_mode('NORM')  # Normal trigger mode
                self.oscilloscope.single_acquisition()
                log.info('Oscilloscope armed for single trigger')

            # Step 3: Send start command
            log.info('Step 3: Sending start command')
            self.aps.start()
            log.info(f'DPT test started: {self.test_current}A, {self.test_voltage}V')
            
            # Record test start
            self.pulse_count = 1
            self.emit('results', {
                'Timestamp': time.time(),
                'Pulse': self.pulse_count,
                'Voltage (V)': self.test_voltage,
                'Current (A)': self.test_current
            })

            # Step 4: Monitor messages from APS controller
            log.info('Step 4: Monitoring APS controller messages...')
            self._monitor_aps_messages()
            
            # Step 5: Capture oscilloscope waveform and screenshot
            if self.oscilloscope:
                log.info('Step 5: Capturing oscilloscope data')
                self._capture_waveform()
                self._capture_screenshot()

        except Exception as e:
            log.exception('Error during DPT test execution: %s', e)

    def _monitor_aps_messages(self):
        """Monitor APS controller messages for test completion.
        
        This method continuously monitors the APS serial communication for:
        - 'measurement complete' messages (stops monitoring)
        
        Note: Cannot use get_status() during test - must rely on received messages only.
        """
        start_time = time.time()
        measurement_complete = False
        
        while time.time() - start_time < self.timeout and not measurement_complete:
            try:
                # Read any available messages from APS controller
                messages = self._read_aps_messages()
                
                for message in messages:
                    # Check if measurement complete was received
                    if self._process_aps_message(message):
                        measurement_complete = True
                        log.info('DPT test completed - measurement complete received')
                        break
                
                # Small delay to prevent excessive CPU usage
                time.sleep(0.01)
                
            except Exception as e:
                log.debug(f'Error during message monitoring: {e}')
                time.sleep(0.1)
        
        # Timeout occurred
        if time.time() - start_time >= self.timeout and not measurement_complete:
            log.warning(f'DPT monitoring timed out after {self.timeout}s')

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
            
        Returns:
            True if "measurement complete" was received, False otherwise
        """
        log.info(f'APS Message: {message}')
        
        # Check for measurement complete
        if 'measurement complete' in message.lower():
            log.info('Measurement complete received - test finished')
            return True
        
        # Log other messages (no emit)
        log.debug(f'Other APS message: {message}')
        return False

    def _capture_waveform(self):
        """Capture and save oscilloscope waveform.
        
        Saves waveform data to the data directory.
        """
        if not self.oscilloscope:
            log.warning('No oscilloscope available for waveform capture')
            return

        try:
            log.info('Waiting for oscilloscope acquisition to complete...')
            
            # Wait for acquisition to complete (with timeout)
            timeout = 10.0
            start_time = time.time()
            while time.time() - start_time < timeout:
                # Check if acquisition is complete
                # This would need a method to check acquisition state
                time.sleep(0.1)
                # For now, just wait a fixed time
                break
            
            time.sleep(1.0)  # Give scope time to complete
            
            # Save waveform data
            timestamp = time.strftime('%Y%m%d_%H%M%S')
            waveform_filename = f'DPT_waveform_{timestamp}'
            
            log.info(f'Saving waveform data as {waveform_filename}')
            self.oscilloscope.save_waveform_data(
                channels=[1, 2, 3, 4],
                filename=waveform_filename,
                format='H5'
            )
            log.info('Waveform data saved successfully')
            
        except Exception as e:
            log.error(f'Error capturing oscilloscope waveform: {e}')

    def _capture_screenshot(self):
        """Capture and save oscilloscope screenshot.
        
        Saves screenshot to the data directory.
        """
        if not self.oscilloscope:
            log.warning('No oscilloscope available for screenshot capture')
            return

        try:
            log.info('Capturing oscilloscope screenshot...')
            
            # Create filename with timestamp
            timestamp = time.strftime('%Y%m%d_%H%M%S')
            screenshot_filename = f'DPT_screenshot_{timestamp}.png'
            
            log.info(f'Saving screenshot as {screenshot_filename}')
            self.oscilloscope.capture_screenshot(
                filename=screenshot_filename,
                format='PNG',
                inksaver=False
            )
            log.info('Screenshot saved successfully')
            
        except Exception as e:
            log.error(f'Error capturing oscilloscope screenshot: {e}')

    def shutdown(self):
        """Clean shutdown - stop any running tests and disconnect from instruments."""
        try:
            # Shutdown APS controller
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
            
            # Shutdown NGE100 PSU
            if self.nge100 is not None:
                try:
                    #self.nge100.enable_output(1, False)
                    #self.nge100.enable_output(2, False)
                    #self.nge100.enable_output(3, False)
                    self.nge100.disconnect()
                    log.info('Disconnected from NGE100 PSU')
                except Exception as e:
                    log.debug('Failed to disconnect from NGE100: %s', e)
                
                self.nge100 = None
            
            # Shutdown oscilloscope
            if self.oscilloscope is not None:
                try:
                    self.oscilloscope.disconnect()
                    log.info('Disconnected from oscilloscope')
                except Exception as e:
                    log.debug('Failed to disconnect from oscilloscope: %s', e)
                
                self.oscilloscope = None
                
        except Exception:
            log.debug('Exception in DPT procedure shutdown', exc_info=True)
    
    def set_dpt_parameter(self, parameter: str, value: float):
        """Set a DPT parameter on the APS controller.
        
        Args:
            parameter: Parameter name
            value: Parameter value
            
        Returns:
            True if successful, False otherwise
        """
        if self.aps is not None:
            try:
                result = self.aps.dpt_parameter(parameter, value)
                log.info(f'Set DPT parameter {parameter} = {value}')
                return result
            except Exception as e:
                log.error(f'Failed to set DPT parameter {parameter}: {e}')
                return False
        else:
            log.warning('No APS controller available to set parameters')
            return False
    
    def get_dpt_parameter(self, parameter: str):
        """Get a DPT parameter from the APS controller.
        
        Args:
            parameter: Parameter name
            
        Returns:
            Parameter value or None if error
        """
        if self.aps is not None:
            try:
                value = self.aps.dpt_parameter(parameter)
                log.info(f'Got DPT parameter {parameter} = {value}')
                return value
            except Exception as e:
                log.error(f'Failed to get DPT parameter {parameter}: {e}')
                return None
        else:
            log.warning('No APS controller available to get parameters')
            return None
