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



    def test_watermark_archive_vs_single_file_mode(self):
        import sys, os
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
        from src.overview_minimap import OverviewMinimap

        class DummyDay:
            def __init__(self, d):
                self.date = d

        class DummyCanvas:
            def __init__(self, is_archive: bool):
                self.is_archive_mode = is_archive
                self.active_day = DummyDay(datetime(2026, 10, 5).date())

        minimap = OverviewMinimap.__new__(OverviewMinimap)

        # Archive mode: should display formatted day label
        minimap.canvas = DummyCanvas(is_archive=True)
        self.assertEqual(minimap.get_day_watermark_label(), "Monday, Oct 5, 2026")

        # Single-file mode: watermark must NOT be displayed
        minimap.canvas = DummyCanvas(is_archive=False)
        self.assertIsNone(minimap.get_day_watermark_label())

    def test_single_file_mode_session_badge_omits_date(self):
        import sys, os
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
        from src.archive import SessionRecord, MidiData
        from src.timeline_canvas import TimelineCanvas, TimelineSessionItem

        s = SessionRecord(
            id=None,
            start_time=datetime(2026, 10, 5, 14, 30),
            end_time=datetime(2026, 10, 5, 14, 35),
            duration_seconds=120.0,
            active_play_seconds=60.0,
            note_count=42,
            device_name="my_recording.mid",
            file_path="/tmp/my_recording.mid",
            _cached_midi=MidiData(duration=120.0)
        )
        item = TimelineSessionItem(s, 0.0)
        canvas = TimelineCanvas.__new__(TimelineCanvas)

        # In archive mode: includes formatted date and time
        canvas.is_archive_mode = True
        badge_arch = canvas.get_session_badge_text(item, 200.0)
        self.assertIn("Oct 5", badge_arch)
        self.assertIn("my_recording.mid", badge_arch)

        # In single file mode: must NOT include date or time
        canvas.is_archive_mode = False
        badge_single = canvas.get_session_badge_text(item, 200.0)
        self.assertEqual(badge_single, "my_recording.mid")
        self.assertNotIn("Oct 5", badge_single)
        self.assertNotIn("PM", badge_single)


if __name__ == "__main__":
    unittest.main()
