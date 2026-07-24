from unittest.mock import patch

from hardware.gss_controller import GSSController
from hardware.rs_hmc8043 import RSHMC8043Controller


class FakePSU:
    def __init__(self):
        self.commands = []

    def write(self, command):
        self.commands.append(command)

    def query(self, command):
        self.commands.append(command)
        return 'ON'


def test_measure_supply_accepts_zero_and_signed_values():
    controller = GSSController('/dev/null')
    controller._send_command = lambda command: 'POS:+-0.34 NEG:4.95\nGSS_CTRL>'

    assert controller.get_output_voltages() == (-0.34, 4.95)

    controller._send_command = lambda command: 'POS:+0.00 NEG:0.00\nGSS_CTRL>'

    assert controller.get_output_voltages() == (0.0, 0.0)


def test_batch_completion_uses_cycle_count_without_status_polling():
    controller = GSSController('/dev/null')
    controller._send_command = lambda command, timeout=None: 'GSS starting: cycles=1\nGSS_CTRL>'
    controller.get_cycle_count = lambda: 123

    def unsupported_status():
        raise AssertionError('status polling is unsupported by this firmware')

    controller.is_running = unsupported_status

    clock = [0.0]
    with patch('hardware.gss_controller.time.time', side_effect=lambda: clock[0]), patch(
        'hardware.gss_controller.time.sleep',
        side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    ):
        assert controller.run_batch(1, 2, 0.5, extra_timeout_s=0) == 123


def test_cycle_count_does_not_match_batch_configuration():
    assert GSSController._extract_cycle_count(
        'GSS starting: cycles=1000, configured_frequency=1000.0 Hz'
    ) is None
    assert GSSController._extract_cycle_count('CYCLES 65006000\nGSS_CTRL>') == 65006000


def test_hmc8043_selects_channel_before_changing_output_state():
    controller = RSHMC8043Controller.__new__(RSHMC8043Controller)
    controller.num_channels = 3
    controller.psu = FakePSU()

    controller.enable_output(2, True)

    assert controller.psu.commands == [
        'INSTrument:NSELect 2',
        'OUTPut:STATe ON',
    ]


def test_hmc8043_selects_channel_before_querying_output_state():
    controller = RSHMC8043Controller.__new__(RSHMC8043Controller)
    controller.num_channels = 3
    controller.psu = FakePSU()

    assert controller.get_output_state(2) is True
    assert controller.psu.commands == [
        'INSTrument:NSELect 2',
        'OUTPut:STATe?',
    ]
