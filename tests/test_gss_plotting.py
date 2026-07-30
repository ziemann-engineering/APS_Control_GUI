import pandas as pd
import pyqtgraph as pg

from gss_plotting import DUTResultsCurve, new_dut_curves


class FakeResults:
    def __init__(self):
        self.data = pd.DataFrame({
            'DUT': [1, 2, 1, 2],
            'Timestamp': [1, 1, 2, 2],
            'Vth (V)': [2.1, 2.8, 2.2, 2.9],
        })


def test_dut_results_curve_filters_interleaved_vth_rows():
    curve = DUTResultsCurve.__new__(DUTResultsCurve)
    curve.results = FakeResults()
    curve.force_reload = False
    curve.dut = 2
    curve.x = 'Timestamp'
    curve.y = 'Vth (V)'
    captured = {}
    curve.setData = lambda x, y: captured.update(x=list(x), y=list(y))

    curve.update_data()

    assert captured == {'x': [1, 2], 'y': [2.8, 2.9]}


def test_dut_curves_keep_run_color_and_distinguish_duts(monkeypatch):
    created = []

    class FakePlot:
        legend = None

        def addLegend(self):
            self.legend = object()

    class FakeWidget:
        plot = FakePlot()
        linewidth = 1

        class plot_frame:
            x_axis = 'Timestamp'
            y_axis = 'Vth (V)'

    class FakeCurve:
        def __init__(self, *args, **kwargs):
            created.append(kwargs)

        def setSymbol(self, symbol):
            pass

        def setSymbolBrush(self, brush):
            pass

    monkeypatch.setattr('gss_plotting.DUTResultsCurve', FakeCurve)

    new_dut_curves(FakeWidget(), FakeResults(), 6, pg.intColor(3), 'GSS-12')

    assert [curve['name'] for curve in created] == [
        'CTRL GSS-12, DUT 1',
        'CTRL GSS-12, DUT 2',
        'CTRL GSS-12, DUT 3',
        'CTRL GSS-12, DUT 4',
        'CTRL GSS-12, DUT 5',
        'CTRL GSS-12, DUT 6',
    ]
    assert created[0]['pen'].style() != created[1]['pen'].style()
    assert created[5]['pen'].color().lightness() > created[0]['pen'].color().lightness()