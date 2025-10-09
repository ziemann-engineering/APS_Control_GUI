# Import necessary packages
from pymeasure.instruments.keithley import Keithley2400
from pymeasure.instruments.resources import list_resources
from pymeasure.instruments import find_serial_port

import numpy as np
import pandas as pd
from time import sleep

# Set the input parameters
data_points = 50
averages = 10
max_current = 0.001
min_current = -max_current

# Set source_current and measure_voltage parameters
voltage = 20 # V
compliance_current = 0.1  # A
measure_nplc = 1  # Number of power line cycles
current_range = 1e-6  # A

# Connect and configure the instrument
# 2470 via USB: USB0::0x05e6::0x2470::[serial number]::INSTR

list_resources()

resource_name = find_serial_port(vendor_id=0x05e6, product_id=0x2470, serial_number="sn56X")

sourcemeter = Keithley2400("USB0::0x05e6::0x2470::[serial number]::INSTR")
sourcemeter.reset()
sourcemeter.use_front_terminals()
sourcemeter.apply_voltage(voltage, compliance_current)
sourcemeter.measure_current(measure_nplc, current_range)
sleep(0.1)  # wait here to give the instrument time to react
sourcemeter.stop_buffer()
sourcemeter.disable_buffer()

# Allocate arrays to store the measurement results
currents = np.linspace(min_current, max_current, num=data_points)
voltages = np.zeros_like(currents)
voltage_stds = np.zeros_like(currents)

sourcemeter.enable_source()

# Loop through each current point, measure and record the voltage
for i in range(data_points):
    sourcemeter.config_buffer(averages)
    sourcemeter.source_current = currents[i]
    sourcemeter.start_buffer()
    sourcemeter.wait_for_buffer()
    # Record the average and standard deviation
    voltages[i] = sourcemeter.means[0]
    sleep(1.0)
    voltage_stds[i] = sourcemeter.standard_devs[0]

# Save the data columns in a CSV file
data = pd.DataFrame({
    'Current (A)': currents,
    'Voltage (V)': voltages,
    'Voltage Std (V)': voltage_stds,
})
data.to_csv('example.csv')

sourcemeter.shutdown()