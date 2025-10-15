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

class NGE100:
    """Interface lib for Rohde & Schwarz NGE100 power supplies."""
    
    def __init__(self, resource_string: str, channels: int = 3):
        """
        Initialize power supply connection.
        
        Args:
            resource_string: VISA resource string (e.g., 'USB0::0x0AAD::0x0197::103456::INSTR')
            channels: Number of channels (default: 3 for NGE103)
        """
        self.resource_string = resource_string
        self.psu: Optional[pyvisa.Resource] = None
        self.rm = pyvisa.ResourceManager()
        self.num_channels = channels
        
    def connect(self) -> bool:
        """Connect to the power supply."""
        try:
            self.psu = self.rm.open_resource(self.resource_string)
            self.psu.timeout = 5000  # 5 second timeout

            # Test connection
            ID = self.ID()
            if "Rohde&Schwarz,NGE10" not in ID:
                print("Connected to unsupported device:", ID)
                self.psu = None
                return False
            return True

        except Exception:
            self.psu = None
            return False
            
    def ID(self) -> bool:
        """Connect to the power supply."""
        if not self.psu:
            raise RuntimeError("Not connected to power supply")
        return self.psu.query('*IDN?')


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
        
        self.psu.write('*RST')
        self.psu.write('*CLS')
        time.sleep(1)  # Wait for reset to complete

    def remote(self, enabled: bool = True):
        """Enable or disable remote control."""
        if not self.psu:
            raise RuntimeError("Not connected to power supply")

        if enabled:
            self.psu.write('SYST:REM')
        else:
            self.psu.write('SYST:LOC')
    
    def enable_master_output(self, enabled: bool):
        """
        Enable or disable the instrument master output.

        Uses the SCPI command OUTP:GEN ON/OFF which affects all channels at once.
        """
        if not self.psu:
            raise RuntimeError("Not connected to power supply")

        state = 'ON' if enabled else 'OFF'
        self.psu.write(f'OUTP:GEN {state}')

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
        # Select the channel first, then send the channel-less OUTP command
        self.select_channel(channel)
        self.psu.write(f'OUTP {state}')
    
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

        # Select the target channel first, then set voltage without channel suffix
        self.select_channel(channel)
        self.psu.write(f'VOLT {voltage}')
    
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

        # Select target channel first, then use channel-less command
        self.select_channel(channel)
        self.psu.write(f'CURR {current}')

    def select_channel(self, channel: int):
        """
        Select the active channel on the instrument using INST:NSEL <n>.

        After selecting, subsequent channel-less SCPI commands apply to this channel.
        """
        if not self.psu:
            raise RuntimeError("Not connected to power supply")

        if not (1 <= channel <= self.num_channels):
            raise ValueError(f"Channel must be between 1 and {self.num_channels}")

        # Use the instrument channel select command
        self.psu.write(f'INST:NSEL {channel}')
    
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
            # Select the channel then query the channel-less parameter
            self.select_channel(channel)
            response = self.psu.query('VOLT?').strip()
            voltage = float(response)
            return voltage
        except Exception:
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
            self.select_channel(channel)
            response = self.psu.query('CURR?').strip()
            current = float(response)
            return current
        except Exception:
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
            self.select_channel(channel)
            response = self.psu.query('MEAS:VOLT?').strip()
            voltage = float(response)
            return voltage
        except Exception:
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
            self.select_channel(channel)
            response = self.psu.query('MEAS:CURR?').strip()
            current = float(response)
            return current
        except Exception:
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
            # Select the channel and query output state without channel suffix
            self.select_channel(channel)
            response = self.psu.query('OUTP?').strip()
            # Some instruments return '1'/'0' others 'ON'/'OFF' - handle both
            if response in ('1', 'ON', 'On', 'on'):
                return True
            if response in ('0', 'OFF', 'Off', 'off'):
                return False
            # Fallback: try numeric conversion
            try:
                return int(response) == 1
            except Exception:
                return None
        except Exception:
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
        except Exception:
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
        
    # Configuration details (previously printed) removed
        
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
        """Disable all outputs immediately.
        """
    # Emergency stop: attempt global disable

        if not self.psu:
            raise RuntimeError("Not connected to power supply")

        self.enable_master_output(False)
        self.enable_output(1, False)
        self.enable_output(2, False)
        self.enable_output(3, False)
    # Master output disabled
        return

def print_channel_status(status: Dict):
    """Return a simple textual representation of channel status (no printing)."""
    return (
        f"Channel {status['channel']}:\n"
        f"  Output: {'ON' if status['output_enabled'] else 'OFF'}\n"
        f"  Voltage: {status['voltage_setpoint']:.3f} V (set) / {status['voltage_measured']:.3f} V (measured)\n"
        f"  Current: {status['current_setpoint']:.3f} A (limit) / {status['current_measured']:.3f} A (measured)"
    )


def show_nge103_commands():
    """Show NGE103 SCPI commands."""
    # Previously displayed SCPI commands; output suppressed
    return [
        "INST:NSEL <n>",
        "VOLT <value>",
        "CURR <value>",
        "OUTP ON/OFF",
        "OUTP:GEN ON/OFF",
        "MEAS:VOLT?",
        "MEAS:CURR?",
        "VOLT?",
        "OUTP:TRACK <mode>",
    ]


def main():
    """Test the NGE103 power supply controller."""
    
    # Configuration
    RESOURCE_STRING = 'ASRL8::INSTR'  # Update with your NGE103 address
    
    # Show commands (no output)
    show_nge103_commands()

    # List available resources
    rm = pyvisa.ResourceManager()
    resources = rm.list_resources()
    print("Available VISA resources:", resources)
    
    if not resources:
        return
    
    # Use first available resource if default not found
    if RESOURCE_STRING not in resources:
        if resources:
            RESOURCE_STRING = resources[0]
        else:
            return
    
    print("Testing NGE100 connection and control")
    print("Connecting to:", RESOURCE_STRING)

    # Create controller and connect
    psu = NGE100(RESOURCE_STRING, channels=3)

    
    if not psu.connect():
        print("Failed to connect to power supply.")
        return

    print("Connected to:", psu.ID())

    try:
        print("Running test sequence...")
        # Reset to known state
        psu.reset()

        # Configure Channel 1: 5V, 1A limit
        psu.configure_channel(1, voltage=5.0, current=1.0, enabled=False)

        # Configure Channel 2: 10V, 0.5A limit
        psu.configure_channel(2, voltage=10.0, current=0.5, enabled=False)

        # Configure Channel 3: 15V, 0.2A limit
        psu.configure_channel(3, voltage=15.0, current=0.2, enabled=False)

        statuses = psu.get_all_channels_status()
        [print_channel_status(s) for s in statuses]

        # Enable outputs one by one
        psu.enable_output(1, True)
        time.sleep(0.5)

        psu.enable_output(2, True)
        time.sleep(0.5)

        psu.enable_output(3, True)
        time.sleep(0.5)

        statuses = psu.get_all_channels_status()
        [print_channel_status(s) for s in statuses]

        # Adjust Channel 1 voltage
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

        # Adjust Channel 2 current limit
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

        # Turn outputs on/off in sequence
        for i in range(3):
            # Disable all
            for channel in range(1, 4):
                psu.enable_output(channel, False)
                time.sleep(0.2)

            # Enable all
            for channel in range(1, 4):
                psu.enable_output(channel, True)
                time.sleep(0.2)

        # Final status
        statuses = psu.get_all_channels_status()
        [print_channel_status(s) for s in statuses]
        
    except KeyboardInterrupt:
        pass

    except Exception:
        pass

    finally:
        # Safety: Disable all outputs before disconnecting
        psu.emergency_stop()
        psu.remote(False)
        psu.disconnect()


if __name__ == '__main__':
    main()