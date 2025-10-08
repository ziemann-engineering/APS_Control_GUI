#!/usr/bin/env python3
"""
Test script for controlling Keysight DSO-S series oscilloscopes.
Tests channel configuration, trigger settings, and basic measurements.

Requirements:
- pyvisa
- numpy (optional, for data processing)

Usage:
    python test_keysight_dso_s.py
"""

import pyvisa
import time
from typing import Optional

class KeysightDSOSController:
    """Controller for Keysight DSO-S series oscilloscopes."""
    
    def __init__(self, resource_string: str):
        """
        Initialize oscilloscope connection.
        
        Args:
            resource_string: VISA resource string (e.g., 'USB0::0x2A8D::0x900E::MY12345678::INSTR')
        """
        self.resource_string = resource_string
        self.scope: Optional[pyvisa.Resource] = None
        self.rm = pyvisa.ResourceManager()
        
    def connect(self) -> bool:
        """Connect to the oscilloscope."""
        try:
            self.scope = self.rm.open_resource(self.resource_string)
            self.scope.timeout = 10000  # 10 second timeout
            
            # Test connection
            idn = self.scope.query('*IDN?').strip()
            print(f"Connected to: {idn}")
            return True
            
        except Exception as e:
            print(f"Failed to connect to oscilloscope: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from the oscilloscope."""
        if self.scope:
            self.scope.close()
            self.scope = None
        self.rm.close()
    
    def reset(self):
        """Reset oscilloscope to default state."""
        if not self.scope:
            raise RuntimeError("Not connected to oscilloscope")
        
        print("Resetting oscilloscope...")
        self.scope.write('*RST')
        self.scope.write('*CLS')
        time.sleep(2)  # Wait for reset to complete
    
    def setup_channel(self, channel: int, enabled: bool = True, scale: float = 1.0, 
                     offset: float = 0.0, coupling: str = 'DC', impedance: str = '1MEG'):
        """
        Configure an oscilloscope channel.
        
        Args:
            channel: Channel number (1-4)
            enabled: Enable/disable channel
            scale: Vertical scale in V/div
            offset: Vertical offset in V
            coupling: Input coupling ('DC', 'AC', 'GND')
            impedance: Input impedance ('1MEG', '50')
        """
        if not self.scope:
            raise RuntimeError("Not connected to oscilloscope")
        
        print(f"Configuring Channel {channel}:")
        print(f"  Enabled: {enabled}")
        print(f"  Scale: {scale} V/div")
        print(f"  Offset: {offset} V")
        print(f"  Coupling: {coupling}")
        print(f"  Impedance: {impedance}")
        
        # Enable/disable channel
        self.scope.write(f':CHAN{channel}:DISP {"ON" if enabled else "OFF"}')
        
        if enabled:
            # Set vertical scale
            self.scope.write(f':CHAN{channel}:SCAL {scale}')
            
            # Set vertical offset
            self.scope.write(f':CHAN{channel}:OFFS {offset}')
            
            # Set coupling
            self.scope.write(f':CHAN{channel}:COUP {coupling}')
            
            # Set input impedance
            self.scope.write(f':CHAN{channel}:IMP {impedance}')
    
    def setup_timebase(self, scale: float = 1e-3, offset: float = 0.0):
        """
        Configure timebase settings.
        
        Args:
            scale: Time scale in s/div
            offset: Time offset in s
        """
        if not self.scope:
            raise RuntimeError("Not connected to oscilloscope")
        
        print("Configuring timebase:")
        print(f"  Scale: {scale} s/div")
        print(f"  Offset: {offset} s")
        
        self.scope.write(f':TIM:SCAL {scale}')
        self.scope.write(f':TIM:OFFS {offset}')
    
    def setup_trigger(self, source: str = 'CHAN1', level: float = 0.0, 
                     slope: str = 'POS', mode: str = 'EDGE'):
        """
        Configure trigger settings.
        
        Args:
            source: Trigger source ('CHAN1', 'CHAN2', 'CHAN3', 'CHAN4', 'EXT')
            level: Trigger level in V
            slope: Trigger slope ('POS', 'NEG', 'EITH')
            mode: Trigger mode ('EDGE', 'PULS', 'SLOP', 'VID')
        """
        if not self.scope:
            raise RuntimeError("Not connected to oscilloscope")
        
        print("Configuring trigger:")
        print(f"  Source: {source}")
        print(f"  Level: {level} V")
        print(f"  Slope: {slope}")
        print(f"  Mode: {mode}")
        
        # Set trigger mode
        self.scope.write(f':TRIG:MODE {mode}')
        
        # Set trigger source
        self.scope.write(f':TRIG:{mode}:SOUR {source}')
        
        # Set trigger level
        self.scope.write(f':TRIG:{mode}:LEV {level}')
        
        # Set trigger slope (for edge trigger)
        if mode == 'EDGE':
            self.scope.write(f':TRIG:{mode}:SLOP {slope}')
    
    def set_acquisition_mode(self, mode: str = 'NORM', averages: int = 1):
        """
        Set acquisition mode.
        
        Args:
            mode: Acquisition mode ('NORM', 'AVER', 'PEAK', 'HRES')
            averages: Number of averages (for AVER mode)
        """
        if not self.scope:
            raise RuntimeError("Not connected to oscilloscope")
        
        print(f"Setting acquisition mode: {mode}")
        self.scope.write(f':ACQ:TYPE {mode}')
        
        if mode == 'AVER':
            print(f"Setting averages: {averages}")
            self.scope.write(f':ACQ:AVER {averages}')
    
    def single_acquisition(self) -> bool:
        """
        Perform a single acquisition.
        
        Returns:
            True if acquisition completed successfully
        """
        if not self.scope:
            raise RuntimeError("Not connected to oscilloscope")
        
        print("Starting single acquisition...")
        self.scope.write(':SING')
        
        # Wait for acquisition to complete
        timeout = 10  # seconds
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            status = self.scope.query(':TRIG:STAT?').strip()
            if status == 'STOP':
                print("Acquisition completed")
                return True
            time.sleep(0.1)
        
        print("Acquisition timeout")
        return False
    
    def get_waveform_data(self, channel: int):
        """
        Get waveform data from specified channel.
        
        Args:
            channel: Channel number (1-4)
            
        Returns:
            Tuple of (time_data, voltage_data) or None if error
        """
        if not self.scope:
            raise RuntimeError("Not connected to oscilloscope")
        
        try:
            # Set data source
            self.scope.write(f':WAV:SOUR CHAN{channel}')
            
            # Set waveform format
            self.scope.write(':WAV:FORM ASCII')
            
            # Get waveform preamble for scaling
            preamble = self.scope.query(':WAV:PRE?').strip().split(',')
            y_increment = float(preamble[7])
            y_origin = float(preamble[8])
            y_reference = float(preamble[9])
            x_increment = float(preamble[4])
            x_origin = float(preamble[5])
            
            # Get waveform data
            data_str = self.scope.query(':WAV:DATA?').strip()
            
            # Parse data (remove header if present)
            if data_str.startswith('#'):
                # Binary block header - find start of data
                header_len = int(data_str[1]) + 2
                data_str = data_str[header_len:]
            
            # Convert to voltage values
            raw_data = [float(x) for x in data_str.split(',')]
            voltage_data = [(y - y_reference) * y_increment + y_origin for y in raw_data]
            
            # Generate time data
            time_data = [i * x_increment + x_origin for i in range(len(voltage_data))]
            
            print(f"Retrieved {len(voltage_data)} data points from Channel {channel}")
            return time_data, voltage_data
            
        except Exception as e:
            print(f"Error getting waveform data: {e}")
            return None
    
    def measure_parameter(self, channel: int, parameter: str) -> Optional[float]:
        """
        Measure a parameter on specified channel.
        
        Args:
            channel: Channel number (1-4)
            parameter: Parameter to measure ('FREQ', 'PER', 'AMPL', 'HIGH', 'LOW', 'PKPK', 'RMS', 'MEAN')
            
        Returns:
            Measurement value or None if error
        """
        if not self.scope:
            raise RuntimeError("Not connected to oscilloscope")
        
        try:
            # Set measurement source
            self.scope.write(f':MEAS:SOUR CHAN{channel}')
            
            # Get measurement
            result = self.scope.query(f':MEAS:{parameter}?').strip()
            value = float(result)
            
            print(f"Channel {channel} {parameter}: {value}")
            return value
            
        except Exception as e:
            print(f"Error measuring {parameter} on Channel {channel}: {e}")
            return None


def main():
    """Test the oscilloscope controller."""
    
    # Configuration
    RESOURCE_STRING = 'USB0::0x2A8D::0x900E::MY12345678::INSTR'  # Update with your scope's address
    
    print("=== Keysight DSO-S Oscilloscope Test ===")
    
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
    scope = KeysightDSOSController(RESOURCE_STRING)
    
    if not scope.connect():
        print("Failed to connect to oscilloscope")
        return
    
    try:
        # Reset to known state
        scope.reset()
        
        # Configure channels
        scope.setup_channel(1, enabled=True, scale=1.0, offset=0.0, coupling='DC')
        scope.setup_channel(2, enabled=True, scale=0.5, offset=0.0, coupling='AC')
        scope.setup_channel(3, enabled=False)
        scope.setup_channel(4, enabled=False)
        
        # Configure timebase
        scope.setup_timebase(scale=1e-3, offset=0.0)  # 1 ms/div
        
        # Configure trigger
        scope.setup_trigger(source='CHAN1', level=0.5, slope='POS', mode='EDGE')
        
        # Set acquisition mode
        scope.set_acquisition_mode(mode='NORM')
        
        # Perform single acquisition
        if scope.single_acquisition():
            # Get waveform data
            data = scope.get_waveform_data(1)
            if data:
                time_data, voltage_data = data
                print(f"Sample data points: {len(voltage_data)}")
                print(f"Time range: {min(time_data):.6f} to {max(time_data):.6f} s")
                print(f"Voltage range: {min(voltage_data):.3f} to {max(voltage_data):.3f} V")
            
            # Make measurements
            scope.measure_parameter(1, 'FREQ')
            scope.measure_parameter(1, 'AMPL')
            scope.measure_parameter(1, 'RMS')
        
        print("\nTest completed successfully!")
        
    except Exception as e:
        print(f"Test failed: {e}")
        
    finally:
        scope.disconnect()


if __name__ == '__main__':
    main()