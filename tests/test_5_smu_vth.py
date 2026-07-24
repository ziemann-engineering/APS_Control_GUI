"""
Standalone test for KeithleySMU Vth measurement.

Adjust VISA_RESOURCE and the measurement parameters below, then run:
    python tests/test_5_smu_vth.py

To discover available VISA resources run:
    python -c "import pyvisa; rm=pyvisa.ResourceManager(); print(rm.list_resources())"
"""

import logging
import os
import sys
from pathlib import Path

import pyvisa

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hardware.keithley_2636 import KeithleySMU

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s  %(levelname)-8s  %(name)s: %(message)s',
)

# ---------------------------------------------------------------------------
# Configuration — adjust before running
# ---------------------------------------------------------------------------
# Set APS_SMU_RESOURCE to force a particular address.  Leave this as None to
# discover a supported Keithley automatically.  USB resource names can differ
# between VISA backends / operating systems, so do not copy a Windows address
# to the Raspberry Pi without verifying it first.
VISA_RESOURCE = os.environ.get('APS_SMU_RESOURCE') or None
CHANNEL = 'a'               # 'a' or 'b' for 2636B; ignored for 2450/2410

# Force-current method (vth_method = 'force_current')
FORCE_CURRENT_A = 2.8e-3      # vth_current_ma = 1.0 → 1 mA
COMPLIANCE_V    = 10.0      # vth_compliance_voltage

# Ramp-voltage method (vth_method = 'ramp_voltage')
PRECOND_V       = 20       # vth_precond_voltage  (0.0 = skip)
START_V         = 6.0       # vth_ramp_start_voltage
STOP_V          = 0.0       # vth_ramp_stop_voltage
STEP_V          = 0.005      # vth_ramp_step_voltage
THRESHOLD_I_A   = 2.8e-3      # vth_threshold_current
# ---------------------------------------------------------------------------


def find_smu_resource():
    """Return a VISA resource for a supported Keithley SMU, if one is found."""
    if VISA_RESOURCE:
        return VISA_RESOURCE

    try:
        rm = pyvisa.ResourceManager()
        resources = rm.list_resources()
    except Exception as exc:
        print(f'ERROR: Cannot open the VISA backend: {exc}')
        return None

    if not resources:
        print('ERROR: VISA reported no resources.')
        print('Check that this is the same Python environment used by the GUI,')
        print('that pyvisa-py and pyusb are installed, and that the current user')
        print('has permission to access the USB device.')
        rm.close()
        return None

    print(f'VISA resources: {", ".join(resources)}')
    try:
        for resource in resources:
            try:
                with rm.open_resource(resource) as instrument:
                    instrument.timeout = 3_000
                    idn = instrument.query('*IDN?').strip()
                print(f'  {resource} -> {idn}')
                if any(model in idn.upper() for model in ('2636', '2604', '2602', '2450', '2410')):
                    return resource
            except Exception as exc:
                print(f'  {resource} -> unavailable ({exc})')
    finally:
        rm.close()

    print('ERROR: No supported Keithley SMU was found in the VISA resources above.')
    return None


def main():
    resource = find_smu_resource()
    if not resource:
        return

    print(f'Using VISA resource: {resource}')
    smu = KeithleySMU(resource)

    if not smu.connect():
        print('ERROR: Could not connect to SMU. See the log above for the VISA error.')
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
