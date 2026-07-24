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
    controller.is_running = lambda: (_ for _ in ()).throw(
        AssertionError('status polling is unsupported by this firmware')
    )

    assert controller.run_batch(1, 10_000, 0.5, extra_timeout_s=0) == 123


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
