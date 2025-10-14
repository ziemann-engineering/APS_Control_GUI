# Import necessary packages
from pymeasure.instruments.keithley import Keithley2400
#from pymeasure.instruments.resources import list_resources
#from pymeasure.instruments import find_serial_port

from time import sleep, monotonic

# Set the input parameters
data_points = 10
averages = 10
max_current = 0.001
min_current = -max_current

# Set source_current and measure_voltage parameters
voltage = 0.001 # V
compliance_current = 0.1  # A
measure_nplc = 1  # Number of power line cycles
current_range = 1e-9  # A

#list_resources()

#resource_name = find_serial_port(vendor_id=0x05e6, product_id=0x2470, serial_number="sn56X")

#sourcemeter = Keithley2400("USB0::0x05e6::0x2410::[serial number]::INSTR")
sourcemeter = Keithley2400("GPIB::24")
print(sourcemeter.id)
sourcemeter.reset()
sourcemeter.line_frequency = 50
sourcemeter.use_front_terminals()
sourcemeter.apply_voltage(voltage_range=None, compliance_current=compliance_current) # autorange, set compliance
sourcemeter.source_voltage = voltage    # voltage to apply
sourcemeter.measure_current(nplc=10, current=2e-9, auto_range=False) 
#sourcemeter.measure_voltage(nplc=10, voltage=0.002, auto_range=False)

sourcemeter.enable_source()
sleep(1)  # wait for voltage to settle

a = monotonic()
#print("Voltage:", sourcemeter.voltage, "V")
print("Current:", sourcemeter.current, "A")
b = monotonic()

sourcemeter.disable_source()


print("Measure takes", b - a, "s")

sourcemeter.shutdown()