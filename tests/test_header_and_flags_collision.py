# test_header_and_flags_collision.py
#
# Copyright 2026 Matthew Samson
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for session header bounding and marker flags collision handling with hover expansion."""

import unittest
from datetime import datetime
import cairo
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.archive import SessionRecord, MidiData, NoteEvent, MarkerEvent, Section
from src.player import AudioPlayer, NullSynthEngine
from src.timeline_canvas import TimelineCanvas, TimelineSessionItem


class DummyAdjustment:
    def __init__(self, value=0.0, upper=1000.0, page_size=800.0):
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


class TestHeaderAndFlagsCollision(unittest.TestCase):
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
        self.canvas.hover_mouse_x = 0.0
        self.canvas.hover_mouse_y = 0.0
        self.canvas.hover_mouse_time = 0.0
        self.canvas.autoscroll_active = False
        self.canvas.selection_range = None
        self.canvas.scrub_active = False
        self.canvas.on_star_toggled = None
        self.canvas.hovered_header_item = None
        self.canvas.hovered_marker = None
        self.canvas._rendered_headers = []
        self.canvas._rendered_markers = []
        self.canvas.set_cursor_from_name = lambda name: None
        self.canvas.grab_focus = lambda: None

        self.surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 800, 300)
        self.cr = cairo.Context(self.surf)

    def _make_session(self, sess_id, dt, duration, markers=None, name="Piano"):
        midi = MidiData(
            duration=duration,
            notes=[NoteEvent(60, 90, 0.0, min(duration, 2.0))],
            markers=markers or [],
            sections=[Section(0.0, duration)]
        )
        return SessionRecord(
            id=sess_id,
            start_time=dt,
            end_time=dt,
            duration_seconds=duration,
            active_play_seconds=duration,
            note_count=10,
            device_name=name,
            file_path=f"/tmp/s_{sess_id}.mid",
            _cached_midi=midi
        )

    def test_session_header_truncation_when_obstructed_by_next_clip(self):
        s1 = self._make_session(1, datetime(2026, 10, 5, 14, 0), 10.0, name="Very Long Device Name That Cannot Fit")
        item1 = TimelineSessionItem(s1, 0.0)

        # 1. Heavily constrained available width (40px)
        is_collapsed, full_badge, stats = self.canvas._draw_session_header(self.cr, item1, 0.0, avail_w=40.0)
        self.assertTrue(is_collapsed)
        self.assertIn("Very Long Device Name", full_badge)

        # 2. Ample available width (500px)
        is_collapsed2, full_badge2, stats2 = self.canvas._draw_session_header(self.cr, item1, 0.0, avail_w=500.0)
        self.assertFalse(is_collapsed2)
        self.assertEqual(full_badge, full_badge2)

    def test_marker_flags_dynamic_expansion_and_obstacle_bounding(self):
        # Unobstructed: marker displays full text
        chip_w, is_col, text = self.canvas._draw_marker_chip(
            self.cr, "Verse 1 Vocal Lead", x=100.0, avail_w=400.0
        )
        self.assertFalse(is_col)
        self.assertEqual(text, "Verse 1 Vocal Lead")
        self.assertGreater(chip_w, 60.0)

        # Obstructed by adjacent flag or clip boundary (only 35px available)
        chip_w2, is_col2, text2 = self.canvas._draw_marker_chip(
            self.cr, "Verse 1 Vocal Lead", x=100.0, avail_w=35.0
        )
        self.assertTrue(is_col2)
        self.assertTrue(text2.endswith("…"))
        self.assertLessEqual(chip_w2, 35.0)

    def test_on_draw_computes_exact_obstacles_and_renders_cleanly(self):
        m1 = MarkerEvent(time=3.0, text="Intro Riff")
        s1 = self._make_session(1, datetime(2026, 10, 5, 10, 0), 10.0, markers=[m1], name="Dev1")
        m2 = MarkerEvent(time=2.0, text="Bridge Chord")
        s2 = self._make_session(2, datetime(2026, 10, 5, 10, 15), 15.0, markers=[m2], name="Dev2")

        self.canvas.load_sessions([s1, s2], is_archive_mode=True)
        self.canvas._on_draw(self.canvas, self.cr, 800, 300)

        self.assertEqual(len(self.canvas._rendered_headers), 2)
        self.assertEqual(len(self.canvas._rendered_markers), 2)

        # Header 1 bounded to start of Session 2 (10s + 3s gap = 13s -> 13 * 25 = 325px)
        s2_x = self.canvas.time_to_x(self.canvas.session_items[1].timeline_offset)
        self.assertEqual(self.canvas._rendered_headers[0]['avail_w'], s2_x - 6.0)

        # Marker 1 at 3s (75px) bounded to Session 2 start at 325px (325 - 75 - 4 = 246px)
        m1_x = self.canvas.time_to_x(3.0)
        self.assertEqual(self.canvas._rendered_markers[0]['avail_w'], s2_x - m1_x - 4.0)
        self.assertFalse(self.canvas._rendered_markers[0]['is_collapsed'])

    def test_hover_state_and_tooltip_overlay_rendering(self):
        m1 = MarkerEvent(time=1.0, text="Chorus Hook")
        s1 = self._make_session(1, datetime(2026, 10, 5, 12, 0), 5.0, markers=[m1], name="Synthesizer")
        item = TimelineSessionItem(s1, 0.0)

        self.canvas.session_items = [item]
        self.canvas._rendered_headers = [{
            'item': item,
            'x': 0.0,
            'w': 50.0,
            'avail_w': 50.0,
            'is_collapsed': True,
            'full_badge': 'Oct 5, 12:00 PM • Synthesizer',
            'stats': '5s • 10 notes',
            'rect_x': 0.0,
            'rect_y': 0.0,
            'rect_w': 50.0,
            'rect_h': 44.0
        }]
        self.canvas._rendered_markers = [{
            'item': item,
            'marker': m1,
            'x': 25.0,
            'chip_y': 23.0,
            'chip_w': 30.0,
            'avail_w': 30.0,
            'is_collapsed': True,
            'display_text': 'Cho…',
            'full_text': 'Chorus Hook',
            'rect_x': 23.0,
            'rect_y': 18.0,
            'rect_w': 34.0,
            'rect_h': 24.0
        }]

        # Hover marker
        self.canvas._on_motion(None, 30.0, 25.0)
        self.assertEqual(self.canvas.hovered_marker, (item, m1))
        self.assertIsNone(self.canvas.hovered_header_item)

        # Draw tooltip overlay with hovered marker active
        self.canvas._draw_expanded_marker_tooltip(self.cr, self.canvas._rendered_markers[0], scroll_x=0.0, screen_width=800)

        # Hover header
        self.canvas._on_motion(None, 10.0, 10.0)
        self.assertEqual(self.canvas.hovered_header_item, item)
        self.assertIsNone(self.canvas.hovered_marker)

        # Draw tooltip overlay with hovered header active
        self.canvas._draw_expanded_header_tooltip(self.cr, self.canvas._rendered_headers[0], scroll_x=0.0, screen_width=800)

        # Hover leave
        self.canvas._on_motion_leave(None)
        self.assertIsNone(self.canvas.hovered_marker)
        self.assertIsNone(self.canvas.hovered_header_item)

    def test_marker_click_seeks_accurately(self):
        m = MarkerEvent(time=7.5, text="Drop")
        s = self._make_session(1, datetime(2026, 10, 5, 10, 0), 20.0, markers=[m])
        item = TimelineSessionItem(s, 0.0)
        self.player.duration = 20.0

        self.canvas.session_items = [item]
        self.canvas._rendered_markers = [{
            'item': item,
            'marker': m,
            'x': 187.5,
            'rect_x': 185.0,
            'rect_y': 18.0,
            'rect_w': 50.0,
            'rect_h': 24.0,
            'chip_w': 46.0
        }]

        class DummyGesture:
            def get_current_button(self): return 1

        # Click inside marker chip
        self.canvas._on_click_pressed(DummyGesture(), 1, 200.0, 24.0)
        self.assertEqual(self.player.current_time, 7.5)


if __name__ == '__main__':
    unittest.main()
