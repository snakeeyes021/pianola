# test_overview_minimap.py
import unittest
from datetime import datetime

class DummyHadjustment:
    def __init__(self, value=0.0, upper=1000.0, page_size=200.0):
        self.value = value
        self.upper = upper
        self.page_size = page_size

    def get_value(self):
        return self.value

    def get_upper(self):
        return self.upper

    def get_page_size(self):
        return self.page_size

    def set_value(self, v):
        self.value = max(0.0, min(self.upper - self.page_size, v))


class TestOverviewMinimapLogic(unittest.TestCase):
    def test_lens_coordinates(self):
        hadj = DummyHadjustment(value=250.0, upper=1000.0, page_size=200.0)
        minimap_width = 500.0
        scale = minimap_width / hadj.get_upper()

        lens_x = hadj.get_value() * scale
        lens_w = hadj.get_page_size() * scale

        self.assertEqual(scale, 0.5)
        self.assertEqual(lens_x, 125.0)
        self.assertEqual(lens_w, 100.0)

    def test_click_centering(self):
        hadj = DummyHadjustment(value=0.0, upper=1000.0, page_size=200.0)
        minimap_width = 500.0
        click_x = 250.0  # Click in exact center of minimap

        target_canvas_x = (click_x / minimap_width) * hadj.get_upper() - (hadj.get_page_size() / 2.0)
        hadj.set_value(target_canvas_x)

        # Expected: centered on canvas (500 - 100 = 400)
        self.assertEqual(hadj.get_value(), 400.0)

    def test_drag_panning(self):
        hadj = DummyHadjustment(value=100.0, upper=1000.0, page_size=200.0)
        minimap_width = 500.0
        start_val = hadj.get_value()
        offset_x = 50.0

        delta_canvas = (offset_x / minimap_width) * hadj.get_upper()
        hadj.set_value(start_val + delta_canvas)

        # Offset 50px on 500px minimap is 100px on 1000px canvas: 100 + 100 = 200
        self.assertEqual(hadj.get_value(), 200.0)


if __name__ == "__main__":
    unittest.main()
