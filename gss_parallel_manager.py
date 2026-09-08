"""PyMeasure manager that runs queued GSS experiments concurrently."""

import logging

from pymeasure.display.listeners import Monitor
from pymeasure.display.manager import Manager
from pymeasure.experiment import Procedure
from pymeasure.experiment.workers import Worker

log = logging.getLogger(__name__)


class GSSParallelManager(Manager):
    """Run all queued experiments while retaining PyMeasure's display API."""

    def __init__(self, widget_list, browser, log_level=logging.INFO, parent=None):
        super().__init__(widget_list, browser, log_level=log_level, parent=parent)
        self._runs = {}

    def is_running(self):
        return bool(self._runs)

    def running_experiment(self):
        if not self._runs:
            raise Exception('There is no Experiment running')
        return next(iter(self._runs))

    def queue(self, experiment):
        self.load(experiment)
        self.queued.emit(experiment)
        if self._start_on_add:
            self.next()

    def next(self):
        """Start every queued GSS experiment instead of waiting for one slot."""
        while self._start_on_add and self.experiments.has_next():
            experiment = self.experiments.next()
            worker = Worker(experiment.results, port=None, log_level=self.log_level)
            worker.is_last = lambda: not self.experiments.has_next()
            experiment.procedure.status = Procedure.RUNNING
            monitor = Monitor(worker.monitor_queue)

            monitor.worker_running.connect(
                lambda experiment=experiment: self.running.emit(experiment)
            )
            monitor.worker_failed.connect(
                lambda experiment=experiment: self._complete(experiment, 'failed')
            )
            monitor.worker_abort_returned.connect(
                lambda experiment=experiment: self._complete(experiment, 'aborted')
            )
            monitor.worker_finished.connect(
                lambda experiment=experiment: self._complete(experiment, 'finished')
            )
            monitor.progress.connect(
                lambda progress, experiment=experiment: experiment.browser_item.setProgress(progress)
            )
            monitor.status.connect(
                lambda status, experiment=experiment: self._update_experiment_status(
                    experiment, status
                )
            )
            monitor.log.connect(self._update_log)

            self._runs[experiment] = (worker, monitor)
            monitor.start()
            worker.start()

    @staticmethod
    def _update_experiment_status(experiment, status):
        experiment.procedure.status = status
        experiment.browser_item.setStatus(status)

    def _complete(self, experiment, outcome):
        run = self._runs.pop(experiment, None)
        if run is None:
            return
        worker, monitor = run
        worker.join()
        monitor.wait()

        if outcome == 'finished':
            experiment.browser_item.setProgress(100)
            for curve in experiment.curve_list:
                if curve:
                    curve.update_data()
            self.finished.emit(experiment)
        elif outcome == 'failed':
            self.failed.emit(experiment)
        else:
            self.abort_returned.emit(experiment)

        if self._is_continuous:
            self.next()

    def abort(self):
        """Request an orderly stop after the current worker operation."""
        if not self._runs:
            raise Exception('Attempting to abort when no experiment is running')
        self._start_on_add = False
        self._is_continuous = False
        for experiment, (worker, _monitor) in list(self._runs.items()):
            worker.stop()
            self.aborted.emit(experiment)

    def resume(self):
        self._start_on_add = True
        self._is_continuous = True
        self.next()
