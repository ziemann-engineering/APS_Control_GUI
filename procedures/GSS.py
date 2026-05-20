"""
Gate Switching Stress (GSS) Procedure

Runs long-duration (weeks) gate switching stress tests on N GSS controllers
simultaneously.  Each controller is managed by a dedicated background worker
thread.  All workers share one SMU (protected by a threading.Lock) for
periodic Vth measurement.

Configuration
-------------
The number of controllers and their individual settings are encoded as a JSON
array in the *controller_config_json* parameter.  Each element is a dict with
the following keys:

    id          str   Human-readable controller identifier (e.g. "Ctrl1")
    port        str   Serial port or VISA ASRL string (e.g. "COM5")
    freq_hz     float Switching frequency in Hz (1 000 – 10 000 000)
    duty_cycle  float Duty cycle (0.0 – 1.0)
    v_gate_on   float Positive gate voltage in V (0 – +32 V)
    v_gate_off  float Negative gate voltage in V (0 – −32 V, store as negative float)
    num_duts    int   Number of DUTs on this controller (1 – 8)

    # Optional – PSU (NGE103B or HMC8043)
    psu_resource  str  VISA resource string; empty string if no PSU control
    psu_ch_pos    int  PSU channel for positive gate voltage (default 1)
    psu_ch_neg    int  PSU channel for negative gate voltage (default 2)

    # Optional – Temperature controller (TCU)
    tcu_port      str   Serial port; empty string if no TCU
    tcu_channel   int   TCU channel index (1-based, default 1)
    temperature_c float Target temperature in °C (default 25.0)

    # Optional – SMU channel for Vth measurement
    smu_channel   str   SMU channel: 'a' or 'b' (2636B), ignored for 2450
                        (default 'a')

Example (JSON):
    [
      {
        "id": "Ctrl1", "port": "COM5",
        "freq_hz": 100000, "duty_cycle": 0.5,
        "v_gate_on": 15.0, "v_gate_off": -5.0, "num_duts": 4,
        "psu_resource": "ASRL8::INSTR", "psu_ch_pos": 1, "psu_ch_neg": 2,
        "tcu_port": "COM7", "tcu_channel": 1, "temperature_c": 150.0,
        "smu_channel": "a"
      },
      {
        "id": "Ctrl2", "port": "COM6",
        "freq_hz": 200000, "duty_cycle": 0.4,
        "v_gate_on": 18.0, "v_gate_off": -3.0, "num_duts": 2,
        "psu_resource": "ASRL8::INSTR", "psu_ch_pos": 1, "psu_ch_neg": 2,
        "tcu_port": "COM7", "tcu_channel": 2, "temperature_c": 125.0,
        "smu_channel": "b"
      }
    ]

In the above example both controllers share one PSU and one TCU (different
channels).  The software opens only one VISA/serial connection per unique
resource string.

Data saved
----------
* One CSV row per DUT per log interval, emitted via pymeasure's results
  mechanism (visible in the standard results table).
* One CSV file per controller, written directly to *data_directory*.
  Named  ``GSS_<id>_<YYYY-MM-DD_HH-MM-SS>.csv``.

Aborting
--------
Click "Abort" in the GUI.  All workers stop within *worker_shutdown_timeout_s*
seconds, all PSU / TCU outputs are disabled, and all connections are closed.
"""

import csv
import json
import logging
import math
import os
import queue
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from pymeasure.experiment import (
    BooleanParameter,
    FloatParameter,
    IntegerParameter,
    ListParameter,
    Parameter,
    Procedure,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Device registry — populated by update_device_choices() before the main
# window is created so ListParameter dropdowns show discovered serials.
# ---------------------------------------------------------------------------

_DEVICE_REGISTRY: Dict[str, dict] = {}  # serial_number → device info dict


def update_device_choices(discovered_devices: list) -> None:
    """Populate GateStressTest ListParameter choices from discovered devices.

    Call this from APS GUI.py *before* ManagedDockWindow.__init__ so that the
    INPUTS dropdowns already show the discovered serials when the user first
    opens the 'New Experiment' dialog.
    """
    global _DEVICE_REGISTRY
    _DEVICE_REGISTRY = {
        d['serial']: d for d in discovered_devices if d.get('serial')
    }

    def _serials(dtype: str):
        return [''] + [d['serial'] for d in discovered_devices if d.get('type') == dtype]

    gss_sn = _serials('gss')
    tcu_sn = _serials('tcu')
    psu_sn = [''] + [
        d['serial'] for d in discovered_devices
        if d.get('type') in ('nge103', 'hmc8043')
    ]
    smu_sn = _serials('keithley')

    if gss_sn[1:]:
        GateStressTest.gss_serial.choices = gss_sn
        GateStressTest.gss_serial.default = gss_sn[1]
    if tcu_sn[1:]:
        GateStressTest.tcu_serial.choices = tcu_sn
        GateStressTest.tcu_serial.default = tcu_sn[1]
    if psu_sn[1:]:
        GateStressTest.psu_serial.choices = psu_sn
        GateStressTest.psu_serial.default = psu_sn[1]
    if smu_sn[1:]:
        GateStressTest.smu_serial.choices = smu_sn
        GateStressTest.smu_serial.default = smu_sn[1]


# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------

@dataclass
class ControllerConfig:
    """Validated configuration for a single GSS controller."""
    # Identity
    id: str
    port: str                        # COM port for GSS controller
    gss_serial: str = ''             # Serial number from ID command

    # DUT count
    num_duts: int = 1

    # Switching parameters
    freq_hz: float = 100_000.0
    duty_cycle: float = 0.5

    # Vth measurement settings
    vth_method: str = 'force_current'   # 'force_current' | 'ramp_voltage'
    vth_force_current_ua: float = 250.0
    vth_precond_voltage: float = 0.0
    vth_threshold_current: float = 1e-6
    vth_compliance_voltage: float = 10.0

    # Optional PSU
    psu_resource: str = ''
    psu_serial: str = ''
    psu_ch_pos: int = 1              # channel for V_on  (positive rail)
    psu_ch_neg: int = 2              # channel for V_off (negative rail)
    v_gate_on: float = 15.0
    v_gate_off: float = -5.0

    # Optional TCU
    tcu_port: str = ''
    tcu_serial: str = ''
    tcu_channel: int = 1
    temperature_c: float = 25.0

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'ControllerConfig':
        return cls(
            id=str(d.get('id', 'Ctrl?')),
            port=str(d.get('port', '')),
            gss_serial=str(d.get('gss_serial', '')),
            num_duts=int(d.get('num_duts', 1)),
            freq_hz=float(d.get('freq_hz', 100_000)),
            duty_cycle=float(d.get('duty_cycle', 0.5)),
            vth_method=str(d.get('vth_method', 'force_current')),
            vth_force_current_ua=float(d.get('vth_force_current_ua', 250.0)),
            vth_precond_voltage=float(d.get('vth_precond_voltage', 0.0)),
            vth_threshold_current=float(d.get('vth_threshold_current', 1e-6)),
            vth_compliance_voltage=float(d.get('vth_compliance_voltage', 10.0)),
            psu_resource=str(d.get('psu_resource', '')),
            psu_serial=str(d.get('psu_serial', '')),
            psu_ch_pos=int(d.get('psu_ch_pos', 1)),
            psu_ch_neg=int(d.get('psu_ch_neg', 2)),
            v_gate_on=float(d.get('v_gate_on', 15.0)),
            v_gate_off=float(d.get('v_gate_off', -5.0)),
            tcu_port=str(d.get('tcu_port', '')),
            tcu_serial=str(d.get('tcu_serial', '')),
            tcu_channel=int(d.get('tcu_channel', 1)),
            temperature_c=float(d.get('temperature_c', 25.0)),
        )


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class GSSWorker:
    """Manages one GSS controller for the full duration of the stress test.

    Runs in a daemon thread.  Results are deposited into *result_queue* as
    plain dicts matching GateStressTest.DATA_COLUMNS.
    """

    # Seconds between log entries (can be reduced by the procedure's
    # log_interval_s at construction time, kept here for restart robustness).
    _MIN_SLEEP_S = 1.0

    def __init__(
        self,
        cfg: ControllerConfig,
        procedure: 'GateStressTest',
        result_queue: queue.Queue,
        smu,
        smu_lock: threading.Lock,
        psu=None,
        psu_lock: Optional[threading.Lock] = None,
        tcu=None,
    ):
        self.cfg = cfg
        self.procedure = procedure
        self.result_queue = result_queue
        self.smu = smu
        self.smu_lock = smu_lock
        self.psu = psu
        self.psu_lock = psu_lock or threading.Lock()
        self.tcu = tcu

        self.controller = None
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Live state (updated by the worker, read by _emit_row)
        self.cycle_count: int = 0
        self.last_vth: Dict[int, float] = {}      # dut (1-based) → V
        self.last_temperature: Optional[float] = None
        self.last_v_on: Optional[float] = None
        self.last_v_off: Optional[float] = None
        self.status: str = 'initializing'
        self.last_error: str = ''

    # ------------------------------------------------------------------
    # Thread management
    # ------------------------------------------------------------------

    def start(self):
        """Spawn the worker thread."""
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f'GSS-{self.cfg.id}',
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 10.0):
        """Signal the worker to stop and wait for its thread to finish."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    # Main worker loop
    # ------------------------------------------------------------------

    def _run(self):
        log.info(f'[{self.cfg.id}] Worker started')
        self.status = 'connecting'

        try:
            self._connect_controller()
            # PSU voltages and TCU temperature are applied and verified by
            # GateStressTest.startup() before the workers are started.
            self._configure_switching()
            self._start_switching()
        except Exception as exc:
            log.error(f'[{self.cfg.id}] Startup failed: {exc}')
            self.status = f'startup error'
            self.last_error = str(exc)
            self._emit_row(dut=0)
            return

        self.status = 'running'
        log.info(f'[{self.cfg.id}] Stress test running')

        log_interval = self.procedure.log_interval_s
        vth_interval = self.procedure.vth_interval_min * 60.0

        last_log_time = 0.0
        last_vth_time = 0.0

        while not self._stop_event.is_set() and not self.procedure.should_stop():
            now = time.time()

            # ---- periodic telemetry log ----
            if now - last_log_time >= log_interval:
                try:
                    self._update_cycle_count()
                    self._update_psu_readings()
                    self._update_temperature()
                except Exception as exc:
                    log.warning(f'[{self.cfg.id}] Telemetry read error: {exc}')
                    self.last_error = str(exc)

                for dut in range(1, self.cfg.num_duts + 1):
                    self._emit_row(dut=dut)
                last_log_time = now

            # ---- periodic Vth measurement ----
            if self.smu is not None and (now - last_vth_time >= vth_interval):
                try:
                    self._measure_vth_all_duts()
                except Exception as exc:
                    log.warning(f'[{self.cfg.id}] Vth measurement error: {exc}')
                    self.last_error = str(exc)
                last_vth_time = now

            time.sleep(self._MIN_SLEEP_S)

        # ---- shutdown ----
        try:
            self._stop_switching()
        except Exception as exc:
            log.warning(f'[{self.cfg.id}] Stop error: {exc}')

        self.status = 'stopped'
        log.info(f'[{self.cfg.id}] Worker stopped')

    # ------------------------------------------------------------------
    # Hardware interactions
    # ------------------------------------------------------------------

    def _connect_controller(self):
        from hardware.gss_controller import GSSController
        self.controller = GSSController(self.cfg.port)
        if not self.controller.connect():
            raise RuntimeError(
                f'Failed to connect to GSS controller on {self.cfg.port}'
            )
        log.info(f'[{self.cfg.id}] GSS controller connected on {self.cfg.port}')

    def _apply_psu_voltages(self):
        if self.psu is None:
            return
        try:
            with self.psu_lock:
                self.psu.set_voltage(self.cfg.psu_ch_pos, abs(self.cfg.v_gate_on))
                self.psu.set_current(self.cfg.psu_ch_pos, 1.0)
                self.psu.enable_output(self.cfg.psu_ch_pos, True)
                self.psu.set_voltage(self.cfg.psu_ch_neg, abs(self.cfg.v_gate_off))
                self.psu.set_current(self.cfg.psu_ch_neg, 1.0)
                self.psu.enable_output(self.cfg.psu_ch_neg, True)
            log.info(
                f'[{self.cfg.id}] PSU set: '
                f'ch{self.cfg.psu_ch_pos}={self.cfg.v_gate_on}V, '
                f'ch{self.cfg.psu_ch_neg}={self.cfg.v_gate_off}V'
            )
        except Exception as exc:
            log.warning(f'[{self.cfg.id}] PSU voltage apply failed: {exc}')

    def _apply_temperature(self):
        if self.tcu is None:
            return
        try:
            self.tcu.set_temperature(self.cfg.tcu_channel, self.cfg.temperature_c)
            self.tcu.enable_channel(self.cfg.tcu_channel)
            log.info(
                f'[{self.cfg.id}] TCU ch{self.cfg.tcu_channel} → '
                f'{self.cfg.temperature_c} °C'
            )
        except Exception as exc:
            log.warning(f'[{self.cfg.id}] TCU setup failed: {exc}')

    def _configure_switching(self):
        try:
            self.controller.configure(self.cfg.freq_hz, self.cfg.duty_cycle)
            log.info(
                f'[{self.cfg.id}] Switching configured: '
                f'{self.cfg.freq_hz:.0f} Hz, DC={self.cfg.duty_cycle:.3f}'
            )
        except NotImplementedError:
            log.warning(
                f'[{self.cfg.id}] configure() is TBD – '
                'skipping switching configuration (simulation mode)'
            )

    def _start_switching(self):
        try:
            self.controller.start()
            log.info(f'[{self.cfg.id}] Switching started')
        except NotImplementedError:
            log.warning(
                f'[{self.cfg.id}] start() is TBD – '
                'running in simulation mode (no actual switching)'
            )

    def _stop_switching(self):
        if self.controller is None:
            return
        try:
            self.controller.stop()
        except NotImplementedError:
            pass
        except Exception as exc:
            log.warning(f'[{self.cfg.id}] stop() error: {exc}')

    def _update_cycle_count(self):
        try:
            count = self.controller.get_cycle_count()
            if count is not None:
                self.cycle_count = count
        except NotImplementedError:
            pass  # TBD – silently ignore until firmware ready
        except Exception as exc:
            log.warning(f'[{self.cfg.id}] cycle count read error: {exc}')

    def _update_psu_readings(self):
        if self.psu is None:
            return
        try:
            with self.psu_lock:
                v_on = self.psu.get_voltage_setpoint(self.cfg.psu_ch_pos)
                v_off_raw = self.psu.get_voltage_setpoint(self.cfg.psu_ch_neg)
            self.last_v_on = v_on
            # PSU ch_neg supplies the absolute value; negate to get gate-off
            self.last_v_off = -v_off_raw if v_off_raw is not None else None
        except Exception as exc:
            log.warning(f'[{self.cfg.id}] PSU readback error: {exc}')

    def _update_temperature(self):
        if self.tcu is None:
            return
        try:
            t = self.tcu.get_temperature(self.cfg.tcu_channel)
            if t is not None and not math.isnan(t):
                self.last_temperature = t
        except Exception as exc:
            log.warning(f'[{self.cfg.id}] TCU read error: {exc}')

    def _select_dut_for_measurement(self, dut: int):
        """Route DUT *dut* to the SMU.  Silently skipped while TBD."""
        try:
            self.controller.select_dut(dut)
        except NotImplementedError:
            pass  # DUT MUX command is TBD

    def _measure_vth_all_duts(self):
        """Measure Vth for every DUT, one at a time, via the shared SMU."""
        force_current = self.cfg.vth_force_current_ua * 1e-6
        compliance = self.cfg.vth_compliance_voltage
        method = self.cfg.vth_method
        precond_v = self.cfg.vth_precond_voltage
        threshold_i = self.cfg.vth_threshold_current

        for dut in range(1, self.cfg.num_duts + 1):
            self._select_dut_for_measurement(dut)

            vth: Optional[float] = None
            for attempt in range(1, 4):  # up to 3 attempts
                acquired = self.smu_lock.acquire(timeout=30.0)
                if acquired:
                    try:
                        # Apply precondition voltage first
                        if precond_v != 0.0:
                            self.smu.apply_precondition_voltage(
                                precond_voltage_v=precond_v,
                                duration_s=0.1,
                            )
                        # Measure Vth
                        if method == 'ramp_voltage':
                            vth = self.smu.measure_vth_ramp(
                                start_voltage_v=precond_v,
                                stop_voltage_v=compliance,
                                step_voltage_v=0.05,
                                threshold_current_a=threshold_i,
                            )
                        else:  # force_current (default)
                            vth = self.smu.measure_vth(
                                force_current_a=force_current,
                                compliance_voltage_v=compliance,
                            )
                    finally:
                        self.smu_lock.release()
                    break
                else:
                    log.warning(
                        f'[{self.cfg.id}] DUT {dut}: SMU busy, '
                        f'retry {attempt}/3 in 30 s'
                    )
                    time.sleep(30.0)

            if vth is not None:
                self.last_vth[dut] = vth
                log.info(f'[{self.cfg.id}] DUT {dut} Vth = {vth:.4f} V')
            else:
                log.warning(f'[{self.cfg.id}] DUT {dut} Vth measurement failed')

    # ------------------------------------------------------------------
    # Result emission
    # ------------------------------------------------------------------

    def _emit_row(self, dut: int):
        """Put one result row into the shared result_queue."""
        row = {
            'Timestamp': time.time(),
            'Controller': self.cfg.id,
            'DUT': dut,
            'Cycles': self.cycle_count,
            'Vth (V)': self.last_vth.get(dut, float('nan')),
            'Temperature (°C)': (
                self.last_temperature
                if self.last_temperature is not None
                else float('nan')
            ),
            'V_on (V)': (
                self.last_v_on if self.last_v_on is not None else float('nan')
            ),
            'V_off (V)': (
                self.last_v_off if self.last_v_off is not None else float('nan')
            ),
            'Status': self.status,
        }
        self.result_queue.put(row)


# ---------------------------------------------------------------------------
# Procedure
# ---------------------------------------------------------------------------

class GateStressTest(Procedure):
    """Gate Switching Stress test procedure.

    Manages N GSS controllers simultaneously via worker threads.
    """

    name = 'Gate Switching Stress (GSS)'
    internal_name = 'Gate_Switching_Stress'
    short_name = 'GSS'
    description = (
        'Long-duration gate switching stress test. '
        'Manages multiple GSS controllers simultaneously, '
        'with optional SMU Vth measurement, PSU control, and temperature control.'
    )

    # ---- Connection parameters (pre-filled by startup dialog) -------------
    # These are ListParameters so the discovered serial numbers appear as a
    # dropdown in the 'New Experiment' dialog.  Choices are populated by
    # update_device_choices() before the main window opens.

    smu_serial = ListParameter('SMU', choices=[''])

    # ---- GSS controller ---------------------------------------------------

    gss_serial = ListParameter('GSS Controller', choices=[''])

    num_duts = IntegerParameter(
        'DUT Count', default=1, minimum=1, maximum=8,
    )

    # ---- Switching --------------------------------------------------------

    freq_hz = FloatParameter(
        'Switching Frequency', units='Hz',
        default=100_000.0, minimum=1_000.0, maximum=10_000_000.0,
    )
    duty_cycle = FloatParameter(
        'Duty Cycle', default=0.5, minimum=0.01, maximum=0.99,
    )

    # ---- SMU / Vth measurement -------------------------------------------

    vth_method = ListParameter(
        'Vth Method',
        choices=['force_current', 'ramp_voltage'],
    )
    vth_force_current_ua = FloatParameter(
        'Vth Force Current', units='µA',
        default=250.0, minimum=0.1, maximum=10_000.0,
    )
    vth_precond_voltage = FloatParameter(
        'Vth Precondition Voltage', units='V',
        default=0.0, minimum=0.0, maximum=30.0,
    )
    vth_threshold_current_na = FloatParameter(
        'Vth Threshold Current', units='nA',
        default=1000.0, minimum=0.001, maximum=1e6,
    )
    vth_compliance_voltage = FloatParameter(
        'Vth Compliance Voltage', units='V',
        default=10.0, minimum=0.1, maximum=30.0,
    )

    # ---- PSU --------------------------------------------------------------

    psu_serial = ListParameter('PSU', choices=[''])
    psu_ch_pos = IntegerParameter(
        'PSU Channel V_on', default=1, minimum=1, maximum=3,
    )
    psu_ch_neg = IntegerParameter(
        'PSU Channel V_off', default=2, minimum=1, maximum=3,
    )
    v_gate_on = FloatParameter(
        'V_on (Gate On)', units='V',
        default=15.0, minimum=0.0, maximum=32.0,
    )
    v_gate_off = FloatParameter(
        'V_off (Gate Off)', units='V',
        default=-5.0, minimum=-32.0, maximum=0.0,
    )

    # ---- TCU --------------------------------------------------------------

    tcu_serial = ListParameter('TCU', choices=[''])
    tcu_channel = IntegerParameter(
        'TCU Channel', default=1, minimum=1, maximum=4,
    )
    temperature_c = FloatParameter(
        'Temperature', units='°C',
        default=25.0, minimum=-40.0, maximum=250.0,
    )

    # ---- Timing -----------------------------------------------------------

    log_interval_s = IntegerParameter(
        'Log Interval', units='s',
        default=60, minimum=10, maximum=3600,
    )
    vth_interval_min = IntegerParameter(
        'Vth Measurement Interval', units='min',
        default=60, minimum=5, maximum=1440,
    )

    # ---- Misc -------------------------------------------------------------

    worker_shutdown_timeout_s = IntegerParameter(
        'Worker Shutdown Timeout', units='s',
        default=15, minimum=5, maximum=60,
    )
    data_directory = Parameter(
        'Data Directory',
        default=os.path.join('data', 'GSS'),
    )
    nas_directory = Parameter(
        'NAS Directory (optional)',
        default='',
    )

    # How often (seconds) local CSV files are copied to the NAS.
    # Syncs run in a background thread so they never block the stress test.
    _NAS_SYNC_INTERVAL_S: int = 3600

    # ---- Advanced: multi-controller JSON (optional) ----------------------
    # Leave empty to use the individual parameters above.
    # Provide a JSON array of ControllerConfig dicts to run N controllers.
    controller_config_json = Parameter(
        'Controller Configuration (JSON, advanced)',
        default='',
    )

    # -----------------------------------------------------------------------

    DATA_COLUMNS = [
        'Timestamp',
        'Controller',
        'DUT',
        'Cycles',
        'Vth (V)',
        'Temperature (°C)',
        'V_on (V)',
        'V_off (V)',
        'Status',
    ]

    INPUTS = [
        'smu_serial',
        'gss_serial',
        'num_duts',
        'freq_hz',
        'duty_cycle',
        'vth_method',
        'vth_force_current_ua',
        'vth_precond_voltage',
        'vth_threshold_current_na',
        'vth_compliance_voltage',
        'psu_serial',
        'psu_ch_pos',
        'psu_ch_neg',
        'v_gate_on',
        'v_gate_off',
        'tcu_serial',
        'tcu_channel',
        'temperature_c',
        'log_interval_s',
        'vth_interval_min',
        'data_directory',
        'nas_directory',
    ]
    DISPLAYS = INPUTS

    X_AXIS = 'Timestamp'
    Y_AXIS = 'Vth (V)'

    HARDWARE = {
        'keithley_smu': {
            'display_name': 'Keithley SMU (2636B / 2604B / 2450)',
            'parameters': {
                'connection': {
                    'label': 'VISA Resource',
                    'default': '',
                    'placeholder': 'e.g. GPIB::26 or USB0::0x05E6::…::INSTR',
                }
            },
        },
        'nge103_psu': {
            'display_name': 'R&S NGE103B Power Supply (optional)',
            'parameters': {
                'connection': {
                    'label': 'VISA Resource',
                    'default': '',
                    'placeholder': 'e.g. ASRL8::INSTR for COM8',
                }
            },
        },
        'hmc8043_psu': {
            'display_name': 'R&S HMC8043 Power Supply (optional)',
            'parameters': {
                'connection': {
                    'label': 'VISA Resource',
                    'default': '',
                    'placeholder': 'e.g. USB0::0x0403::0xED72::…::INSTR',
                }
            },
        },
        'tcu': {
            'display_name': 'Temperature Controller / TCU (optional)',
            'parameters': {
                'connection': {
                    'label': 'Serial Port',
                    'default': '',
                    'placeholder': 'e.g. COM9',
                }
            },
        },
    }

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def startup(self):
        """Parse configuration, connect all shared hardware."""
        self._workers: List[GSSWorker] = []
        self._result_queue: queue.Queue = queue.Queue()
        self._csv_handles: Dict[str, Any] = {}      # ctrl_id → {'file': …, 'writer': …}
        self._local_csv_paths: Dict[str, str] = {}  # ctrl_id → absolute local path

        # CSVs are written to a local temp directory to avoid blocking on NAS
        # latency.  Contents are periodically copied to data_directory and
        # optionally to nas_directory.
        self._local_cache_dir: str = tempfile.mkdtemp(prefix='gss_cache_')
        log.info(f'GSS local cache: {self._local_cache_dir}')

        self._last_nas_sync: float = time.monotonic()
        self._sync_thread: Optional[threading.Thread] = None

        self._apply_connection_parameters()

        # Build controller config list.
        # If controller_config_json is provided, parse it (multi-controller advanced mode).
        # Otherwise build a single ControllerConfig from the individual parameters.
        raw_json = (self.controller_config_json or '').strip()
        if raw_json and raw_json not in ('[]', '{}'):
            self._configs: List[ControllerConfig] = self._parse_configs()
        else:
            # Resolve serial numbers → actual connection strings via device registry.
            # Fallback: use the serial string directly (allows manual entry).
            _gss_info = _DEVICE_REGISTRY.get(self.gss_serial, {})
            _psu_info = _DEVICE_REGISTRY.get(self.psu_serial, {})
            _tcu_info = _DEVICE_REGISTRY.get(self.tcu_serial, {})
            _gss_port    = _gss_info.get('port',     self.gss_serial)
            _psu_res     = _psu_info.get('resource', self.psu_serial)
            _tcu_port    = _tcu_info.get('port',     self.tcu_serial)
            self._configs = [ControllerConfig(
                id='Ctrl1',
                port=_gss_port,
                gss_serial=self.gss_serial,
                num_duts=self.num_duts,
                freq_hz=self.freq_hz,
                duty_cycle=self.duty_cycle,
                vth_method=self.vth_method,
                vth_force_current_ua=self.vth_force_current_ua,
                vth_precond_voltage=self.vth_precond_voltage,
                vth_threshold_current=self.vth_threshold_current_na * 1e-9,
                vth_compliance_voltage=self.vth_compliance_voltage,
                psu_resource=_psu_res,
                psu_serial=self.psu_serial,
                psu_ch_pos=self.psu_ch_pos,
                psu_ch_neg=self.psu_ch_neg,
                v_gate_on=self.v_gate_on,
                v_gate_off=self.v_gate_off,
                tcu_port=_tcu_port,
                tcu_serial=self.tcu_serial,
                tcu_channel=self.tcu_channel,
                temperature_c=self.temperature_c,
            )]

        if not self._configs:
            log.warning('No controller configurations found; procedure will run with no controllers.')

        # Connect shared SMU — resolve serial number → VISA resource
        self._smu = None
        self._smu_lock = threading.Lock()
        _smu_info = _DEVICE_REGISTRY.get(self.smu_serial, {})
        _smu_resource = _smu_info.get('resource', self.smu_serial)
        if _smu_resource:
            from hardware.keithley_2636 import KeyithleySMU
            smu = KeyithleySMU(_smu_resource)
            if smu.connect():
                self._smu = smu
                log.info(f'SMU connected: {smu.idn}')
            else:
                log.error('Failed to connect to SMU; Vth measurement disabled')

        # Connect shared PSUs (one object per unique resource string)
        self._psu_pool: Dict[str, Any] = {}         # resource → driver
        self._psu_locks: Dict[str, threading.Lock] = {}  # resource → lock
        for cfg in self._configs:
            if cfg.psu_resource and cfg.psu_resource not in self._psu_pool:
                psu = self._connect_psu(cfg.psu_resource)
                self._psu_pool[cfg.psu_resource] = psu  # may be None on failure
                self._psu_locks[cfg.psu_resource] = threading.Lock()

        # Connect shared TCUs (one object per unique port)
        self._tcu_pool: Dict[str, Any] = {}         # port → driver
        for cfg in self._configs:
            if cfg.tcu_port and cfg.tcu_port not in self._tcu_pool:
                tcu = self._connect_tcu(cfg.tcu_port)
                self._tcu_pool[cfg.tcu_port] = tcu  # may be None on failure

        # Check for conflicting PSU/TCU channel assignments
        self._check_psu_tcu_conflicts(self._configs)

        # Set PSU voltages / TCU temperatures and verify before switching starts
        self._verify_hardware_setup()

        # Ensure data directory exists
        os.makedirs(self.data_directory, exist_ok=True)

        # Build workers
        run_ts = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        for cfg in self._configs:
            psu = self._psu_pool.get(cfg.psu_resource)
            psu_lock = self._psu_locks.get(cfg.psu_resource, threading.Lock())
            tcu = self._tcu_pool.get(cfg.tcu_port)
            worker = GSSWorker(
                cfg=cfg,
                procedure=self,
                result_queue=self._result_queue,
                smu=self._smu,
                smu_lock=self._smu_lock,
                psu=psu,
                psu_lock=psu_lock,
                tcu=tcu,
            )
            # Open per-controller CSV in local cache dir
            self._open_controller_csv(cfg.id, run_ts)
            self._workers.append(worker)

        log.info(f'GSS startup complete: {len(self._workers)} controller(s) configured')

    def execute(self):
        """Start all workers and drain the result queue until stopped."""
        if not self._workers:
            log.warning('No workers to start; aborting GSS procedure')
            return

        # Start all workers
        for worker in self._workers:
            worker.start()

        log.info('All GSS workers started')

        # Main loop: drain result queue and forward rows to pymeasure + CSV
        while True:
            # Drain any available rows
            drained_any = False
            while True:
                try:
                    row = self._result_queue.get_nowait()
                except queue.Empty:
                    break

                self.emit('results', row)
                self._write_controller_csv(row['Controller'], row)
                drained_any = True

            # Periodic NAS sync (non-blocking background thread)
            if time.monotonic() - self._last_nas_sync >= self._NAS_SYNC_INTERVAL_S:
                self._sync_to_nas(final=False)

            # Check stop conditions
            if self.should_stop():
                log.info('GSS abort requested')
                break

            # If all workers have exited naturally, we are done
            if all(not w.is_alive for w in self._workers):
                log.info('All GSS workers have finished')
                break

            time.sleep(0.5)

        # Drain any remaining rows after workers stop
        try:
            while True:
                row = self._result_queue.get_nowait()
                self.emit('results', row)
                self._write_controller_csv(row['Controller'], row)
        except queue.Empty:
            pass

    def shutdown(self):
        """Stop all workers and clean up hardware."""
        log.info('GSS shutdown: stopping workers...')
        for worker in self._workers:
            worker.stop(timeout=self.worker_shutdown_timeout_s)

        # Disable all PSU outputs
        for resource, psu in self._psu_pool.items():
            if psu is None:
                continue
            psu_lock = self._psu_locks.get(resource, threading.Lock())
            try:
                with psu_lock:
                    for ch in range(1, 4):
                        try:
                            psu.enable_output(ch, False)
                        except Exception:
                            pass
                    psu.disconnect()
                    log.info(f'PSU {resource} outputs disabled and disconnected')
            except Exception as exc:
                log.warning(f'PSU shutdown error ({resource}): {exc}')

        # Disable all TCU channels
        for port, tcu in self._tcu_pool.items():
            if tcu is None:
                continue
            try:
                for cfg in self._configs:
                    if cfg.tcu_port == port:
                        tcu.disable_channel(cfg.tcu_channel)
                tcu.disconnect()
                log.info(f'TCU {port} channels disabled and disconnected')
            except Exception as exc:
                log.warning(f'TCU shutdown error ({port}): {exc}')

        # Disconnect SMU
        if self._smu is not None:
            try:
                self._smu.disconnect()
                log.info('SMU disconnected')
            except Exception as exc:
                log.warning(f'SMU disconnect error: {exc}')

        # Close CSV files
        for ctrl_id, handle in self._csv_handles.items():
            try:
                handle['file'].close()
                log.debug(f'Closed CSV for {ctrl_id}')
            except Exception:
                pass

        # Final sync: copy closed CSVs to data_directory and NAS
        self._sync_to_nas(final=True)

        log.info('GSS shutdown complete')

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _check_psu_tcu_conflicts(self, configs: List['ControllerConfig']):
        """Raise ValueError if two controllers share a PSU/TCU channel at different setpoints.

        Two controllers sharing the same channel with identical settings is
        allowed (the hardware object + Lock are deduplicated in startup()).
        """
        psu_channels: Dict[tuple, tuple] = {}   # (resource, ch) -> (voltage, ctrl_id)
        tcu_channels: Dict[tuple, tuple] = {}   # (port, ch)     -> (temp, ctrl_id)

        for cfg in configs:
            if cfg.psu_resource:
                for ch, voltage in [
                    (cfg.psu_ch_pos, abs(cfg.v_gate_on)),
                    (cfg.psu_ch_neg, abs(cfg.v_gate_off)),
                ]:
                    key = (cfg.psu_resource, ch)
                    if key in psu_channels:
                        existing_v, existing_id = psu_channels[key]
                        if abs(existing_v - voltage) > 0.01:
                            raise ValueError(
                                f"PSU channel conflict: '{existing_id}' and '{cfg.id}' "
                                f"both use {cfg.psu_resource} ch{ch} at different "
                                f"voltages ({existing_v:.3f} V vs {voltage:.3f} V)"
                            )
                    else:
                        psu_channels[key] = (voltage, cfg.id)

            if cfg.tcu_port:
                key = (cfg.tcu_port, cfg.tcu_channel)
                if key in tcu_channels:
                    existing_t, existing_id = tcu_channels[key]
                    if abs(existing_t - cfg.temperature_c) > 0.5:
                        raise ValueError(
                            f"TCU channel conflict: '{existing_id}' and '{cfg.id}' "
                            f"both use {cfg.tcu_port} ch{cfg.tcu_channel} at different "
                            f"temperatures ({existing_t:.1f} \u00b0C vs {cfg.temperature_c:.1f} \u00b0C)"
                        )
                else:
                    tcu_channels[key] = (cfg.temperature_c, cfg.id)

    def _verify_hardware_setup(self):
        """Set PSU voltages + TCU temperatures, wait for settling, then verify.

        PSU voltage verification: wait 1 s, read actual voltage from the GSS
        controller.  If get_output_voltages() raises NotImplementedError (TBD
        firmware), log a warning and continue.

        Temperature verification: wait 2 min per 25 °C above 25 °C baseline,
        polling every 30 s.  Raises RuntimeError if the temperature does not
        reach the target within the expected window.
        """
        # ---- Apply PSU voltages -----------------------------------------
        psu_done: set = set()   # tracks (resource, ch) already configured
        for cfg in self._configs:
            if not cfg.psu_resource:
                continue
            psu = self._psu_pool.get(cfg.psu_resource)
            if psu is None:
                continue
            lock = self._psu_locks.get(cfg.psu_resource, threading.Lock())
            with lock:
                for ch, voltage in [
                    (cfg.psu_ch_pos, abs(cfg.v_gate_on)),
                    (cfg.psu_ch_neg, abs(cfg.v_gate_off)),
                ]:
                    if (cfg.psu_resource, ch) not in psu_done:
                        psu.set_voltage(ch, voltage)
                        psu.set_current(ch, 1.0)
                        psu.enable_output(ch, True)
                        psu_done.add((cfg.psu_resource, ch))
            log.info(f'[{cfg.id}] PSU voltages set: V_on={cfg.v_gate_on} V, V_off={cfg.v_gate_off} V')

        # ---- Apply TCU temperatures -------------------------------------
        tcu_done: set = set()
        tcu_wait_minutes: float = 0.0
        for cfg in self._configs:
            if not cfg.tcu_port:
                continue
            tcu = self._tcu_pool.get(cfg.tcu_port)
            if tcu is None:
                continue
            key = (cfg.tcu_port, cfg.tcu_channel)
            if key not in tcu_done:
                tcu.set_temperature(cfg.tcu_channel, cfg.temperature_c)
                tcu.enable_channel(cfg.tcu_channel)
                tcu_done.add(key)
                wait = max(0.0, (cfg.temperature_c - 25.0) / 25.0) * 2.0
                tcu_wait_minutes = max(tcu_wait_minutes, wait)
            log.info(f'[{cfg.id}] TCU ch{cfg.tcu_channel} \u2192 {cfg.temperature_c} \u00b0C')

        # ---- Verify PSU voltages (wait 1 s then read from GSS) ----------
        if psu_done:
            log.info('Waiting 1 s for PSU voltages to settle…')
            time.sleep(1.0)
            for cfg in self._configs:
                if not cfg.psu_resource:
                    continue
                # GSSController is connected by the worker; we use a fresh
                # temporary connection here just for verification.
                from hardware.gss_controller import GSSController
                ctrl = GSSController(cfg.port)
                if not ctrl.connect():
                    log.warning(f'[{cfg.id}] Cannot connect GSS for voltage check')
                    continue
                try:
                    v_on, v_off = ctrl.get_output_voltages()
                    if v_on is not None and abs(v_on - cfg.v_gate_on) > 0.5:
                        raise RuntimeError(
                            f'[{cfg.id}] Voltage check failed: '
                            f'V_on expected {cfg.v_gate_on:.2f} V, '
                            f'measured {v_on:.2f} V'
                        )
                    if v_off is not None and abs(v_off - cfg.v_gate_off) > 0.5:
                        raise RuntimeError(
                            f'[{cfg.id}] Voltage check failed: '
                            f'V_off expected {cfg.v_gate_off:.2f} V, '
                            f'measured {v_off:.2f} V'
                        )
                    log.info(
                        f'[{cfg.id}] Voltage verified: '
                        f'V_on={v_on:.2f} V, V_off={v_off:.2f} V'
                    )
                except NotImplementedError:
                    log.warning(
                        f'[{cfg.id}] get_output_voltages() not yet implemented '
                        '– skipping voltage verification'
                    )
                except RuntimeError:
                    raise
                except Exception as exc:
                    log.warning(f'[{cfg.id}] Voltage check error: {exc}')
                finally:
                    ctrl.disconnect()

        # ---- Wait for temperatures to settle ----------------------------
        if tcu_done and tcu_wait_minutes > 0:
            wait_s = tcu_wait_minutes * 60.0
            log.info(
                f'Waiting up to {tcu_wait_minutes:.1f} min for temperature(s) '
                'to settle (polling every 30 s)…'
            )
            deadline = time.time() + wait_s
            tolerance_c = 2.0

            while time.time() < deadline:
                if self.should_stop():
                    raise RuntimeError('Test aborted during temperature settling')

                all_settled = True
                for cfg in self._configs:
                    if not cfg.tcu_port:
                        continue
                    tcu = self._tcu_pool.get(cfg.tcu_port)
                    if tcu is None:
                        continue
                    actual_t = tcu.get_temperature(cfg.tcu_channel)
                    if actual_t is None or math.isnan(float(actual_t)):
                        all_settled = False
                        continue
                    delta = abs(actual_t - cfg.temperature_c)
                    log.debug(
                        f'[{cfg.id}] Temperature: {actual_t:.1f} \u00b0C '
                        f'(target {cfg.temperature_c:.1f} \u00b0C, \u0394{delta:.1f} \u00b0C)'
                    )
                    if delta > tolerance_c:
                        all_settled = False

                if all_settled:
                    log.info('All temperatures settled.')
                    break

                time.sleep(30.0)
            else:
                # After full wait, do a final check and raise if way off
                for cfg in self._configs:
                    if not cfg.tcu_port:
                        continue
                    tcu = self._tcu_pool.get(cfg.tcu_port)
                    if tcu is None:
                        continue
                    actual_t = tcu.get_temperature(cfg.tcu_channel)
                    if actual_t is not None and not math.isnan(float(actual_t)):
                        if abs(actual_t - cfg.temperature_c) > tolerance_c * 2:
                            raise RuntimeError(
                                f'[{cfg.id}] Temperature target not reached '
                                f'after {tcu_wait_minutes:.1f} min: '
                                f'expected {cfg.temperature_c:.1f} \u00b0C, '
                                f'got {actual_t:.1f} \u00b0C'
                            )

    def _apply_connection_parameters(self):
        """Refresh _DEVICE_REGISTRY from startup dialog connection parameters.

        The actual hardware resolution (serial → port/VISA resource) happens in
        startup() by looking up _DEVICE_REGISTRY[serial].  This method ensures
        the registry is populated when startup() is called without a preceding
        call to update_device_choices() (e.g. during unit tests).
        """
        global _DEVICE_REGISTRY
        params = getattr(self, 'connection_parameters', None)
        if not params:
            params = getattr(self.__class__, '_startup_connection_parameters', None)
        if not params or not isinstance(params, dict):
            return

        discovered = params.get('gss_discovered_devices', [])
        if discovered and not _DEVICE_REGISTRY:
            _DEVICE_REGISTRY = {
                d['serial']: d for d in discovered if d.get('serial')
            }

    def _parse_configs(self) -> List[ControllerConfig]:
        """Parse controller_config_json into a list of ControllerConfig objects."""
        raw = self.controller_config_json or '[]'
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.error(f'Invalid controller_config_json: {exc}')
            return []

        if not isinstance(data, list):
            log.error('controller_config_json must be a JSON array')
            return []

        configs = []
        for i, item in enumerate(data):
            try:
                cfg = ControllerConfig.from_dict(item)
                configs.append(cfg)
                log.info(
                    f'Controller {cfg.id}: port={cfg.port}, '
                    f'freq={cfg.freq_hz:.0f} Hz, DC={cfg.duty_cycle:.3f}, '
                    f'V_on={cfg.v_gate_on} V, V_off={cfg.v_gate_off} V, '
                    f'DUTs={cfg.num_duts}'
                )
            except Exception as exc:
                log.error(f'Cannot parse controller config [{i}]: {exc}')

        return configs

    def _connect_psu(self, resource: str):
        """Connect to a PSU and return the driver object, or None on failure."""
        try:
            from hardware.rs_nge103 import NGE100
            psu = NGE100(resource)
            if psu.connect():
                log.info(f'PSU connected: {resource} ({psu.ID().strip()})')
                return psu
            # Try HMC8043 as fallback
            from hardware.rs_hmc8043 import RSHMC8043Controller
            psu = RSHMC8043Controller(resource)
            if psu.connect():
                log.info(f'PSU connected (HMC8043): {resource}')
                return psu
            log.error(f'Failed to connect to PSU on {resource}')
            return None
        except Exception as exc:
            log.error(f'PSU connect error ({resource}): {exc}')
            return None

    def _connect_tcu(self, port: str):
        """Connect to a TCU and return the driver object, or None on failure."""
        try:
            from hardware.tcu_driver import TCUDriver
            tcu = TCUDriver(port)
            if tcu.connect():
                log.info(f'TCU connected on {port}')
                return tcu
            log.error(f'Failed to connect to TCU on {port}')
            return None
        except Exception as exc:
            log.error(f'TCU connect error ({port}): {exc}')
            return None

    # -----------------------------------------------------------------------
    # Per-controller CSV file management
    # -----------------------------------------------------------------------

    def _open_controller_csv(self, ctrl_id: str, timestamp: str):
        """Open (and write the header to) the CSV file for *ctrl_id*.

        The file is created in the local cache directory to avoid blocking on
        NAS latency.  It is copied to *data_directory* (and optionally
        *nas_directory*) by :meth:`_sync_to_nas`.
        """
        safe_id = ctrl_id.replace(' ', '_').replace('/', '-')
        filename = f'GSS_{safe_id}_{timestamp}.csv'
        filepath = os.path.join(self._local_cache_dir, filename)
        try:
            f = open(filepath, 'w', newline='', encoding='utf-8')
            writer = csv.DictWriter(f, fieldnames=self.DATA_COLUMNS)
            writer.writeheader()
            self._csv_handles[ctrl_id] = {'file': f, 'writer': writer}
            self._local_csv_paths[ctrl_id] = filepath
            log.info(f'Opened CSV for {ctrl_id}: {filepath}')
        except Exception as exc:
            log.error(f'Cannot open CSV for {ctrl_id}: {exc}')

    def _write_controller_csv(self, ctrl_id: str, row: dict):
        """Write one row to the per-controller CSV."""
        handle = self._csv_handles.get(ctrl_id)
        if handle is None:
            return
        try:
            handle['writer'].writerow(row)
            handle['file'].flush()
        except Exception as exc:
            log.warning(f'CSV write error ({ctrl_id}): {exc}')

    def _sync_to_nas(self, final: bool = False) -> None:
        """Copy local cache CSVs to *data_directory* and *nas_directory*.

        When *final* is ``False`` the copy runs in a background thread so it
        never blocks the stress test.  When *final* is ``True`` (called from
        :meth:`shutdown` after the CSV handles are already closed) the copy
        runs synchronously so no data is lost on exit.

        A background sync that is still running when a new interval fires is
        silently skipped — the next interval will catch up.
        """
        if not self._local_csv_paths:
            return

        if not final:
            # Skip if a previous background sync is still in progress
            if self._sync_thread is not None and self._sync_thread.is_alive():
                log.debug('NAS sync already in progress; skipping this interval')
                return

        destinations = [self.data_directory]
        nas = (self.nas_directory or '').strip()
        if nas:
            destinations.append(nas)

        def _do_sync():
            for dest_dir in destinations:
                try:
                    os.makedirs(dest_dir, exist_ok=True)
                except Exception as exc:
                    log.warning(f'Cannot create sync destination {dest_dir!r}: {exc}')
                    continue
                for ctrl_id, local_path in list(self._local_csv_paths.items()):
                    if not os.path.exists(local_path):
                        continue
                    # Flush before copying (handle may already be closed on final sync)
                    handle = self._csv_handles.get(ctrl_id)
                    if handle is not None:
                        try:
                            handle['file'].flush()
                        except Exception:
                            pass
                    dest_path = os.path.join(dest_dir, os.path.basename(local_path))
                    try:
                        shutil.copy2(local_path, dest_path)
                        log.debug(f'Synced {ctrl_id} → {dest_path}')
                    except Exception as exc:
                        log.warning(f'Sync failed ({ctrl_id} → {dest_dir!r}): {exc}')
            self._last_nas_sync = time.monotonic()
            log.info(f'NAS sync complete (destinations: {destinations})')

        if final:
            _do_sync()
        else:
            self._sync_thread = threading.Thread(
                target=_do_sync, name='gss-nas-sync', daemon=True
            )
            self._sync_thread.start()
