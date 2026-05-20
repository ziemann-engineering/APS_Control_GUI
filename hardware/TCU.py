import serial
import numpy as np

class channel:
    def __init__(self):
        self.enabled = False
        self.actual_temperature= 0
        self.setpoint_temperature = None

class TCU():
    def __init__(self, port="COM1", channels=2, baudrate=38400):
        self.port = port

        self.connected = False
        self.enabled = False

        self.channel = [channel() for i in range(channels+1)]   # create an extra channel, ignore channel 0

        #self.setpoint_voltage = None
        #self.setpoint_current = None
        #self.voltage_ramp_rate = None

        self.ser = serial.Serial(self.port, baudrate, timeout=1, write_timeout=1)

        # Identify the device via *IDN?
        # Expected format (target): Ziemann Engineering,TCU2,<SN>,<version>
        # TODO: update TCU firmware to respond with the standard IDN format.
        #       For now, accept any response that contains 'ZE TCU' (legacy) or
        #       'TCU' (new unified format).
        self.ser.write(b'*IDN?\n')
        try:
            response = self.ser.readline().decode()
        except Exception:
            raise RuntimeError(u'This does not seem to be a ZE TCU, received: %s' % response)
        if 'ZE TCU' not in response and 'TCU' not in response:
            raise RuntimeError(u'This does not seem to be a ZE TCU, received: %s' % response)
        self.info = response.strip()
        self.connected = True     # save connected state
        #self.ser.write(b'*RST\r\n')  # reset PSU(s)
        self.ser.readline()       # read a line

    def __enter__(self):
        return self

    def serialwrite(self, text):
        if self.connected:
            self.ser.write(b'%s\n' % text.encode())

    def get_temperature(self, channel):
        if self.connected:
            self.serialwrite('T? %d' % channel)
            #self.ser.readline()    # read a line: first we get the readback and status
            response = self.ser.readline().decode()    # read a line: then we get the data we need
            if len(response) == 0:
                print("Error reading voltage: no response")
                return np.nan
            try:
                self.channel[channel].actual_temperature = float(response)
            except ValueError:
                print("Error converting to float: %s" % response)
                return np.nan
            return self.channel[channel].actual_temperature

    def set_temperature(self, channel, temperature=0):
        if self.connected:
            self.serialwrite('T_set %d %d' % (channel, temperature))
            self.channel[channel].setpoint_temperature = temperature

    def enable_channel(self, channel):
        if self.connected:
            self.serialwrite('Ch %d on' % channel)
            self.channel[channel].enabled = True

    def disable_channel(self, channel):
        if self.connected:
            self.serialwrite('Ch %d off' % channel)
            self.channel[channel].enabled = False

    def close(self):
        if self.connected:
            #self.ser.write(b'*RST\r\n')   # reset
            self.ser.close()
            self.connected = False

    def __exit__(self, exc_type, exc_value, tb):
        self.close()
