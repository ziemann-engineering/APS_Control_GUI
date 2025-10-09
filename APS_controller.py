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
import time
import re
import threading
from typing import Optional, Union
from dataclasses import dataclass
from enum import Enum

compatible_build_date = (2025, 10, 1)  # enter compatible version's build: Year, Month, Day
compatible_board_type = "APS Control Board 1.3" # enter compatible board type string


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
    
    def __init__(self, port: str, baudrate: int = 38400, timeout: float = 5.0):
        """
        Initialize APS controller interface.
        
        Args:
            port: Serial port name (e.g., 'COM3' on Windows, '/dev/ttyUSB0' on Linux)
            baudrate: Serial communication baud rate (default: 38400)
            timeout: Command timeout in seconds (default: 5.0)
        """
        self.port = port
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
            time.sleep(0.5)
            
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
                response_timeout = timeout or self.timeout
                start_time = time.time()
                response_lines = []
                
                while time.time() - start_time < response_timeout:
                    if self.serial_conn.in_waiting > 0:
                        line = self.serial_conn.readline().decode('ascii', errors='ignore').strip()
                        if line:
                            response_lines.append(line)
                            # Look for shell prompt to indicate end of response
                            if line.endswith('>') or line.endswith('$'):
                                break
                    else:
                        time.sleep(0.01)
                
                return '\n'.join(response_lines) if response_lines else None
                
            except Exception as e:
                raise APSCommunicationError(f"Communication error: {e}")
    
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
        return self._send_command("selftest")
    
    def start(self) -> Optional[str]:
        """
        Send start command to the APS controller.
        
        Returns:
            Response string from controller, or None if error
        """
        return self._send_command("start")
    
    def info(self, print_response: bool = True) -> Optional[dict]:
        """
        Get system information from the APS controller.
        
        Args:
            print_response: Whether to print the raw response (default: True)
        
        Returns:
            Dictionary with parsed system information or None if error
        """
        response = self._send_command("info")
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
        Validate that the connected board is the correct type and firmware version.
        
        Returns:
            True if board validation passes, False otherwise
        """
        try:
            # Get system info without printing
            info_data = self.info(print_response=False)
            if not info_data:
                print("Failed to get system information for validation")
                return False
            
            # Check board type
            board_name = info_data.get('board') or info_data.get('Board', '')
            
            if board_name != compatible_board_type:
                print("Board validation failed:")
                print(f"  Expected: {compatible_board_type}")
                print(f"  Found: {board_name}")
                return False
            
            # Check build time (must be after September 2025)
            build_time_str = info_data.get('build_time') or info_data.get('Build time', '')
            if not build_time_str:
                print("Build time not found in system information")
                return False
            
            if not self._validate_build_time(build_time_str):
                return False
            
            print(f"Board validation passed: {board_name}")
            print(f"Build time: {build_time_str}")
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
    
    def coss_test(self, *args) -> bool:
        """
        Run COSS (Output Capacitance) test.
        
        Args:
            *args: Test parameters (implementation-specific)
            
        Returns:
            True if test started successfully
        """
        cmd = "COSS_test"
        if args:
            cmd += " " + " ".join(str(arg) for arg in args)
        
        response = self._send_command(cmd)
        return response is not None
    
    # ===========================================
    # UIS (Unclamped Inductive Switching) Test Commands
    # ===========================================
    
    def uis_test(self, *args) -> bool:
        """
        Run UIS (Unclamped Inductive Switching) test.
        
        Args:
            *args: Test parameters (implementation-specific)
            
        Returns:
            True if test started successfully
        """
        cmd = "UIS_test"
        if args:
            cmd += " " + " ".join(str(arg) for arg in args)
        
        response = self._send_command(cmd)
        return response is not None
    
    # ===========================================
    # SCT (Short Circuit Test) Commands
    # ===========================================
    
    def sct_test(self, *args) -> Optional[str]:
        """
        Run SCT (Short Circuit Test).
        
        Args:
            *args: Test parameters (implementation-specific)
            
        Returns:
            Response string from controller, or None if error
        """
        cmd = "SCT_test"
        if args:
            cmd += " " + " ".join(str(arg) for arg in args)
        
        return self._send_command(cmd)
    
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
    
    def cmti_test(self, *args) -> bool:
        """
        Run CMTI (Common Mode Transient Immunity) test.
        
        Args:
            *args: Test parameters (implementation-specific)
            
        Returns:
            True if test started successfully
        """
        cmd = "CMTI_test"
        if args:
            cmd += " " + " ".join(str(arg) for arg in args)
        
        response = self._send_command(cmd)
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
    
    def zcs_test(self, *args) -> bool:
        """
        Run ZCS (Zero Current Switching) test.
        
        Args:
            *args: Test parameters (implementation-specific)
            
        Returns:
            True if test started successfully
        """
        cmd = "ZCS_test"
        if args:
            cmd += " " + " ".join(str(arg) for arg in args)
        
        response = self._send_command(cmd)
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
    
    def hppt_test(self, *args) -> bool:
        """
        Run HPPT (High Power Pulse Test).
        
        Args:
            *args: Test parameters (implementation-specific)
            
        Returns:
            True if test started successfully
        """
        cmd = "HPPT_test"
        if args:
            cmd += " " + " ".join(str(arg) for arg in args)
        
        response = self._send_command(cmd)
        return response
    
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
    
    def cgd_test(self, *args) -> bool:
        """
        Run CGD (Gate Charge) test.
        
        Args:
            *args: Test parameters (implementation-specific)
            
        Returns:
            True if test started successfully
        """
        cmd = "CGD_test"
        if args:
            cmd += " " + " ".join(str(arg) for arg in args)
        
        response = self._send_command(cmd)
        return response is not None
    
    # ===========================================
    # CGG2 Test Commands
    # ===========================================
    
    def cgg2a_test(self, *args) -> bool:
        """
        Run CGG2A test.
        
        Args:
            *args: Test parameters (implementation-specific)
            
        Returns:
            True if test started successfully
        """
        cmd = "CGG2A_test"
        if args:
            cmd += " " + " ".join(str(arg) for arg in args)
        
        response = self._send_command(cmd)
        return response is not None
    
    def cgg2d_test(self, *args) -> bool:
        """
        Run CGG2D test.
        
        Args:
            *args: Test parameters (implementation-specific)
            
        Returns:
            True if test started successfully
        """
        cmd = "CGG2D_test"
        if args:
            cmd += " " + " ".join(str(arg) for arg in args)
        
        response = self._send_command(cmd)
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
    PORT = 'COM3'  # Update with your port
    
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
            
            if not status.is_safe:
                print("System not safe - aborting demo")
                return
            
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
            dpt_response = aps.dpt_test(current_a=5.0, voltage_v=10.0)
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
            else:
                print("Start command skipped.")
            
            print("\nDemo completed successfully!")
            
    except APSControllerError as e:
        print(f"APS Controller Error: {e}")
    except Exception as e:
        print(f"Unexpected error: {e}")


if __name__ == '__main__':
    main()