"""
Gate Switching Stress (GSS) Procedure

Runs a long-duration (weeks) gate switching stress test on one GSS
controller.  To run several controllers at once, queue one GSS experiment
per controller (each uses its own GUI window / queued run); this procedure
itself always manages exactly one controller.

Data saved
----------
* One CSV row per DUT at procedure state changes, progress polls, Vth
    measurements, and batch completion, emitted via pymeasure's results
    mechanism. This is the same "Results" CSV file shown in the GUI's
  browser/plot (saved under the toolbar's Directory field).
* That single Results file (and its checkpoint, used to resume after a
  crash/restart) is periodically mirrored to *nas_directory* when
  configured, so the same data exists in exactly two places: the local
  Directory and the NAS backup.

Aborting
--------
Click "Abort" in the GUI.  All workers stop within *worker_shutdown_timeout_s*
seconds, all PSU / TCU outputs are disabled, and all connections are closed.
"""

import json
import logging
import math
import os
import queue
import shutil
import threading
import time
from dataclasses import dataclass
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


def update_device_choices(
    discovered_devices: list, selected_devices: Optional[Dict[str, str]] = None,
) -> None:
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

    def _set_choices(param, choices, selected=''):
        # ListParameter.choices is a read-only property (no setter) -- the
        # underlying _choices dict must be updated directly, otherwise the
        # assignment silently raises AttributeError and the dropdown never
        # actually changes (this used to be swallowed by a broad except in
        # APS GUI.py, hiding the failure).
        keys = [str(c) for c in choices]
        param._choices = {k: c for k, c in zip(keys, choices)}
        value = selected if selected in choices else choices[1]
        param.default = value
        param.value = value

    gss_sn = _serials('gss')
    tcu_sn = _serials('tcu')
    psu_sn = [''] + [
        d['serial'] for d in discovered_devices
        if d.get('type') in ('nge103', 'hmc8043')
    ]
    smu_sn = _serials('keithley')

    selected_devices = selected_devices or {}
    if gss_sn[1:]:
        _set_choices(GateStressTest.gss_serial, gss_sn, selected_devices.get('gss', ''))
    if tcu_sn[1:]:
        _set_choices(GateStressTest.tcu_serial, tcu_sn, selected_devices.get('tcu', ''))
    if psu_sn[1:]:
        _set_choices(GateStressTest.psu_serial, psu_sn, selected_devices.get('nge103', '') or selected_devices.get('hmc8043', ''))
    if smu_sn[1:]:
        _set_choices(GateStressTest.smu_serial, smu_sn, selected_devices.get('keithley', ''))


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
    vth_method: str = 'ramp_voltage'   # 'force_current' | 'ramp_voltage'
    vth_current_ma: float = 1.0
    vth_precond_voltage: float = 15.0
    vth_ramp_start_voltage: float = 6.0
    vth_ramp_stop_voltage: float = 0.0
    vth_ramp_step_voltage: float = 0.05
    vth_ramp_fine_step_voltage: float = 0.001
    vth_threshold_current: float = 1e-3
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


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class GSSWorker:
    """Manages one GSS controller for the full duration of the stress test.

    Runs in a daemon thread.  Results are deposited into *result_queue* as
    plain dicts matching GateStressTest.DATA_COLUMNS.
    """

    # Seconds between stop checks outside firmware-side batches.
    _MIN_SLEEP_S = 1.0
    _MAX_FIRMWARE_BATCH_CYCLES = 4_000_000_000
    _DUT_RELAY_SETTLE_S = 0.05

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
        tcu_lock: Optional[threading.Lock] = None,
        checkpoint_path: str = '',
    ):
        self.cfg = cfg
        self.procedure = procedure
        self.result_queue = result_queue
        self.smu = smu
        self.smu_lock = smu_lock
        self.psu = psu
        self.psu_lock = psu_lock or threading.Lock()
        self.tcu = tcu
        self.tcu_lock = tcu_lock or threading.Lock()
        self.checkpoint_path = checkpoint_path

        self.controller = None
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # The firmware's GSS_cycles counter accumulates across ALL batches
        # since the controller was last powered on/reset -- it never resets
        # per test. _cycle_baseline is the raw firmware count captured right
        # after connecting, and _session_start_cycle_count is this run's
        # logical progress (e.g. resumed from a checkpoint) at that same
        # moment. Together they let raw firmware counts be converted into
        # this run's own cycle count via _absolute_cycle_count().
        self._cycle_baseline: int = 0
        self._session_start_cycle_count: int = 0

        # Live state (updated by the worker, read by _emit_row)
        self.cycle_count: int = 0
        self.last_vth: Dict[int, float] = {}      # dut (1-based) → V
        self.last_temperature: Optional[float] = None
        self.last_v_on: Optional[float] = None
        self.last_v_off: Optional[float] = None
        self.status: str = 'initializing'
        self.last_error: str = ''
        self.batch_number: int = 0

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
        if self.smu_lock.acquire(blocking=False):
            try:
                if self.smu is not None and hasattr(self.smu, 'emergency_shutdown'):
                    self.smu.emergency_shutdown()
            finally:
                self.smu_lock.release()
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
            self._capture_cycle_baseline()
        except Exception as exc:
            log.error(f'[{self.cfg.id}] Startup failed: {exc}')
            self.status = 'startup error'
            self.last_error = str(exc)
            self._emit_row(dut=0)
            return

        try:
            self._run_batches()
        finally:
            try:
                self._stop_switching()
            except Exception as exc:
                log.warning(f'[{self.cfg.id}] Stop error: {exc}')
            try:
                if self.controller is not None:
                    self.controller.disconnect()
            except Exception as exc:
                log.warning(f'[{self.cfg.id}] Disconnect error: {exc}')
            self.status = 'stopped'
            self._save_checkpoint()
            log.info(f'[{self.cfg.id}] Worker stopped')

    def _run_batches(self):
        self.status = 'running'
        log.info(f'[{self.cfg.id}] Stress test running')

        target_cycles = int(self.procedure.target_cycles)
        batch_duration_s = max(1.0, float(self.procedure.batch_duration_min) * 60.0)
        vth_interval = float(self.procedure.vth_interval_min) * 60.0
        last_vth_time = time.time()

        self._load_checkpoint(target_cycles)
        self._session_start_cycle_count = self.cycle_count
        self._emit_all_rows()
        if self.smu is not None and self.procedure.pre_start_vth and self.cycle_count == 0:
            self.status = 'pre-run Vth'
            self._emit_all_rows()
            if self._run_with_retries(self._measure_vth_all_duts, 'pre-run Vth measurement'):
                self._save_checkpoint(target_cycles)
                self._emit_all_rows()

        while self.cycle_count < target_cycles:
            if self._stop_requested():
                self.status = 'manual shutdown requested'
                break

            remaining = target_cycles - self.cycle_count
            planned_cycles = int(self.cfg.freq_hz * batch_duration_s)
            batch_cycles = max(1, min(remaining, planned_cycles, self._MAX_FIRMWARE_BATCH_CYCLES))

            self.status = 'switching'
            self.batch_number += 1
            log.info(
                f'[{self.cfg.id}] Batch {self.batch_number}: '
                f'{batch_cycles} cycles at {self.cfg.freq_hz:.0f} Hz'
            )

            completed = self._run_batch_with_recovery(batch_cycles)
            if completed is None:
                break

            self.cycle_count = max(self.cycle_count, completed)
            self.status = 'between batches'
            self._refresh_telemetry_with_recovery()
            self._save_checkpoint(target_cycles)
            self._emit_all_rows()

            if self.smu is not None and time.time() - last_vth_time >= vth_interval:
                self.status = 'measuring Vth'
                if self._run_with_retries(self._measure_vth_all_duts, 'Vth measurement'):
                    last_vth_time = time.time()
                    self._save_checkpoint(target_cycles)
                    self._emit_all_rows()

        if (self.smu is not None and self.procedure.post_shutdown_vth
                and not self._stop_requested()):
            self.status = 'post-run Vth'
            self._emit_all_rows()
            self._run_with_retries(self._measure_vth_all_duts, 'post-run Vth measurement')
            self._save_checkpoint(target_cycles)
            self._emit_all_rows()

        if self.cycle_count >= target_cycles:
            self.status = 'complete'
        elif self._stop_requested():
            self.status = 'manual shutdown complete'
        self._emit_all_rows()

    def _run_batch_with_recovery(self, batch_cycles: int) -> Optional[int]:
        # Progress within this batch is only an estimate (elapsed time ×
        # freq_hz) since the firmware only updates its cycle counter once,
        # when the whole batch finishes. Baseline it against this run's
        # cycle count as of the start of this specific batch, so retries of
        # the same batch don't double-count.
        batch_start_cycle_count = self.cycle_count

        def _report_progress(estimated_cycles_in_batch: int):
            self.cycle_count = max(
                self.cycle_count, batch_start_cycle_count + estimated_cycles_in_batch
            )
            self._emit_all_rows()

        for attempt in range(1, int(self.procedure.hardware_retry_count) + 1):
            try:
                completed = self.controller.run_batch(
                    cycles=batch_cycles,
                    freq_hz=self.cfg.freq_hz,
                    duty_cycle=self.cfg.duty_cycle,
                    dut_channels=range(1, self.cfg.num_duts + 1),
                    should_stop=self._stop_requested,
                    on_progress=_report_progress,
                )
                if completed is None:
                    raise RuntimeError('GSS_test returned no cycle count')
                self.last_error = ''
                return self._absolute_cycle_count(completed)
            except Exception as exc:
                self.last_error = str(exc)
                self.status = f'batch retry {attempt}'
                log.warning(f'[{self.cfg.id}] Batch failed (attempt {attempt}): {exc}')
                self._emit_all_rows()
                if self._sleep_interruptible(float(self.procedure.hardware_retry_delay_s)):
                    return None

        while not self._stop_requested():
            self.status = 'waiting for operator'
            log.error(
                f'[{self.cfg.id}] Batch failed after retries; waiting for operator intervention. '
                f'Last error: {self.last_error}'
            )
            self._emit_all_rows()
            if self._sleep_interruptible(float(self.procedure.operator_retry_wait_s)):
                return None
            for attempt in range(1, int(self.procedure.hardware_retry_count) + 1):
                try:
                    completed = self.controller.run_batch(
                        cycles=batch_cycles,
                        freq_hz=self.cfg.freq_hz,
                        duty_cycle=self.cfg.duty_cycle,
                        dut_channels=range(1, self.cfg.num_duts + 1),
                        should_stop=self._stop_requested,
                        on_progress=_report_progress,
                    )
                    if completed is None:
                        raise RuntimeError('GSS_test returned no cycle count')
                    self.last_error = ''
                    return self._absolute_cycle_count(completed)
                except Exception as exc:
                    self.last_error = str(exc)
                    log.warning(f'[{self.cfg.id}] Operator retry failed (attempt {attempt}): {exc}')
                    if self._sleep_interruptible(float(self.procedure.hardware_retry_delay_s)):
                        return None
        return None

    def _refresh_telemetry_with_recovery(self):
        self._run_with_retries(self._update_cycle_count, 'cycle count read')
        self._run_with_retries(self._update_psu_readings, 'PSU readback')
        self._run_with_retries(self._update_temperature, 'TCU readback')

    def _run_with_retries(self, func, label: str) -> bool:
        for attempt in range(1, int(self.procedure.hardware_retry_count) + 1):
            if self._stop_requested():
                return False
            try:
                func()
                self.last_error = ''
                return True
            except Exception as exc:
                self.last_error = str(exc)
                self.status = f'{label} retry {attempt}'
                log.warning(f'[{self.cfg.id}] {label} failed (attempt {attempt}): {exc}')
                self._emit_all_rows()
                if self._sleep_interruptible(float(self.procedure.hardware_retry_delay_s)):
                    return False

        while not self._stop_requested():
            self.status = 'waiting for operator'
            log.error(
                f'[{self.cfg.id}] {label} failed after retries; waiting for operator intervention. '
                f'Last error: {self.last_error}'
            )
            self._emit_all_rows()
            if self._sleep_interruptible(float(self.procedure.operator_retry_wait_s)):
                return False
            for attempt in range(1, int(self.procedure.hardware_retry_count) + 1):
                if self._stop_requested():
                    return False
                try:
                    func()
                    self.last_error = ''
                    return True
                except Exception as exc:
                    self.last_error = str(exc)
                    log.warning(f'[{self.cfg.id}] {label} operator retry failed (attempt {attempt}): {exc}')
                    if self._sleep_interruptible(float(self.procedure.hardware_retry_delay_s)):
                        return False
        return False

    def _stop_requested(self) -> bool:
        return self._stop_event.is_set() or self.procedure.should_stop()

    def _sleep_interruptible(self, seconds: float) -> bool:
        deadline = time.time() + max(0.0, seconds)
        while time.time() < deadline:
            if self._stop_requested():
                return True
            time.sleep(min(self._MIN_SLEEP_S, deadline - time.time()))
        return self._stop_requested()

    def _emit_all_rows(self):
        for dut in range(1, self.cfg.num_duts + 1):
            self._emit_row(dut=dut)

    def _load_checkpoint(self, target_cycles: int):
        if not self.checkpoint_path or not os.path.exists(self.checkpoint_path):
            return
        try:
            with open(self.checkpoint_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if int(data.get('target_cycles', target_cycles)) != target_cycles:
                log.warning(f'[{self.cfg.id}] Ignoring checkpoint with different target cycle count')
                return
            self.cycle_count = int(data.get('cycle_count', self.cycle_count))
            self.batch_number = int(data.get('batch_number', self.batch_number))
            log.info(f'[{self.cfg.id}] Resumed checkpoint at {self.cycle_count} cycles')
        except Exception as exc:
            log.warning(f'[{self.cfg.id}] Could not load checkpoint: {exc}')

    def _save_checkpoint(self, target_cycles: Optional[int] = None):
        if not self.checkpoint_path:
            return
        data = {
            'controller': self.cfg.id,
            'cycle_count': self.cycle_count,
            'target_cycles': target_cycles if target_cycles is not None else int(getattr(self.procedure, 'target_cycles', 0)),
            'batch_number': self.batch_number,
            'status': self.status,
            'last_error': self.last_error,
            'saved_at': datetime.now().isoformat(timespec='seconds'),
        }
        tmp = self.checkpoint_path + '.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self.checkpoint_path)
        except Exception as exc:
            log.warning(f'[{self.cfg.id}] Checkpoint save failed: {exc}')

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

    def _capture_cycle_baseline(self):
        """Snapshot the firmware's raw (lifetime) cycle count right after
        connecting, so later raw counts can be converted into this run's own
        cycle count via :meth:`_absolute_cycle_count`. GSS_cycles never
        resets on its own -- it accumulates across every batch ever run on
        the controller since it was last powered on/reset.
        """
        baseline = None
        try:
            baseline = self.controller.get_cycle_count()
        except Exception as exc:
            log.warning(f'[{self.cfg.id}] Could not read initial cycle count baseline: {exc}')
        self._cycle_baseline = baseline if baseline is not None else 0

    def _absolute_cycle_count(self, raw_count: int) -> int:
        """Convert a raw firmware (lifetime) cycle count into this run's
        logical cycle count."""
        return self._session_start_cycle_count + max(0, raw_count - self._cycle_baseline)

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
            with self.tcu_lock:
                self.tcu.set_temperature(self.cfg.tcu_channel, self.cfg.temperature_c)
                self.tcu.enable_channel(self.cfg.tcu_channel)
            log.info(
                f'[{self.cfg.id}] TCU ch{self.cfg.tcu_channel} → '
                f'{self.cfg.temperature_c} °C'
            )
        except Exception as exc:
            log.warning(f'[{self.cfg.id}] TCU setup failed: {exc}')

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
                self.cycle_count = max(self.cycle_count, self._absolute_cycle_count(count))
            else:
                raise RuntimeError('GSS_cycles returned no parseable cycle count')
        except NotImplementedError:
            pass  # TBD – silently ignore until firmware ready
        except Exception as exc:
            log.warning(f'[{self.cfg.id}] cycle count read error: {exc}')
            raise

    def _update_psu_readings(self):
        if self.psu is not None:
            try:
                with self.psu_lock:
                    v_on = self.psu.get_voltage_setpoint(self.cfg.psu_ch_pos)
                    v_off_raw = self.psu.get_voltage_setpoint(self.cfg.psu_ch_neg)
                if v_on is None or v_off_raw is None:
                    raise RuntimeError('PSU returned incomplete voltage readback')
                self.last_v_on = v_on
                # PSU ch_neg supplies the absolute value; negate to get gate-off
                self.last_v_off = -v_off_raw
            except Exception as exc:
                log.warning(f'[{self.cfg.id}] PSU readback error: {exc}')
                raise
            return

        # No external PSU configured -- fall back to the GSS controller's own
        # onboard gate-supply-rail measurement (measure_supply / ADC5).
        try:
            v_pos, v_neg = self.controller.get_output_voltages()
            if v_pos is None or v_neg is None:
                raise RuntimeError('Controller returned no supply voltage reading')
            self.last_v_on = v_pos
            self.last_v_off = v_neg
        except NotImplementedError:
            pass
        except Exception as exc:
            log.warning(f'[{self.cfg.id}] Controller supply readback error: {exc}')
            raise

    def _update_temperature(self):
        if self.tcu is None:
            return
        try:
            with self.tcu_lock:
                t = self.tcu.get_temperature(self.cfg.tcu_channel)
            if t is not None and not math.isnan(t):
                self.last_temperature = t
            else:
                raise RuntimeError('TCU returned no valid temperature')
        except Exception as exc:
            log.warning(f'[{self.cfg.id}] TCU read error: {exc}')
            raise

    def _select_dut_for_measurement(self, dut: int):
        """Route DUT *dut* while holding exclusive ownership of the SMU path."""
        with self.smu_lock:
            try:
                self.controller.select_dut(dut)
                time.sleep(self._DUT_RELAY_SETTLE_S)
            except NotImplementedError:
                pass  # DUT MUX command is TBD

    def _measure_vth_all_duts(self):
        """Measure all DUTs while exclusively owning the SMU and its routing."""
        vth_current = self.cfg.vth_current_ma * 1e-3
        compliance = self.cfg.vth_compliance_voltage
        method = self.cfg.vth_method
        precond_v = self.cfg.vth_precond_voltage
        threshold_i = vth_current
        coarse_step_v = self.cfg.vth_ramp_step_voltage
        fine_step_v = self.cfg.vth_ramp_fine_step_voltage

        if method == 'ramp_voltage' and coarse_step_v == 0 and fine_step_v == 0:
            log.info(f'[{self.cfg.id}] Vth ramp measurement skipped: both step sizes are 0 V')
            return

        while not self._stop_requested():
            if self.smu_lock.acquire(timeout=1.0):
                break
        else:
            raise RuntimeError('Vth measurement interrupted while waiting for SMU')

        try:
            for dut in range(1, self.cfg.num_duts + 1):
                if self._stop_requested():
                    raise RuntimeError('Vth measurement interrupted by stop request')
                self._select_dut_for_measurement(dut)

                if method == 'ramp_voltage':
                    ramp_start_v = self.cfg.vth_ramp_start_voltage
                    ramp_stop_v = self.cfg.vth_ramp_stop_voltage
                    vth = None
                    if coarse_step_v > 0:
                        coarse_vth = self.smu.measure_vth_ramp(
                            precondition_voltage_v=precond_v,
                            start_voltage_v=ramp_start_v,
                            stop_voltage_v=ramp_stop_v,
                            step_voltage_v=coarse_step_v,
                            threshold_current_a=threshold_i,
                        )
                        if coarse_vth is not None:
                            log.info(
                                f'[{self.cfg.id}] DUT {dut} Vth coarse pass = {coarse_vth:.4f} V '
                                f'(range {ramp_start_v:.4f} to {ramp_stop_v:.4f} V, '
                                f'step {coarse_step_v:.4f} V)'
                            )
                        vth = coarse_vth
                    if coarse_step_v > 0 and fine_step_v > 0 and vth is not None and vth != ramp_stop_v:
                        direction = 1 if ramp_stop_v > ramp_start_v else -1
                        fine_start_v = vth - direction * coarse_step_v
                        fine_stop_v = vth + direction * coarse_step_v
                        fine_start_v = min(max(fine_start_v, min(ramp_start_v, ramp_stop_v)),
                                           max(ramp_start_v, ramp_stop_v))
                        fine_stop_v = min(max(fine_stop_v, min(ramp_start_v, ramp_stop_v)),
                                           max(ramp_start_v, ramp_stop_v))
                        fine_vth = self.smu.measure_vth_ramp(
                            precondition_voltage_v=precond_v,
                            start_voltage_v=fine_start_v,
                            stop_voltage_v=fine_stop_v,
                            step_voltage_v=fine_step_v,
                            threshold_current_a=threshold_i,
                        )
                        if fine_vth is not None:
                            log.info(
                                f'[{self.cfg.id}] DUT {dut} Vth fine pass = {fine_vth:.4f} V '
                                f'(range {fine_start_v:.4f} to {fine_stop_v:.4f} V, '
                                f'step {fine_step_v:.4f} V)'
                            )
                        vth = fine_vth
                    elif coarse_step_v == 0 and fine_step_v > 0:
                        fine_vth = self.smu.measure_vth_ramp(
                            precondition_voltage_v=precond_v,
                            start_voltage_v=ramp_start_v,
                            stop_voltage_v=ramp_stop_v,
                            step_voltage_v=fine_step_v,
                            threshold_current_a=threshold_i,
                        )
                        if fine_vth is not None:
                            log.info(
                                f'[{self.cfg.id}] DUT {dut} Vth fine pass = {fine_vth:.4f} V '
                                f'(range {ramp_start_v:.4f} to {ramp_stop_v:.4f} V, '
                                f'step {fine_step_v:.4f} V)'
                            )
                        vth = fine_vth
                else:
                    self.smu.apply_precondition_voltage(
                        precond_voltage_v=precond_v,
                        duration_s=0.1,
                    )
                    vth = self.smu.measure_vth(
                        force_current_a=vth_current,
                        compliance_voltage_v=compliance,
                    )

                if vth is None:
                    log.warning(f'[{self.cfg.id}] DUT {dut} Vth measurement failed')
                    raise RuntimeError(f'DUT {dut} Vth measurement failed')
                if method == 'ramp_voltage' and (
                    math.isclose(vth, ramp_start_v) or math.isclose(vth, ramp_stop_v)
                ):
                    log.warning(
                        f'[{self.cfg.id}] DUT {dut}: Measured device Vth appears out of range, '
                        'check DUT contact and range settings.'
                    )
                self.last_vth[dut] = vth
                log.info(f'[{self.cfg.id}] DUT {dut} Vth = {vth:.4f} V')
        finally:
            try:
                self._select_dut_for_measurement(0)
            finally:
                self.smu_lock.release()

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
            'Batch': self.batch_number,
            'Status': self.status,
            'Last Error': self.last_error,
        }
        self.result_queue.put(row)


# ---------------------------------------------------------------------------
# Procedure
# ---------------------------------------------------------------------------

class GateStressTest(Procedure):
    """Gate Switching Stress test procedure.

    Manages exactly one GSS controller via a background worker thread. To
    stress multiple controllers at once, queue one GSS experiment per
    controller.
    """

    name = 'Gate Switching Stress (GSS)'
    internal_name = 'Gate_Switching_Stress'
    short_name = 'GSS'
    description = (
        'Long-duration gate switching stress test on one GSS controller, '
        'with optional SMU Vth measurement, PSU control, and temperature control.'
    )

    # ---- Connection parameters (pre-filled by startup dialog) -------------
    # These are ListParameters so the discovered serial numbers appear as a
    # dropdown in the 'New Experiment' dialog.  Choices are populated by
    # update_device_choices() before the main window opens.

    smu_serial = ListParameter('SMU SN', choices=[''])

    # ---- GSS controller ---------------------------------------------------

    gss_serial = ListParameter('GSS Controller SN', choices=[''])

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
        choices=['ramp_voltage', 'force_current'],
        default='ramp_voltage',
    )
    vth_current_ma = FloatParameter(
        'Vth Current', units='mA',
        default=1.0, minimum=0.001, maximum=1000.0,
    )
    vth_precond_voltage = FloatParameter(
        'Vth Precondition Voltage', units='V',
        default=15.0, minimum=0.0, maximum=30.0,
    )
    vth_ramp_start_voltage = FloatParameter(
        'Vth Ramp Start Voltage', units='V',
        default=6.0, minimum=0.0, maximum=30.0,
    )
    vth_ramp_stop_voltage = FloatParameter(
        'Vth Ramp Stop Voltage', units='V',
        default=0.0, minimum=0.0, maximum=30.0,
    )
    vth_ramp_step_voltage = FloatParameter(
        'Vth Ramp Coarse Step Size', units='V',
        default=0.05, minimum=0.0, maximum=10.0,
    )
    vth_ramp_fine_step_voltage = FloatParameter(
        'Vth Ramp Fine Step Size', units='V',
        default=0.001, minimum=0.0, maximum=10.0,
    )
    # vth_current_ma is used as both force current (force_current mode) and
    # threshold current (ramp_voltage mode).
    vth_compliance_voltage = FloatParameter(
        'Vth Compliance Voltage', units='V',
        default=10.0, minimum=0.1, maximum=30.0,
    )

    # ---- PSU --------------------------------------------------------------

    psu_serial = ListParameter('PSU SN', choices=[''])
    psu_ch_neg = IntegerParameter(
        'PSU Channel V_off', default=2, minimum=1, maximum=3,
    )
    psu_ch_pos = IntegerParameter(
        'PSU Channel V_on', default=1, minimum=1, maximum=3,
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

    # FloatParameter is used (instead of IntegerParameter) so the GUI input
    # accepts scientific notation (e.g. "1e12"); Qt's integer spin box is
    # also limited to 32-bit values, which is far too small for cycle counts.
    target_cycles = FloatParameter(
        'Target Cycles', default=1_000_000_000, minimum=1, maximum=1e15,
    )
    batch_duration_min = FloatParameter(
        'Batch Duration', units='min',
        default=60.0, minimum=0.1, maximum=1440.0,
    )
    vth_interval_min = IntegerParameter(
        'Vth Measurement Interval', units='min',
        default=360, minimum=5, maximum=10080,
    )
    pre_start_vth = BooleanParameter(
        'Pre-run Vth Measurement', default=True,
    )
    post_shutdown_vth = BooleanParameter(
        'Post-run Vth Measurement', default=True,
    )

    # ---- Misc -------------------------------------------------------------

    hardware_retry_count = IntegerParameter(
        'Hardware Retry Count', default=3, minimum=1, maximum=10,
    )
    hardware_retry_delay_s = IntegerParameter(
        'Hardware Retry Delay', units='s', default=30, minimum=1, maximum=3600,
    )
    operator_retry_wait_s = IntegerParameter(
        'Operator Retry Wait', units='s', default=300, minimum=10, maximum=86400,
    )
    worker_shutdown_timeout_s = IntegerParameter(
        'Worker Shutdown Timeout', units='s',
        default=120, minimum=5, maximum=7200,
    )
    nas_directory = Parameter(
        'NAS Backup Directory (optional)',
        default='',
    )

    # How often (seconds) the Results file is mirrored to the NAS.
    # Syncs run in a background thread so they never block the stress test.
    _NAS_SYNC_INTERVAL_S: int = 3600

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
        'Batch',
        'Status',
        'Last Error',
    ]

    # String-valued columns that cannot be plotted; APS GUI.py removes these
    # from the plot's X/Y axis dropdowns so selecting them can't error out.
    NON_PLOTTABLE_COLUMNS = ['Controller', 'Status', 'Last Error']

    INPUTS = [
        # ---- GSS Controller ----
        'gss_serial',
        'num_duts',
        'freq_hz',
        'duty_cycle',
        # ---- SMU (Vth Measurement) ----
        'smu_serial',
        'vth_method',
        'vth_current_ma',
        'vth_precond_voltage',
        'vth_ramp_start_voltage',
        'vth_ramp_stop_voltage',
        'vth_ramp_step_voltage',
        'vth_ramp_fine_step_voltage',
        'vth_compliance_voltage',
        'vth_interval_min',
        'pre_start_vth',
        'post_shutdown_vth',
        # ---- PSU (Gate Drive) ----
        'psu_serial',
        'psu_ch_pos',
        'psu_ch_neg',
        'v_gate_on',
        'v_gate_off',
        # ---- TCU (Temperature) ----
        'tcu_serial',
        'tcu_channel',
        'temperature_c',
        # ---- General Settings ----
        'target_cycles',
        'batch_duration_min',
        'hardware_retry_count',
        'hardware_retry_delay_s',
        'operator_retry_wait_s',
        'nas_directory',
    ]
    DISPLAYS = INPUTS

    # Section headline shown above the first input of each group in the GUI.
    # Consumed by APS GUI.py's compact input-panel layout (generic; any
    # procedure can define this).
    INPUT_SECTIONS = {
        'gss_serial': 'GSS Controller',
        'smu_serial': 'SMU (Vth Measurement)',
        'psu_serial': 'PSU (Gate Drive)',
        'tcu_serial': 'TCU (Temperature)',
        'target_cycles': 'General Settings',
    }

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
        from hardware.shared_resources import shared_hardware

        self._workers: List[GSSWorker] = []
        self._result_queue: queue.Queue = queue.Queue()
        # Initialise pools early so shutdown() is always safe even if startup
        # raises an exception before reaching the sections that populate them.
        self._configs: List['ControllerConfig'] = []
        self._psu_pool: Dict[str, Any] = {}
        self._psu_locks: Dict[str, threading.Lock] = {}
        self._tcu_pool: Dict[str, Any] = {}
        self._tcu_locks: Dict[str, threading.Lock] = {}
        self._smu = None
        self._smu_lease = None
        self._psu_leases = {}
        self._tcu_leases = {}
        self._gss_lease = None

        # The GUI (APS GUI.py) computes the exact path of pymeasure's own
        # Results CSV before queuing this procedure and stores it here, so
        # this run's checkpoint file can sit next to it and both can be
        # mirrored to nas_directory. Left empty if "Save data" is unchecked.
        self._results_filepath: str = getattr(self, 'results_filepath', '') or ''
        self._checkpoint_path: str = ''
        if self._results_filepath:
            base, _ext = os.path.splitext(self._results_filepath)
            self._checkpoint_path = base + '.checkpoint.json'
        log.info(f'GSS Results file: {self._results_filepath or "(not saved)"}')

        self._last_nas_sync: float = time.monotonic()
        self._sync_thread: Optional[threading.Thread] = None

        self._apply_connection_parameters()

        # Build the controller config from the individual parameters. Resolve
        # serial numbers to actual connection strings via the device registry.
        # Fall back to the serial string directly to allow manual entry.
        gss_info = _DEVICE_REGISTRY.get(self.gss_serial, {})
        psu_info = _DEVICE_REGISTRY.get(self.psu_serial, {})
        tcu_info = _DEVICE_REGISTRY.get(self.tcu_serial, {})
        gss_port = gss_info.get('port', self.gss_serial)
        psu_resource = psu_info.get('resource', self.psu_serial)
        tcu_port = tcu_info.get('port', self.tcu_serial)
        controller_id = self.gss_serial or gss_port
        self._configs = [ControllerConfig(
            id=controller_id,
            port=gss_port,
            gss_serial=self.gss_serial,
            num_duts=self.num_duts,
            freq_hz=self.freq_hz,
            duty_cycle=self.duty_cycle,
            vth_method=self.vth_method,
            vth_current_ma=self.vth_current_ma,
            vth_precond_voltage=self.vth_precond_voltage,
            vth_ramp_start_voltage=self.vth_ramp_start_voltage,
            vth_ramp_stop_voltage=self.vth_ramp_stop_voltage,
            vth_ramp_step_voltage=self.vth_ramp_step_voltage,
            vth_ramp_fine_step_voltage=self.vth_ramp_fine_step_voltage,
            vth_threshold_current=self.vth_current_ma * 1e-3,
            vth_compliance_voltage=self.vth_compliance_voltage,
            psu_resource=psu_resource,
            psu_serial=self.psu_serial,
            psu_ch_pos=self.psu_ch_pos,
            psu_ch_neg=self.psu_ch_neg,
            v_gate_on=self.v_gate_on,
            v_gate_off=self.v_gate_off,
            tcu_port=tcu_port,
            tcu_serial=self.tcu_serial,
            tcu_channel=self.tcu_channel,
            temperature_c=self.temperature_c,
        )]

        if not gss_port:
            raise ValueError('A GSS controller must be selected')
        self._gss_lease = shared_hardware.claim_exclusive('gss', gss_port)

        # Connect shared SMU — resolve serial number → VISA resource
        self._smu = None
        self._smu_lock = threading.RLock()
        _smu_info = _DEVICE_REGISTRY.get(self.smu_serial, {})
        _smu_resource = _smu_info.get('resource', self.smu_serial)
        if _smu_resource:
            from hardware.keithley_2636 import KeithleySMU
            def _connect_smu():
                smu = KeithleySMU(_smu_resource)
                return smu if smu.connect() else None

            self._smu_lease = shared_hardware.acquire('smu', _smu_resource, _connect_smu)
            if self._smu_lease is not None:
                self._smu = self._smu_lease.device
                self._smu_lock = self._smu_lease.lock
                log.info(f'SMU available: {self._smu.idn}')
            else:
                raise RuntimeError(f'Failed to connect to requested SMU {_smu_resource}')

        # Connect shared PSUs (one object per unique resource string)
        self._psu_pool: Dict[str, Any] = {}         # resource → driver
        self._psu_locks: Dict[str, threading.Lock] = {}  # resource → lock
        for cfg in self._configs:
            if cfg.psu_resource and cfg.psu_resource not in self._psu_pool:
                resource = cfg.psu_resource
                lease = shared_hardware.acquire(
                    'psu', resource, lambda resource=resource: self._connect_psu(resource)
                )
                self._psu_leases[resource] = lease
                self._psu_pool[resource] = lease.device if lease else None
                self._psu_locks[resource] = lease.lock if lease else threading.Lock()
                if lease is None:
                    raise RuntimeError(f'Failed to connect to requested PSU {resource}')

        # Connect shared TCUs (one object per unique port)
        self._tcu_pool: Dict[str, Any] = {}         # port → driver
        for cfg in self._configs:
            if cfg.tcu_port and cfg.tcu_port not in self._tcu_pool:
                port = cfg.tcu_port
                lease = shared_hardware.acquire(
                    'tcu', port, lambda port=port: self._connect_tcu(port)
                )
                self._tcu_leases[port] = lease
                self._tcu_pool[port] = lease.device if lease else None
                self._tcu_locks[port] = lease.lock if lease else threading.Lock()
                if lease is None:
                    raise RuntimeError(f'Failed to connect to requested TCU {port}')

        # Check for conflicting PSU/TCU channel assignments
        self._check_psu_tcu_conflicts(self._configs)

        for cfg in self._configs:
            psu_lease = self._psu_leases.get(cfg.psu_resource)
            if psu_lease is not None:
                psu_lease.reserve_channel(cfg.psu_ch_pos, abs(cfg.v_gate_on), 0.01)
                psu_lease.reserve_channel(cfg.psu_ch_neg, abs(cfg.v_gate_off), 0.01)
            tcu_lease = self._tcu_leases.get(cfg.tcu_port)
            if tcu_lease is not None:
                tcu_lease.reserve_channel(cfg.tcu_channel, cfg.temperature_c, 0.5)

        # Set PSU voltages / TCU temperatures and verify before switching starts
        self._verify_hardware_setup()

        # Build workers
        for cfg in self._configs:
            psu = self._psu_pool.get(cfg.psu_resource)
            psu_lock = self._psu_locks.get(cfg.psu_resource, threading.Lock())
            tcu = self._tcu_pool.get(cfg.tcu_port)
            tcu_lock = self._tcu_locks.get(cfg.tcu_port, threading.Lock())
            worker = GSSWorker(
                cfg=cfg,
                procedure=self,
                result_queue=self._result_queue,
                smu=self._smu,
                smu_lock=self._smu_lock,
                psu=psu,
                psu_lock=psu_lock,
                tcu=tcu,
                tcu_lock=tcu_lock,
                checkpoint_path=self._checkpoint_path,
            )
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
        stop_requested = False
        target_cycles = max(1.0, float(self.target_cycles))

        # Main loop: drain result queue and forward rows to pymeasure + CSV
        while True:
            # Drain any available rows
            while True:
                try:
                    row = self._result_queue.get_nowait()
                except queue.Empty:
                    break

                self.emit('results', row)

            # Report overall progress (pymeasure otherwise only shows 0% and
            # 100%, since nothing else in this procedure emits 'progress').
            done = sum(w.cycle_count for w in self._workers)
            self.emit('progress', max(0.0, min(100.0, 100.0 * done / target_cycles)))

            # Periodic NAS sync (non-blocking background thread)
            if time.monotonic() - self._last_nas_sync >= self._NAS_SYNC_INTERVAL_S:
                self._sync_to_nas(final=False)

            # Check stop conditions
            if self.should_stop():
                if not stop_requested:
                    log.info('GSS manual shutdown requested; waiting for workers to reach batch boundary')
                    for worker in self._workers:
                        worker._stop_event.set()
                    stop_requested = True

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
        except queue.Empty:
            pass

    def shutdown(self):
        """Stop all workers and clean up hardware."""
        log.info('GSS shutdown: stopping workers...')
        for worker in self._workers:
            worker.stop(timeout=self.worker_shutdown_timeout_s)

        # Release PSU channels. Disable only when the final owner exits.
        for resource, lease in self._psu_leases.items():
            if lease is None:
                continue
            psu = lease.device
            configured_channels = sorted({
                ch
                for cfg in self._configs
                if cfg.psu_resource == resource
                for ch in (cfg.psu_ch_pos, cfg.psu_ch_neg)
            })
            try:
                for ch in configured_channels:
                    try:
                        lease.release_channel(
                            ch,
                            on_final=lambda ch=ch: psu.enable_output(ch, False),
                        )
                    except Exception as exc:
                        log.warning(f'PSU {resource} ch{ch} disable failed: {exc}')
                lease.close()
                log.info(f'PSU {resource} lease released')
            except Exception as exc:
                log.warning(f'PSU shutdown error ({resource}): {exc}')

        # Release TCU channels with the same final-owner rule.
        for port, lease in self._tcu_leases.items():
            if lease is None:
                continue
            try:
                channels = sorted({
                    cfg.tcu_channel for cfg in self._configs if cfg.tcu_port == port
                })
                for channel in channels:
                    lease.release_channel(
                        channel,
                        on_final=lambda channel=channel: lease.device.disable_channel(channel),
                    )
                lease.close()
                log.info(f'TCU {port} lease released')
            except Exception as exc:
                log.warning(f'TCU shutdown error ({port}): {exc}')

        # Release the shared SMU connection after any in-flight measurement.
        if self._smu_lease is not None:
            try:
                with self._smu_lease.lock:
                    pass
                self._smu_lease.close()
                log.info('SMU lease released')
            except Exception as exc:
                log.warning(f'SMU release error: {exc}')

        if self._gss_lease is not None:
            self._gss_lease.close()
            log.info('GSS controller lease released')

        # Final sync: mirror the Results file (and checkpoint) to the NAS
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
                lock = self._tcu_locks.get(cfg.tcu_port, threading.Lock())
                with lock:
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
                    lock = self._tcu_locks.get(cfg.tcu_port, threading.Lock())
                    with lock:
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

                deadline_sleep = time.time() + 30.0
                while time.time() < deadline_sleep:
                    if self.should_stop():
                        raise RuntimeError('Test aborted during temperature settling')
                    time.sleep(min(1.0, deadline_sleep - time.time()))
            else:
                # After full wait, do a final check and raise if way off
                for cfg in self._configs:
                    if not cfg.tcu_port:
                        continue
                    tcu = self._tcu_pool.get(cfg.tcu_port)
                    if tcu is None:
                        continue
                    lock = self._tcu_locks.get(cfg.tcu_port, threading.Lock())
                    with lock:
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
            from hardware.ZE_TCUv2 import ZETCUv2
            tcu = ZETCUv2(port)
            if tcu.connect():
                log.info(f'TCU connected on {port}')
                return tcu
            log.error(f'Failed to connect to TCU on {port}')
            return None
        except Exception as exc:
            log.error(f'TCU connect error ({port}): {exc}')
            return None

    # -----------------------------------------------------------------------
    # NAS mirroring
    # -----------------------------------------------------------------------

    def _sync_to_nas(self, final: bool = False) -> None:
        """Copy the Results file (and its checkpoint) to *nas_directory*.

        The Results CSV itself is written directly by pymeasure to the local
        Directory chosen in the GUI, so there is no separate local-cache copy
        step. This just mirrors that one file (plus the checkpoint used to
        resume after a crash/restart) to the NAS, when configured.

        When *final* is ``False`` the copy runs in a background thread so it
        never blocks the stress test.  When *final* is ``True`` (called from
        :meth:`shutdown`) the copy runs synchronously so no data is lost on
        exit.  A background sync that is still running when a new interval
        fires is silently skipped — the next interval will catch up.
        """
        nas = (self.nas_directory or '').strip()
        if not nas or not self._results_filepath:
            return

        if not final:
            if self._sync_thread is not None and self._sync_thread.is_alive():
                log.debug('NAS sync already in progress; skipping this interval')
                return

        sources = [self._results_filepath, self._checkpoint_path]

        def _do_sync():
            try:
                os.makedirs(nas, exist_ok=True)
            except Exception as exc:
                log.warning(f'Cannot create NAS directory {nas!r}: {exc}')
                return
            for src in sources:
                if not src or not os.path.exists(src):
                    continue
                dest = os.path.join(nas, os.path.basename(src))
                try:
                    shutil.copy2(src, dest)
                    log.debug(f'Synced {src} → {dest}')
                except Exception as exc:
                    log.warning(f'NAS sync failed ({src} → {nas!r}): {exc}')
            self._last_nas_sync = time.monotonic()
            log.info(f'NAS sync complete ({nas})')

        if final:
            _do_sync()
        else:
            self._sync_thread = threading.Thread(
                target=_do_sync, name='gss-nas-sync', daemon=True
            )
            self._sync_thread.start()
