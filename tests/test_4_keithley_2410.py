from keithley_visa_drivers import ConfigureSMU, Keithley
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


configuration = ConfigureSMU(smu_model="2470", address="USB0::0x05E6::0x2470::04530624::INSTR", syntax="SCPI")

smu = Keithley(configuration=configuration)

print(smu.get_idn())
