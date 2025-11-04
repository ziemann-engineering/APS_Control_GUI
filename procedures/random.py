import random
import logging
from time import sleep
from pymeasure.experiment import Procedure
from pymeasure.experiment import IntegerParameter, FloatParameter, Parameter

#from datetime import datetime

log = logging.getLogger(__name__)


class RandomProcedure(Procedure):
    # common properties of the procedure
    name = 'Random Number Test' # For display
    internal_name = 'Random_Number_Test' # For internal use, no spaces or special chars
    short_name = 'Random' # For directory naming
    description = "A simple test procedure that generates random numbers. No hardware required."
    filename = r'{datetime.now():%Y-%m-%d_%H-%M-%S}' # Default filename pattern, can also use {date}, {time}, {measurement_voltage}, etc.

    # parameters for the procedure
    iterations = IntegerParameter('Loop Iterations', default=10)
    delay = FloatParameter('Delay Time', units='s', default=0.2)
    seed = Parameter('Random Seed', default='12345')

    DATA_COLUMNS = ['Iteration', 'Random Number 1', 'Random Number 2', 'Random Number 3']
    
    # GUI Configuration
    INPUTS = ['iterations', 'delay', 'seed']
    DISPLAYS = INPUTS
    X_AXIS = 'Iteration'
    Y_AXIS = ['Random Number 1', 'Random Number 2']
    
    # Hardware Configuration for Startup Dialog (no hardware needed)
    HARDWARE = {}
    
    # Hardware Configuration for Startup Dialog (no hardware needed)
    HARDWARE = {}

    def startup(self):
        log.info("Setting the seed of the random number generator")
        random.seed(self.seed)

    def execute(self):
        log.info("Starting the loop of %d iterations" % self.iterations)
        for i in range(self.iterations):
            data = {
                'Iteration': i,
                'Random Number 1': random.random(),
                'Random Number 2': random.random(),
                'Random Number 3': random.random()
            }
            self.emit('results', data)
            log.debug("Emitting results: %s" % data)
            self.emit('progress', 100 * i / self.iterations)
            sleep(self.delay)
            if self.should_stop():
                log.warning("Caught the stop flag in the procedure")
                break
