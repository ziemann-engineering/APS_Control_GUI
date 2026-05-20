#!/usr/bin/env python3
"""
APS Control Software Interface Library

A Python library for interfacing with the APS (Automated Power Semiconductor) 
test control software via serial communication.

This library provides a high-level interface to all commands supported by the 
APS control software, including various test procedures (DPT, UIS, SCT, etc.) 
and system control functions.

Requirements:
- pyserial

Usage:
    from aps_interface import APSController
    
    # Connect to APS controller
    aps = APSController('COM3')  # Windows
    # aps = APSController('/dev/ttyUSB0')  # Linux
    
    # Check status
    status = aps.get_status()
    print(f"Current test: {status['test_running']}")
    
    # Run a DPT test
    aps.dpt_test(current_a=50.0, voltage_v=1200.0)
    
    # Configure parameters
    aps.dpt_parameter('R_DUT', 0.025)
"""

import serial
import datetime
import os
import time
import re
import threading
from typing import Optional, Union
from dataclasses import dataclass
from enum import Enum

compatible_build_date = (2025, 10, 1)  # (year, month, day) — minimum acceptable firmware build
compatible_board_type = "APS Control Board 1.3"  # device name field in *IDN? response

# Directory containing firmware .bin files.
# Resolved relative to this file's location (hardware/ -> Python Software/ -> firmware/).
FIRMWARE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'firmware'
)

_DATE_RE = re.compile(
    rb'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)'
    rb'[ \t]+\d{1,2}[ \t]+\d{4}'
)


def _parse_aps_idn(response: str) -> Optional[dict]:
    """Parse the *IDN? response from the APS controller.

    Expected format::

        APS,APS Control Board 1.3,<SN_hex>,<build date>

    Returns a dict with keys ``manufacturer``, ``device``, ``serial``,
    ``fw_date_str``, ``fw_date`` (datetime.date or None),
    or ``None`` if parsing fails.
    """
    for line in response.splitlines():
        line = line.strip().rstrip('>')
        if ',' in line:
            fields = [f.strip() for f in line.split(',')]
            if len(fields) >= 4:
                manufacturer, device, serial, fw_date_str = (
                    fields[0], fields[1], fields[2], ','.join(fields[3:]))
                fw_date = None
                date_str = ' '.join(fw_date_str.split())
                for fmt in ('%b %d %Y', '%B %d %Y', '%Y-%m-%d',
                            '%d-%m-%Y', '%m/%d/%Y'):
                    try:
                        fw_date = datetime.datetime.strptime(date_str, fmt).date()
                        break
                    except ValueError:
                        continue
                return {'manufacturer': manufacturer, 'device': device,
                        'serial': serial, 'fw_date_str': fw_date_str,
                        'fw_date': fw_date}
    return None


class TestType(Enum):
    """Enumeration of available test types."""
    DPT = "DPT"
    COSS = "COSS"
    UIS = "UIS"
    SCT = "SCT"
    CMTI = "CMTI"
    ZCS = "ZCS"
    HPPT = "HPPT"
    CGD = "CGD"
    CGG2A = "CGG2A"
    CGG2D = "CGG2D"


class SafetyState(Enum):
    """Safety system states."""
    SAFE = "safe"
    COVER_OPEN = "cover_open"
    EMERGENCY_PRESSED = "emergency_pressed"


@dataclass
class SystemStatus:
    """System status information."""
    test_running: Optional[str]
    safety_cover: str
    emergency_button: str
    is_safe: bool


class APSControllerError(Exception):
    """Base exception for APS controller errors."""
    pass


class APSCommunicationError(APSControllerError):
    """Communication error with APS controller."""
    pass


class APSSafetyError(APSControllerError):
    """Safety system error."""
    pass


class APSController:
    """
    Interface to APS Control Software via serial communication.
    
    This class provides a Python interface to all commands supported by the
    APS control software, including test procedures, parameter configuration,
    and system control functions.
    """
    
    @staticmethod
    def _visa_to_com_port(resource_string: str) -> str:
        """
        Convert VISA resource string to serial port format.
        
        Args:
            resource_string: VISA resource string or serial port path
                - Windows VISA: 'ASRL7::INSTR' -> 'COM7'
                - Linux VISA: 'ASRL/dev/ttyUSB0::INSTR' -> '/dev/ttyUSB0'
                - Direct port: 'COM7' or '/dev/ttyUSB0' (unchanged)
            
        Returns:
            Serial port string (e.g., 'COM7' on Windows, '/dev/ttyUSB0' on Linux)
            
        Examples:
            'ASRL7::INSTR' -> 'COM7'
            'ASRL3::INSTR' -> 'COM3'
            'ASRL/dev/ttyUSB0::INSTR' -> '/dev/ttyUSB0'
            'ASRL/dev/ttyACM0::INSTR' -> '/dev/ttyACM0'
            'COM7' -> 'COM7' (unchanged)
            '/dev/ttyUSB0' -> '/dev/ttyUSB0' (unchanged)
        """
        # If it's already a COM port or device path, return as-is
        if resource_string.upper().startswith('COM') or resource_string.startswith('/dev/'):
            return resource_string
        
        # Parse VISA ASRL format with Linux device path: ASRL/dev/ttyXXX::INSTR TODO:test this
        match = re.match(r'ASRL(/dev/[^:]+)::INSTR', resource_string, re.IGNORECASE)
        if match:
            return match.group(1)
        
        # Parse VISA ASRL format with Windows port number: ASRL<number>::INSTR
        match = re.match(r'ASRL(\d+)::INSTR', resource_string, re.IGNORECASE)
        if match:
            port_number = match.group(1)
            return f'COM{port_number}'
        
        # If no pattern matches, return as-is and let serial.Serial handle the error
        return resource_string
    
    def __init__(self, port: str, baudrate: int = 38400, timeout: float = 0.1):
        """
        Initialize APS controller interface.
        
        Args:
            port: Serial port name (e.g., 'COM3', 'ASRL3::INSTR' on Windows, '/dev/ttyUSB0' on Linux)
            baudrate: Serial communication baud rate (default: 38400)
            timeout: Command timeout in seconds
        """
        # Convert VISA resource string to COM port if needed
        self.port = self._visa_to_com_port(port)
        self.original_port = port  # Keep original for reference
        self.baudrate = baudrate
        self.timeout = timeout
        self.serial_conn: Optional[serial.Serial] = None
        self._response_buffer = []
        self._lock = threading.Lock()
        
    def connect(self) -> bool:
        """
        Connect to the APS controller.
        
        Returns:
            True if connection successful, False otherwise
        """
        print(f"Connecting to APS controller on port {self.port} at {self.baudrate} baud")
        try:
            self.serial_conn = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.timeout,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                bytesize=serial.EIGHTBITS
            )
            
            # Wait for connection to stabilize
            time.sleep(0.01)

            # Test connection with status command
            response = self._send_command("status")
            if response is None:
                self.disconnect()
                return False
            
            # Validate board type and firmware version
            if not self._validate_board_info():
                self.disconnect()
                return False
            
            print(f"Connected to APS Controller on {self.port}")
            return True
                
        except Exception as e:
            print(f"Failed to connect to APS controller: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from the APS controller."""
        if self.serial_conn and self.serial_conn.is_open:
            self.serial_conn.close()
            self.serial_conn = None
    
    def _send_command(self, command: str, timeout: Optional[float] = None) -> Optional[str]:
        """
        Send command to APS controller and return response.
        
        Args:
            command: Command string to send
            timeout: Override default timeout
            
        Returns:
            Response string or None if error/timeout
        """
        if not self.serial_conn or not self.serial_conn.is_open:
            raise APSCommunicationError("Not connected to APS controller")
        
        with self._lock:
            try:
                # Clear input buffer
                self.serial_conn.reset_input_buffer()
                
                # Send command
                cmd_bytes = (command + '\r\n').encode('ascii')
                self.serial_conn.write(cmd_bytes)
                self.serial_conn.flush()
                
                # Read response
                response_timeout = timeout if timeout is not None else self.timeout
                start_time = time.time()
                response_lines = []
                
                while time.time() - start_time < response_timeout:
                    if self.serial_conn.in_waiting > 0:
                        line = self.serial_conn.readline().decode('ascii', errors='ignore').strip()
                        if line:
                            response_lines.append(line)
                            # Look for shell prompt to indicate end of response
                            if line.endswith('>'):
                                break
                    else:
                        time.sleep(0.01)
                
                return '\n'.join(response_lines) if response_lines else None
                
            except Exception as e:
                raise APSCommunicationError(f"Communication error: {e}")
    
    def read_message(self, timeout: float = 0.1) -> Optional[str]:
        """
        Read a single message line from the controller without sending a command.
        
        This is useful for monitoring asynchronous messages during test execution.
        Does not use the lock, so can be called while other operations are in progress.
        
        Args:
            timeout: Time to wait for a message (seconds)
            
        Returns:
            Message string (stripped) or None if no message received
        """
        if not self.serial_conn or not self.serial_conn.is_open:
            return None
        
        try:
            # Temporarily set timeout for this read
            old_timeout = self.serial_conn.timeout
            self.serial_conn.timeout = timeout
            
            try:
                if self.serial_conn.in_waiting > 0:
                    line = self.serial_conn.readline().decode('ascii', errors='ignore').strip()
                    return line if line else None
                return None
            finally:
                self.serial_conn.timeout = old_timeout
                
        except Exception:
            return None
    
    def monitor_messages(self, callback, stop_condition=None, timeout: Optional[float] = None):
        """
        Monitor messages from the controller and call callback for each message.
        
        This is designed for use during test execution where the controller sends
        asynchronous status messages.
        
        Args:
            callback: Function to call for each message. Should accept (message: str) and
                     return True to continue monitoring, False to stop.
            stop_condition: Optional function that returns True when monitoring should stop.
                          Checked on each iteration.
            timeout: Maximum time to monitor (None for no timeout)
            
        Returns:
            None
            
        Example:
            def handle_message(msg):
                print(f"Controller: {msg}")
                return "measurement complete" not in msg.lower()
            
            aps.monitor_messages(handle_message, timeout=60.0)
        """
        start_time = time.time()
        
        while True:
            # Check timeout
            if timeout and (time.time() - start_time) > timeout:
                break
            
            # Check stop condition
            if stop_condition and stop_condition():
                break
            
            # Read message
            msg = self.read_message(timeout=0.1)
            if msg:
                # Call callback, stop if it returns False
                if not callback(msg):
                    break
            else:
                # Small delay if no message
                time.sleep(0.01)
    
    def _parse_status_response(self, response: str) -> SystemStatus:
        """Parse status command response."""
        lines = response.split('\n')
        
        test_running = None
        safety_cover = "unknown"
        emergency_button = "unknown"
        
        for line in lines:
            line = line.strip()
            if line.startswith("Test running:"):
                test_value = line.split(":", 1)[1].strip()
                test_running = test_value if test_value != "None" else None
            elif line.startswith("Safety cover:"):
                safety_cover = line.split(":", 1)[1].strip()
            elif line.startswith("Emergency off button:"):
                emergency_button = line.split(":", 1)[1].strip()
        
        is_safe = (safety_cover == "closed" and emergency_button == "not pressed")
        
        return SystemStatus(
            test_running=test_running,
            safety_cover=safety_cover,
            emergency_button=emergency_button,
            is_safe=is_safe
        )
    
    # ===========================================
    # System Control Commands
    # ===========================================
    
    def get_status(self) -> SystemStatus:
        """
        Get system status including running tests and safety state.
        
        Returns:
            SystemStatus object with current system state
        """
        response = self._send_command("status")
        if response is None:
            raise APSCommunicationError("Failed to get status")
        
        return self._parse_status_response(response)
    
    def stop_test(self) -> Optional[str]:
        """
        Stop any running test immediately.
        
        Returns:
            Response string from controller, or None if error
        """
        return self._send_command("stop")
    
    def abort_test(self) -> Optional[str]:
        """Alias for stop_test()."""
        return self.stop_test()
    
    def cancel_test(self) -> Optional[str]:
        """Alias for stop_test()."""
        return self.stop_test()
    
    def reset_system(self) -> Optional[str]:
        """
        Reset the APS controller system.
        
        Returns:
            Response string from controller, or None if error
        """
        response = self._send_command("reset", timeout=1.0)
        # System will reset, so connection will be lost
        self.disconnect()
        return response
    
    def self_test(self) -> Optional[str]:
        """
        Run system self-test (toggles LEDs and outputs).
        
        Returns:
            Response string from controller, or None if error
        """
        return self._send_command("selftest", timeout=10.0)
    
    def start(self) -> Optional[str]:
        """
        Send start command to the APS controller.
        
        Returns:
            Response string from controller, or None if error
        """
        return self._send_command("start")

    def get_idn(self) -> Optional[dict]:
        """Send *IDN? and return a parsed dict or None.

        Returns keys: ``manufacturer``, ``device``, ``serial``,
        ``fw_date_str``, ``fw_date`` (datetime.date or None).
        """
        response = self._send_command('*IDN?')
        if response is None:
            return None
        return _parse_aps_idn(response)

    @staticmethod
    def probe_port(port: str, baudrate: int = 38400, timeout: float = 0.5) -> Optional[dict]:
        """Non-destructive probe of *port* to identify an APS control board.

        Opens the port, sends ``*IDN?\\r\\n``, and parses the response.
        The port is always closed afterwards regardless of result.

        Returns a dict with keys ``device_type`` ('aps'), ``port``, ``serial``,
        ``version``, ``label``; or *None* if no APS board answered.
        """
        try:
            with serial.Serial(port, baudrate, timeout=timeout) as ser:
                ser.reset_input_buffer()
                ser.write(b'*IDN?\r\n')
                time.sleep(0.3)
                data = ser.read(512).decode('ascii', errors='ignore')
            idn = _parse_aps_idn(data)
            if idn is None or 'APS Control Board' not in idn.get('device', ''):
                return None
            sn = idn.get('serial', '')
            ver = idn.get('fw_date_str', '')
            return {'device_type': 'aps', 'port': port, 'serial': sn,
                    'version': ver, 'build_date': idn.get('fw_date'),
                    'label': f"APS  SN:{sn}  {ver}  ({port})"}
        except Exception:
            return None

    @staticmethod
    def find_firmware_update(current_build_date: 'datetime.date | str | None',
                             firmware_dir: str = FIRMWARE_DIR) -> Optional[str]:
        """Search *firmware_dir* for an APS firmware ``.bin`` newer than *current_build_date*.

        The build date is read directly from each ``.bin`` by locating the
        ``__DATE__`` ASCII string that GCC embeds when it is referenced in source
        (e.g. in the ``*IDN?`` response).  No special filename convention required.

        Parameters
        ----------
        current_build_date:
            Build date of the running firmware from ``*IDN?``.  Accepts a
            :class:`datetime.date`, an ISO-format string, or ``None`` (treat any
            file with a parseable date as a candidate).
        """
        if not os.path.isdir(firmware_dir):
            return None
        if isinstance(current_build_date, str):
            # Parse ISO date string
            for fmt in ('%Y-%m-%d', '%b %d %Y', '%B %d %Y'):
                try:
                    current_build_date = datetime.datetime.strptime(
                        ' '.join(current_build_date.split()), fmt).date()
                    break
                except ValueError:
                    pass
            else:
                current_build_date = None

        best_path: Optional[str] = None
        best_date: Optional[datetime.date] = current_build_date
        for fname in os.listdir(firmware_dir):
            if not fname.lower().endswith('.bin'):
                continue
            fpath = os.path.join(firmware_dir, fname)
            try:
                with open(fpath, 'rb') as f:
                    data = f.read()
                m = _DATE_RE.search(data)
                if not m:
                    continue
                date_str = ' '.join(m.group(0).decode('ascii', errors='ignore').split())
                fdate = None
                for fmt in ('%b %d %Y', '%B %d %Y'):
                    try:
                        fdate = datetime.datetime.strptime(date_str, fmt).date()
                        break
                    except ValueError:
                        pass
                if fdate is None:
                    continue
                if best_date is None or fdate > best_date:
                    best_date = fdate
                    best_path = fpath
            except OSError:
                continue
        return best_path

    def info(self, print_response: bool = True, timeout: Optional[float] = None) -> Optional[dict]:
        """
        Get system information from the APS controller.
        
        Args:
            print_response: Whether to print the raw response (default: True)
        
        Returns:
            Dictionary with parsed system information or None if error
        """
        response = self._send_command("info", timeout=timeout)
        if response:
            if print_response:
                print(response)
            return self._parse_info_response(response)
        return None
    
    def _parse_info_response(self, response: str) -> dict:
        """
        Parse the info command response into a structured dictionary.
        
        Args:
            response: Raw info response string
            
        Returns:
            Dictionary with parsed system information
        """
        info_dict = {}
        lines = response.split('\n')
        
        for line in lines:
            line = line.strip()
            if ':' in line:
                # Split on first colon to handle cases like "Build time: 2023-10-08 - 14:30:15"
                key, value = line.split(':', 1)
                key = key.strip()
                value = value.strip()
                
                # Convert key to snake_case for consistency
                key_snake = key.lower().replace(' ', '_').replace('-', '_')
                
                # Store both original key and snake_case key for flexibility
                info_dict[key] = value
                info_dict[key_snake] = value
        
        return info_dict
    
    def get_system_info(self, field: Optional[str] = None) -> Union[dict, str, None]:
        """
        Get system information with optional field filtering.
        
        Args:
            field: Optional specific field to retrieve (e.g., 'kernel', 'board', 'build_time')
            
        Returns:
            Full info dictionary if no field specified, specific field value if field specified,
            or None if error/field not found
        """
        info_data = self.info(print_response=False)
        if info_data is None:
            return None
        
        if field is None:
            return info_data
        
        # Try both original key format and snake_case
        return info_data.get(field) or info_data.get(field.replace('_', ' ').title())
    
    def send_raw_command(self, command: str) -> Optional[str]:
        """
        Send a raw command to the APS controller and return the response.
        
        Args:
            command: Raw command string to send
            
        Returns:
            Response string from controller, or None if error
        """
        return self._send_command(command)
    
    def _validate_board_info(self) -> bool:
        """
        Validate that the connected board is the correct type and firmware version
        using the *IDN? response.

        Returns:
            True if board validation passes, False otherwise
        """
        try:
            response = self._send_command('*IDN?')
            if not response:
                print("Board validation failed: no response to *IDN?")
                return False

            idn = _parse_aps_idn(response)
            if idn is None:
                print(f"Board validation failed: could not parse *IDN? response: {response!r}")
                return False

            if idn['device'] != compatible_board_type:
                print("Board validation failed:")
                print(f"  Expected: {compatible_board_type}")
                print(f"  Found: {idn['device']}")
                return False

            fw_date = idn.get('fw_date')
            if fw_date is None:
                print("Warning: could not parse firmware build date from *IDN? response")
            else:
                min_date = datetime.date(*compatible_build_date)
                if fw_date < min_date:
                    print("Firmware build date validation failed:")
                    print(f"  Build date: {fw_date}")
                    print(f"  Required: on or after {min_date.strftime('%d %B %Y')}")
                    return False

            print(f"Board validation passed: {idn['device']}  SN:{idn['serial']}  Built:{idn['fw_date_str']}")
            return True

        except Exception as e:
            print(f"Board validation error: {e}")
            return False
    
    def _validate_build_time(self, build_time_str: str) -> bool:
        """
        Validate that build time is after cutoff for compatibility.
        
        Args:
            build_time_str: Build time string from info command
            
        Returns:
            True if build time is valid, False otherwise
        """
        try:
            import datetime
            
            # Parse build time string (format: "2023-10-08 - 14:30:15" or similar)
            # Handle various possible formats
            date_part = build_time_str.split(' - ')[0].strip()
            
            # Try different date formats
            date_formats = [
                '%Y-%m-%d',      # 2025-10-08
                '%b %d %Y',      # Oct 08 2025
                '%B %d %Y',      # October 08 2025
                '%d-%m-%Y',      # 08-10-2025
                '%m/%d/%Y',      # 10/08/2025
            ]
            
            build_date = None
            for fmt in date_formats:
                try:
                    build_date = datetime.datetime.strptime(date_part, fmt).date()
                    break
                except ValueError:
                    continue
            
            if build_date is None:
                print(f"Unable to parse build date: {build_time_str}")
                return False
            
            min_date = datetime.date(compatible_build_date[0], compatible_build_date[1], compatible_build_date[2]) 
            
            if build_date < min_date:
                print("Build time validation failed:")
                print(f"  Build date: {build_date}")
                print(f"  Required: after {min_date.strftime('%d %B %Y')}")
                return False
            
            return True
            
        except Exception as e:
            print(f"Build time validation error: {e}")
            return False
    
    # ===========================================
    # Hardware Control Commands
    # ===========================================
    
    def control_relay(self, relay_type: str, state: str) -> Optional[str]:
        """
        Control relay states.
        
        Args:
            relay_type: 'connect' or 'charge'
            state: 'on' or 'off'
            
        Returns:
            Response string from controller, or None if error
        """
        if relay_type not in ['connect', 'charge']:
            raise ValueError("relay_type must be 'connect' or 'charge'")
        if state not in ['on', 'off']:
            raise ValueError("state must be 'on' or 'off'")
        
        return self._send_command(f"relays {relay_type} {state}")
    
    def control_optical(self, output: str, state: str) -> bool:
        """
        Control optical outputs.
        
        Args:
            output: 'DUT', 'LV', or 'HV'
            state: 'on' or 'off'
            
        Returns:
            True if command successful
        """
        if output not in ['DUT', 'LV', 'HV']:
            raise ValueError("output must be 'DUT', 'LV', or 'HV'")
        if state not in ['on', 'off']:
            raise ValueError("state must be 'on' or 'off'")
        
        response = self._send_command(f"optical {output} {state}")
        return response is not None
    
    def dut_test(self) -> Optional[str]:
        """
        Perform DUT gate test (switches DUT on and triggers oscilloscope).
        
        Returns:
            Response string from controller, or None if error
        """
        return self._send_command("DUT_test")
    
    def control_psu(self, psu_id: str, action: str, voltage: Optional[float] = None, 
                   current: Optional[float] = None) -> bool:
        """
        Control power supply units.
        
        Args:
            psu_id: 'LV' or 'HV'
            action: 'on', 'off', 'measure', 'setup', or voltage/current setting
            voltage: Voltage value (for setting)
            current: Current value (for setting)
            
        Returns:
            True if command successful
        """
        if psu_id not in ['LV', 'HV']:
            raise ValueError("psu_id must be 'LV' or 'HV'")
        
        if action in ['on', 'off', 'measure', 'setup']:
            response = self._send_command(f"psu {psu_id} {action}")
        elif voltage is not None and current is not None:
            response = self._send_command(f"psu {psu_id} {voltage}V {current}A")
        else:
            raise ValueError("Invalid PSU command parameters")
        
        return response is not None
    
    # ===========================================
    # DPT (Double Pulse Test) Commands
    # ===========================================
    
    def dpt_test(self, current_a: float, voltage_v: float) -> Optional[str]:
        """
        Run DPT (Double Pulse Test).
        
        Args:
            current_a: Desired current in Amperes
            voltage_v: Test voltage in Volts
            
        Returns:
            Response string from controller, or None if error
        """
        return self._send_command(f"DPT_test {current_a}A {voltage_v}V")
    
    def dpt_parameter(self, parameter: str, value: Optional[float] = None) -> Union[str, float, None]:
        """
        Get or set DPT parameters.
        
        Args:
            parameter: Parameter name (e.g., 'R_DUT', 'V_DUT', 'PCB_count', etc.)
            value: Value to set (if None, parameter will be read)
            
        Returns:
            If setting: Response string from controller, or None if error
            If reading: Parameter value as float, or None if error
        """
        if value is not None:
            return self._send_command(f"DPT_parameter {parameter} {value}")
        else:
            response = self._send_command(f"DPT_parameter {parameter}")
            if response:
                # Parse response to extract value
                match = re.search(rf"{parameter}\s*=\s*([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)", response)
                if match:
                    return float(match.group(1))
                # If parsing fails, return the raw response
                return response
            return None
    
    # ===========================================
    # COSS (Output Capacitance) Test Commands
    # ===========================================
    
    def coss_test(self) -> bool:
        """
        Run COSS (Output Capacitance) test.
        
        Note: The COSS test in the firmware appears to have no user-configurable parameters
        via the test command. Parameters may need to be set separately if available.
        
        Returns:
            True if test started successfully
        """
        response = self._send_command("COSS_test")
        return response is not None
    
    # ===========================================
    # UIS (Unclamped Inductive Switching) Test Commands
    # ===========================================
    
    def uis_test(self, voltage_v: float, time_s: float) -> bool:
        """
        Run UIS (Unclamped Inductive Switching) test.
        
        Args:
            voltage_v: Charging voltage in Volts (max 200V)
            time_s: Charging duration in seconds (can also be specified in microseconds)
            
        Returns:
            True if test started successfully
            
        Example:
            uis_test(100.0, 50e-6)  # 100V, 50 microseconds
        """
        return self._send_command(f"UIS_test {voltage_v}V {time_s}s") is not None
    
    # ===========================================
    # SCT (Short Circuit Test) Commands
    # ===========================================
    
    def sct_test(self, voltage_v: float, time_s: float, current_a: Optional[float] = None) -> Optional[str]:
        """
        Run SCT (Short Circuit Test).
        
        Args:
            voltage_v: Test voltage in Volts (max 2000V, or 200V for LV mode)
            time_s: Short circuit duration in seconds
            current_a: Optional test current in Amperes (positive for type 2, negative for type 3)
                      If None, runs type 1 test
            
        Returns:
            Response string from controller, or None if error
            
        Examples:
            sct_test(600.0, 10e-6)           # Type 1: 600V, 10us
            sct_test(600.0, 10e-6, 50.0)     # Type 2: 600V, 10us, 50A
            sct_test(600.0, 10e-6, -50.0)    # Type 3: 600V, 10us, -50A (inverted current)
        """
        if current_a is None:
            return self._send_command(f"SCT_test {voltage_v}V {time_s}s")
        else:
            return self._send_command(f"SCT_test {voltage_v}V {time_s}s {current_a}A")
    
    def sct_parameter(self, parameter: str, value: Optional[float] = None) -> Union[bool, float]:
        """
        Get or set SCT parameters.
        
        Args:
            parameter: Parameter name
            value: Value to set (if None, parameter will be read)
            
        Returns:
            If setting: True if successful
            If reading: Parameter value as float
        """
        if value is not None:
            response = self._send_command(f"SCT_parameter {parameter} {value}")
            return response is not None
        else:
            response = self._send_command(f"SCT_parameter {parameter}")
            if response:
                match = re.search(rf"{parameter}\s*=\s*([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)", response)
                if match:
                    return float(match.group(1))
            return None
    
    # ===========================================
    # CMTI (Common Mode Transient Immunity) Test Commands
    # ===========================================
    
    def cmti_test(self, test_voltage_v: float, driver_voltage_v: float) -> bool:
        """
        Run CMTI (Common Mode Transient Immunity) test.
        
        Args:
            test_voltage_v: Test voltage in Volts (0V to 2000V)
            driver_voltage_v: Driver voltage in Volts (0V to 200V)
            
        Returns:
            True if test started successfully
            
        Example:
            cmti_test(1200.0, 15.0)  # 1200V test voltage, 15V driver voltage
        """
        response = self._send_command(f"CMTI_test {test_voltage_v}V {driver_voltage_v}V")
        return response is not None
    
    def cmti_parameter(self, parameter: str, value: Optional[float] = None) -> Union[bool, float]:
        """
        Get or set CMTI parameters.
        
        Args:
            parameter: Parameter name
            value: Value to set (if None, parameter will be read)
            
        Returns:
            If setting: True if successful
            If reading: Parameter value as float
        """
        if value is not None:
            response = self._send_command(f"CMTI_parameter {parameter} {value}")
            return response is not None
        else:
            response = self._send_command(f"CMTI_parameter {parameter}")
            if response:
                match = re.search(rf"{parameter}\s*=\s*([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)", response)
                if match:
                    return float(match.group(1))
            return None
    
    # ===========================================
    # ZCS (Zero Current Switching) Test Commands
    # ===========================================
    
    def zcs_test(self, input_voltage_v: float, output_voltage_v: float, cycles: int) -> bool:
        """
        Run ZCS (Zero Current Switching) test.
        
        Args:
            input_voltage_v: Input voltage in Volts (0V to 2000V)
            output_voltage_v: Output voltage in Volts (0V to 200V)
            cycles: Number of switching cycles to perform
            
        Returns:
            True if test started successfully
            
        Example:
            zcs_test(800.0, 12.0, 1000)  # 800V input, 12V output, 1000 cycles
        """
        response = self._send_command(f"ZCS_test {input_voltage_v}V {output_voltage_v}V {cycles}")
        return response is not None
    
    def zcs_parameter(self, parameter: str, value: Optional[float] = None) -> Union[bool, float]:
        """
        Get or set ZCS parameters.
        
        Args:
            parameter: Parameter name
            value: Value to set (if None, parameter will be read)
            
        Returns:
            If setting: True if successful
            If reading: Parameter value as float
        """
        if value is not None:
            response = self._send_command(f"ZCS_parameter {parameter} {value}")
            return response is not None
        else:
            response = self._send_command(f"ZCS_parameter {parameter}")
            if response:
                match = re.search(rf"{parameter}\s*=\s*([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)", response)
                if match:
                    return float(match.group(1))
            return None
    
    # ===========================================
    # HPPT (High Power Pulse Test) Commands
    # ===========================================
    
    def hppt_test(self, voltage_v: float, on_time_ns: int, period_s: float, 
                  pulse_count: int, measurement: bool = False) -> bool:
        """
        Run HPPT (High Power Pulse Test).
        
        Args:
            voltage_v: Test voltage in Volts (0V to 2000V)
            on_time_ns: DUT on-time in nanoseconds (14ns to 3000ns, will be rounded to nearest 7ns)
            period_s: Pulse repetition period in seconds (up to 1s)
            pulse_count: Number of pulses to generate
            measurement: If True, wait for gate current measurement after each burst (default: False)
            
        Returns:
            True if test started successfully
            
        Example:
            hppt_test(1200.0, 100, 0.001, 1000)  # 1200V, 100ns pulses, 1ms period, 1000 pulses
            hppt_test(800.0, 500, 0.0001, 5000, measurement=True)  # with measurement
        """
        measurement_flag = 1 if measurement else 0
        response = self._send_command(f"HPPT_test {voltage_v}V {on_time_ns}ns {period_s}s {pulse_count} {measurement_flag}")
        return response is not None
    
    def hppt_parameter(self, parameter: str, value: Optional[float] = None) -> Union[bool, float]:
        """
        Get or set HPPT parameters.
        
        Args:
            parameter: Parameter name
            value: Value to set (if None, parameter will be read)
            
        Returns:
            If setting: True if successful
            If reading: Parameter value as float
        """
        if value is not None:
            response = self._send_command(f"HPPT_parameter {parameter} {value}")
            return response is not None
        else:
            response = self._send_command(f"HPPT_parameter {parameter}")
            if response:
                match = re.search(rf"{parameter}\s*=\s*([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)", response)
                if match:
                    return float(match.group(1))
            return None
    
    # ===========================================
    # CGD (Gate Charge) Test Commands
    # ===========================================
    
    def cgd_test(self, voltage_v: float, pulse_width_s: float, 
                 prebias_time_s: Optional[float] = None, mode: str = '+') -> bool:
        """
        Run CGD (Gate-Drain Capacitance) test.
        
        Args:
            voltage_v: Drain voltage in Volts (max 200V)
            pulse_width_s: Test pulse width in seconds
            prebias_time_s: Optional pre-bias time in seconds (applies positive gate voltage before test)
            mode: '+' for positive dV/dt or '-' for negative dV/dt (default: '+')
            
        Returns:
            True if test started successfully
            
        Examples:
            cgd_test(100.0, 10e-6)                    # 100V, 10us pulse, positive dV/dt
            cgd_test(100.0, 10e-6, mode='-')          # negative dV/dt
            cgd_test(100.0, 10e-6, prebias_time_s=1e-3)  # with 1ms pre-bias
        """
        cmd_parts = [f"{voltage_v}V", f"{pulse_width_s}s"]
        if prebias_time_s is not None:
            cmd_parts.append(f"{prebias_time_s}s")
        if mode != '+':
            cmd_parts.append(mode)
        
        response = self._send_command(f"CGD_test {' '.join(cmd_parts)}")
        return response is not None
    
    # ===========================================
    # CGG2 Test Commands
    # ===========================================
    
    def cgg2a_test(self, mode: str, ramp_time_s: float, voltage_v: float) -> bool:
        """
        Run CGG2A (Analog Gate-Gate Capacitance) test.
        
        Args:
            mode: 'RF' or 'RISING' for rise then fall, 'FR' or 'FALLING' for fall then rise
            ramp_time_s: Ramp duration in seconds
            voltage_v: Drain voltage in Volts (max 200V)
            
        Returns:
            True if test started successfully
            
        Examples:
            cgg2a_test('RF', 10e-6, 100.0)      # Rising then falling, 10us ramps, 100V
            cgg2a_test('RISING', 5e-6, 50.0)    # Same as 'RF'
        """
        response = self._send_command(f"CGG2A_test {mode} {ramp_time_s}s {voltage_v}V")
        return response is not None
    
    def cgg2d_test(self, mode: str, ramp_time_s: float, voltage_v: float, 
                   hold_time_s: Optional[float] = None) -> bool:
        """
        Run CGG2D (Digital Gate-Gate Capacitance) test.
        
        Args:
            mode: 'R' or 'RISING' for rising, 'F' or 'FALLING' for falling,
                  'RF' or 'BOTH' for rise then fall, 'FR' for fall then rise
            ramp_time_s: Ramp duration in seconds
            voltage_v: Drain voltage in Volts (max 200V)
            hold_time_s: Optional hold time between ramps (for RF/FR modes)
            
        Returns:
            True if test started successfully
            
        Examples:
            cgg2d_test('R', 10e-6, 100.0)              # Rising ramp only
            cgg2d_test('RF', 10e-6, 100.0, 1e-6)       # Rise-fall with 1us hold time
            cgg2d_test('BOTH', 5e-6, 50.0, 500e-9)     # Same as 'RF'
        """
        cmd_parts = [mode, f"{ramp_time_s}s", f"{voltage_v}V"]
        if hold_time_s is not None:
            cmd_parts.append(f"{hold_time_s}s")
        
        response = self._send_command(f"CGG2D_test {' '.join(cmd_parts)}")
        return response is not None
    
    # ===========================================
    # Convenience Methods
    # ===========================================
    
    def wait_for_test_completion(self, check_interval: float = 1.0, 
                                timeout: Optional[float] = None) -> bool:
        """
        Wait for current test to complete.
        
        Args:
            check_interval: How often to check status (seconds)
            timeout: Maximum time to wait (None for no timeout)
            
        Returns:
            True if test completed, False if timeout
        """
        start_time = time.time()
        
        while True:
            try:
                status = self.get_status()
                if status.test_running is None:
                    return True
                
                if timeout and (time.time() - start_time) > timeout:
                    return False
                
                time.sleep(check_interval)
                
            except Exception:
                # If we can't get status, assume test completed
                return True
    
    def is_safe(self) -> bool:
        """
        Check if system is in safe state for testing.
        
        Returns:
            True if safe, False otherwise
        """
        try:
            status = self.get_status()
            return status.is_safe
        except Exception:
            return False
    
    def ensure_safe_state(self) -> bool:
        """
        Ensure system is in safe state before proceeding.
        
        Returns:
            True if safe, False otherwise
            
        Raises:
            APSSafetyError: If system is not safe
        """
        if not self.is_safe():
            status = self.get_status()
            if status.safety_cover != "closed":
                raise APSSafetyError("Safety cover is open")
            if status.emergency_button != "not pressed":
                raise APSSafetyError("Emergency button is pressed")
            return False
        return True
    
    # ===========================================
    # Context Manager Support
    # ===========================================
    
    def __enter__(self):
        """Context manager entry."""
        if not self.connect():
            raise APSCommunicationError("Failed to connect to APS controller")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()


# ===========================================
# High-Level Test Classes
# ===========================================

class DPTTest:
    """High-level interface for DPT tests."""
    
    def __init__(self, controller: APSController):
        self.controller = controller
    
    def configure(self, **parameters) -> bool:
        """
        Configure DPT test parameters.
        
        Args:
            **parameters: Parameter name-value pairs
            
        Returns:
            True if all parameters set successfully
        """
        success = True
        for param, value in parameters.items():
            response = self.controller.dpt_parameter(param, value)
            if response is None:
                success = False
        return success
    
    def run(self, current_a: float, voltage_v: float, 
           wait_for_completion: bool = False) -> Union[bool, str]:
        """
        Run DPT test with specified parameters.
        
        Args:
            current_a: Current in Amperes
            voltage_v: Voltage in Volts
            wait_for_completion: Whether to wait for test completion
            
        Returns:
            If wait_for_completion=True: True if test completed successfully
            If wait_for_completion=False: Response string from controller, or None if error
        """
        self.controller.ensure_safe_state()
        
        response = self.controller.dpt_test(current_a, voltage_v)
        if response:
            if wait_for_completion:
                return self.controller.wait_for_test_completion()
            return response
        return None


def main():
    """Example usage of the APS interface library."""
    
    # Configuration
    PORT = 'COM7'  # Update with your port
    
    print("=== APS Control Software Interface Demo ===")
    
    try:
        # Connect using context manager
        with APSController(PORT) as aps:
            
            # Get system status
            status = aps.get_status()
            print("System Status:")
            print(f"  Test running: {status.test_running}")
            print(f"  Safety cover: {status.safety_cover}")
            print(f"  Emergency button: {status.emergency_button}")
            print(f"  System safe: {status.is_safe}")
                        
            # Run self-test
            print("\nRunning self-test...")
            selftest_response = aps.self_test()
            if selftest_response:
                print(f"Self-test response: {selftest_response}")
            
            # Get system information
            print("\nGetting system information...")
            info_data = aps.info()
            if info_data:
                print("\nParsed system information:")
                print(f"  Kernel: {info_data.get('kernel', 'Unknown')}")
                print(f"  Board: {info_data.get('board', 'Unknown')}")
                print(f"  Platform: {info_data.get('platform', 'Unknown')}")
                print(f"  Build time: {info_data.get('build_time', 'Unknown')}")
            
            # Configure DPT parameters
            print("\nConfiguring DPT parameters...")
            param_response = aps.dpt_parameter('R_DUT', 0.025)
            if param_response:
                print(f"R_DUT set response: {param_response}")
            param_response = aps.dpt_parameter('V_DUT', 0.0)
            if param_response:
                print(f"V_DUT set response: {param_response}")
            
            # Read back parameter
            r_dut = aps.dpt_parameter('R_DUT')
            print(f"R_DUT = {r_dut}")
            
            #Example: Run DPT test (commented out for safety)
            print("\nRunning DPT test...")
            dpt_response = aps.dpt_test(current_a=5.0, voltage_v=50.0)
            if dpt_response:
                print(f"DPT test response: {dpt_response}")

            # Get user confirmation before starting
            print("\nReady to send start command to APS controller.")
            user_input = input("Type 'start' to proceed or any other key to skip: ").strip().lower()
            
            if user_input == 'start':
                print("Sending start command...")
                start_response = aps.start()
                if start_response:
                    print(f"Start command response: {start_response}")
                else:
                    print("Start command sent (no response)")
                
                done = False
                while not done:
                    # Read available data from serial port
                    if aps.serial_conn and aps.serial_conn.in_waiting > 0:
                        try:
                            line = aps.serial_conn.readline().decode('utf-8', errors='ignore').strip()
                            if line:
                                # Check if test is complete
                                if line.endswith(">"):
                                    done = True
                                    print("-" * 60)
                                    print("Test completed!")                             
                                else:
                                    print(line)
                        except Exception as e:
                            print(f"Error reading from serial: {e}")
                            break
                    else:
                        # Small delay
                        time.sleep(0.1)
            else:
                print("Start command skipped.")
            
            print("\nDemo completed successfully!")
            
    except APSControllerError as e:
        print(f"APS Controller Error: {e}")
    except Exception as e:
        print(f"Unexpected error: {e}")


if __name__ == '__main__':
    main()