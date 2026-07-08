"""
Standalone test for KeyithleySMU Vth measurement.

Adjust VISA_RESOURCE and the measurement parameters below, then run:
    python tests/test_5_smu_vth.py

To discover available VISA resources run:
    python -c "import pyvisa; rm=pyvisa.ResourceManager(); print(rm.list_resources())"
"""

import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hardware.keithley_2636 import KeyithleySMU

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s  %(levelname)-8s  %(name)s: %(message)s',
)

# ---------------------------------------------------------------------------
# Configuration — adjust before running
# ---------------------------------------------------------------------------
VISA_RESOURCE = 'USB0::0x05E6::0x2636::XXXXXXXX::INSTR'   # ← your VISA address
CHANNEL = 'a'               # 'a' or 'b' for 2636B; ignored for 2450/2410

# Force-current method (vth_method = 'force_current')
FORCE_CURRENT_A = 1e-3      # vth_current_ma = 1.0 → 1 mA
COMPLIANCE_V    = 10.0      # vth_compliance_voltage

# Ramp-voltage method (vth_method = 'ramp_voltage')
PRECOND_V       = 0.0       # vth_precond_voltage  (0.0 = skip)
START_V         = 6.0       # vth_ramp_start_voltage
STOP_V          = 0.0       # vth_ramp_stop_voltage
STEP_V          = 0.05      # vth_ramp_step_voltage
THRESHOLD_I_A   = 1e-6      # vth_threshold_current
# ---------------------------------------------------------------------------


def main():
    smu = KeyithleySMU(VISA_RESOURCE)

    if not smu.connect():
        print('ERROR: Could not connect to SMU. Check VISA_RESOURCE above.')
        return

    print(f'Connected: {smu.idn}')

    # --- Force-current Vth ---
    print('\n--- Force-current Vth ---')
    vth_fc = smu.measure_vth(
        channel=CHANNEL,
        force_current_a=FORCE_CURRENT_A,
        compliance_voltage_v=COMPLIANCE_V,
    )
    print(f'Vth (force-current, {FORCE_CURRENT_A*1e3:.3g} mA): {vth_fc} V')

    # --- Ramp-voltage Vth ---
    print('\n--- Ramp-voltage Vth ---')
    vth_ramp = smu.measure_vth_ramp(
        channel=CHANNEL,
        precondition_voltage_v=PRECOND_V,
        start_voltage_v=START_V,
        stop_voltage_v=STOP_V,
        step_voltage_v=STEP_V,
        threshold_current_a=THRESHOLD_I_A,
    )
    print(f'Vth (ramp, {START_V} V → {STOP_V} V, I_th={THRESHOLD_I_A:.1e} A): {vth_ramp} V')

    smu.disconnect()
    print('\nDone.')


if __name__ == '__main__':
    main()
