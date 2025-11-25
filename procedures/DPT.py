import logging
import time
import re
from pymeasure.experiment import Procedure
from pymeasure.experiment import FloatParameter, Parameter, BooleanParameter

#from datetime import datetime

from hardware.APS_controller import APSController

log = logging.getLogger(__name__)


class DoublePulseTest(Procedure):
    # common properties of the procedure
    name = 'Double Pulse Test (DPT)' # For display
    internal_name = 'Double_Pulse_Test' # For internal use, no spaces or special chars
    short_name = 'DPT' # For directory naming
    description = "Double Pulse Switching Test using APS controller, auxiliary PSU, and oscilloscope."
    #filename = f'{datetime.now():%Y-%m-%d_%H-%M-%S}' # Default filename pattern, can also use {date}, {time}, {measurement_voltage}, etc.

    # APS connection parameters (configured via startup dialog)
    aps_port = Parameter('APS Serial Port', default='COM3')
    aux_psu_resource = Parameter('Auxiliary PSU Resource', default='')
    oscilloscope_resource = Parameter('Oscilloscope Resource', default='USB0::0x2A8D::0x904A::MY58150189::INSTR')
    
    # DPT test parameters (from firmware: current_a, voltage_v)
    test_current = FloatParameter('Test Current', units='A', default=10.0, 
                                  minimum=0.0, maximum=100.0)
    test_voltage = FloatParameter('Test Voltage', units='V', default=400.0, 
                                  minimum=0.0, maximum=2000.0)
    
    # Auxiliary PSU channel parameters (format: "voltage, current" e.g. "24.0, 0.5")
    aux_psu_ch1 = Parameter('AUX PSU Ch1 (V, A)', default='24.0, 0.5')
    aux_psu_ch2 = Parameter('AUX PSU Ch2 (V, A)', default='5.0, 0.1')
    aux_psu_ch3 = Parameter('AUX PSU Ch3 (V, A)', default='20.0, 0.1')

    # General test parameters
    wait_for_completion = BooleanParameter('Wait for test completion', default=True)
    timeout = FloatParameter('Test timeout', units='s', default=60.0)

    DATA_COLUMNS = ['Timestamp', 'Pulse', 'Voltage (V)', 'Current (A)']
    
    # GUI Configuration
    INPUTS = [
        'test_current',
        'test_voltage',
        'aux_psu_ch1',
        'aux_psu_ch2',
        'aux_psu_ch3',
        'wait_for_completion', 
        'timeout'
    ]
    
    DISPLAYS = INPUTS
    
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
        """Connect to the APS controller, auxiliary PSU, and oscilloscope."""
        self.aps = None
        self.aux_psu = None
        self.aux_psu_type = None
        self.oscilloscope = None
        self.pulse_count = 0  # Track total pulse count
        self.pulse_duration_us = None  # Will be set from APS response

        # Get connection parameters from startup config
        self._apply_connection_parameters()
        
        # Configure auxiliary PSU FIRST (before APS controller)
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
                from hardware.keysight_dso_s import KeysightDSOS
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
        if self.aux_psu is None:
            log.warning('Could not connect to auxiliary PSU; power supply control will be skipped')
        if self.oscilloscope is None:
            log.warning('Could not connect to oscilloscope; waveform capture will be skipped')

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
        
        # Oscilloscope
        osc_params = params.get('keysight_oscilloscope', {})
        if isinstance(osc_params, dict):
            osc_res = osc_params.get('connection') or osc_params.get('resource')
            if osc_res:
                self.oscilloscope_resource = osc_res

    def _get_aux_psu_configuration(self):
        # Try instance attribute first, then class attribute (set by main.py)
        params = getattr(self, 'connection_parameters', None)
        if not params:
            params = getattr(self.__class__, '_startup_connection_parameters', None)
        if not params or not isinstance(params, dict):
            params = {}
        aux_info = params.get('aux_psu') if isinstance(params, dict) else {}
        if not isinstance(aux_info, dict):
            aux_info = {}
        resource = aux_info.get('connection') or aux_info.get('resource') or self.aux_psu_resource
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
                
                # Check for completion
                if 'measurement complete' in msg_lower or msg.endswith('>'):
                    log.info('DPT test completed')
                    return False  # Stop monitoring
                
                return True  # Continue monitoring
            
            # Monitor with timeout
            self.aps.monitor_messages(handle_message, timeout=self.timeout)
            
            # Step 5: Capture oscilloscope waveform and screenshot
            if self.oscilloscope:
                log.info('Step 5: Capturing oscilloscope data')
                self._capture_waveform()
                self._capture_screenshot()

        except Exception as e:
            log.exception('Error during DPT test execution: %s', e)


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
            waveform_filename = f'DPT_{timestamp}'
            
            log.info(f'Saving waveform data as {waveform_filename}')
            self.oscilloscope.save_waveform_data(
                channels="all",
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
            
            # Shutdown auxiliary PSU
            if self.aux_psu is not None:
                try:
                    self.aux_psu.disconnect()
                    log.info('Disconnected from auxiliary PSU')
                except Exception as e:
                    log.debug('Failed to disconnect from auxiliary PSU: %s', e)
                
                self.aux_psu = None
            
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
