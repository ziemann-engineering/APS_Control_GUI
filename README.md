# ZE APS Measurement GUI — User Manual

**Version:** Compatible with APS Control Board 1.3 (firmware build ≥ 2025-10-01)  
**Platform:** Windows (primary), Linux (experimental)  
**Python:** 3.8.9 or later

---

## Table of Contents

1. [Overview](#1-overview)
2. [System Architecture](#2-system-architecture)
3. [Hardware Requirements](#3-hardware-requirements)
4. [Software Installation](#4-software-installation)
5. [First-Time Setup](#5-first-time-setup)
6. [Startup Dialog](#6-startup-dialog)
7. [Main GUI Interface](#7-main-gui-interface)
8. [Procedure Reference](#8-procedure-reference)
   - 8.1 [Random Number Test](#81-random-number-test)
   - 8.2 [Double Pulse Test (DPT)](#82-double-pulse-test-dpt)
   - 8.3 [High Power Pulse Test (HPPT)](#83-high-power-pulse-test-hppt)
   - 8.4 [Gate Switching Stress (GSS)](#84-gate-switching-stress-gss)
9. [Hardware Reference](#9-hardware-reference)
   - 9.1 [APS Control Board](#91-aps-control-board)
   - 9.2 [GSS Controller](#92-gss-controller)
   - 9.3 [Keithley SMU](#93-keithley-smu)
   - 9.4 [R&S NGE103 Power Supply](#94-rs-nge103-power-supply)
   - 9.5 [R&S HMC8043 Power Supply](#95-rs-hmc8043-power-supply)
   - 9.6 [Keysight DSO-S Oscilloscope](#96-keysight-dso-s-oscilloscope)
   - 9.7 [Temperature Control Unit (TCU)](#97-temperature-control-unit-tcu)
10. [Data Management](#10-data-management)
11. [Settings and Configuration](#11-settings-and-configuration)
12. [Log Files](#12-log-files)
13. [Safety](#13-safety)
14. [Troubleshooting](#14-troubleshooting)
15. [Appendix A: APS Controller Command Reference](#appendix-a-aps-controller-command-reference)
16. [Appendix B: VISA Resource String Formats](#appendix-b-visa-resource-string-formats)

---

## 1. Overview

The **ZE APS Measurement GUI** is a PyQt5-based desktop application for automated power-semiconductor device testing. It provides a unified interface to the Ziemann Engineering **APS Control Board** and associated peripheral instruments (power supplies, oscilloscopes, source-measure units, and temperature controllers).

### Key Features

- **Procedure-driven** architecture: select a test procedure at startup and the GUI automatically configures itself for that procedure's hardware requirements.
- **Automatic hardware discovery**: the startup dialog scans all connected VISA instruments and serial ports and populates drop-downs with discovered devices.
- **Real-time data plotting**: results are streamed live to interactive plots as the procedure runs.
- **Persistent settings**: hardware connection strings, data directories, and window layouts are automatically saved and restored between sessions.
- **CSV data export**: every measurement run is saved to a timestamped CSV file.
- **Structured logging**: detailed session logs are written to a `logs/` directory.

### Supported Test Procedures

| Procedure | Short Name | Purpose |
|---|---|---|
| Random Number Test | Random | Software demonstration; no hardware required |
| Double Pulse Test | DPT | Switching characterisation (turn-on / turn-off losses) |
| High Power Pulse Test | HPPT | Repetitive high-power pulse conditioning and measurement |
| Gate Switching Stress | GSS | Long-duration (days/weeks) gate-oxide reliability stress |

---

## 2. System Architecture

```
┌─────────────────────────────────────────────────────┐
│                  APS GUI.py (Main)                  │
│   SettingsManager │ MainWindow (ManagedDockWindow)   │
└──────────┬──────────────────────────────────────────┘
           │ selects & launches
           ▼
┌─────────────────────────────────────────────────────┐
│               startup_dialog.py                      │
│  • Procedure discovery (procedures/ folder)          │
│  • Hardware connection configuration & test         │
│  • VISA/serial device scanning                       │
└──────────┬──────────────────────────────────────────┘
           │ passes procedure instance + connection params
           ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        procedures/                                    │
│  random.py   DPT.py   HPPT.py   GSS.py                               │
│  (pymeasure Procedure subclasses)                                     │
└──────┬───────────────────────┬──────────────────────────┬────────────┘
       │                       │                          │
       ▼                       ▼                          ▼
┌─────────────┐   ┌────────────────────────┐   ┌───────────────────────┐
│  hardware/  │   │  hardware/             │   │  hardware/            │
│APS_control  │   │  keithley_2636.py      │   │  gss_controller.py    │
│   ler.py    │   │  rs_nge103.py          │   │  tcu_driver.py        │
│             │   │  rs_hmc8043.py         │   │                       │
│             │   │  keysight_dso_s.py     │   │                       │
└─────────────┘   └────────────────────────┘   └───────────────────────┘
```

The application is built on [pymeasure](https://pymeasure.readthedocs.io/), which provides the `ManagedDockWindow` base class for the live-plot GUI, the `Procedure` base class for test logic, and built-in CSV data management.

---

## 3. Hardware Requirements

### Minimum (for software demonstration)

- A Windows PC with Python 3.8.9 or later.

### Full System (hardware-in-the-loop testing)

| Component | Model | Interface |
|---|---|---|
| APS Control Board | ZE APS Control Board 1.3 | Serial (COM port, 38 400 baud) |
| GSS Controller | ZE GSS Control Board | Serial (COM port, 38 400 baud) |
| Source-Measure Unit | Keithley 2636B / 2604B / 2450 / 2410 | GPIB or USB/VISA |
| Auxiliary Power Supply | R&S NGE103 or HMC8043 | USB/VISA or Serial/VISA |
| Oscilloscope | Keysight DSO-S series | USB/VISA |
| Temperature Controller | ZE TCU | Serial (COM port) |

> **Note:** Not all instruments are required for every procedure. Refer to the individual procedure sections for the specific instruments needed.

---

## 4. Software Installation

### 4.1 Prerequisites

- Python 3.8.9 or later (Python 3.11 recommended for latest builds)
- pip
- National Instruments VISA runtime or equivalent (for GPIB and USB-VISA instruments)

### 4.2 Install Python Dependencies

Clone or download the repository and install the required packages:

```bash
pip install -r requirements.txt
```

For a pinned, reproducible environment on Python 3.8.9:

```bash
pip install -r requirements_3.8.9_pinned.txt
```

### 4.3 Key Dependencies

| Package | Purpose |
|---|---|
| `PyMeasure >= 0.15` | Experiment framework, GUI base classes |
| `PyQt5` | GUI framework |
| `PyVISA` | VISA instrument communication |
| `pyserial` | Serial (COM port) communication |
| `numpy`, `pandas` | Data processing |
| `pyqtgraph` | Real-time data plotting |
| `toml` | Settings file handling |

### 4.4 Launch the Application

```bash
python "APS GUI.py"
```

On first launch a startup dialog appears; the main window opens once you click **Launch**.

---

## 5. First-Time Setup

1. **Connect hardware** — plug in all instruments via their respective cables (USB, GPIB, COM port).
2. **Install VISA runtime** — for GPIB and USB instruments, install the NI-VISA runtime or equivalent (e.g., R&S VISA, Keysight IO Libraries).
3. **Launch the application** — run `python "APS GUI.py"`.
4. **Select a procedure** — in the startup dialog, choose the desired test procedure from the dropdown.
5. **Enable instruments** — check the **Enable** checkbox for each instrument you want to use.
6. **Enter connection strings** — type the VISA resource string or COM port name for each instrument, or use the auto-discovered values from the dropdown.
7. **Test connections** — click the **Test** button next to each instrument to verify connectivity.
8. **Launch** — click **Launch** to open the main GUI.

---

## 6. Startup Dialog

The startup dialog is the first window that appears when the application starts. It handles procedure selection and hardware configuration.

### 6.1 Procedure Selection

A dropdown at the top of the dialog lists all procedures discovered in the `procedures/` folder. Select the desired test procedure; the hardware configuration panel below will update to show only the instruments required by that procedure.

The selected procedure is persisted in `settings.toml` and pre-selected on the next launch.

### 6.2 Hardware Configuration Panel

For each required instrument, the panel shows:

| Element | Description |
|---|---|
| **Enable** checkbox | Tick to include this instrument in the session. Untick to skip (the instrument will not be initialised). |
| **VISA Resource / COM Port** field | Enter the connection string (see [Appendix B](#appendix-b-visa-resource-string-formats)). Previously used values are restored automatically. |
| **Test** button | Opens a background thread that attempts to connect, queries the instrument ID, and reports success or failure without blocking the UI. |
| Status indicator | Shows the result of the last connection test (green = OK, red = failed). |

> **Tip:** Only one auxiliary PSU type (NGE103 **or** HMC8043) can be enabled at a time. Enabling one automatically disables the other.

### 6.3 Auto-Discovery (GSS Procedure)

For the Gate Switching Stress procedure, the startup dialog scans all serial ports for recognised ZE hardware (GSS controllers, TCU, NGE103 via serial). Discovered devices appear in the dropdowns with their serial numbers as labels.

### 6.4 Launch Button

Clicking **Launch** passes the selected procedure and all connection parameters to the main window and closes the dialog. The main window then opens, connects to the instruments, and is ready to start measurements.

---

## 7. Main GUI Interface

The main window is built on pymeasure's `ManagedDockWindow` and provides the following areas:

### 7.1 Menu Bar

Standard pymeasure menus:

- **File** — Open existing results, manage the results browser.
- **Help** — About dialog.

### 7.2 Input Parameters Panel (left dock)

Displays all configurable parameters for the selected procedure. Values can be edited before starting a new measurement. Connection parameters (serial port, VISA address) are pre-filled from the startup dialog.

Parameters are preserved between runs; the most recent values are restored on the next launch.

### 7.3 Results Table (right dock, lower)

A scrollable table showing every data row emitted by the running procedure. Columns match the procedure's `DATA_COLUMNS` definition.

### 7.4 Live Plot (right dock, upper)

Real-time plot of the measurement data as it streams in. The x-axis and y-axis variables are defined by each procedure (see individual procedure sections). Grid lines are enabled by default.

Multiple results files can be overlaid in the plot by loading them from the file browser.

### 7.5 Status Bar

Shows the current experiment state: *Idle*, *Running*, *Aborted*, or *Finished*.

### 7.6 Control Buttons

| Button | Action |
|---|---|
| **Start** | Begins a new measurement with the current parameter values. |
| **Abort** | Requests a graceful stop of the current measurement. Hardware is cleaned up. |
| **Resume** (if applicable) | Resumes a paused measurement. |

### 7.7 File Selection

At the top of the window, a file path field controls where the CSV output is saved. The default filename is a timestamp (`YYYY-MM-DD_HH-MM-SS`). Accepted extensions are `.csv`, `.txt`, and `.data`.

The **Save data** toggle must be enabled for data to be written to disk.

### 7.8 Data Directory

Below the file field, a directory chooser sets the folder for output files. The selected directory is automatically remembered per-procedure in `settings.toml`.

---

## 8. Procedure Reference

### 8.1 Random Number Test

**Purpose:** Software demonstration and framework testing. No hardware is required.

**File:** `procedures/random.py`

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| Loop Iterations | Integer | 10 | Number of data points to generate |
| Delay Time | Float (s) | 0.2 | Pause between iterations |
| Random Seed | String | 12345 | Seed for the random number generator |

#### Data Output

| Column | Description |
|---|---|
| Iteration | Loop counter (0-based) |
| Random Number 1 | Uniformly distributed random value in [0, 1) |
| Random Number 2 | Uniformly distributed random value in [0, 1) |
| Random Number 3 | Uniformly distributed random value in [0, 1) |

**Plot:** Iteration vs. Random Number 1 and Random Number 2.

#### Procedure

1. Select **Random Number Test** in the startup dialog and click **Launch**.
2. Set the desired parameter values in the Input Parameters panel.
3. Click **Start**. The GUI plots values as they are generated.
4. The run finishes automatically after *Loop Iterations* steps.

---

### 8.2 Double Pulse Test (DPT)

**Purpose:** Characterise the switching behaviour (turn-on and turn-off losses, dV/dt, di/dt) of a power semiconductor device under controlled conditions.

**File:** `procedures/DPT.py`

#### Required Hardware

| Instrument | Role |
|---|---|
| APS Control Board | Generates the double-pulse waveform and controls HV relay / charger |
| Auxiliary PSU (NGE103 or HMC8043) | Supplies gate driver power rails (typically 24 V, 5 V, 20 V) |
| Keysight DSO-S Oscilloscope | Captures voltage and current waveforms during the switching transient |

#### Parameters

| Parameter | Units | Range | Default | Description |
|---|---|---|---|---|
| Test Current | A | 0 – 100 | 10.0 | Target inductor current at turn-off |
| Test Voltage | V | 0 – 2000 | 400.0 | DC link voltage |
| AUX PSU Ch1 (V, A) | — | — | `24.0, 0.5` | Gate driver supply (voltage, current limit) |
| AUX PSU Ch2 (V, A) | — | — | `5.0, 0.1` | Gate driver auxiliary supply |
| AUX PSU Ch3 (V, A) | — | — | `20.0, 0.1` | Additional supply |
| Wait for test completion | Boolean | — | true | Block until APS reports test done |
| Test timeout | s | — | 60.0 | Maximum wait time before abort |

#### Data Output

| Column | Description |
|---|---|
| Timestamp | UNIX timestamp of the data point |
| Pulse | Pulse number (1 = first pulse, 2 = second pulse) |
| Voltage (V) | Measured drain-source voltage |
| Current (A) | Measured drain current |

**Plot:** Timestamp vs. Voltage and Current.

#### DPT Procedure — Step by Step

1. **Prepare the test cell:**
   - Connect the DUT to the APS test cell.
   - Connect the gate driver power supply cables to the auxiliary PSU.
   - Connect oscilloscope probes (voltage and current) to the designated measurement points.
   - Ensure the safety cover is closed and the emergency-off button is released.

2. **Launch the software:**
   - Run `python "APS GUI.py"`.
   - Select **Double Pulse Test (DPT)** in the startup dialog.
   - Enable all required instruments and enter their connection strings.
   - Click **Test** for each instrument to verify connectivity; all must show success.
   - Click **Launch**.

3. **Configure parameters:**
   - Set **Test Voltage** to the desired DC link voltage.
   - Set **Test Current** to the inductor current at which you want to characterise switching.
   - Set the AUX PSU channels to the voltages required by your gate driver circuit.
   - Verify or update the oscilloscope VISA resource.

4. **Start the test:**
   - Enter a descriptive filename in the file field.
   - Select the output directory.
   - Click **Start**.
   - The APS Control Board charges the DC link capacitor, generates the double pulse, and the oscilloscope captures the transient waveforms.
   - Results appear in the table and plot as they are received.

5. **Stop / Abort:**
   - Click **Abort** at any time to request a graceful stop.
   - The APS controller discharges the DC link and disables all outputs before the software exits the procedure.

6. **Data retrieval:**
   - The CSV file is written to the selected directory.
   - Oscilloscope waveform data (if configured) is saved separately by the oscilloscope controller.

---

### 8.3 High Power Pulse Test (HPPT)

**Purpose:** Apply repetitive high-power pulses to a device under test (DUT) and measure conduction or gate-drive parameters after each burst using a Keithley SMU.

**File:** `procedures/HPPT.py`

#### Required Hardware

| Instrument | Role |
|---|---|
| APS Control Board | Generates repetitive HV pulse bursts |
| Keithley SMU (2400/2410) | Measures voltage or current after each burst |
| Auxiliary PSU (NGE103 or HMC8043) | Supplies gate driver power rails |

#### Parameters

| Parameter | Units | Range | Default | Description |
|---|---|---|---|---|
| Test Voltage | V | 0 – 2000 | 100.0 | Pulse voltage applied to DUT |
| DUT On-Time | ns | 14 – 3000 | 100 | Duration of each pulse (rounded to nearest 7 ns) |
| Pulse Period | ms | 0.001 – 1000 | 1.0 | Time between pulse starts |
| Pulse Count | — | 1 – 1 000 000 | 1000 | Total pulses per burst |
| Wait for Gate Measurement | Boolean | — | false | Pause after each burst for SMU measurement |
| Keithley Measurement Voltage | V | — | 20.0 | SMU force-voltage for measurement |
| Wait for test completion | Boolean | — | true | Block until APS reports burst complete |
| AUX PSU Ch1 (V, A) | — | — | `24.0, 0.5` | Gate driver main supply |
| AUX PSU Ch2 (V, A) | — | — | `5.0, 0.1` | Gate driver auxiliary supply |
| AUX PSU Ch3 (V, A) | — | — | `15.0, 0.1` | Additional supply |
| Test timeout | s | — | 300.0 | Maximum wait time per burst |

#### Data Output

| Column | Description |
|---|---|
| Timestamp | UNIX timestamp of the data point |
| Burst | Burst number (increments after each completed burst) |
| Current (A) | SMU-measured current |
| Voltage (V) | SMU-measured voltage |

**Plot:** Burst vs. Current.

#### HPPT Procedure — Step by Step

1. **Prepare the test cell:**
   - Mount the DUT in the APS test fixture.
   - Connect the gate driver supply cables to the auxiliary PSU.
   - Connect the Keithley SMU to the DUT measurement port.
   - Close the safety cover and release the emergency-off button.

2. **Launch the software:**
   - Run `python "APS GUI.py"`.
   - Select **High Power Pulse Test (HPPT)** in the startup dialog.
   - Enable the **APS Controller**, **Keithley SMU**, and the active **Auxiliary PSU** type.
   - Enter the connection strings (COM port for APS, GPIB address for Keithley, VISA string for PSU).
   - Click **Test** for each instrument.
   - Click **Launch**.

3. **Configure parameters:**
   - Set **Test Voltage** to the desired pulse voltage.
   - Set **DUT On-Time** (minimum 14 ns; rounded to nearest 7 ns by firmware).
   - Set **Pulse Period** and **Pulse Count** to define the burst.
   - Enable **Wait for Gate Measurement** if you want an SMU measurement after every burst.
   - Set **Keithley Measurement Voltage** to the voltage the SMU should force during measurement.

4. **Start the test:**
   - Enter a filename and select the output directory.
   - Click **Start**.
   - AUX PSU channels 2 and 3 are enabled automatically when the measurement begins.
   - The APS generates bursts; after each burst the Keithley measures and reports back.
   - Results are plotted in real time.

5. **Stop / Abort:**
   - Click **Abort** to stop after the current burst completes.
   - All PSU channels are disabled and the APS is disconnected during cleanup.

6. **Post-test:**
   - Review the Current vs. Burst plot for trends (degradation or parametric shift).
   - Retrieve the CSV data from the output directory.

---

### 8.4 Gate Switching Stress (GSS)

**Purpose:** Apply long-duration (hours to weeks) repetitive gate switching stress to N devices under test simultaneously, while periodically measuring the threshold voltage (Vth) and logging temperature and supply voltages.

**File:** `procedures/GSS.py`

#### Required Hardware

| Instrument | Role |
|---|---|
| GSS Controller(s) | Generates the repetitive gate waveform for each DUT group |
| Keithley SMU (2636B / 2604B / 2450) | Measures Vth periodically (shared by all controllers) |
| Auxiliary PSU (NGE103 or HMC8043) | Supplies positive and negative gate voltage rails |
| Temperature Controller (TCU) | Controls the DUT temperature (optional) |

#### Parameters

| Parameter | Units | Range | Default | Description |
|---|---|---|---|---|
| SMU | — | discovered serials | — | Serial number of the Keithley SMU to use |
| GSS Controller | — | discovered serials | — | Serial number of the GSS controller for this DUT group |
| DUT Count | — | 1 – 8 | 1 | Number of DUTs connected to this GSS controller |
| Switching Frequency | Hz | 1 000 – 10 000 000 | 100 000 | Gate switching frequency |
| Duty Cycle | — | 0.01 – 0.99 | 0.5 | Gate switching duty cycle |
| Vth Method | — | force_current / ramp_voltage | force_current | Method used to measure threshold voltage |
| Vth Force Current | µA | 0.1 – 10 000 | 250 | Force current for Vth measurement (force_current method) |
| Vth Precondition Voltage | V | 0 – 30 | 0.0 | Gate preconditioning voltage before Vth measurement |
| Vth Threshold Current | nA | 0.001 – 1 000 000 | 1000 | Current threshold for ramp_voltage Vth method |
| Vth Compliance Voltage | V | 0.1 – 30 | 10.0 | SMU compliance (maximum) voltage during Vth measurement |
| PSU | — | discovered serials | — | Serial number of the gate supply PSU |
| PSU Channel V_on | — | 1 – 3 | 1 | PSU channel supplying positive gate voltage |
| PSU Channel V_off | — | 1 – 3 | 2 | PSU channel supplying negative gate voltage |
| V_on (Gate On) | V | 0 – 32 | 15.0 | Positive gate voltage |
| V_off (Gate Off) | V | −32 – 0 | −5.0 | Negative gate voltage (enter as negative) |
| TCU | — | discovered serials | — | Serial number of the temperature controller |
| TCU Channel | — | 1 – 4 | 1 | TCU channel index (1-based) |
| Temperature | °C | −40 – 250 | 25.0 | Target DUT temperature |
| Log Interval | s | 10 – 3600 | 60 | Period between telemetry log entries |
| Vth Measurement Interval | min | 5 – 1440 | 60 | Period between Vth measurements |
| Data Directory | — | — | `data/GSS` | Directory for per-controller CSV files |

#### Data Output (per log entry, per DUT)

| Column | Description |
|---|---|
| Timestamp | UNIX timestamp |
| Controller | Controller identifier string (e.g., "Ctrl1") |
| DUT | DUT index (1-based) |
| Cycles | Total gate switching cycles completed |
| Vth (V) | Last measured threshold voltage |
| Temperature (°C) | Last measured temperature |
| V_on (V) | PSU positive rail voltage readback |
| V_off (V) | PSU negative rail voltage readback |
| Status | Worker status string |

**Plot:** Timestamp vs. Vth (V).

In addition to the pymeasure results table, each controller also writes its own CSV file directly to the **Data Directory** with the naming pattern `GSS_<id>_<YYYY-MM-DD_HH-MM-SS>.csv`.

#### GSS Procedure — Step by Step

1. **Prepare the stress setup:**
   - Mount all DUTs in their test sockets on the GSS board(s).
   - Connect the gate supply PSU positive and negative rails to the correct channels.
   - If temperature control is required, connect the TCU to the thermal chuck or oven and route the thermocouple.
   - Connect the Keithley SMU to the Vth measurement bus.
   - Connect all GSS controller COM ports and the TCU COM port.

2. **Launch the software:**
   - Run `python "APS GUI.py"`.
   - Select **Gate Switching Stress (GSS)** in the startup dialog.
   - The dialog automatically scans serial ports and populates the device dropdowns.
   - Enable all required instruments: **Keithley SMU**, **R&S NGE103 / HMC8043** (PSU), **TCU** (if used).
   - Click **Test** for each to verify connectivity.
   - Click **Launch**.

3. **Configure parameters:**
   - Select the correct devices from the dropdowns (SMU, GSS Controller, PSU, TCU).
   - Set **DUT Count** to the number of devices connected to this controller.
   - Set the switching **Frequency** and **Duty Cycle** for the desired gate stress.
   - Set **V_on** and **V_off** to the gate driver voltage levels.
   - If using temperature control, set **Temperature** and the correct **TCU Channel**.
   - Choose the **Vth Method**: `force_current` forces a constant current and measures gate voltage (standard); `ramp_voltage` sweeps gate voltage and detects the current threshold.
   - Adjust **Log Interval** and **Vth Measurement Interval** as needed.
   - Set the **Data Directory**.

4. **Multi-controller advanced mode (optional):**
   - To run several GSS controllers simultaneously with individual configurations, populate the **Controller Configuration (JSON)** field with a JSON array (see below).
   - When this field is non-empty it takes precedence over the individual parameter fields.

   ```json
   [
     {
       "id": "Ctrl1", "port": "COM5",
       "freq_hz": 100000, "duty_cycle": 0.5,
       "v_gate_on": 15.0, "v_gate_off": -5.0, "num_duts": 4,
       "psu_resource": "ASRL8::INSTR", "psu_ch_pos": 1, "psu_ch_neg": 2,
       "tcu_port": "COM7", "tcu_channel": 1, "temperature_c": 150.0,
       "smu_channel": "a"
     },
     {
       "id": "Ctrl2", "port": "COM6",
       "freq_hz": 200000, "duty_cycle": 0.4,
       "v_gate_on": 18.0, "v_gate_off": -3.0, "num_duts": 2,
       "psu_resource": "ASRL8::INSTR", "psu_ch_pos": 1, "psu_ch_neg": 2,
       "tcu_port": "COM7", "tcu_channel": 2, "temperature_c": 125.0,
       "smu_channel": "b"
     }
   ]
   ```

   Both controllers above share one PSU and one TCU (different channels). The software opens only one VISA/serial connection per unique resource string.

5. **Start the stress test:**
   - Enter a filename and verify the output directory.
   - Click **Start**.
   - The software connects to all instruments, configures the PSU rails, sets the temperature (if TCU enabled), and starts each GSS controller in a dedicated background thread.
   - Telemetry (cycles, temperature, PSU voltage) is logged every **Log Interval** seconds.
   - Vth is measured for every DUT every **Vth Measurement Interval** minutes, one DUT at a time, with the SMU protected by a mutex.

6. **Monitoring a long-duration test:**
   - The live plot shows Vth vs. time for all DUTs.
   - The results table shows one row per log entry per DUT.
   - Per-controller CSV files accumulate in the **Data Directory**.
   - The Status column in the table shows the worker state (`running`, `stopped`, `startup error`, etc.).

7. **Stop / Abort:**
   - Click **Abort** to request a graceful stop.
   - All worker threads stop within the **Worker Shutdown Timeout** seconds.
   - PSU outputs and TCU channels are disabled.
   - All connections are closed cleanly.

---

## 9. Hardware Reference

### 9.1 APS Control Board

**File:** `hardware/APS_controller.py`

The APS Control Board is the central hardware in DPT and HPPT tests. It is connected via a serial (COM) port at 38 400 baud.

#### Connection

| Parameter | Value |
|---|---|
| Baud rate | 38 400 |
| Data bits | 8 |
| Parity | None |
| Stop bits | 1 |
| Flow control | None |

The VISA resource string format `ASRL<n>::INSTR` (where `<n>` is the COM port number) is supported in addition to plain `COM<n>`.

#### Firmware Compatibility

The software validates the connected board on every connection attempt:

- **Board type** must be `"APS Control Board 1.3"`.
- **Build date** must be on or after **1 October 2025**.

If validation fails the connection is rejected and an error is logged.

#### Safety Interlock

The APS system has two safety interlocks:

| Interlock | Description |
|---|---|
| Safety cover | Physical cover over the high-voltage section. Must be `closed` for tests to run. |
| Emergency-off button | Mushroom-head button. Must be `not pressed` for tests to run. |

If either interlock is active when a test is started the software logs an error and refuses to proceed.

#### Supported Test Types

The APS firmware supports the following test types (accessible via the Python API in `APSController`):

| Test | Method | Description |
|---|---|---|
| DPT | `dpt_test(current_a, voltage_v)` | Double Pulse Test |
| COSS | `coss_test()` | Output Capacitance measurement |
| UIS | `uis_test(voltage_v, time_s)` | Unclamped Inductive Switching |
| SCT | `sct_test(voltage_v, time_s, current_a)` | Short Circuit Test |
| CMTI | `cmti_test(test_voltage_v, driver_voltage_v)` | Common Mode Transient Immunity |
| ZCS | `zcs_test(input_voltage_v, output_voltage_v, cycles)` | Zero Current Switching |
| HPPT | `hppt_test(voltage_v, on_time_ns, period_s, pulse_count)` | High Power Pulse Test |
| CGD | `cgd_test(voltage_v, pulse_width_s)` | Gate-Drain Capacitance |
| CGG2A | `cgg2a_test(mode, ramp_time_s, voltage_v)` | Analog Gate-Gate Capacitance |
| CGG2D | `cgg2d_test(mode, ramp_time_s, voltage_v)` | Digital Gate-Gate Capacitance |

---

### 9.2 GSS Controller

**File:** `hardware/gss_controller.py`

The GSS (Gate Switching Stress) Controller is a dedicated board that generates the repetitive gate waveform during stress testing. It communicates via serial at 38 400 baud.

#### Protocol

Same shell-prompt protocol as the APS board: commands terminated with CR+LF, responses end with `>`.

#### Key Commands

| Command | Description |
|---|---|
| `GSS_test <cycles> <freq_hz> <duty>` | Run one batch of switching cycles |
| `GSS_cycles` | Query total cycle count |
| `measure_supply` | Read gate supply voltages (POS/NEG) |
| `measure_DUT <0-8>` | Select DUT for measurement (0 = deselect all) |
| `ID` | Returns board identifier and serial number |
| `status` | Returns running state |
| `stop` | Abort switching between batches |

#### Firmware Compatibility

- **Board type** must be `"GSS Control Board"`.
- **Build date** must be on or after **1 January 2026**.

---

### 9.3 Keithley SMU

**File:** `hardware/keithley_2636.py`

The `KeyithleySMU` class provides a unified interface for Keithley SMUs. The instrument model is auto-detected from the `*IDN?` response.

#### Supported Models

| Model | Family | Channels |
|---|---|---|
| 2636B | TSP (2600-series) | a, b |
| 2604B | TSP (2600-series) | a, b |
| 2450 | SCPI | 1 (channel ignored) |
| 2400 / 2410 | SCPI | 1 (channel ignored) |

#### Vth Measurement Methods

**`force_current` method:**  
Forces a constant current through the gate (default: 250 µA) and measures the resulting gate voltage. The measured voltage is the threshold voltage.

**`ramp_voltage` method:**  
Sweeps gate voltage from the precondition voltage to the compliance voltage in 50 mV steps and detects the voltage at which the gate current exceeds the threshold current.

#### Thread Safety

The driver is **not** internally thread-safe. When the SMU is shared between multiple GSS worker threads, the `GateStressTest` procedure protects it with an external `threading.Lock` (`smu_lock`). User code that calls `measure_vth()` from multiple threads must do the same.

---

### 9.4 R&S NGE103 Power Supply

**File:** `hardware/rs_nge103.py`

The `NGE100` class interfaces with Rohde & Schwarz NGE100-series power supplies (3-channel NGE103).

#### Connection

VISA resource string examples:

- USB: `USB0::0x0AAD::0x0197::103456::INSTR`
- Serial: `ASRL8::INSTR` (for COM8)

#### Usage

The NGE103 is used as the **Auxiliary PSU** in DPT and HPPT, and as the **Gate Supply** in GSS.

| Role | Typical Channels |
|---|---|
| Gate driver main supply (e.g. 24 V for logic) | Ch1 |
| Gate driver negative supply (e.g. −5 V) | Ch2 |
| Auxiliary low-voltage supply | Ch3 |

> **Note:** In GSS, Ch1 is the *positive gate rail* and Ch2 is the *negative gate rail*.  
> The PSU channel number for each rail is configurable via the **PSU Channel V_on** and **PSU Channel V_off** parameters.

---

### 9.5 R&S HMC8043 Power Supply

**File:** `hardware/rs_hmc8043.py`

The `RSHMC8043Controller` class interfaces with the 3-channel R&S HMC8043 power supply.

#### Connection

USB VISA: `USB0::0x0403::0xED72::<serial>::INSTR`

#### Usage

The HMC8043 can be used as an alternative to the NGE103 as the auxiliary power supply. Only one auxiliary PSU type can be enabled at a time. Select the appropriate type in the startup dialog.

---

### 9.6 Keysight DSO-S Oscilloscope

**File:** `hardware/keysight_dso_s.py`

The `KeysightDSOSController` class interfaces with Keysight DSO-S series oscilloscopes.

#### Connection

USB VISA: `USB0::0x2A8D::0x904A::<serial>::INSTR`

#### Usage (DPT)

During a DPT run, the oscilloscope captures voltage and current waveforms at the switching instant. Configure the oscilloscope probes (scale, offset, attenuation, coupling) before starting the test. The software triggers the oscilloscope via the APS controller.

#### Screenshot and Waveform Export

The driver supports:

- `capture_screenshot('filename.png')` — save a PNG/BMP/TIFF of the oscilloscope display.
- `save_waveform_data('filename.h5', channels=[1, 2])` — save waveform data to HDF5.
- `save_waveform_data('filename.mat', channels=[1, 2])` — save waveform data to MATLAB `.mat`.

---

### 9.7 Temperature Control Unit (TCU)

**File:** `hardware/tcu_driver.py`

The `TCUDriver` class wraps the ZE TCU library, which must be installed separately in a known location relative to the workspace.

#### Connection

Serial port: e.g., `COM7` or `/dev/ttyUSB1`.

#### Usage (GSS)

During a GSS stress test, the TCU maintains the DUT at the target temperature. Each GSS worker applies the configured temperature to its assigned TCU channel at startup. The actual temperature is read back and logged every **Log Interval** seconds.

#### TCU Library Location

The driver searches for `TCU.py` in:

1. `../../../../GSS/Python software/TCU lib/` relative to the hardware folder.
2. `e:\Projekte\Ziemann Engineering\Projekte\GSS\Python software\TCU lib`.

If `TCU.py` is not found, the driver logs a warning. `TCUDriver.connect()` will raise `ImportError` at runtime if the TCU is enabled.

---

## 10. Data Management

### 10.1 CSV Output Files

Every measurement run writes a CSV file with:

- One header row listing all `DATA_COLUMNS`.
- One data row per `emit('results', data)` call from the procedure.
- Timestamped filename (default: `YYYY-MM-DD_HH-MM-SS.csv`).

#### Directory per Procedure

Each procedure has its own output directory, configurable via the **Data Directory** field in the main window. The selected directory is persisted in `settings.toml` under `[directories]`.

Default directories from `settings.toml`:

| Procedure | Default Path |
|---|---|
| Random Number Test | `./data/Random` |
| Double Pulse Test | `./data` |
| High Power Pulse Test | `./data` |
| Gate Switching Stress | `./data/GSS` |

### 10.2 GSS Per-Controller CSV

In addition to the pymeasure results file, the GSS procedure writes one CSV file per GSS controller directly to the **Data Directory**:

```
GSS_<controller_id>_<YYYY-MM-DD_HH-MM-SS>.csv
```

These files contain the same columns as `DATA_COLUMNS`, written with Python's `csv.writer`.

### 10.3 Loading Previous Results

Use the results browser in the main window (accessible from the **File** menu or the results list panel) to load and overlay previous CSV files in the live plot.

---

## 11. Settings and Configuration

Application settings are stored in `settings.toml` in the application directory. The file is read on startup and updated automatically as settings change.

### Key Sections

```toml
[gui]
last_procedure = "GateStressTest"  # Procedure selected at last exit

[window]
geometry = "..."                   # Base64-encoded window geometry
state = "..."                      # Base64-encoded dock widget state

[directories]
High_Power_Pulse_Test = "./data"   # Per-procedure output directories
Gate_Switching_Stress = "./data/GSS"

[docks]
# Per-procedure dock layout JSON strings (managed automatically)

[gui.connections.<ProcedureName>.<device_type>]
connection = "COM5"                # Last-used connection string per device

[parameters.<ProcedureName>]
# Last-used parameter values per procedure

[gui.enabled.<ProcedureName>]
nge103_psu = true                  # Which instruments were enabled
keithley_smu = true
```

> **Tip:** If the application fails to start due to a corrupted settings file, delete `settings.toml` and it will be recreated with defaults on the next launch.

---

## 12. Log Files

The application writes detailed log files to the `logs/` directory (created automatically). Each session creates one file named `YYYY-MM-DD_HH-MM-SS.log`.

Log entries include:

- Application startup and procedure selection.
- All instrument connection attempts and results.
- Serial port and VISA communication details.
- Procedure lifecycle events (startup, execute, shutdown).
- Warnings for skipped or unavailable instruments.
- Errors and exceptions with stack traces.

If the `logs/` directory cannot be created (e.g., read-only filesystem), the application falls back to logging to the console (stderr).

---

## 13. Safety

> ⚠️ **High Voltage Warning:** The APS Control Board operates with DC link voltages up to 2000 V. Always follow your facility's high-voltage safety procedures.

### Before Every Test

1. Ensure the **safety cover** is fully closed over the high-voltage section.
2. Ensure the **emergency-off mushroom button** is in the released (not pressed) position.
3. Ensure no personnel are inside or near the high-voltage enclosure.
4. Verify that all connections are secure and that cables are rated for the test voltage.

### APS Safety Interlock

The APS Control Board continuously monitors the safety cover and emergency button. If either interlock is active:

- The firmware refuses to start any test.
- The Python driver (`APSController.is_safe()` / `ensure_safe_state()`) checks the interlock state and logs an error if not safe.
- The procedure will not proceed.

### Emergency Stop

- Press the **emergency-off mushroom button** at any time to immediately disable all APS outputs.
- Click **Abort** in the GUI to request a graceful software stop. The procedure will finish its current operation and then clean up hardware.
- Do **not** power-cycle the APS board while a test is running; use the software abort or the emergency button.

### Post-Test Discharge

The APS controller automatically discharges the DC link capacitor when a test ends or is aborted. Wait for the discharge indicator before opening the safety cover.

---

## 14. Troubleshooting

### Application Does Not Start

| Symptom | Possible Cause | Solution |
|---|---|---|
| `ModuleNotFoundError` | Missing Python package | Run `pip install -r requirements.txt` |
| GUI window appears then immediately closes | Python error during startup | Check the console for a traceback |
| `settings.toml` parse error | Corrupted settings file | Delete `settings.toml` and restart |

### Instrument Connection Failures

| Symptom | Possible Cause | Solution |
|---|---|---|
| APS: "Failed to connect" | Wrong COM port or board not powered | Verify COM port in Device Manager; check APS power LED |
| APS: "Board validation failed" | Wrong firmware version or board type | Update firmware to a build ≥ 2025-10-01 |
| APS: "Safety interlock active" | Cover open or E-stop pressed | Close cover; release E-stop |
| Keithley: `GpibError dev()` | GPIB hardware not present | Verify GPIB cable and NI-VISA installation; check GPIB address |
| NGE103: "Connected to unsupported device" | Wrong VISA resource string | Check VISA resource with NI MAX or `pyvisa.ResourceManager().list_resources()` |
| Oscilloscope: "Failed to connect" | USB not enumerated | Reconnect USB; install Keysight IO Libraries |

### Measurement Issues

| Symptom | Possible Cause | Solution |
|---|---|---|
| DPT: No waveform data | Oscilloscope trigger not configured | Configure trigger level and source on the oscilloscope manually |
| HPPT: Current always 0 | Keithley not connected or wrong mode | Check Keithley connection and verify measurement voltage is non-zero |
| GSS: Vth measurement always NaN | SMU busy or DUT routing not connected | Check that `smu_lock` timeout is sufficient; verify DUT measurement cable |
| GSS: "startup error" in Status column | Controller not powered or wrong port | Check GSS controller power and COM port assignment |

### Checking VISA Resources

To list all available VISA resources from a Python prompt:

```python
import pyvisa
rm = pyvisa.ResourceManager()
print(rm.list_resources())
```

---

## Appendix A: APS Controller Command Reference

The following commands are sent to the APS Control Board via serial. They are wrapped by the `APSController` Python class but can also be sent manually via a serial terminal.

### System Commands

| Command | Description |
|---|---|
| `status` | Returns test running state, safety cover state, and E-stop state |
| `stop` | Stops any running test immediately |
| `reset` | Performs a software reset of the APS board |
| `selftest` | Toggles LEDs and outputs for visual verification |
| `start` | Sends the start command (used after configuration) |
| `info` | Returns board type, build date, kernel version, and other info |

### Hardware Control Commands

| Command | Description |
|---|---|
| `relays connect on/off` | Controls the connect relay |
| `relays charge on/off` | Controls the charge relay |
| `optical DUT/LV/HV on/off` | Controls optical isolation outputs |
| `DUT_test` | Performs DUT gate test |
| `psu LV/HV on/off/measure/setup` | Controls on-board PSUs |

### Test Commands

| Command | Example | Description |
|---|---|---|
| `DPT_test` | `DPT_test 50A 1200V` | Run DPT at 50 A, 1200 V |
| `DPT_parameter` | `DPT_parameter R_DUT 0.025` | Set/read DPT parameter |
| `COSS_test` | `COSS_test` | Run COSS test |
| `UIS_test` | `UIS_test 100V 50e-6s` | Run UIS at 100 V, 50 µs |
| `SCT_test` | `SCT_test 600V 10e-6s` | Run SCT at 600 V, 10 µs |
| `CMTI_test` | `CMTI_test 1200V 15V` | Run CMTI at 1200 V, 15 V driver |
| `ZCS_test` | `ZCS_test 800V 12V 1000` | Run ZCS at 800 V in, 12 V out, 1000 cycles |
| `HPPT_test` | `HPPT_test 1200V 100ns 0.001s 1000 0` | Run HPPT |
| `CGD_test` | `CGD_test 100V 10e-6s` | Run CGD test |
| `CGG2A_test` | `CGG2A_test RF 10e-6s 100V` | Run CGG2A |
| `CGG2D_test` | `CGG2D_test RF 10e-6s 100V 1e-6s` | Run CGG2D |

### Communication Protocol

- **Baud rate:** 38 400
- **Command terminator:** CR+LF (`\r\n`)
- **Response terminator:** Shell prompt character `>`
- **Encoding:** ASCII

---

## Appendix B: VISA Resource String Formats

| Interface | Format | Example |
|---|---|---|
| USB | `USB0::<VID>::<PID>::<serial>::INSTR` | `USB0::0x2A8D::0x904A::MY58150189::INSTR` |
| GPIB | `GPIB::<address>` | `GPIB::24` |
| Serial (Windows VISA) | `ASRL<n>::INSTR` | `ASRL5::INSTR` (= COM5) |
| Serial (Linux VISA) | `ASRL/dev/ttyUSB0::INSTR` | `ASRL/dev/ttyUSB0::INSTR` |
| Direct serial (APS) | `COM<n>` | `COM5` |
| Direct serial (Linux) | `/dev/ttyUSB<n>` | `/dev/ttyUSB0` |

> The `APSController` driver automatically converts `ASRL<n>::INSTR` to `COM<n>` on Windows, so either format can be used in the startup dialog.

To discover VISA resource strings for connected instruments:

```python
import pyvisa
rm = pyvisa.ResourceManager()
for resource in rm.list_resources():
    try:
        inst = rm.open_resource(resource)
        print(resource, '->', inst.query('*IDN?').strip())
        inst.close()
    except Exception:
        pass
```

---

*ZE APS Measurement GUI — User Manual*  
*© Ziemann Engineering*
