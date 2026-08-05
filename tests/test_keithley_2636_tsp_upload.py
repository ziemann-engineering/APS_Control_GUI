import unittest
from unittest.mock import Mock, patch

from hardware.keithley_2636 import KeithleySMU, SMUError


RAMP_ARGS = ('a', 15.0, 0.1, 0.01, 6.0, 0.0, -0.005, 2.8e-3)


class KeithleyTSPUploadTest(unittest.TestCase):
    def test_constructor_preserves_opaque_visa_resource(self):
        resource = 'USB0::0x05E6::2636::123456\x00\r\n::INSTR'
        smu = KeithleySMU(resource)

        self.assertEqual(smu.resource, resource)

    def test_connect_retries_a_first_open_failure(self):
        first_manager = Mock()
        second_manager = Mock()
        instrument = Mock()
        second_manager.open_resource.return_value = instrument
        instrument.query.return_value = 'Keithley Instruments,2636B,123456,1.0'

        with patch('hardware.keithley_2636.pyvisa.ResourceManager',
                   side_effect=(first_manager, second_manager)), \
                patch('hardware.keithley_2636.time.sleep') as sleep:
            first_manager.open_resource.side_effect = RuntimeError('device not ready')
            smu = KeithleySMU('USB0::INSTR')
            self.assertTrue(smu.connect())

        self.assertEqual(first_manager.open_resource.call_count, 1)
        self.assertEqual(second_manager.open_resource.call_count, 1)
        instrument.query.assert_called_once_with('*IDN?')
        self.assertEqual(smu.idn, 'Keithley Instruments,2636B,123456,1.0')
        sleep.assert_called_once_with(smu._CONNECT_RETRY_DELAY_S)

    def test_pyvisa_py_uses_script_upload_by_default_and_can_fall_back(self):
        smu = KeithleySMU('USB0::INSTR')
        with patch.object(smu, '_is_pyvisa_py_backend', return_value=True), \
                patch.object(smu, '_ramp_vth_tsp_uploaded_script', return_value=3.1), \
                patch.object(smu, '_ramp_vth_tsp_direct', return_value=3.2):
            self.assertEqual(smu._ramp_vth_tsp(*RAMP_ARGS), 3.1)

            smu.use_tsp_script_upload = False
            self.assertEqual(smu._ramp_vth_tsp(*RAMP_ARGS), 3.2)

    def test_uploaded_script_uses_short_writes_and_runs_on_instrument(self):
        smu = KeithleySMU('USB0::INSTR')
        writes = []
        queries = []
        responses = iter(('0', '3.475'))

        def query(command):
            queries.append(command)
            return next(responses)

        with patch.object(smu, '_write', side_effect=writes.append), \
                patch.object(smu, '_query', side_effect=query):
            result = smu._ramp_vth_tsp_uploaded_script(*RAMP_ARGS)

        self.assertEqual(result, 3.475)
        self.assertEqual(writes[0:2], ['errorqueue.clear()', 'loadscript aps_vth_ramp'])
        self.assertEqual(writes[-1], 'endscript')
        self.assertLess(max(map(len, writes)), 512)
        self.assertEqual(queries, ['print(errorqueue.count)', 'aps_vth_ramp()'])

    def test_uploaded_script_reports_tsp_compile_error(self):
        smu = KeithleySMU('USB0::INSTR')
        responses = iter(('1', '-104\t"Malformed script"'))
        with patch.object(smu, '_write'), \
                patch.object(smu, '_query', side_effect=lambda command: next(responses)), \
                self.assertRaisesRegex(SMUError, 'Malformed script'):
            smu._ramp_vth_tsp_uploaded_script(*RAMP_ARGS)


if __name__ == '__main__':
    unittest.main()