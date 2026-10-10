# test_day_scoped_timeline.py
#
# Copyright 2026 Matthew Samson
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for day-scoped timeline canvas scaling and date display."""

import unittest
from datetime import datetime, date
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.archive import SessionRecord, MidiData, NoteEvent, Section
from src.player import AudioPlayer, NullSynthEngine
from src.timeline_canvas import TimelineCanvas, TimelineSessionItem, DayGroup


class DummyAdjustment:
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

    def set_upper(self, val):
        self.upper = val

    def set_page_size(self, val):
        self.page_size = val

    def set_value(self, val):
        self.value = val

    def connect(self, sig, cb):
        pass


class TestDayScopedTimeline(unittest.TestCase):
    def setUp(self):
        self.synth = NullSynthEngine()
        self.player = AudioPlayer(synth=self.synth)
        self.canvas = TimelineCanvas.__new__(TimelineCanvas)
        self.canvas.player = self.player
        self.canvas.session_items = []
        self.canvas.day_groups = {}
        self.canvas.ordered_days = []
        self.canvas.collapsed_gaps = []
        self.canvas.tracks = []
        self.canvas.px_per_sec = 25.0
        self.canvas.multi_track_mode = False
        self.canvas.is_archive_mode = True
        self.canvas.total_timeline_duration = 0.0
        self.canvas.total_visual_duration = 0.0
        self.canvas.hadj = DummyAdjustment()
        self.canvas.get_width = lambda: 800
        self.canvas.queue_draw = lambda: None

    def _make_session(self, sess_id, dt, duration, notes_count=5):
        notes = [NoteEvent(pitch=60 + i, velocity=90, start_time=float(i), end_time=float(i + 1)) for i in range(notes_count)]
        midi = MidiData(
            duration=duration,
            notes=notes,
            sections=[Section(0.0, duration)]
        )
        return SessionRecord(
            id=sess_id,
            start_time=dt,
            end_time=dt,
            duration_seconds=duration,
            active_play_seconds=duration,
            note_count=notes_count,
            device_name=f'Device_{sess_id}',
            file_path=f'/tmp/sess_{sess_id}.mid',
            _cached_midi=midi
        )

    def test_day_scoped_canvas_duration_and_width(self):
        # Day 1: two sessions on 2026-10-05 (durations 100s and 200s, gap 3.0s -> 303s total)
        s1 = self._make_session(1, datetime(2026, 10, 5, 10, 0), 100.0)
        s2 = self._make_session(2, datetime(2026, 10, 5, 14, 0), 200.0)

        # Day 2: session on 2026-10-06 (duration 500s)
        s3 = self._make_session(3, datetime(2026, 10, 6, 9, 0), 500.0)

        # Load Day 1 slice only
        self.canvas.load_sessions([s1, s2], is_archive_mode=True)

        # Canvas total duration should only be Day 1's duration: 100 + 3(gap) + 200 = 303.0s
        self.assertEqual(self.canvas.total_timeline_duration, 303.0)
        self.assertEqual(len(self.canvas.session_items), 2)
        self.assertEqual(len(self.canvas.ordered_days), 1)
        self.assertEqual(self.canvas.ordered_days[0].date, date(2026, 10, 5))

        # Canvas adjustment width must only represent Day 1 length
        expected_w = max(800.0, (self.canvas.total_visual_duration * self.canvas.px_per_sec) + 120.0)
        self.assertEqual(self.canvas.hadj.get_upper(), expected_w)

        # Now load adjacent day (Day 2 slice)
        self.canvas.load_sessions([s3], is_archive_mode=True)

        # Canvas total duration should now strictly be Day 2's duration: 500.0s
        self.assertEqual(self.canvas.total_timeline_duration, 500.0)
        self.assertEqual(len(self.canvas.session_items), 1)
        self.assertEqual(self.canvas.ordered_days[0].date, date(2026, 10, 6))

        expected_w_day2 = max(800.0, (self.canvas.total_visual_duration * self.canvas.px_per_sec) + 120.0)
        self.assertEqual(self.canvas.hadj.get_upper(), expected_w_day2)

    def test_single_file_mode_scoping(self):
        s = self._make_session(99, datetime(2026, 10, 5, 12, 0), 180.0)
        self.canvas.load_sessions([s], is_archive_mode=False)

        self.assertFalse(self.canvas.is_archive_mode)
        self.assertEqual(self.canvas.total_timeline_duration, 180.0)
        badge = self.canvas.get_session_badge_text(self.canvas.session_items[0], 200.0)
        self.assertEqual(badge, 'Device_99')
        self.assertNotIn('Oct 5', badge)


if __name__ == '__main__':
    unittest.main()
