import keithley_visa_drivers as kvd

keithley_conf = {
    "smu_model": "2470",
    "address": "GPIB::24",
    "syntax": "SCPI",
}

# Create a configuration object
configuration = kvd.ConfigureSMU(**keithley_conf)

# Create a Keithley instance
smu = kvd.Keithley(configuration=configuration)


smu.reset()  # Reset the SMU
smu.two_wire()  # 2-wire measurement
smu.set_nplc(1)  # Set NPLC to 1
print(f"NPLC: {smu.get_nplc()}")
smu.readback_on()  # Readback on (if supported)
print(f"Readback: {smu.get_readback_status()}")
smu.set_sourcev_range(0.001)  # Set source voltage range
print(f"SourceV Range: {smu.get_sourcev_range()}")
smu.autorange_rebound_on()  # Autorange rebound on (if supported)
smu.set_icompliance(0.001)  # Set current compliance to 1 A
smu.output_on()  # Turn on output

smu.set_volt(0.001) 

result = smu.get_iv()
print(f"Voltage: {result[0]} V, Current: {result[1]} A")
smu.close()