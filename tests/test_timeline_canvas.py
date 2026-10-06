# test_timeline_canvas.py
#
# Copyright 2026 Matthew Samson
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for src/timeline_canvas.py."""

import os
import sys
from datetime import datetime

# Initialize GTK without display for headless tests
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gdk, Adw

# Initialize Gtk safely
has_display = bool(Gtk.init_check() and Gdk.Display.get_default() is not None)
if has_display:
    Adw.init()


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.archive import NoteEvent, MarkerEvent, Section, SessionRecord, MidiData
from src.player import AudioPlayer, NullSynthEngine
from src.timeline_canvas import TimelineCanvas, TimelineSessionItem


def test_timeline_session_mapping():
    print("[TEST] test_timeline_session_mapping...")
    synth = NullSynthEngine()
    player = AudioPlayer(synth=synth)
    canvas = TimelineCanvas(player)

    # Create two synthetic sessions
    s1_midi = MidiData(
        duration=10.0,
        notes=[NoteEvent(pitch=60, velocity=100, start_time=1.0, end_time=3.0)],
        markers=[MarkerEvent(time=2.0, text="Clapper ★")],
        sections=[Section(start_time=1.0, end_time=3.0)]
    )
    s1 = SessionRecord(
        id=1,
        start_time=datetime(2026, 10, 5, 10, 0),
        end_time=datetime(2026, 10, 5, 10, 10),
        duration_seconds=10.0,
        active_play_seconds=2.0,
        note_count=1,
        device_name="Keyboard 1",
        file_path="/tmp/s1.mid",
        _cached_midi=s1_midi
    )

    s2_midi = MidiData(
        duration=15.0,
        notes=[NoteEvent(pitch=67, velocity=90, start_time=2.0, end_time=5.0)],
        sections=[Section(start_time=2.0, end_time=5.0)]
    )
    s2 = SessionRecord(
        id=2,
        start_time=datetime(2026, 10, 5, 14, 0),
        end_time=datetime(2026, 10, 5, 14, 15),
        duration_seconds=15.0,
        active_play_seconds=3.0,
        note_count=1,
        device_name="Keyboard 2",
        file_path="/tmp/s2.mid",
        _cached_midi=s2_midi
    )

    canvas.load_sessions([s1, s2])

    assert len(canvas.session_items) == 2, "Expected 2 session items"
    # Session 1 starts at 0.0s
    assert canvas.session_items[0].timeline_offset == 0.0
    # Session 2 starts at s1.duration + INTER_SESSION_GAP (10.0 + 3.0 = 13.0)
    assert canvas.session_items[1].timeline_offset == 13.0

    # Total timeline duration: 13.0 + 15.0 = 28.0s
    assert canvas.total_timeline_duration == 28.0

    # Verify notes in player are continuous
    assert len(player.notes) == 2
    assert player.notes[0].pitch == 60
    assert player.notes[0].start_time == 1.0
    assert player.notes[1].pitch == 67
    assert player.notes[1].start_time == 13.0 + 2.0  # 15.0s

    print("  ✓ Passed multi-session continuous timeline mapping.")


def test_timeline_navigation_and_jumps():
    print("[TEST] test_timeline_navigation_and_jumps...")
    synth = NullSynthEngine()
    player = AudioPlayer(synth=synth)
    canvas = TimelineCanvas(player)

    s1_midi = MidiData(duration=5.0, notes=[NoteEvent(60, 100, 0, 5)])
    s1 = SessionRecord(
        id=1,
        start_time=datetime(2026, 10, 4, 12, 0),
        end_time=datetime(2026, 10, 4, 12, 5),
        duration_seconds=5.0,
        active_play_seconds=5.0,
        note_count=1,
        device_name="Dev1",
        file_path="/tmp/s1.mid",
        _cached_midi=s1_midi
    )

    s2_midi = MidiData(duration=5.0, notes=[NoteEvent(64, 100, 0, 5)])
    s2 = SessionRecord(
        id=2,
        start_time=datetime(2026, 10, 5, 12, 0),
        end_time=datetime(2026, 10, 5, 12, 5),
        duration_seconds=5.0,
        active_play_seconds=5.0,
        note_count=1,
        device_name="Dev2",
        file_path="/tmp/s2.mid",
        _cached_midi=s2_midi
    )

    canvas.load_sessions([s1, s2])

    # Default position jump: latest session
    canvas.jump_to_latest_session()
    assert player.current_time == 8.0  # Session 2 starts at 5 + 3 = 8.0s

    # Jump to archive start
    canvas.jump_to_archive_start()
    assert player.current_time == 0.0

    # Jump to day
    canvas.jump_to_day(datetime(2026, 10, 5))
    assert player.current_time == 8.0

    canvas.jump_to_day(datetime(2026, 10, 4))
    assert player.current_time == 0.0

    # Jump prev / next file
    canvas.jump_to_next_file()
    assert player.current_time == 8.0
    canvas.jump_to_prev_file()
    assert player.current_time == 0.0

    print("  ✓ Passed hierarchical jumps (archive, latest session, day, file).")


def test_zoom_and_marquee():
    print("[TEST] test_zoom_and_marquee...")
    synth = NullSynthEngine()
    player = AudioPlayer(synth=synth)
    canvas = TimelineCanvas(player)

    canvas.set_zoom(50.0)
    assert canvas.px_per_sec == 50.0

    canvas.zoom_in()
    assert canvas.px_per_sec > 50.0

    canvas.zoom_out()
    assert round(canvas.px_per_sec, 1) == 50.0

    # Time to X and X to time conversions
    t = 10.0
    x = canvas.time_to_x(t)
    assert round(canvas.x_to_time(x), 1) == 10.0

    # Marquee selection range
    canvas.selection_range = (5.0, 15.0)
    assert canvas.selection_range == (5.0, 15.0)

    print("  ✓ Passed zoom math and marquee selection range.")


def test_silence_compacting():
    print('[TEST] test_silence_compacting...')
    synth = NullSynthEngine()
    player = AudioPlayer(synth=synth)
    canvas = TimelineCanvas(player)

    # Session with 10s playing, 60s silence (gap), 10s playing
    s_midi = MidiData(
        duration=80.0,
        notes=[
            NoteEvent(60, 100, 0.0, 10.0),
            NoteEvent(64, 100, 70.0, 80.0)
        ],
        sections=[
            Section(0.0, 10.0),
            Section(70.0, 80.0)
        ]
    )
    s = SessionRecord(
        id=1,
        start_time=datetime(2026, 10, 5, 12, 0),
        end_time=datetime(2026, 10, 5, 12, 5),
        duration_seconds=80.0,
        active_play_seconds=20.0,
        note_count=2,
        device_name='Dev',
        file_path='/tmp/s.mid',
        _cached_midi=s_midi
    )
    canvas.load_sessions([s])

    # Should detect 1 collapsed gap between 10.0s and 70.0s (60s silence)
    assert len(canvas.collapsed_gaps) == 1
    gap = canvas.collapsed_gaps[0]
    assert gap.real_start == 10.0
    assert gap.real_end == 70.0
    assert gap.visual_duration == 2.0
    assert gap.saved == 58.0

    # Test coordinate mapping
    v10 = canvas.time_to_visual(10.0)
    assert v10 == 10.0
    v70 = canvas.time_to_visual(70.0)
    assert v70 == 12.0  # 10s + 2s collapsed gap
    v80 = canvas.time_to_visual(80.0)
    assert v80 == 22.0  # 80s - 58s saved = 22s visual

    # Test inverse mapping
    assert round(canvas.visual_to_time(10.0), 2) == 10.0
    assert round(canvas.visual_to_time(12.0), 2) == 70.0
    assert round(canvas.visual_to_time(22.0), 2) == 80.0

    print('  ✓ Passed silence compacting and inverse mapping.')


if __name__ == '__main__':
    if not has_display:
        print('[SKIP] No display server available in sandboxed terminal (Display is None); skipping UI widget tests.')
        print('       (To run UI tests, execute with an active Wayland/X11 session or in GNOME Builder)')
    else:
        test_timeline_session_mapping()
        test_timeline_navigation_and_jumps()
        test_zoom_and_marquee()
        test_silence_compacting()
        print('All timeline tests passed successfully!')
