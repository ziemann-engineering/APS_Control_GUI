import pandas as pd

from gss_plotting import DUTResultsCurve


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