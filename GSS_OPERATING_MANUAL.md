# GSS Test Setup Operating Manual

**Applies to:** ZE APS Measurement GUI, Gate Switching Stress (GSS) procedure

## 1. Setup Overview

The GSS setup performs long-duration gate switching stress on up to eight devices under test (DUTs) connected to one GSS controller. One queued GSS experiment controls exactly one controller. Run multiple controllers by queuing one experiment per controller; shared instruments are serialized by the software.

### Hardware

| Component | Required | Purpose | Connection |
|---|---:|---|---|
| GSS Control Board | Yes | Generates gate switching and routes a DUT to the Vth measurement path | Serial COM port, 38 400 baud |
| DUTs and GSS fixture | Yes | Devices under test; up to 8 per controller | Fixture wiring |
| Keithley 2636B, 2604B, or 2450 SMU | Optional | Periodic threshold-voltage (Vth) measurements | USB/GPIB through VISA |
| R&S NGE103B or HMC8043 PSU | Optional | Positive and negative gate rails | USB, serial, or GPIB through VISA |
| ZE TCU | Optional | DUT temperature control and readback | Serial COM port |
| PC | Yes | Runs the GUI and stores results | USB/serial/VISA interfaces |

When no external PSU is selected, the procedure attempts to log the controller's onboard gate-rail readback. With no SMU or TCU selected, Vth or temperature values respectively remain unavailable in the results.

### Software

The PC runs `APS GUI.py`, a PyQt/PyMeasure application. Select **Gate Switching Stress (GSS)** in its startup dialog. The dialog scans serial ports for GSS controllers and TCUs and scans VISA resources for supported Keithley SMUs and PSUs. Device selections are passed to the GSS procedure by serial number.

The procedure creates one Results CSV in the GUI's selected local directory and a JSON checkpoint beside it. The checkpoint permits a restarted run with the same target cycle count to continue from its saved logical cycle count. An optional NAS directory receives copies of both files every hour and during shutdown.

### Safety and Preconditions

Only trained personnel should connect or change DUT, PSU, or fixture wiring. Before a run, verify polarity, current limits, DUT seating, thermal connections, and the intended voltage/frequency limits for the DUT and fixture. Do not connect or disconnect DUT wiring while gate rails are enabled. Use **Abort** before changing a setup.

## 2. Normal Operation

1. With outputs disabled, install DUTs and connect the GSS controller, optional PSU, optional TCU, and optional SMU.
2. Start `APS GUI.py`, choose **Gate Switching Stress (GSS)**, then select **Scan All Devices**.
3. Confirm detected hardware and use **Test** for every selected device. Apply an offered GSS firmware update before the run if required.
4. Launch the main window and select the GSS controller and any SMU, PSU, and TCU by serial number.
5. Set the stress, measurement, supply, temperature, logging, and storage parameters; choose a unique local output filename and directory.
6. Check fixture wiring, rail channels, setpoints, and DUT count against the physical setup.
7. Select **Start**. The procedure configures enabled PSUs and TCUs, validates setup where supported, then begins switching batches.
8. Monitor the live plot, results table, `Status`, and `Last Error` columns. Preserve the local CSV and checkpoint throughout a long test.
9. At completion, or when stopping is necessary, select **Abort** and wait for cleanup to finish before handling hardware.

## 3. Software Features and Parameters

### Operating Behavior

- **Switching:** The controller receives batches of gate cycles. Batch length is calculated from switching frequency and batch duration, limited by target cycles. Progress within a batch is estimated; the controller reports its accumulated count after a batch.
- **Vth measurement:** With an SMU selected, every DUT may be measured before the run, periodically between batches, and after shutdown. The controller selects each DUT and the SMU is locked so parallel GSS runs cannot use it concurrently.
- **Gate rails and temperature:** An enabled PSU has selected positive and negative channels set to the requested absolute voltage with a 1 A current limit. An enabled TCU is set to its selected channel and temperature. Shared-channel requests must be compatible.
- **Data and recovery:** One row per DUT contains timestamp, controller, DUT, cycles, Vth, temperature, positive/negative rail values, batch number, status, and last error. Checkpoints are updated after batches and Vth measurements.
- **Retry handling:** Hardware actions retry by the configured count and delay. Repeated failure changes status to `waiting for operator` and retries at the operator-retry interval until recovery or abort.
- **Stop behavior:** **Abort** requests a stop at the next controllable point. The controller is stopped, owned PSU/TCU channels are disabled when their final user exits, and instrument connections are released. An enabled post-run Vth measurement may still run.
- **Parallel operation:** Queue one experiment per controller and use a distinct output filename/local directory per run. GSS controllers are exclusive; an SMU, PSU, or TCU can be shared only with compatible channel settings.

### Settable Parameters

All entries below are set in the GSS New Experiment/Input panel. Device lists are populated by discovery; a blank optional device leaves it unused.

| Group | Parameter | Default | Allowed values | Effect |
|---|---|---:|---|---|
| Controller | GSS Controller SN | none | Discovered controller serial | Selects the required controller. |
| Controller | DUT Count | 1 | 1 to 8 | Number of fixture DUT channels switched and measured. |
| Controller | Switching Frequency | 100000 Hz | 1000 to 10000000 Hz | Gate-switching frequency. |
| Controller | Duty Cycle | 0.5 | 0.01 to 0.99 | Active fraction of each cycle. |
| SMU | SMU SN | none | Discovered Keithley serial | Enables Vth measurement. |
| SMU | Vth Method | `ramp_voltage` | `ramp_voltage`, `force_current` | Selects voltage ramp or forced-current Vth measurement. |
| SMU | Vth Current | 1.0 mA | 0.001 to 1000 mA | Forced current or ramp threshold current. |
| SMU | Vth Precondition Voltage | 15.0 V | 0 to 30 V | Gate preconditioning voltage before Vth measurement. |
| SMU | Vth Ramp Start Voltage | 6.0 V | 0 to 30 V | Ramp start voltage. |
| SMU | Vth Ramp Stop Voltage | 0.0 V | 0 to 30 V | Ramp stop voltage. |
| SMU | Vth Ramp Coarse Step Size | 0.05 V | 0 to 10 V | Coarse increment; 0 skips coarse pass. |
| SMU | Vth Ramp Fine Step Size | 0.001 V | 0 to 10 V | Fine increment; 0 skips refinement. If coarse is 0, this is the full-range step. |
| SMU | Vth Compliance Voltage | 10.0 V | 0.1 to 30 V | SMU compliance in forced-current mode. |
| SMU | Vth Measurement Interval | 360 min | 5 to 10080 min | Period between Vth measurements. |
| SMU | Pre-run Vth Measurement | enabled | enabled/disabled | Measure each DUT before switching. |
| SMU | Post-run Vth Measurement | enabled | enabled/disabled | Measure each DUT after switching or abort. |
| PSU | PSU SN | none | Discovered NGE103B or HMC8043 serial | Selects external gate-supply PSU. |
| PSU | PSU Channel V_on | 1 | 1 to 3 | Channel supplying positive gate rail. |
| PSU | PSU Channel V_off | 2 | 1 to 3 | Channel supplying negative gate rail. |
| PSU | V_on (Gate On) | 15.0 V | 0 to 32 V | Positive gate-rail setpoint. |
| PSU | V_off (Gate Off) | -5.0 V | -32 to 0 V | Negative gate-rail setpoint; enter a negative value. |
| TCU | TCU | none | Discovered TCU serial | Enables temperature control/readback. |
| TCU | TCU Channel | 1 | 1 to 4 | TCU output/measurement channel. |
| TCU | Temperature | 25.0 degC | -40 to 250 degC | Target temperature for selected TCU channel. |
| General | Target Cycles | 1000000000 | 1 to 1e15 | Logical number of cycles at which the run completes. |
| General | Batch Duration | 60 min | 0.1 to 1440 min | Nominal duration of one controller command batch. |
| General | Hardware Retry Count | 3 | 1 to 10 | Attempts before waiting for operator intervention. |
| General | Hardware Retry Delay | 30 s | 1 to 3600 s | Wait between automatic retries. |
| General | Operator Retry Wait | 300 s | 10 to 86400 s | Wait between retry groups while waiting for operator. |
| General | NAS Backup Directory (optional) | empty | Existing writable path | Destination for hourly/final CSV and checkpoint copies. |

The current procedure emits rows at run state changes, retry/progress polls, Vth measurements, and batch completion. The worker shutdown timeout is an internal 120-second default and is not displayed in the normal input panel.

## 4. Setup and Update Instructions

### PC Software Setup

1. Install Python 3.8 or later, Git, and an appropriate VISA runtime for USB/GPIB instruments, such as NI-VISA, R&S VISA, or Keysight IO Libraries.
2. Open PowerShell in the project directory and run:

   ```powershell
   .\deploy_windows.ps1
   ```

   The script updates or clones the project, creates `.venv`, installs `requirements.txt`, and verifies PyQt5, pyqtgraph, PyMeasure, and PyVISA imports.
3. Launch the application with:

   ```powershell
   .\.venv\Scripts\python.exe 'APS GUI.py'
   ```

On Linux/Pi OS, run `./deploy_pi_os.sh setup` for a new installation. It installs Python, PyQt5, VISA/USB prerequisites, `dfu-util`, USB access rules, and Python dependencies. Run `./deploy_pi_os.sh update` to update an existing Git checkout and Python packages; it does not redo OS configuration.

### Hardware Setup

1. Install the GSS controller and DUT fixture with all power outputs disabled.
2. Connect the controller and optional TCU to separate serial ports.
3. Connect the optional PSU and SMU through VISA-capable USB/GPIB or serial interfaces.
4. Start the GUI, select GSS, scan all devices, and test each selected device.
5. Record controller serial number, fixture channel allocation, PSU channels, and instrument resource addresses in the test record before a long run.

### GSS Firmware Update

The GSS scan compares the controller build date reported by `*IDN?` with firmware `.bin` files in the local `firmware` directory. When a newer file is available, the GUI offers an update.

1. End or abort any active GSS run and ensure only the intended controller is connected in DFU mode.
2. Install `dfu-util` and verify `dfu-util --version` succeeds. On Linux, `deploy_pi_os.sh setup` installs it. On Windows, install it separately and add it to `PATH`.
3. Place the approved GSS `.bin` in the project's `firmware` directory. The Linux deployment script downloads `GSS_CONTROL.bin` automatically when online.
4. Select GSS in the startup dialog, scan devices, accept the offered update, and wait for the controller to restart. The GUI enters DFU mode, waits for the USB bootloader, and flashes the selected binary.
5. Scan again and confirm the controller is identified as `GSS Control Board` with the intended firmware version/build date. Test it before starting a stress run.

If the updater reports that the controller reset before `dfu-util` received final status, the GUI treats the transfer as successful. If no DFU bootloader is found, check USB, `dfu-util` on `PATH`, and that only the intended controller entered DFU mode.