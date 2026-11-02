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



    def test_cross_day_section_navigation_logic(self):
        # Setup simulated window state
        dates = [date(2026, 10, 5), date(2026, 10, 6)]
        curr_date = dates[0]

        # Day 1 data: sections at 0.0s and 20.0s
        day1_sections = [Section(0.0, 10.0), Section(20.0, 30.0)]
        self.player.load_midi_data(MidiData(duration=50.0, notes=[], sections=day1_sections))

        # Position at the last section of Day 1
        self.player.seek(20.0)

        # 1. Simulating next section when at the last section of Day 1:
        curr_t = self.player.current_time
        next_secs = [s for s in self.player.sections if s.start_time > curr_t + 0.05]
        self.assertEqual(len(next_secs), 0)

        # Cross to Day 2!
        idx = dates.index(curr_date)
        self.assertTrue(idx < len(dates) - 1)
        curr_date = dates[idx + 1]

        # Load Day 2: sections at 0.0s and 15.0s
        day2_sections = [Section(0.0, 10.0), Section(15.0, 25.0)]
        self.player.load_midi_data(MidiData(duration=40.0, notes=[], sections=day2_sections))

        # Playhead lands on the first section of Day 2
        target_t = self.player.sections[0].start_time
        self.player.seek(target_t)
        self.assertEqual(self.player.current_time, 0.0)
        self.assertEqual(curr_date, date(2026, 10, 6))

        # 2. On Day 2, jumping next section moves to section 2 (15.0s)
        t = self.player.jump_next_section()
        self.assertEqual(t, 15.0)

        # 3. Jumping prev section moves back to section 1 (0.0s)
        t = self.player.jump_prev_section()
        self.assertEqual(t, 0.0)

        # 4. Jumping prev section when already at start (0.0s) of Day 2:
        curr_t = self.player.current_time
        first_sec_start = self.player.sections[0].start_time
        prev_starts = [s.start_time for s in self.player.sections if s.start_time < curr_t - 1.2]
        self.assertEqual(len(prev_starts), 0)
        self.assertTrue(curr_t <= first_sec_start + 0.5)

        # Cross back to Day 1!
        idx = dates.index(curr_date)
        self.assertTrue(idx > 0)
        curr_date = dates[idx - 1]

        # Reload Day 1
        self.player.load_midi_data(MidiData(duration=50.0, notes=[], sections=day1_sections))

        # Playhead lands on the LAST section of Day 1 (20.0s)
        target_t = self.player.sections[-1].start_time
        self.player.seek(target_t)
        self.assertEqual(self.player.current_time, 20.0)
        self.assertEqual(curr_date, date(2026, 10, 5))

    def test_per_track_and_multi_track_selection(self):
        from gi.repository import Gdk
        from src.archive import TrackInfo

        tracks = [
            TrackInfo(track_index=0, channel=0, name='Violin', program=40, instrument_name='Violin', min_pitch=60, max_pitch=72, note_count=2),
            TrackInfo(track_index=1, channel=1, name='Cello', program=42, instrument_name='Cello', min_pitch=48, max_pitch=60, note_count=2),
            TrackInfo(track_index=2, channel=2, name='Flute', program=73, instrument_name='Flute', min_pitch=65, max_pitch=80, note_count=2),
        ]
        notes = [
            NoteEvent(pitch=65, velocity=100, start_time=1.0, end_time=4.0, channel=0, track_index=0),
            NoteEvent(pitch=50, velocity=95, start_time=1.0, end_time=4.0, channel=1, track_index=1),
            NoteEvent(pitch=74, velocity=90, start_time=1.0, end_time=4.0, channel=2, track_index=2),
        ]
        midi = MidiData(duration=10.0, notes=notes, sections=[Section(0.0, 10.0)], tracks=tracks)
        sess = SessionRecord(
            id=1,
            start_time=datetime(2026, 10, 5, 10, 0),
            end_time=datetime(2026, 10, 5, 10, 0, 10),
            duration_seconds=10.0,
            active_play_seconds=10.0,
            note_count=3,
            device_name='Orchestra',
            file_path='/tmp/orch.mid',
            _cached_midi=midi,
        )
        self.canvas.get_height = lambda: 344
        self.canvas.load_sessions([sess], is_archive_mode=False)
        self.canvas.set_multi_track_mode(True)

        # Compute lane Y centers (roll_top=44, roll_bottom=320)
        lanes = self.canvas.get_track_lane_geometries(44.0, 320.0)
        self.assertEqual(len(lanes), 3)
        y_track0 = (lanes[0][1] + lanes[0][2]) / 2.0
        y_track1 = (lanes[1][1] + lanes[1][2]) / 2.0
        y_track2 = (lanes[2][1] + lanes[2][2]) / 2.0

        # 1. Single-track drag on Track 0 (Violin) from 1.5s to 3.5s
        self.canvas._begin_pointer_interaction(self.canvas.time_to_x(1.5), y_track0, Gdk.ModifierType(0))
        self.canvas._apply_drag_selection(1.5, 3.5, y_track0, y_track0)
        self.assertEqual(self.canvas.selection_range, (1.5, 3.5))
        self.assertEqual(self.canvas.selected_tracks, {(0, 0)})
        sliced = self.canvas.get_selected_notes()
        self.assertEqual(len(sliced), 1)
        self.assertEqual(sliced[0].pitch, 65)
        self.assertTrue(self.canvas.is_point_in_selection(2.0, y_track0, height=344))
        self.assertFalse(self.canvas.is_point_in_selection(2.0, y_track1, height=344))

        # 2. Dragging across Track 0 and Track 1 selects that stretch on both tracks
        self.canvas._begin_pointer_interaction(self.canvas.time_to_x(1.5), y_track0, Gdk.ModifierType(0))
        self.canvas._apply_drag_selection(1.5, 3.5, y_track0, y_track1)
        self.assertEqual(self.canvas.selected_tracks, {(0, 0), (1, 1)})
        self.assertEqual(len(self.canvas.get_selected_notes()), 2)

        # 3. Ctrl-drag on Track 2 (Flute) adds non-contiguous Track 2 [2.0, 3.0] while keeping Track 0 & 1 [1.5, 3.5]
        self.canvas._begin_pointer_interaction(self.canvas.time_to_x(2.0), y_track2, Gdk.ModifierType.CONTROL_MASK)
        self.canvas._drag_moved = True
        self.canvas._apply_drag_selection(2.0, 3.0, y_track2, y_track2)
        self.assertEqual(self.canvas.selected_tracks, {(0, 0), (1, 1), (2, 2)})
        eff = self.canvas.get_effective_track_intervals()
        self.assertEqual(eff[(0, 0)], [(1.5, 3.5)])
        self.assertEqual(eff[(2, 2)], [(2.0, 3.0)])

        # 4. Ctrl-drag inside already selected Track 1 subtracts from Track 1
        self.canvas._begin_pointer_interaction(self.canvas.time_to_x(2.0), y_track1, Gdk.ModifierType.CONTROL_MASK)
        self.canvas._drag_moved = True
        self.canvas._apply_drag_selection(1.5, 3.5, y_track1, y_track1)
        self.assertEqual(self.canvas.selected_tracks, {(0, 0), (2, 2)})

        # 5. Dragging on the footer time ruler selects whole-range across all tracks
        y_ruler = 330.0
        self.canvas._begin_pointer_interaction(self.canvas.time_to_x(1.0), y_ruler, Gdk.ModifierType(0))
        self.canvas._apply_drag_selection(1.0, 3.0, y_ruler, y_ruler)
        self.assertEqual(self.canvas.selection_range, (1.0, 3.0))
        self.assertIsNone(self.canvas.selected_tracks)
        self.assertEqual(len(self.canvas.get_selected_notes()), 3)


if __name__ == '__main__':
    unittest.main()
