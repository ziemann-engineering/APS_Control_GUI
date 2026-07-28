"""Plot helpers for displaying one GSS trace per DUT."""

import pyqtgraph as pg

from pymeasure.display.Qt import QtCore
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


def new_dut_curves(widget, results, dut_count, run_color, controller_id):
    """Create labelled DUT curves using one base color for each GSS run."""
    if widget.plot.legend is None:
        widget.plot.addLegend()

    styles = (
        QtCore.Qt.PenStyle.SolidLine,
        QtCore.Qt.PenStyle.DashLine,
        QtCore.Qt.PenStyle.DotLine,
        QtCore.Qt.PenStyle.DashDotLine,
        QtCore.Qt.PenStyle.DashDotDotLine,
    )
    curves = []
    for dut in range(1, dut_count + 1):
        style_index = (dut - 1) % len(styles)
        color = run_color.lighter(135) if dut > len(styles) else run_color
        curve = DUTResultsCurve(
            results,
            dut,
            wdg=widget,
            x=widget.plot_frame.x_axis,
            y=widget.plot_frame.y_axis,
            pen=pg.mkPen(
                color=color,
                width=widget.linewidth,
                style=styles[style_index],
            ),
            antialias=False,
            name=f'CTRL {controller_id}, DUT {dut}',
        )
        curve.setSymbol(None)
        curve.setSymbolBrush(None)
        curves.append(curve)
    return curves