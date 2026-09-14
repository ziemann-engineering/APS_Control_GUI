from unittest.mock import Mock, patch

from hardware.rs_nge103 import NGE100
from procedures.GSS import GateStressTest


def test_nge100_retries_transient_visa_open_error():
    resource_manager = Mock()
    resource = Mock()
    resource.query.return_value = 'Rohde&Schwarz,NGE103B,123456,1.0\n'
    resource_manager.open_resource.side_effect = [OSError(5, 'Input/output error'), resource]

    with patch('hardware.rs_nge103.pyvisa.ResourceManager', return_value=resource_manager), patch(
        'hardware.rs_nge103.time.sleep'
    ) as sleep:
        psu = NGE100('USB0::0x0AAD::0x0197::123456::INSTR')
        assert psu.connect()

    assert resource_manager.open_resource.call_count == 2
    sleep.assert_called_once_with(NGE100.CONNECT_RETRY_DELAY_S)
    assert psu.psu is resource
    resource.query.assert_called_once_with('*IDN?')


def test_gss_connects_discovered_hmc8043_without_probing_nge103():
    hmc = Mock()
    hmc.connect.return_value = True

    with patch('hardware.rs_hmc8043.RSHMC8043Controller', return_value=hmc) as hmc_class, patch(
        'hardware.rs_nge103.NGE100'
    ) as nge_class:
        connected = GateStressTest.__new__(GateStressTest)._connect_psu(
            'USB0::HMC::INSTR', 'hmc8043'
        )

    assert connected is hmc
    hmc_class.assert_called_once_with('USB0::HMC::INSTR')
    hmc.connect.assert_called_once_with()
    nge_class.assert_not_called()