import unittest
from unittest.mock import call, patch

from hardware.ZE_TCUv2 import ZETCUv2


class FakeSerial:
    def __init__(self, *args, **kwargs):
        self.timeout = kwargs['timeout']
        self.write_timeout = kwargs['write_timeout']
        self.writes = []
        self.responses = []
        self.closed = False

    def write(self, data):
        command = data.decode().strip()
        self.writes.append(command)
        replies = {
            '*IDN?': 'Ziemann Engineering, TCUv2, 1, 1.1.0',
            'T? 1': '42.5',
            'T_set 1 55': 'Channel 1 setpoint set to 55',
            'Ch 1 on': 'Channel 1 enabled.',
            'Ch 1 off': 'Channel 1 disabled.',
        }
        self.responses.extend([command, replies[command]])

    def readline(self):
        if not self.responses:
            return b''
        return (self.responses.pop(0) + '\n').encode()

    def close(self):
        self.closed = True

    def reset_input_buffer(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class TCUProtocolTests(unittest.TestCase):
    @patch('hardware.ZE_TCUv2.serial.Serial', autospec=True)
    def test_commands_skip_echoes_and_consume_acknowledgements(self, serial_constructor):
        fake_serial = FakeSerial(timeout=1, write_timeout=1)
        serial_constructor.return_value = fake_serial

        with ZETCUv2('COM9') as tcu:
            tcu.set_temperature(1, 55)
            tcu.enable_channel(1)
            self.assertEqual(tcu.get_temperature(1), 42.5)
            tcu.disable_channel(1)

        self.assertEqual(
            fake_serial.writes,
            ['*IDN?', 'T_set 1 55', 'Ch 1 on', 'T? 1', 'Ch 1 off'],
        )
        self.assertEqual(
            serial_constructor.call_args,
            call('COM9', 38400, timeout=1, write_timeout=1),
        )
        self.assertFalse(fake_serial.responses)
        self.assertTrue(fake_serial.closed)

    @patch('serial.Serial', autospec=True)
    def test_probe_uses_current_identity_command(self, serial_constructor):
        fake_serial = FakeSerial(timeout=0.5, write_timeout=None)
        serial_constructor.return_value = fake_serial

        result = ZETCUv2.probe_port('COM9')

        self.assertEqual(
            serial_constructor.call_args,
            call('COM9', 38400, timeout=0.5),
        )
        self.assertEqual(fake_serial.writes, ['*IDN?'])
        self.assertEqual(
            result,
            {
                'device_type': 'tcu',
                'port': 'COM9',
                'serial': '1',
                'label': 'TCU  SN:1  (COM9)',
            },
        )


if __name__ == '__main__':
    unittest.main()