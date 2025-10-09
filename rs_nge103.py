#!/usr/bin/env python3
"""
Test script for controlling Rohde & Schwarz NGE103 power supply.
Tests channel configuration, voltage/current setting, and output control.

Requirements:
- pyvisa

Usage:
    python test_rs_nge103.py
"""

import pyvisa
import time
from typing import Optional, List, Dict

class RSNGE103Controller:
    """Controller for Rohde & Schwarz NGE103 power supply."""
    
    def __init__(self, resource_string: str):
        """
        Initialize power supply connection.
        
        Args:
            resource_string: VISA resource string (e.g., 'USB0::0x0AAD::0x0197::103456::INSTR')
        """
        self.resource_string = resource_string
        self.psu: Optional[pyvisa.Resource] = None
        self.rm = pyvisa.ResourceManager()
        self.num_channels = 3  # NGE103 has 3 channels
        
    def connect(self) -> bool:
        """Connect to the power supply."""
        try:
            self.psu = self.rm.open_resource(self.resource_string)
            self.psu.timeout = 5000  # 5 second timeout
            
            # Test connection
            idn = self.psu.query('*IDN?').strip()
            print(f"Connected to: {idn}")
            return True
            
        except Exception as e:
            print(f"Failed to connect to power supply: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from the power supply."""
        if self.psu:
            self.psu.close()
            self.psu = None
        self.rm.close()
    
    def reset(self):
        """Reset power supply to default state."""
        if not self.psu:
            raise RuntimeError("Not connected to power supply")
        
        print("Resetting power supply...")
        self.psu.write('*RST')
        self.psu.write('*CLS')
        time.sleep(1)  # Wait for reset to complete
    
    def enable_output(self, channel: int, enabled: bool = True):
        """
        Enable or disable output for specified channel.
        
        Args:
            channel: Channel number (1-3)
            enabled: True to enable, False to disable
        """
        if not self.psu:
            raise RuntimeError("Not connected to power supply")
        
        if not (1 <= channel <= self.num_channels):
            raise ValueError(f"Channel must be between 1 and {self.num_channels}")
        
        state = "ON" if enabled else "OFF"
        print(f"Channel {channel} output: {state}")
        self.psu.write(f'OUTP{channel} {state}')
    
    def set_voltage(self, channel: int, voltage: float):
        """
        Set output voltage for specified channel.
        
        Args:
            channel: Channel number (1-3)
            voltage: Voltage in V
        """
        if not self.psu:
            raise RuntimeError("Not connected to power supply")
        
        if not (1 <= channel <= self.num_channels):
            raise ValueError(f"Channel must be between 1 and {self.num_channels}")
        
        print(f"Setting Channel {channel} voltage to {voltage} V")
        self.psu.write(f'VOLT{channel} {voltage}')
    
    def set_current(self, channel: int, current: float):
        """
        Set output current limit for specified channel.
        
        Args:
            channel: Channel number (1-3)
            current: Current in A
        """
        if not self.psu:
            raise RuntimeError("Not connected to power supply")
        
        if not (1 <= channel <= self.num_channels):
            raise ValueError(f"Channel must be between 1 and {self.num_channels}")
        
        print(f"Setting Channel {channel} current limit to {current} A")
        self.psu.write(f'CURR{channel} {current}')
    
    def get_voltage_setpoint(self, channel: int) -> Optional[float]:
        """
        Get voltage setpoint for specified channel.
        
        Args:
            channel: Channel number (1-3)
            
        Returns:
            Voltage setpoint in V or None if error
        """
        if not self.psu:
            raise RuntimeError("Not connected to power supply")
        
        if not (1 <= channel <= self.num_channels):
            raise ValueError(f"Channel must be between 1 and {self.num_channels}")
        
        try:
            response = self.psu.query(f'VOLT{channel}?').strip()
            voltage = float(response)
            return voltage
        except Exception as e:
            print(f"Error reading voltage setpoint for Channel {channel}: {e}")
            return None
    
    def get_current_setpoint(self, channel: int) -> Optional[float]:
        """
        Get current limit setpoint for specified channel.
        
        Args:
            channel: Channel number (1-3)
            
        Returns:
            Current setpoint in A or None if error
        """
        if not self.psu:
            raise RuntimeError("Not connected to power supply")
        
        if not (1 <= channel <= self.num_channels):
            raise ValueError(f"Channel must be between 1 and {self.num_channels}")
        
        try:
            response = self.psu.query(f'CURR{channel}?').strip()
            current = float(response)
            return current
        except Exception as e:
            print(f"Error reading current setpoint for Channel {channel}: {e}")
            return None
    
    def measure_voltage(self, channel: int) -> Optional[float]:
        """
        Measure actual output voltage for specified channel.
        
        Args:
            channel: Channel number (1-3)
            
        Returns:
            Measured voltage in V or None if error
        """
        if not self.psu:
            raise RuntimeError("Not connected to power supply")
        
        if not (1 <= channel <= self.num_channels):
            raise ValueError(f"Channel must be between 1 and {self.num_channels}")
        
        try:
            response = self.psu.query(f'MEAS:VOLT{channel}?').strip()
            voltage = float(response)
            return voltage
        except Exception as e:
            print(f"Error measuring voltage for Channel {channel}: {e}")
            return None
    
    def measure_current(self, channel: int) -> Optional[float]:
        """
        Measure actual output current for specified channel.
        
        Args:
            channel: Channel number (1-3)
            
        Returns:
            Measured current in A or None if error
        """
        if not self.psu:
            raise RuntimeError("Not connected to power supply")
        
        if not (1 <= channel <= self.num_channels):
            raise ValueError(f"Channel must be between 1 and {self.num_channels}")
        
        try:
            response = self.psu.query(f'MEAS:CURR{channel}?').strip()
            current = float(response)
            return current
        except Exception as e:
            print(f"Error measuring current for Channel {channel}: {e}")
            return None
    
    def get_output_status(self, channel: int) -> Optional[bool]:
        """
        Get output status for specified channel.
        
        Args:
            channel: Channel number (1-3)
            
        Returns:
            True if output enabled, False if disabled, None if error
        """
        if not self.psu:
            raise RuntimeError("Not connected to power supply")
        
        if not (1 <= channel <= self.num_channels):
            raise ValueError(f"Channel must be between 1 and {self.num_channels}")
        
        try:
            response = self.psu.query(f'OUTP{channel}?').strip()
            return response == '1'
        except Exception as e:
            print(f"Error reading output status for Channel {channel}: {e}")
            return None
    
    def get_channel_status(self, channel: int) -> Optional[Dict]:
        """
        Get comprehensive status for specified channel.
        
        Args:
            channel: Channel number (1-3)
            
        Returns:
            Dictionary with status information or None if error
        """
        if not (1 <= channel <= self.num_channels):
            raise ValueError(f"Channel must be between 1 and {self.num_channels}")
        
        try:
            status = {
                'channel': channel,
                'output_enabled': self.get_output_status(channel),
                'voltage_setpoint': self.get_voltage_setpoint(channel),
                'current_setpoint': self.get_current_setpoint(channel),
                'voltage_measured': self.measure_voltage(channel),
                'current_measured': self.measure_current(channel)
            }
            return status
        except Exception as e:
            print(f"Error getting channel status: {e}")
            return None
    
    def get_all_channels_status(self) -> List[Dict]:
        """
        Get status for all channels.
        
        Returns:
            List of status dictionaries for all channels
        """
        statuses = []
        for channel in range(1, self.num_channels + 1):
            status = self.get_channel_status(channel)
            if status:
                statuses.append(status)
        return statuses
    
    def configure_channel(self, channel: int, voltage: float, current: float, 
                         enabled: bool = True):
        """
        Configure a channel with voltage, current limit, and output state.
        
        Args:
            channel: Channel number (1-3)
            voltage: Output voltage in V
            current: Current limit in A
            enabled: Enable output after configuration
        """
        if not (1 <= channel <= self.num_channels):
            raise ValueError(f"Channel must be between 1 and {self.num_channels}")
        
        print(f"Configuring Channel {channel}:")
        print(f"  Voltage: {voltage} V")
        print(f"  Current limit: {current} A")
        print(f"  Output enabled: {enabled}")
        
        # Disable output first for safety
        self.enable_output(channel, False)
        
        # Set voltage and current
        self.set_voltage(channel, voltage)
        self.set_current(channel, current)
        
        # Wait a moment for settings to take effect
        time.sleep(0.1)
        
        # Enable output if requested
        if enabled:
            self.enable_output(channel, True)
    
    def emergency_stop(self):
        """Disable all outputs immediately."""
        print("EMERGENCY STOP - Disabling all outputs!")
        for channel in range(1, self.num_channels + 1):
            try:
                self.enable_output(channel, False)
            except Exception as e:
                print(f"Error disabling Channel {channel}: {e}")
    
    def set_tracking_mode(self, mode: str = 'INDEP'):
        """
        Set tracking mode for channels.
        
        Args:
            mode: Tracking mode ('INDEP', 'SER', 'PAR')
                 INDEP = Independent (default)
                 SER = Series tracking
                 PAR = Parallel tracking
        """
        if not self.psu:
            raise RuntimeError("Not connected to power supply")
        
        valid_modes = ['INDEP', 'SER', 'PAR']
        if mode not in valid_modes:
            raise ValueError(f"Mode must be one of {valid_modes}")
        
        print(f"Setting tracking mode to: {mode}")
        self.psu.write(f'OUTP:TRACK {mode}')


def print_channel_status(status: Dict):
    """Pretty print channel status."""
    print(f"Channel {status['channel']}:")
    print(f"  Output: {'ON' if status['output_enabled'] else 'OFF'}")
    print(f"  Voltage: {status['voltage_setpoint']:.3f} V (set) / {status['voltage_measured']:.3f} V (measured)")
    print(f"  Current: {status['current_setpoint']:.3f} A (limit) / {status['current_measured']:.3f} A (measured)")


def show_nge103_commands():
    """Show NGE103 SCPI commands."""
    print("\n=== NGE103 SCPI Commands ===")
    print("Function                | Command")
    print("-" * 40)
    print("Set Voltage Ch1         | VOLT1 5.0")
    print("Set Current Ch2         | CURR2 1.0") 
    print("Enable Output Ch3       | OUTP3 ON")
    print("Measure Voltage Ch1     | MEAS:VOLT1?")
    print("Measure Current Ch2     | MEAS:CURR2?")
    print("Get Voltage Setting     | VOLT1?")
    print("Tracking Mode           | OUTP:TRACK INDEP")
    print("-" * 40)


def main():
    """Test the NGE103 power supply controller."""
    
    # Configuration
    RESOURCE_STRING = 'USB0::0x0AAD::0x0197::103456::INSTR'  # Update with your NGE103 address
    
    print("=== Rohde & Schwarz NGE103 Power Supply Test ===")
    
    # Show commands
    show_nge103_commands()
    
    # List available resources
    rm = pyvisa.ResourceManager()
    resources = rm.list_resources()
    print(f"Available VISA resources: {resources}")
    
    if not resources:
        print("No VISA resources found. Please check connections.")
        return
    
    # Use first available resource if default not found
    if RESOURCE_STRING not in resources:
        if resources:
            RESOURCE_STRING = resources[0]
            print(f"Using first available resource: {RESOURCE_STRING}")
        else:
            print("No resources available")
            return
    
    # Create controller and connect
    psu = RSNGE103Controller(RESOURCE_STRING)
    
    if not psu.connect():
        print("Failed to connect to power supply")
        return
    
    try:
        # Reset to known state
        psu.reset()
        
        # Configure tracking mode
        psu.set_tracking_mode('INDEP')
        
        print("\n=== Initial Configuration ===")
        
        # Configure Channel 1: 5V, 1A limit
        psu.configure_channel(1, voltage=5.0, current=1.0, enabled=False)
        
        # Configure Channel 2: 12V, 0.5A limit
        psu.configure_channel(2, voltage=12.0, current=0.5, enabled=False)
        
        # Configure Channel 3: -5V, 0.2A limit
        psu.configure_channel(3, voltage=-5.0, current=0.2, enabled=False)
        
        print("\n=== Status Check (Outputs Disabled) ===")
        statuses = psu.get_all_channels_status()
        for status in statuses:
            print_channel_status(status)
        
        print("\n=== Enabling Outputs ===")
        
        # Enable outputs one by one
        psu.enable_output(1, True)
        time.sleep(0.5)
        
        psu.enable_output(2, True)
        time.sleep(0.5)
        
        psu.enable_output(3, True)
        time.sleep(0.5)
        
        print("\n=== Status Check (Outputs Enabled) ===")
        statuses = psu.get_all_channels_status()
        for status in statuses:
            print_channel_status(status)
        
        print("\n=== Voltage Adjustment Test ===")
        
        # Adjust Channel 1 voltage
        print("Adjusting Channel 1 voltage: 5V -> 3.3V -> 5V")
        psu.set_voltage(1, 3.3)
        time.sleep(1)
        
        status = psu.get_channel_status(1)
        if status:
            print_channel_status(status)
        
        psu.set_voltage(1, 5.0)
        time.sleep(1)
        
        status = psu.get_channel_status(1)
        if status:
            print_channel_status(status)
        
        print("\n=== Current Limit Test ===")
        
        # Adjust Channel 2 current limit
        print("Adjusting Channel 2 current limit: 0.5A -> 0.1A -> 0.5A")
        psu.set_current(2, 0.1)
        time.sleep(0.5)
        
        status = psu.get_channel_status(2)
        if status:
            print_channel_status(status)
        
        psu.set_current(2, 0.5)
        time.sleep(0.5)
        
        status = psu.get_channel_status(2)
        if status:
            print_channel_status(status)
        
        print("\n=== Sequential Output Test ===")
        
        # Turn outputs on/off in sequence
        for i in range(3):
            print(f"Cycle {i+1}/3:")
            
            # Disable all
            for channel in range(1, 4):
                psu.enable_output(channel, False)
                time.sleep(0.2)
            
            # Enable all
            for channel in range(1, 4):
                psu.enable_output(channel, True)
                time.sleep(0.2)
        
        print("\nTest completed successfully!")
        
        # Final status
        print("\n=== Final Status ===")
        statuses = psu.get_all_channels_status()
        for status in statuses:
            print_channel_status(status)
        
    except KeyboardInterrupt:
        print("\nTest interrupted by user")
        
    except Exception as e:
        print(f"Test failed: {e}")
        
    finally:
        # Safety: Disable all outputs before disconnecting
        print("\nDisabling all outputs...")
        psu.emergency_stop()
        psu.disconnect()


if __name__ == '__main__':
    main()