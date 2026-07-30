import os
import threading
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pymeasure.display.Qt import QtWidgets
from pymeasure.display.manager import Experiment
from pymeasure.experiment import Procedure, Results

from gss_parallel_manager import GSSParallelManager


class BlockingProcedure(Procedure):
    DATA_COLUMNS = ['Value']
    barrier = None
    release = None

    def execute(self):
        self.barrier.wait(timeout=2.0)
        self.release.wait(timeout=2.0)
        self.emit('results', {'Value': 1})


class FakeBrowser:
    def add(self, experiment):
        pass


class FakeBrowserItem:
    def __init__(self):
        self.progress = 0
        self.status = None

    def setProgress(self, progress):
        self.progress = progress

    def setStatus(self, status):
        self.status = status



def test_queued_gss_experiments_execute_concurrently(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    BlockingProcedure.barrier = threading.Barrier(3)
    BlockingProcedure.release = threading.Event()
    manager = GSSParallelManager((), FakeBrowser())

    for index in range(2):
        procedure = BlockingProcedure()
        results = Results(procedure, str(tmp_path / f'run-{index}.csv'))
        manager.queue(Experiment(results, [], FakeBrowserItem()))

    BlockingProcedure.barrier.wait(timeout=2.0)
    assert len(manager._runs) == 2

    BlockingProcedure.release.set()
    deadline = time.time() + 3.0
    while manager.is_running() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert not manager.is_running()
