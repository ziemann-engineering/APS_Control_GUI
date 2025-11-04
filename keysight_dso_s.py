#!/usr/bin/env python3
"""
Controller for Keysight DSO-S series oscilloscopes.

Features:
- Channel configuration (scale, offset, coupling, impedance)
- Timebase and trigger setup
- Acquisition modes (Normal, Average, Peak Detect, High Resolution)
- Waveform data capture and export (HDF5, MATLAB)
- Screenshot capture (PNG, BMP, TIFF)
- Settings save/load
- External probe configuration (attenuation, gain, units)
- Acquisition bandwidth control

Requirements:
- pyvisa
- numpy
- h5py (for HDF5 waveform export)
- scipy (for MATLAB .mat export)

Usage:
    from keysight_dso_s import KeysightDSOS
    
    scope = KeysightDSOS('USB0::0x2A8D::0x904A::MY58150189::INSTR')
    scope.connect()
    
    # Configure probe
    scope.configure_external_probe(1, attenuation=10, units='VOLT')
    
    # Set bandwidth
    scope.set_bandwidth_limit(1, '200MHZ')
    
    # Save settings
    scope.save_settings('my_settings.stp')
    
    # Capture screenshot
    scope.capture_screenshot('scope_display.png')
    
    # Save waveform data
    scope.save_waveform_data('waveforms.h5', channels=[1, 2])
    
    scope.disconnect()
"""

import pyvisa
import time
import numpy as np
import h5py  # type: ignore
from typing import Optional, Dict, Tuple, List
from scipy.io import savemat  # type: ignore

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
    
    def check_errors(self) -> list:
        """
        Check for errors in the oscilloscope error queue.
        
        Returns:
            List of error messages
        """
        if not self.scope:
            return []
        
        errors = []
        try:
            # Read all errors from the queue
            for _ in range(10):  # Max 10 errors to prevent infinite loop
                error = self.scope.query(':SYST:ERR?').strip()
                if error.startswith('+0,') or error.startswith('0,'):
                    break  # No more errors
                errors.append(error)
        except Exception as e:
            errors.append(f"Error reading error queue: {e}")
        
        return errors
    
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
            coupling: Input coupling ('DC', 'AC', 'DCLimit')
            impedance: Input impedance ('1MEG' for 1MΩ, '50' for 50Ω)
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
            self.scope.write(f':CHANnel{channel}:SCALe {scale}')

            # Set vertical offset
            self.scope.write(f':CHANnel{channel}:OFFSet {offset}')

            # Configure input coupling + impedance using the combined INPUT command
            # PDF reference: :CHANnel<N>:INPut {DC | DC50 | AC | LFR1 | LFR2}
            coup = (coupling or 'DC').upper()
            imp = (impedance or '1MEG').upper()

            # Map common user inputs to documented tokens
            if coup == 'DC':
                if imp in ('50', 'FIFT', 'DC50', 'DCFIFT'):
                    input_token = 'DC50'  # DC coupling, 50 Ohm
                else:
                    input_token = 'DC'   # DC coupling, 1 MOhm (default)
            elif coup == 'AC':
                # AC implies 1 MOhm on this series per manual
                input_token = 'AC'
            else:
                # Fallback to DC if unknown
                input_token = coup

            # Send the combined input command
            self.scope.write(f':CHANnel{channel}:INPut {input_token}')

            # If a probe-specific coupling adapter should be set, user can use PROBe:COUPling
            # (not set here by default). Check for errors after configuration.
            errors = self.check_errors()
            if errors:
                print(f"  Errors detected: {errors}")
    
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
        self.scope.write(f':TIM:POS {offset}')
    
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
            self.scope.write(f':ACQ:COUN {averages}')
    
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
            status = self.scope.query(':OPER:COND?').strip()
            # Check if bit 3 (run state) is 0
            if int(status) & 0x08 == 0:
                print("Acquisition completed")
                return True
            time.sleep(0.1)
        
        print("Acquisition timeout")
        return False
    
    def get_waveform_data(self, channel: int):
        """
        Get waveform data from specified channel (ASCII format).
        
        Args:
            channel: Channel number (1-4)
            
        Returns:
            Tuple of (time_data, voltage_data) or None if error
        
        Note:
            Uses :WAVeform:DATA? command (PDF page 1593)
            Uses :WAVeform:SOURce to specify channel
            Uses :WAVeform:PREamble? for scaling information
            Format set to ASCII (ASC) for readability
        """
        if not self.scope:
            raise RuntimeError("Not connected to oscilloscope")
        
        try:
            # Set data source (PDF page 1630)
            self.scope.write(f':WAV:SOUR CHAN{channel}')
            
            # Set waveform format to ASCII
            self.scope.write(':WAV:FORM ASC')
            
            # Get waveform preamble for scaling (PDF page 1621)
            preamble = self.scope.query(':WAV:PRE?').strip().split(',')
            # Preamble format: format,type,points,count,xinc,xorg,xref,yinc,yorg,yref
            y_increment = float(preamble[7])  # Y increment (voltage per count)
            y_origin = float(preamble[8])     # Y origin (voltage at reference)
            y_reference = float(preamble[9])  # Y reference (count at origin)
            x_increment = float(preamble[4])  # X increment (time per point)
            x_origin = float(preamble[5])     # X origin (time at first point)
            x_reference = float(preamble[6])  # X reference (point number of trigger)
            
            # Get waveform data (PDF page 1593)
            data_str = self.scope.query(':WAV:DATA?').strip()
            
            # Parse data (remove header if present)
            if data_str.startswith('#'):
                # IEEE 488.2 binary block header - find start of data
                header_len = int(data_str[1]) + 2
                data_str = data_str[header_len:]
            
            # Convert to voltage values
            raw_data = [float(x) for x in data_str.split(',')]
            voltage_data = [(y - y_reference) * y_increment + y_origin for y in raw_data]
            
            # Generate time data
            time_data = [(i - x_reference) * x_increment + x_origin for i in range(len(voltage_data))]
            
            print(f"Retrieved {len(voltage_data)} data points from Channel {channel}")
            return time_data, voltage_data
            
        except Exception as e:
            print(f"Error getting waveform data: {e}")
            return None
    
    def measure_parameter(self, channel: int, parameter: str, direction: Optional[str] = None) -> Optional[float]:
        """
        Measure a parameter on specified channel.
        
        Args:
            channel: Channel number (1-4)
            parameter: Parameter to measure (see PDF pages 894+):
                      'FREQuency', 'PERiod', 'VAMPlitude', 'VMAX', 'VMIN', 'VPP', 
                      'VRMS', 'VAVerage', 'VOVershoot', 'VPREshoot', 
                      'RISetime', 'FALLtime', 'PWIDth', 'NWIDth', 'PDUTycycle', 'NDUTycycle', etc.
            direction: Optional - 'RISing' or 'FALLing' for some measurements like frequency
            
        Returns:
            Measurement value or None if error
        
        Note:
            Uses :MEASure:<param> command (PDF page 959 for FREQuency example)
            Source can be specified in command: :MEASure:FREQuency CHANnel1
        """
        if not self.scope:
            raise RuntimeError("Not connected to oscilloscope")
        
        try:
            # Build command with source parameter
            # Format: :MEASure:<param> <source>[,<direction>]
            source = f'CHAN{channel}'
            
            if direction:
                cmd = f':MEAS:{parameter}? {source},{direction}'
            else:
                cmd = f':MEAS:{parameter}? {source}'
            
            result = self.scope.query(cmd).strip()
            
            # Handle comma-separated response (value,result_state)
            if ',' in result:
                value_str = result.split(',')[0]
            else:
                value_str = result
            
            value = float(value_str)
            
            print(f"Channel {channel} {parameter}: {value}")
            return value
            
        except Exception as e:
            print(f"Error measuring {parameter} on Channel {channel}: {e}")
            return None
    
    # ========== Settings File Management ==========
    
    def save_settings(self, filename: str) -> bool:
        """
        Save current oscilloscope settings to a file.
        
        Args:
            filename: Path to save settings file (.set extension will be added)
            
        Returns:
            True if successful
        
        Note:
            Uses :DISK:SAVE:SETup command. Default path is C:\\Users\\Public\\Documents\\Infiniium\\setups
        """
        if not self.scope:
            raise RuntimeError("Not connected to oscilloscope")
        
        try:
            # Use :DISK:SAVE:SETup command (PDF page 509)
            self.scope.write(f':DISK:SAVE:SETup "{filename}"')
            time.sleep(0.5)  # Wait for file operation
            
            errors = self.check_errors()
            if errors:
                print(f"Error saving settings: {errors}")
                return False
            
            print(f"Settings saved to: {filename}")
            return True
            
        except Exception as e:
            print(f"Error saving settings: {e}")
            return False
    
    def load_settings(self, filename: str) -> bool:
        """
        Load oscilloscope settings from a file.
        
        Args:
            filename: Path to settings file (.set, .osc, or path)
            
        Returns:
            True if successful
        
        Note:
            Uses :DISK:LOAD command. Default path is C:\\Users\\Public\\Documents\\Infiniium\\Setups
        """
        if not self.scope:
            raise RuntimeError("Not connected to oscilloscope")
        
        try:
            # Use :DISK:LOAD command (PDF page 496)
            self.scope.write(f':DISK:LOAD "{filename}"')
            time.sleep(1.0)  # Wait for settings to apply
            
            errors = self.check_errors()
            if errors:
                print(f"Error loading settings: {errors}")
                return False
            
            print(f"Settings loaded from: {filename}")
            return True
            
        except Exception as e:
            print(f"Error loading settings: {e}")
            return False
    
    # ========== Screenshot Capture ==========
    
    def capture_screenshot(self, filename: str, file_format: str = 'PNG', 
                          area: str = 'SCReen', compression: bool = False,
                          inksaver: str = 'NORMal') -> bool:
        """
        Capture a screenshot of the oscilloscope display.
        
        Args:
            filename: Path to save screenshot (extension added automatically)
            file_format: Image format - 'BMP', 'GIF', 'TIF', 'PNG', or 'JPEG' (default: 'PNG')
            area: 'SCReen' or 'GRATicule' - what to capture (default: 'SCReen')
            compression: Enable compression for BMP format (default: False)
            inksaver: 'NORMal' or 'INVert' - color scheme (default: 'NORMal')
            
        Returns:
            True if successful
        
        Note:
            Uses :DISK:SAVE:IMAGe command (PDF page 502)
            Default path is C:\\Users\\Public\\Documents\\Infiniium
        """
        if not self.scope:
            raise RuntimeError("Not connected to oscilloscope")
        
        try:
            # Set image format
            fmt = file_format.upper()
            if fmt not in ['BMP', 'GIF', 'TIF', 'PNG', 'JPEG']:
                fmt = 'PNG'
            
            # Build command according to PDF syntax (page 502)
            # :DISK:SAVE:IMAGe "<file_name>" [,<format>[,{SCReen | GRATicule}[,{ON | OFF}[,{NORMal | INVert}]]]]
            comp_str = 'ON' if compression else 'OFF'
            
            cmd = f':DISK:SAVE:IMAGe "{filename}",{fmt},{area},{comp_str},{inksaver}'
            self.scope.write(cmd)
            time.sleep(1.0)  # Wait for image capture
            
            errors = self.check_errors()
            if errors:
                print(f"Error capturing screenshot: {errors}")
                return False
            
            print(f"Screenshot saved to: {filename} (format: {fmt})")
            return True
            
        except Exception as e:
            print(f"Error capturing screenshot: {e}")
            return False
    
    # ========== Waveform Data Export ==========
    
    def save_waveform_data(self, filename: str, channels: Optional[List[int]] = None, 
                          file_format: str = 'h5') -> bool:
        """
        Save waveform data from specified channels to file.
        
        Args:
            filename: Path to save waveform data
            channels: List of channel numbers to save (default: all enabled channels)
            file_format: 'h5' for HDF5, 'mat' for MATLAB, or 'csv' to use oscilloscope's CSV export
            
        Returns:
            True if successful
        
        Note:
            For 'h5' and 'mat' formats, data is captured via :WAVeform commands and saved locally.
            For 'csv' format, uses :DISK:SAVE:WAVeform command (PDF page 510) to save directly on scope.
        """
        if not self.scope:
            raise RuntimeError("Not connected to oscilloscope")
        
        try:
            if file_format.lower() == 'csv':
                # Use oscilloscope's built-in CSV save (PDF page 510)
                # :DISK:SAVE:WAVeform <source>,"<file_name>" [,<format>[,<header>]]
                if channels is None or len(channels) == 0:
                    source = 'ALL'
                elif len(channels) == 1:
                    source = f'CHANnel{channels[0]}'
                else:
                    # Save each channel separately for CSV
                    for ch in channels:
                        ch_filename = filename.replace('.csv', f'_CH{ch}.csv')
                        self.scope.write(f':DISK:SAVE:WAVeform CHANnel{ch},"{ch_filename}",CSV,ON')
                        time.sleep(0.5)
                    print("Waveform data saved to multiple CSV files")
                    return True
                
                self.scope.write(f':DISK:SAVE:WAVeform {source},"{filename}",CSV,ON')
                time.sleep(1.0)
                print(f"Waveform data saved to: {filename} (CSV)")
                return True
            
            # For H5 and MAT formats, capture data via :WAVeform commands
            # If no channels specified, get all enabled channels
            if channels is None:
                channels = self._get_enabled_channels()
            
            if not channels:
                print("No channels enabled or specified")
                return False
            
            # Collect waveform data from all channels
            waveform_data = {}
            metadata = {}
            
            for ch in channels:
                print(f"Reading waveform from Channel {ch}...")
                data = self._get_waveform_binary(ch)
                
                if data is not None:
                    time_data, voltage_data, preamble = data
                    waveform_data[f'ch{ch}_time'] = time_data
                    waveform_data[f'ch{ch}_voltage'] = voltage_data
                    metadata[f'ch{ch}_preamble'] = preamble
            
            if not waveform_data:
                print("No waveform data captured")
                return False
            
            # Save to file based on format
            if file_format.lower() == 'h5':
                self._save_h5(filename, waveform_data, metadata)
            elif file_format.lower() == 'mat':
                self._save_mat(filename, waveform_data, metadata)
            else:
                print(f"Unsupported file format: {file_format}")
                return False
            
            print(f"Waveform data saved to: {filename}")
            return True
            
        except Exception as e:
            print(f"Error saving waveform data: {e}")
            return False
    
    def _get_enabled_channels(self) -> List[int]:
        """Get list of enabled channels."""
        enabled = []
        for ch in range(1, 5):
            try:
                status = self.scope.query(f':CHAN{ch}:DISP?').strip()
                if status == '1' or status.upper() == 'ON':
                    enabled.append(ch)
            except Exception:
                pass
        return enabled
    
    def _get_waveform_binary(self, channel: int) -> Optional[Tuple[np.ndarray, np.ndarray, Dict]]:
        """
        Get waveform data in binary format for faster transfer.
        
        Returns:
            Tuple of (time_array, voltage_array, preamble_dict) or None
        """
        try:
            # Set data source
            self.scope.write(f':WAV:SOUR CHAN{channel}')
            
            # Set waveform format to WORD (16-bit binary)
            self.scope.write(':WAV:FORM WORD')
            self.scope.write(':WAV:BYTeorder LSBFirst')
            
            # Get waveform preamble
            preamble_str = self.scope.query(':WAV:PRE?').strip()
            preamble = preamble_str.split(',')
            
            # Extract scaling parameters
            preamble_dict = {
                'format': int(preamble[0]),
                'type': int(preamble[1]),
                'points': int(preamble[2]),
                'count': int(preamble[3]),
                'x_increment': float(preamble[4]),
                'x_origin': float(preamble[5]),
                'x_reference': float(preamble[6]),
                'y_increment': float(preamble[7]),
                'y_origin': float(preamble[8]),
                'y_reference': float(preamble[9])
            }
            
            # Get waveform data
            self.scope.write(':WAV:DATA?')
            raw_data = self.scope.read_raw()
            
            # Parse IEEE 488.2 binary block header
            header_start = raw_data.find(b'#')
            if header_start == -1:
                print("No binary block header found")
                return None
            
            header_len_digits = int(chr(raw_data[header_start + 1]))
            data_len = int(raw_data[header_start + 2:header_start + 2 + header_len_digits])
            data_start = header_start + 2 + header_len_digits
            
            # Extract binary data and convert to int16
            binary_data = raw_data[data_start:data_start + data_len]
            waveform_raw = np.frombuffer(binary_data, dtype=np.int16)
            
            # Convert to voltage using preamble parameters
            voltage_data = ((waveform_raw - preamble_dict['y_reference']) * 
                           preamble_dict['y_increment'] + preamble_dict['y_origin'])
            
            # Generate time data
            num_points = len(voltage_data)
            time_data = (np.arange(num_points) - preamble_dict['x_reference']) * \
                        preamble_dict['x_increment'] + preamble_dict['x_origin']
            
            return time_data, voltage_data, preamble_dict
            
        except Exception as e:
            print(f"Error getting binary waveform data: {e}")
            return None
    
    def _save_h5(self, filename: str, waveform_data: Dict, metadata: Dict):
        """Save waveform data to HDF5 file."""
        if not filename.endswith('.h5'):
            filename += '.h5'
        
        with h5py.File(filename, 'w') as f:
            # Save waveform data
            for key, value in waveform_data.items():
                f.create_dataset(key, data=value)
            
            # Save metadata as attributes
            for ch_key, preamble in metadata.items():
                grp = f.create_group(ch_key)
                for k, v in preamble.items():
                    grp.attrs[k] = v
    
    def _save_mat(self, filename: str, waveform_data: Dict, metadata: Dict):
        """Save waveform data to MATLAB .mat file."""
        if not filename.endswith('.mat'):
            filename += '.mat'
        
        # Combine waveform data and metadata
        save_dict = waveform_data.copy()
        save_dict['metadata'] = metadata
        
        savemat(filename, save_dict)
    
    # ========== Probe Configuration ==========
    
    def set_probe_external(self, channel: int, enabled: bool = True):
        """
        Enable or disable external probe mode for a channel.
        
        Args:
            channel: Channel number (1-4)
            enabled: True to enable external probe mode
        
        Note:
            Must be enabled before setting external probe gain/units (PDF page 387)
        """
        if not self.scope:
            raise RuntimeError("Not connected to oscilloscope")
        
        try:
            state = 'ON' if enabled else 'OFF'
            self.scope.write(f':CHAN{channel}:PROBe:EXTernal {state}')
            print(f"Channel {channel} external probe mode: {state}")
        except Exception as e:
            print(f"Error setting external probe mode: {e}")
    
    def set_probe_gain(self, channel: int, gain: float, units: str = 'RATio'):
        """
        Set probe gain for external active probes.
        
        Args:
            channel: Channel number (1-4)
            gain: Gain value (0.0001 to 1000 for RATio, -80 to 60 for DECibel)
            units: 'RATio' or 'DECibel' (default: 'RATio')
        
        Note:
            Uses :CHANnel<N>:PROBe:EXTernal:GAIN command (PDF page 387)
            External probe mode must be enabled first
        """
        if not self.scope:
            raise RuntimeError("Not connected to oscilloscope")
        
        try:
            # Ensure external probe mode is enabled
            self.set_probe_external(channel, True)
            
            # Set gain with units
            self.scope.write(f':CHAN{channel}:PROBe:EXTernal:GAIN {gain},{units}')
            print(f"Channel {channel} probe gain set to {gain} {units}")
            
        except Exception as e:
            print(f"Error setting probe gain: {e}")
    
    def set_probe_units(self, channel: int, units: str):
        """
        Set the display units for a channel (for external probes).
        
        Args:
            channel: Channel number (1-4)
            units: 'VOLT', 'AMPere', 'WATT', or 'UNKNown'
        
        Note:
            Uses :CHANnel<N>:PROBe:EXTernal:UNITs command (PDF page 389)
            External probe mode must be enabled first
        """
        if not self.scope:
            raise RuntimeError("Not connected to oscilloscope")
        
        try:
            # Ensure external probe mode is enabled
            self.set_probe_external(channel, True)
            
            # Validate and set units
            valid_units = ['VOLT', 'AMPere', 'WATT', 'UNKNown']
            if units.upper() not in [u.upper() for u in valid_units]:
                print(f"Warning: '{units}' may not be valid. Valid units: {valid_units}")
            
            self.scope.write(f':CHAN{channel}:PROBe:EXTernal:UNITs {units}')
            print(f"Channel {channel} units set to {units}")
            
        except Exception as e:
            print(f"Error setting probe units: {e}")
    
    def set_probe_offset(self, channel: int, offset: float):
        """
        Set probe external offset for a channel.
        
        Args:
            channel: Channel number (1-4)
            offset: Offset value in current units
        
        Note:
            External probe mode must be enabled first
        """
        if not self.scope:
            raise RuntimeError("Not connected to oscilloscope")
        
        try:
            self.set_probe_external(channel, True)
            self.scope.write(f':CHAN{channel}:PROBe:EXTernal:OFFSet {offset}')
            print(f"Channel {channel} probe offset set to {offset}")
        except Exception as e:
            print(f"Error setting probe offset: {e}")
    
    def configure_external_probe(self, channel: int, gain: float = 1.0, 
                                units: str = 'VOLT', offset: float = 0.0):
        """
        Configure external probe connection settings.
        
        Args:
            channel: Channel number (1-4)
            gain: Probe gain (default: 1.0)
            units: Measurement units - 'VOLT', 'AMPere', 'WATT' (default: 'VOLT')
            offset: Probe offset (default: 0.0)
        
        Note:
            This enables external probe mode and configures all parameters.
            For passive probes, use gain of 1.0, 10.0, 100.0, etc.
            For current probes, set units to 'AMPere' and gain accordingly.
        """
        if not self.scope:
            raise RuntimeError("Not connected to oscilloscope")
        
        print(f"Configuring external probe on Channel {channel}:")
        print(f"  Gain: {gain}")
        print(f"  Units: {units}")
        print(f"  Offset: {offset}")
        
        # Enable external probe mode
        self.set_probe_external(channel, True)
        
        # Set parameters
        self.set_probe_units(channel, units)
        self.set_probe_gain(channel, gain, 'RATio')
        if offset != 0.0:
            self.set_probe_offset(channel, offset)
    
    # ========== Acquisition Bandwidth ==========
    
    def set_bandwidth_limit(self, channel: int, bandwidth: str = 'OFF'):
        """
        Set acquisition bandwidth limit for a channel (S-Series).
        
        Args:
            channel: Channel number (1-4)
            bandwidth: 'OFF' (full bandwidth), '20e6' (20 MHz), or '200e6' (200 MHz)
        
        Note:
            Uses :CHANnel<N>:BWLimit command (PDF page 344)
            For S-Series: Can be OFF, 20e6, or 200e6
            Bandwidth filter works with both AC and DC coupling
        """
        if not self.scope:
            raise RuntimeError("Not connected to oscilloscope")
        
        try:
            # Validate bandwidth setting for S-Series
            bw_upper = bandwidth.upper()
            
            # Convert friendly names to SCPI values
            if bw_upper in ['OFF', '0', 'FULL']:
                bw_value = 'OFF'
                desc = "Full bandwidth"
            elif bw_upper in ['20MHZ', '20E6']:
                bw_value = '20e6'
                desc = "20 MHz"
            elif bw_upper in ['200MHZ', '200E6']:
                bw_value = '200e6'
                desc = "200 MHz"
            else:
                # Try to use as-is
                bw_value = bandwidth
                desc = bandwidth
            
            self.scope.write(f':CHAN{channel}:BWLimit {bw_value}')
            print(f"Channel {channel} bandwidth limit: {desc}")
            
            errors = self.check_errors()
            if errors:
                print(f"Bandwidth setting errors: {errors}")
                
        except Exception as e:
            print(f"Error setting bandwidth limit: {e}")
    
    def get_bandwidth_limit(self, channel: int) -> Optional[str]:
        """
        Query the current bandwidth limit setting.
        
        Args:
            channel: Channel number (1-4)
            
        Returns:
            Bandwidth setting: '0' (off), '20e6', or '200e6', or None if error
        """
        if not self.scope:
            raise RuntimeError("Not connected to oscilloscope")
        
        try:
            result = self.scope.query(f':CHAN{channel}:BWLimit?').strip()
            return result
        except Exception as e:
            print(f"Error querying bandwidth limit: {e}")
            return None
    
    # ========== Acquisition Settings ==========
    
    def set_acquisition_points(self, points: str = 'AUTO') -> bool:
        """
        Set the analog memory depth (number of points to acquire).
        
        Args:
            points: 'AUTO' or integer value for memory depth
            
        Returns:
            True if successful
        
        Note:
            Uses :ACQuire:POINts[:ANALog] command (PDF page 253)
            Query actual points with get_acquisition_points() after acquisition
            If points=AUTO, oscilloscope selects optimum depth
        """
        if not self.scope:
            raise RuntimeError("Not connected to oscilloscope")
        
        try:
            self.scope.write(f':ACQ:POIN:ANAL {points}')
            print(f"Acquisition points set to: {points}")
            
            errors = self.check_errors()
            if errors:
                print(f"Points setting errors: {errors}")
                return False
            return True
            
        except Exception as e:
            print(f"Error setting acquisition points: {e}")
            return False
    
    def get_acquisition_points(self) -> Optional[int]:
        """
        Query the current analog memory depth setting.
        
        Returns:
            Current memory depth value or None if error
        
        Note:
            Query :ACQuire:POINts[:ANALog]? (PDF page 253)
        """
        if not self.scope:
            raise RuntimeError("Not connected to oscilloscope")
        
        try:
            result = self.scope.query(':ACQ:POIN:ANAL?').strip()
            return int(float(result))
        except Exception as e:
            print(f"Error querying acquisition points: {e}")
            return None
    
    def set_sample_rate(self, rate: str = 'AUTO') -> bool:
        """
        Set the analog acquisition sample rate.
        
        Args:
            rate: 'AUTO', 'MAX', or numeric value in Hz (e.g., '250e6' for 250 MSa/s)
            
        Returns:
            True if successful
        
        Note:
            Uses :ACQuire:SRATe[:ANALog] command (PDF page 266)
            AUTO: Oscilloscope selects rate based on memory depth and timebase
            MAX: Maximum available sample rate
            Numeric: Rounded to next fastest available rate
        """
        if not self.scope:
            raise RuntimeError("Not connected to oscilloscope")
        
        try:
            self.scope.write(f':ACQ:SRAT:ANAL {rate}')
            
            if rate == 'AUTO':
                print("Sample rate set to AUTO")
            elif rate == 'MAX':
                print("Sample rate set to MAX")
            else:
                try:
                    rate_val = float(rate)
                    print(f"Sample rate set to: {rate_val/1e6:.1f} MSa/s")
                except (ValueError, TypeError):
                    print(f"Sample rate set to: {rate}")
            
            errors = self.check_errors()
            if errors:
                print(f"Sample rate setting errors: {errors}")
                return False
            return True
            
        except Exception as e:
            print(f"Error setting sample rate: {e}")
            return False
    
    def get_sample_rate(self) -> Optional[float]:
        """
        Query the current analog acquisition sample rate.
        
        Returns:
            Sample rate in Hz or None if error
        
        Note:
            Query :ACQuire:SRATe[:ANALog]? (PDF page 266)
        """
        if not self.scope:
            raise RuntimeError("Not connected to oscilloscope")
        
        try:
            result = self.scope.query(':ACQ:SRAT:ANAL?').strip()
            return float(result)
        except Exception as e:
            print(f"Error querying sample rate: {e}")
            return None
    
    # ========== Timebase Settings ==========
    
    def set_timebase_reference(self, reference: str = 'CENTer') -> bool:
        """
        Set the horizontal reference position (trigger point position).
        
        Args:
            reference: 'LEFT', 'CENTer', or 'RIGHt'
            
        Returns:
            True if successful
        
        Note:
            Uses :TIMebase:REFerence command (PDF page 1373)
            Sets where trigger point appears on screen
        """
        if not self.scope:
            raise RuntimeError("Not connected to oscilloscope")
        
        try:
            ref_upper = reference.upper()
            if ref_upper not in ['LEFT', 'CENTER', 'RIGHT']:
                print(f"Warning: '{reference}' may not be valid. Use LEFT, CENTer, or RIGHt")
            
            self.scope.write(f':TIM:REF {reference}')
            print(f"Timebase reference set to: {reference}")
            
            errors = self.check_errors()
            if errors:
                print(f"Timebase reference errors: {errors}")
                return False
            return True
            
        except Exception as e:
            print(f"Error setting timebase reference: {e}")
            return False
    
    def get_timebase_reference(self) -> Optional[str]:
        """
        Query the current horizontal reference position.
        
        Returns:
            'LEFT', 'CENTer', 'RIGHt', 'PERCent', or None if error
        
        Note:
            Query :TIMebase:REFerence? (PDF page 1373)
            Returns 'PERCent' if set to percent-of-screen location
        """
        if not self.scope:
            raise RuntimeError("Not connected to oscilloscope")
        
        try:
            result = self.scope.query(':TIM:REF?').strip()
            return result
        except Exception as e:
            print(f"Error querying timebase reference: {e}")
            return None


# Alias for simpler usage
KeysightDSOS = KeysightDSOSController


def main():
    """Test the oscilloscope controller."""
    
    # Configuration
    RESOURCE_STRING = 'USB0::0x2A8D::0x904A::MY58150189::INSTR'  # Update with your scope's address
    
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
        
        # Configure external probes
        scope.configure_external_probe(2, attenuation=0.1, units='AMPere', offset=0.0)
        
        # Set bandwidth limits
        scope.set_bandwidth_limit(1, 'FULL')
        #scope.set_bandwidth_limit(2, '200MHZ')
        
        # Configure channels
        scope.setup_channel(1, enabled=True, scale=100.0, offset=200, coupling='DC')
        scope.setup_channel(2, enabled=True, scale=0.5, offset=1, coupling='DC')
        scope.setup_channel(3, enabled=False)
        scope.setup_channel(4, enabled=False)
        
        # Configure timebase
        scope.setup_timebase(scale=1e-3, offset=0.0)  # 1 ms/div
        
        # Configure trigger
        scope.setup_trigger(source='EXT', level=1, slope='POS', mode='EDGE')
        
        # Set acquisition mode
        scope.set_acquisition_mode(mode='NORM')
        
        # Save settings to file
        print("\n=== Saving Settings ===")
        scope.save_settings('test_settings.stp')
        
        # Perform single acquisition
        if scope.single_acquisition():
            # Capture screenshot
            print("\n=== Capturing Screenshot ===")
            scope.capture_screenshot('screenshot.png', file_format='PNG')
            
            # Save waveform data
            print("\n=== Saving Waveform Data ===")
            scope.save_waveform_data('waveform_data.h5', channels=[1, 2], file_format='h5')
            scope.save_waveform_data('waveform_data.mat', channels=[1, 2], file_format='mat')
            
            # Get waveform data for display
            data = scope.get_waveform_data(1)
            if data:
                time_data, voltage_data = data
                print(f"Sample data points: {len(voltage_data)}")
                print(f"Time range: {min(time_data):.6f} to {max(time_data):.6f} s")
                print(f"Voltage range: {min(voltage_data):.3f} to {max(voltage_data):.3f} V")
            
            # Make measurements
            print("\n=== Measurements ===")
            scope.measure_parameter(1, 'FREQ')
            scope.measure_parameter(1, 'VAMP')
            scope.measure_parameter(1, 'VRMS')
        
        # Test loading settings
        print("\n=== Loading Settings ===")
        scope.load_settings('test_settings.stp')
        
        print("\nTest completed successfully!")
        
    except Exception as e:
        print(f"Test failed: {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        scope.disconnect()


if __name__ == '__main__':
    main()