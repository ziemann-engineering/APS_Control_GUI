"""Plot helpers for displaying one GSS trace per DUT."""

import pyqtgraph as pg

from pymeasure.display.curves import ResultsCurve


class DUTResultsCurve(ResultsCurve):
    """A results curve containing rows for one DUT only."""

    def __init__(self, results, dut, **kwargs):
        super().__init__(results, **kwargs)
        self.dut = dut

    def update_data(self):
        if self.force_reload:
            self.results.reload()
        data = self.results.data
        dut_data = data[data['DUT'] == self.dut]
        self.setData(dut_data[self.x].to_numpy(), dut_data[self.y].to_numpy())


def new_dut_curves(widget, results, dut_count):
    """Create one labelled, differently coloured curve for each GSS DUT."""
    if widget.plot.legend is None:
        widget.plot.addLegend()

    curves = []
    for dut in range(1, dut_count + 1):
        curve = DUTResultsCurve(
            results,
            dut,
            wdg=widget,
            x=widget.plot_frame.x_axis,
            y=widget.plot_frame.y_axis,
            pen=pg.mkPen(color=pg.intColor(dut - 1), width=widget.linewidth),
            antialias=False,
            name=f'DUT {dut}',
        )
        curve.setSymbol(None)
        curve.setSymbolBrush(None)
        curves.append(curve)
    return curves