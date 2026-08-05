import logging
import queue
import threading

from procedures.GSS import ControllerConfig, GSSWorker


class FakeProcedure:
    vth_method = 'ramp_voltage'
    target_cycles = 1
    batch_duration_min = 1
    vth_interval_min = 60
    pre_start_vth = False
    post_shutdown_vth = False
    hardware_retry_count = 1
    hardware_retry_delay_s = 1
    operator_retry_wait_s = 1

    @staticmethod
    def should_stop():
        return False


class BlockingSMU:
    def __init__(self, events):
        self.events = events
        self.measurement_started = threading.Event()
        self.release_measurement = threading.Event()
        self.calls = 0

    def measure_vth_ramp(self, **kwargs):
        self.calls += 1
        self.events.append(f'measure-{self.calls}')
        if self.calls == 1:
            self.measurement_started.set()
            assert self.release_measurement.wait(timeout=2.0)
        return 3.5


class FakeGSSController:
    def __init__(self, name, events):
        self.name = name
        self.events = events
        self.selected = []

    def select_dut(self, dut):
        self.selected.append(dut)
        self.events.append(f'{self.name}-select-{dut}')

    def run_batch(self, **kwargs):
        self.events.append(f'{self.name}-switch')
        return 1

    def get_cycle_count(self):
        return 1

    def get_output_voltages(self):
        return 15.0, -5.0


def make_worker(name, smu, smu_lock, events):
    worker = GSSWorker(
        cfg=ControllerConfig(id=name, port=name),
        procedure=FakeProcedure(),
        result_queue=queue.Queue(),
        smu=smu,
        smu_lock=smu_lock,
    )
    worker.controller = FakeGSSController(name, events)
    return worker


def test_smu_lock_covers_dut_selection_measurement_and_deselection():
    events = []
    smu = BlockingSMU(events)
    smu_lock = threading.RLock()
    first = make_worker('GSS-A', smu, smu_lock, events)
    second = make_worker('GSS-B', smu, smu_lock, events)

    first_thread = threading.Thread(target=first._measure_vth_all_duts)
    second_thread = threading.Thread(target=second._measure_vth_all_duts)
    first_thread.start()
    assert smu.measurement_started.wait(timeout=1.0)

    second_thread.start()
    assert not second.controller.selected

    smu.release_measurement.set()
    first_thread.join(timeout=2.0)
    second_thread.join(timeout=2.0)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert first.controller.selected == [1, 0]
    assert second.controller.selected == [1, 0]
    assert events.index('GSS-A-select-0') < events.index('GSS-B-select-1')


def test_switching_does_not_wait_for_another_controllers_vth_lock():
    events = []
    smu = BlockingSMU(events)
    smu_lock = threading.RLock()
    worker = make_worker('GSS-A', smu, smu_lock, events)

    smu_lock.acquire()
    try:
        switching = threading.Thread(target=worker._run_batches)
        switching.start()
        switching.join(timeout=1.0)

        assert not switching.is_alive()
        assert 'GSS-A-switch' in events
        assert worker.controller.selected == []
    finally:
        smu_lock.release()


def test_ramp_vth_uses_coarse_then_fine_sweeps_with_precondition(caplog):
    class RecordingSMU:
        def __init__(self):
            self.calls = []

        def measure_vth_ramp(self, **kwargs):
            self.calls.append(kwargs)
            return 3.5 if len(self.calls) == 1 else 3.472

    events = []
    smu = RecordingSMU()
    worker = make_worker('GSS-A', smu, threading.RLock(), events)

    caplog.set_level(logging.INFO, logger='procedures.GSS')
    worker._measure_vth_all_duts()

    assert smu.calls == [
        {
            'precondition_voltage_v': 15.0,
            'start_voltage_v': 6.0,
            'stop_voltage_v': 0.0,
            'step_voltage_v': 0.05,
            'threshold_current_a': 1e-3,
        },
        {
            'precondition_voltage_v': 15.0,
            'start_voltage_v': 3.55,
            'stop_voltage_v': 3.45,
            'step_voltage_v': 0.001,
            'threshold_current_a': 1e-3,
        },
    ]
    assert worker.last_vth == {1: 3.472}
    assert (
        '[GSS-A] DUT 1 Vth coarse pass = 3.5000 V '
        '(range 6.0000 to 0.0000 V, step 0.0500 V)'
    ) in caplog.messages
    assert (
        '[GSS-A] DUT 1 Vth fine pass = 3.4720 V '
        '(range 3.5500 to 3.4500 V, step 0.0010 V)'
    ) in caplog.messages


def test_ramp_vth_skips_fine_sweep_when_coarse_sweep_reaches_endpoint(caplog):
    class RecordingSMU:
        def __init__(self):
            self.calls = []

        def measure_vth_ramp(self, **kwargs):
            self.calls.append(kwargs)
            return kwargs['stop_voltage_v']

    events = []
    smu = RecordingSMU()
    worker = make_worker('GSS-A', smu, threading.RLock(), events)

    caplog.set_level(logging.WARNING, logger='procedures.GSS')
    worker._measure_vth_all_duts()

    assert len(smu.calls) == 1
    assert worker.last_vth == {1: 0.0}
    assert (
        '[GSS-A] DUT 1: Measured device Vth appears out of range, '
        'check DUT contact and range settings.'
    ) in caplog.messages


def test_ramp_vth_can_skip_either_configured_pass():
    class RecordingSMU:
        def __init__(self):
            self.calls = []

        def measure_vth_ramp(self, **kwargs):
            self.calls.append(kwargs)
            return 3.5

    events = []
    smu = RecordingSMU()
    worker = make_worker('GSS-A', smu, threading.RLock(), events)
    worker.cfg.vth_ramp_fine_step_voltage = 0

    worker._measure_vth_all_duts()

    assert [call['step_voltage_v'] for call in smu.calls] == [0.05]

    smu.calls.clear()
    worker.cfg.vth_ramp_step_voltage = 0
    worker.cfg.vth_ramp_fine_step_voltage = 0.001

    worker._measure_vth_all_duts()

    assert [call['step_voltage_v'] for call in smu.calls] == [0.001]

    smu.calls.clear()
    worker.cfg.vth_ramp_fine_step_voltage = 0

    worker._measure_vth_all_duts()

    assert smu.calls == []
