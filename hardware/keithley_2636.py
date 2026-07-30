"""
Keithley SMU Driver for Vth Measurement

Supports the Keithley 2636B (dual-channel, TSP-based) and the
Keithley 2450 / 2400-series (single-channel, SCPI-based).

The model is detected automatically from the *IDN? response.

Thread safety
-------------
This driver is NOT internally thread-safe.  Callers that share one
instance across threads must hold an external threading.Lock() around
calls to measure_vth() (and any other state-changing method).  The GSS
procedure does this via its smu_lock.

Usage
-----
    smu = KeithleySMU('GPIB::26')
    smu.connect()
    vth = smu.measure_vth(channel='a', force_current_a=250e-6,
                          compliance_voltage_v=10.0)
    smu.disconnect()
"""

import logging
import time
from typing import Optional

import pyvisa

log = logging.getLogger(__name__)


class SMUError(Exception):
    """Exception for SMU errors."""


class KeithleySMU:
    """Unified interface for Keithley 2636B and 2450/2400-series SMUs.

    Model detection
    ---------------
    Detected from the *IDN? response at connect() time:

    * '2636' in IDN  →  2636B  (TSP-scripting, dual channel a/b)
    * '2604' in IDN  →  2604B  (TSP-scripting, dual channel a/b)
    * '2450' in IDN  →  2450   (SCPI, single channel)
    * '2400' / '2410' in IDN  →  2400-series  (SCPI, single channel)

    Channels
    --------
    * 2636B / 2604B: channel 'a' or 'b'  (case-insensitive)
    * 2450 / 2400-series: channel ignored (always the one channel)
    """

    # Instrument family constants
    _FAMILY_TSP = 'tsp'    # 2600-series (2636B, 2604B, …)
    _FAMILY_2450 = '2450'  # 2450
    _FAMILY_2400 = '2400'  # 2400/2410

    def __init__(self, resource: str):
        """
        Parameters
        ----------
        resource:
            VISA resource string, e.g. 'GPIB::26', 'USB0::0x05E6::…::INSTR'.
        """
        self.resource = resource
        self._rm: Optional[pyvisa.ResourceManager] = None
        self._instr = None
        self._family: Optional[str] = None
        self.idn: str = ''

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Open the VISA connection and identify the instrument.

        Returns True on success, False on failure.
        """
        try:
            self._rm = pyvisa.ResourceManager()
            self._instr = self._rm.open_resource(self.resource)
            self._instr.timeout = 10_000  # 10 s

            self.idn = self._instr.query('*IDN?').strip()
            log.info(f'SMU IDN: {self.idn}')

            self._family = self._detect_family(self.idn)
            if self._family is None:
                log.error(f'Unrecognised SMU model: {self.idn}')
                self.disconnect()
                return False

            log.info(f'SMU connected: {self.resource} (family={self._family})')
            return True

        except Exception as exc:
            log.error(f'SMU connect failed: {exc}')
            self.disconnect()
            return False

    def disconnect(self):
        """Close the VISA connection."""
        try:
            if self._instr is not None:
                self._instr.close()
        except Exception:
            pass
        try:
            if self._rm is not None:
                self._rm.close()
        except Exception:
            pass
        self._instr = None
        self._rm = None
        self._family = None

    # ------------------------------------------------------------------
    # Vth measurement
    # ------------------------------------------------------------------

    def measure_vth(
        self,
        channel: str = 'a',
        force_current_a: float = 250e-6,
        compliance_voltage_v: float = 10.0,
    ) -> Optional[float]:
        """Force a small current and measure the resulting voltage (Vth).

        The DUT must be connected in diode configuration
        (gate tied to drain, source grounded) before calling this method.

        Parameters
        ----------
        channel:
            SMU channel to use.  'a' or 'b' for 2636B/2604B; ignored for
            2450 / 2400-series (always the single channel).
        force_current_a:
            Source current in Amperes (e.g. 250e-6 for 250 µA).
        compliance_voltage_v:
            Voltage compliance limit in Volts.

        Returns
        -------
        Measured voltage in Volts, or None on error.
        """
        if self._instr is None:
            log.error('SMU not connected')
            return None

        try:
            if self._family == self._FAMILY_TSP:
                return self._measure_vth_tsp(channel.lower(), force_current_a, compliance_voltage_v)
            elif self._family in (self._FAMILY_2450, self._FAMILY_2400):
                return self._measure_vth_scpi(force_current_a, compliance_voltage_v)
            else:
                log.error(f'Unknown SMU family: {self._family}')
                return None
        except Exception as exc:
            log.error(f'SMU measure_vth error: {exc}')
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @classmethod
    def _detect_family(cls, idn: str) -> Optional[str]:
        """Return the instrument family string from its IDN response."""
        idn_upper = idn.upper()
        if '2636' in idn_upper or '2604' in idn_upper or '2602' in idn_upper:
            return cls._FAMILY_TSP
        if '2450' in idn_upper:
            return cls._FAMILY_2450
        if '2400' in idn_upper or '2410' in idn_upper or '2420' in idn_upper:
            return cls._FAMILY_2400
        return None

    def _write(self, cmd: str):
        self._instr.write(cmd)

    def _query(self, cmd: str) -> str:
        return self._instr.query(cmd).strip()

    # ------ TSP (2636B / 2604B) ------

    def _measure_vth_tsp(
        self,
        channel: str,
        force_current_a: float,
        compliance_voltage_v: float,
    ) -> Optional[float]:
        """Vth measurement via Lua/TSP scripting (2636B, 2604B)."""
        smu = f'smu{channel}'  # e.g. 'smua' or 'smub'

        self._write(f'{smu}.reset()')
        self._write(f'{smu}.source.func = {smu}.OUTPUT_DCAMPS')
        self._write(f'{smu}.source.leveli = {force_current_a:.6e}')
        self._write(f'{smu}.source.limitv = {compliance_voltage_v:.4f}')
        self._write(f'{smu}.measure.autorangev = {smu}.AUTORANGE_ON')
        self._write(f'{smu}.source.output = {smu}.OUTPUT_ON')

        # Allow the source to settle
        time.sleep(0.1)

        raw = self._query(f'print({smu}.measure.v())')
        self._write(f'{smu}.source.output = {smu}.OUTPUT_OFF')
        self._write(f'{smu}.reset()')

        try:
            return float(raw)
        except ValueError:
            log.error(f'SMU TSP: unexpected voltage response: {raw!r}')
            return None

    # ------ SCPI (2450) ------

    def _measure_vth_scpi_2450(
        self,
        force_current_a: float,
        compliance_voltage_v: float,
    ) -> Optional[float]:
        """Vth measurement via SCPI for the Keithley 2450."""
        self._write('*RST')
        self._write(':SOUR:FUNC CURR')
        self._write(f':SOUR:CURR:LEV {force_current_a:.6e}')
        self._write(f':SOUR:CURR:VLIM {compliance_voltage_v:.4f}')
        self._write(':SENS:FUNC "VOLT"')
        self._write(':SENS:VOLT:RANG:AUTO ON')
        self._write(':OUTP ON')

        time.sleep(0.1)

        raw = self._query(':READ?')
        self._write(':OUTP OFF')

        # 2450 :READ? returns a single value
        try:
            return float(raw.split(',')[0])
        except (ValueError, IndexError):
            log.error(f'SMU 2450: unexpected voltage response: {raw!r}')
            return None

    # ------ SCPI (2400 / 2410) ------

    def _measure_vth_scpi_2400(
        self,
        force_current_a: float,
        compliance_voltage_v: float,
    ) -> Optional[float]:
        """Vth measurement via SCPI for the Keithley 2400 / 2410."""
        self._write('*RST')
        self._write(':SOUR:FUNC CURR')
        self._write(':SOUR:CURR:MODE FIX')
        self._write(':SOUR:CURR:RANG:AUTO 1')
        self._write(f':SOUR:CURR:LEV {force_current_a:.6e}')
        self._write(':SENS:FUNC "VOLT"')
        self._write(':SENS:VOLT:RANG:AUTO ON')
        self._write(f':SENS:VOLT:PROT {compliance_voltage_v:.4f}')
        self._write(':FORM:ELEM VOLT')
        self._write(':OUTP ON')

        time.sleep(0.1)

        raw = self._query(':READ?')
        self._write(':OUTP OFF')

        try:
            return float(raw.split(',')[0])
        except (ValueError, IndexError):
            log.error(f'SMU 2400: unexpected voltage response: {raw!r}')
            return None

    def _measure_vth_scpi(
        self,
        force_current_a: float,
        compliance_voltage_v: float,
    ) -> Optional[float]:
        """Dispatch to the correct SCPI variant."""
        if self._family == self._FAMILY_2450:
            return self._measure_vth_scpi_2450(force_current_a, compliance_voltage_v)
        return self._measure_vth_scpi_2400(force_current_a, compliance_voltage_v)

    # ------------------------------------------------------------------
    # Vth by voltage ramp (sweep method)
    # ------------------------------------------------------------------

    def apply_precondition_voltage(
        self,
        channel: str = 'a',
        precond_voltage_v: float = 0.0,
        duration_s: float = 0.1,
    ):
        """Apply a precondition voltage to the DUT before a Vth measurement.

        The output is turned off after *duration_s* seconds.

        Parameters
        ----------
        channel:
            SMU channel ('a' or 'b' for 2636B/2604B; ignored for single-ch).
        precond_voltage_v:
            Voltage to apply.  0.0 = skip (returns immediately).
        duration_s:
            How long to hold the voltage.
        """
        if precond_voltage_v == 0.0 or self._instr is None:
            return
        try:
            if self._family == self._FAMILY_TSP:
                smu = f'smu{channel.lower()}'
                self._write(f'{smu}.reset()')
                self._write(f'{smu}.source.func = {smu}.OUTPUT_DCVOLTS')
                self._write(f'{smu}.source.levelv = {precond_voltage_v:.4f}')
                self._write(f'{smu}.source.limiti = 0.1')
                self._write(f'{smu}.source.output = {smu}.OUTPUT_ON')
                time.sleep(duration_s)
                self._write(f'{smu}.source.output = {smu}.OUTPUT_OFF')
                self._write(f'{smu}.reset()')
            else:
                self._write('*RST')
                self._write(':SOUR:FUNC VOLT')
                self._write(f':SOUR:VOLT:LEV {precond_voltage_v:.4f}')
                self._write(':SOUR:VOLT:ILIM 0.1')
                self._write(':OUTP ON')
                time.sleep(duration_s)
                self._write(':OUTP OFF')
        except Exception as exc:
            log.warning(f'SMU precondition voltage error: {exc}')

    def measure_vth_ramp(
        self,
        channel: str = 'a',
        precondition_voltage_v: float = 15.0,
        precondition_duration_s: float = 0.1,
        break_time_s: float = 0.01,
        start_voltage_v: float = 0.0,
        stop_voltage_v: float = 10.0,
        step_voltage_v: float = 0.1,
        threshold_current_a: float = 1e-6,
    ) -> Optional[float]:
        """Find Vth by sweeping voltage and detecting when current crosses threshold.

        The voltage is stepped from *start_voltage_v* to *stop_voltage_v* in
        increments of *step_voltage_v*.  The first voltage at which
        |I| >= *threshold_current_a* is returned as Vth.  Returns
        *stop_voltage_v* if the threshold is never reached, or *None* on error.

        2636B / 2604B (TSP, dual-channel)
        ---------------------------------
        Both SMU channels are used simultaneously following the JEP183A
        gate-connected (diode) configuration:

        * *channel* selects the **gate** channel ('a' or 'b').  The remaining
          channel is automatically assigned as the **drain**.
        * Gate voltage = drain voltage at every step (gate tied to drain).
        * Source is assumed to be grounded externally.

        2450 / 2400-series (SCPI, single-channel)
        ------------------------------------------
        Only one SMU channel exists.  *channel* is ignored.  The single
        output acts as the combined gate/drain drive; the DUT must be wired
        in diode configuration (gate shorted to drain, source grounded) before
        calling this method.

        Parameters
        ----------
        channel:
            For 2636B/2604B: selects which SMU channel drives the **gate**
            ('a' or 'b'); the other channel drives the drain automatically.
            For 2450/2400-series: ignored.
        precondition_voltage_v:
            Voltage applied to the gate before the sweep to pre-stress the
            oxide (V).  Set to 0.0 to skip the precondition step.
        precondition_duration_s:
            How long the precondition voltage is held (s).
        break_time_s:
            Dead time between end of precondition and start of sweep (s).
            Only used by the TSP path; the SCPI path relies on
            apply_precondition_voltage() which turns off the output first.
        start_voltage_v:
            First voltage in the sweep (V).
        stop_voltage_v:
            Last voltage in the sweep / compliance limit (V).  May be less
            than *start_voltage_v* for a descending sweep.
        step_voltage_v:
            Magnitude of each voltage step (V).  Sign is adjusted
            automatically to match the sweep direction.
        threshold_current_a:
            Drain (or output) current magnitude at which the device is
            considered ON and Vth is recorded (A).
        """
        if self._instr is None:
            log.error('SMU not connected')
            return None
        if step_voltage_v == 0:
            step_voltage_v = 0.1 if stop_voltage_v >= start_voltage_v else -0.1
        if stop_voltage_v > start_voltage_v and step_voltage_v < 0:
            step_voltage_v = abs(step_voltage_v)
        if stop_voltage_v < start_voltage_v and step_voltage_v > 0:
            step_voltage_v = -step_voltage_v
        try:
            if self._family == self._FAMILY_TSP:
                return self._ramp_vth_tsp(
                    channel.lower(), precondition_voltage_v,
                    precondition_duration_s, break_time_s,
                    start_voltage_v, stop_voltage_v, step_voltage_v,
                    threshold_current_a,
                )
            else:
                self.apply_precondition_voltage(
                    precond_voltage_v=precondition_voltage_v,
                    duration_s=precondition_duration_s,
                )
                return self._ramp_vth_scpi(
                    start_voltage_v, stop_voltage_v,
                    step_voltage_v, threshold_current_a,
                )
        except Exception as exc:
            log.error(f'SMU measure_vth_ramp error: {exc}')
            return None

    def _is_pyvisa_py_backend(self) -> bool:
        """Return whether this resource manager is using the pyvisa-py backend."""
        if self._rm is None:
            return False
        visalib = self._rm.visalib
        return (
            visalib.__class__.__module__.startswith('pyvisa_py.')
            or 'pyvisa-py' in str(getattr(visalib, 'library_path', '')).lower()
        )

    def _ramp_vth_tsp(
        self,
        channel: str,
        precondition_v: float,
        precondition_s: float,
        break_s: float,
        start_v: float, stop_v: float, step_v: float, threshold_i: float,
    ) -> Optional[float]:
        """Run the TSP ramp using the best implementation for the VISA backend."""
        if self._is_pyvisa_py_backend():
            log.info('SMU TSP ramp: using pyvisa-py line-by-line fallback')
            return self._ramp_vth_tsp_direct(
                channel, precondition_v, precondition_s, break_s,
                start_v, stop_v, step_v, threshold_i,
            )

        log.info('SMU TSP ramp: using NI-VISA on-instrument script')
        return self._ramp_vth_tsp_script(
            channel, precondition_v, precondition_s, break_s,
            start_v, stop_v, step_v, threshold_i,
        )

    def _ramp_vth_tsp_script(
        self,
        channel: str,
        precondition_v: float,
        precondition_s: float,
        break_s: float,
        start_v: float, stop_v: float, step_v: float, threshold_i: float,
    ) -> Optional[float]:
        """Fast on-instrument TSP implementation used with NI-VISA."""
        gate_channel = channel if channel in ('a', 'b') else 'a'
        drain_channel = 'b' if gate_channel == 'a' else 'a'
        gate = f'smu{gate_channel}'
        drain = f'smu{drain_channel}'
        source_limit_i = max(abs(threshold_i) * 3.0, 10e-3)
        script = (
            f'{gate}.reset() {drain}.reset() '
            f'{gate}.source.func = {gate}.OUTPUT_DCVOLTS '
            f'{drain}.source.func = {drain}.OUTPUT_DCVOLTS '
            f'{gate}.source.limiti = {source_limit_i:.6e} '
            f'{drain}.source.limiti = {source_limit_i:.6e} '
            f'{drain}.measure.autorangei = {drain}.AUTORANGE_ON '
            f'{gate}.source.levelv = {precondition_v:.4f} '
            f'{drain}.source.levelv = 0 '
            f'{gate}.source.output = {gate}.OUTPUT_ON '
            f'{drain}.source.output = {drain}.OUTPUT_ON '
            f'delay({precondition_s:.6f}) '
            f'{gate}.source.levelv = 0 '
            f'delay({break_s:.6f}) '
            f'local vth = {stop_v:.4f} '
            f'local v = {start_v:.4f} '
            f'local step = {step_v:.4f} '
            f'while ((step > 0 and v <= {stop_v:.4f}) or (step < 0 and v >= {stop_v:.4f})) do '
            f'  {gate}.source.levelv = v '
            f'  {drain}.source.levelv = v '
            f'  delay(0.002) '
            f'  local i = {drain}.measure.i() '
            f'  if ((step > 0 and math.abs(i) >= {threshold_i:.6e}) or '
            f'      (step < 0 and math.abs(i) <= {threshold_i:.6e})) then '
            f'    vth = v '
            f'    break '
            f'  end '
            f'  v = v + step '
            f'end '
            f'{gate}.source.output = {gate}.OUTPUT_OFF '
            f'{drain}.source.output = {drain}.OUTPUT_OFF '
            f'{gate}.reset() {drain}.reset() '
            f'print(vth)'
        )
        raw = self._query(f'do {script} end')
        try:
            return float(raw)
        except ValueError:
            log.error(f'SMU TSP ramp: unexpected response: {raw!r}')
            return None

    def _ramp_vth_tsp_direct(
        self,
        channel: str,
        precondition_v: float,
        precondition_s: float,
        break_s: float,
        start_v: float, stop_v: float, step_v: float, threshold_i: float,
    ) -> Optional[float]:
        """JEP183A-style Vth ramp via Lua/TSP using both SMU channels.

        *channel* is the gate channel; the complementary channel ('a'↔'b')
        is used as the drain.  Gate and drain voltages are stepped together.
        """
        gate_channel = channel if channel in ('a', 'b') else 'a'
        drain_channel = 'b' if gate_channel == 'a' else 'a'
        gate = f'smu{gate_channel}'
        drain = f'smu{drain_channel}'
        source_limit_i = max(abs(threshold_i) * 3, 0.01)
        log.info(f'Source limit: {source_limit_i} A')
        try:
            # Do not upload a multiline TSP script here.  Some VISA backends
            # used on Raspberry Pi do not preserve the script-upload command
            # boundaries, which leaves the SMU idle and makes the final query
            # time out.  Each command below is a complete one-line TSP command.
            self._write(f'{gate}.reset()')
            self._write(f'{drain}.reset()')
            self._write(f'{gate}.source.func = {gate}.OUTPUT_DCVOLTS')
            self._write(f'{drain}.source.func = {drain}.OUTPUT_DCVOLTS')
            self._write(f'{gate}.source.limiti = {source_limit_i:.6e}')
            self._write(f'{drain}.source.limiti = {source_limit_i:.6e}')
            self._write(f'{drain}.measure.autorangei = {drain}.AUTORANGE_ON')
            self._write(f'{gate}.source.levelv = {precondition_v:.4f}')
            self._write(f'{drain}.source.levelv = 0')
            self._write(f'{gate}.source.output = {gate}.OUTPUT_ON')
            self._write(f'{drain}.source.output = {drain}.OUTPUT_ON')
            time.sleep(precondition_s)
            self._write(f'{gate}.source.levelv = 0')
            time.sleep(break_s)

            vth = stop_v
            v = start_v
            while ((step_v > 0 and v <= stop_v)
                   or (step_v < 0 and v >= stop_v)):
                self._write(f'{gate}.source.levelv = {v:.4f}')
                self._write(f'{drain}.source.levelv = {v:.4f}')
                time.sleep(0.002)
                raw = self._query(f'print({drain}.measure.i())')
                try:
                    current = float(raw)
                    log.debug(f'Voltage: {v} V, Current: {current} A')
                except ValueError:
                    log.error(f'SMU TSP ramp: unexpected current response: {raw!r}')
                    return None

                crossed = (
                    (step_v > 0 and abs(current) >= threshold_i)
                    or (step_v < 0 and abs(current) <= threshold_i)
                )
                if crossed:
                    vth = v
                    break
                v += step_v

            return vth
        finally:
            try:
                self._write(f'{gate}.source.output = {gate}.OUTPUT_OFF')
                self._write(f'{drain}.source.output = {drain}.OUTPUT_OFF')
                self._write(f'{gate}.reset()')
                self._write(f'{drain}.reset()')
            except Exception as exc:
                log.warning(f'SMU TSP ramp cleanup error: {exc}')

    def _ramp_vth_scpi(
        self,
        start_v: float, stop_v: float, step_v: float, threshold_i: float,
    ) -> Optional[float]:
        """Voltage-ramp Vth via SCPI (2450 / 2400-series)."""
        self._write('*RST')
        self._write(':SOUR:FUNC VOLT')
        self._write(':SOUR:VOLT:RANG:AUTO ON')
        self._write(f':SOUR:VOLT:ILIM {abs(threshold_i) * 100:.6e}')
        self._write(':SENS:FUNC "CURR"')
        self._write(':SENS:CURR:RANG:AUTO ON')
        self._write(f':SOUR:VOLT:LEV {start_v:.4f}')
        self._write(':OUTP ON')

        v = start_v
        vth = stop_v
        while ((step_v > 0 and v <= stop_v)
               or (step_v < 0 and v >= stop_v)):
            self._write(f':SOUR:VOLT:LEV {v:.4f}')
            time.sleep(0.005)
            raw = self._query(':READ?')
            try:
                i_meas = float(raw.split(',')[0])
            except (ValueError, IndexError):
                break
            crossed = (
                (step_v > 0 and abs(i_meas) >= threshold_i)
                or (step_v < 0 and abs(i_meas) <= threshold_i)
            )
            if crossed:
                vth = v
                break
            v += step_v

        self._write(':OUTP OFF')
        return vth
