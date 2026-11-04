# test_selection_features.py
#
# Copyright 2026 Matthew Samson
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for selection drag-out, whole-track selection, export content, edge auto-scroll and the keybed layout."""

import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Gdk, Gio, GLib

from src.archive import ArchiveManager, MidiData, MidiParser, NoteEvent, Section, SessionRecord, TrackInfo
from src.player import AudioPlayer, NullSynthEngine
from src.timeline_canvas import TimelineCanvas
from src.piano_keyboard import keyboard_key_layout, is_black_key

VIOLIN = (1, 1)
PIANO = (2, 0)


class DummyAdjustment:
    def __init__(self, value=0.0, upper=1000.0, page_size=800.0):
        self.value, self.upper, self.page_size = value, upper, page_size

    def get_value(self):
        return self.value

    def set_value(self, v):
        self.value = v

    def get_upper(self):
        return self.upper

    def set_upper(self, v):
        self.upper = v

    def get_page_size(self):
        return self.page_size

    def set_page_size(self, v):
        self.page_size = v

    def connect(self, *a):
        pass


class FakeEvent:
    def __init__(self, t):
        self.t = t

    def get_time(self):
        return self.t


class FakeGesture:
    """Stands in for GtkGestureClick/GtkGestureDrag: one button press with a timestamp and modifiers."""

    def __init__(self, t, state=0, button=1):
        self.t, self.state, self.button = t, state, button

    def get_current_event(self):
        return FakeEvent(self.t)

    def get_current_event_state(self):
        return Gdk.ModifierType(self.state)

    def get_current_button(self):
        return self.button

    def get_start_point(self):
        return True, 0.0, 0.0


def make_canvas(width=800.0, height=400.0):
    """A TimelineCanvas without a display: GTK widget methods are stubbed (same approach as the other suites)."""
    c = TimelineCanvas.__new__(TimelineCanvas)
    c.player = AudioPlayer(synth=NullSynthEngine())
    for name, value in dict(
        session_items=[], day_groups={}, ordered_days=[], collapsed_gaps=[], tracks=[],
        px_per_sec=50.0, multi_track_mode=False, is_archive_mode=False,
        total_timeline_duration=0.0, total_visual_duration=0.0,
        _selection_range=None, selected_tracks=None, track_selections={}, whole_range_intervals=[],
        _drag_start_time=None, _drag_start_y=None, _drag_is_ctrl=False, _drag_ctrl_mode="add",
        _drag_whole_range=False, _drag_moved=False, _ctrl_base_selection=None,
        _selection_anchor_time=None, _selection_anchor_y=None, _click_press_handled=False,
        _drag_target_selection=False, _drag_target_header=None, _last_press_time=None,
        _marquee_pointer=None, _edge_scroll_tick_id=0, _edge_scroll_last_frame=None,
        ctrl_held=False, space_held=False, scrub_active=False, scrub_cursor_time=None,
        hover_mouse_x=0.0, hover_mouse_y=0.0, hover_mouse_time=0.0, autoscroll_active=False,
        on_star_toggled=None, on_selection_changed=None, on_request_drag_content=None, on_context_menu=None,
        hovered_header_item=None, hovered_marker=None, _rendered_headers=[], _rendered_markers=[],
    ).items():
        setattr(c, name, value)
    c.hadj = DummyAdjustment(page_size=width)
    c.get_width = lambda: int(width)
    c.get_height = lambda: int(height)
    c.get_content_height = lambda: int(height)
    c.queue_draw = lambda: None
    c.grab_focus = lambda: None
    c.add_tick_callback = lambda cb: 1
    c.remove_tick_callback = lambda tid: None
    return c


def two_track_session(duration=60.0):
    notes = []
    for i in range(0, int(duration) - 1, 2):
        notes.append(NoteEvent(pitch=76, velocity=90, start_time=float(i), end_time=i + 1.5, channel=1, track_index=1))
        notes.append(NoteEvent(pitch=48, velocity=80, start_time=i + 0.5, end_time=i + 2.0, channel=0, track_index=2))
    tracks = [
        TrackInfo(track_index=1, channel=1, name="Violin", program=40, min_pitch=76, max_pitch=76, note_count=len(notes) // 2),
        TrackInfo(track_index=2, channel=0, name="Piano", program=0, min_pitch=48, max_pitch=48, note_count=len(notes) // 2),
    ]
    midi = MidiData(duration=duration, notes=notes, sections=[Section(0.0, duration)], tracks=tracks)
    return SessionRecord(
        id=None, start_time=datetime(2026, 11, 3, 20, 0), end_time=datetime(2026, 11, 3, 20, 1),
        duration_seconds=duration, active_play_seconds=duration, note_count=len(notes),
        device_name="Duo", file_path="/nonexistent/duo.mid", _cached_midi=midi,
    )


def lane_mid_y(c, key):
    for trk, top, bottom in c.get_track_lane_geometries(c.HEADER_HEIGHT, c.get_height() - c.FOOTER_HEIGHT):
        if trk is not None and (trk.track_index, trk.channel) == key:
            return (top + bottom) / 2.0
    raise AssertionError(key)


def export_and_parse(c, notes_from=None):
    """Export the canvas selection the way the window does and parse the result back."""
    spec = c.get_export_spec()
    all_notes = []
    for item in c.session_items:
        for n in item.midi_data.notes:
            all_notes.append(NoteEvent(pitch=n.pitch, velocity=n.velocity, start_time=n.start_time + item.timeline_offset,
                                       end_time=n.end_time + item.timeline_offset, channel=n.channel, track_index=n.track_index))
    tmp = tempfile.mkdtemp(prefix="pianola-test-")
    try:
        path = os.path.join(tmp, "slice.mid")
        ArchiveManager.__new__(ArchiveManager).export_slice(all_notes, spec["start"], spec["end"], path,
                                    selected_tracks=spec["selected_tracks"], track_intervals=spec["track_intervals"],
                                    tracks_info=c.tracks)
        return MidiParser.parse_file(path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class TestSelectionDragTarget(unittest.TestCase):
    def setUp(self):
        self.c = make_canvas()
        self.c.load_sessions([two_track_session()], is_archive_mode=False)
        self.c.multi_track_mode = True

    def test_stale_ctrl_flag_does_not_break_drag_from_selection(self):
        """Regression: a Ctrl release swallowed by a dialog left ctrl_held set, turning presses into Ctrl-subtract."""
        c = self.c
        c.select_tracks_stretch({VIOLIN}, 6.0, 10.0, notify=False)
        c.ctrl_held = True  # stale: the release went to the Ctrl+O / Ctrl+E dialog
        x = c.time_to_x(8.0)
        y = lane_mid_y(c, VIOLIN)
        c._begin_pointer_interaction(x, y, Gdk.ModifierType(0))
        self.assertTrue(c._drag_target_selection, "press inside the selection must target it for drag-out")
        self.assertFalse(c._drag_is_ctrl)
        self.assertEqual(c.selection_range, (6.0, 10.0))
        self.assertEqual(c.selected_tracks, {VIOLIN})

        calls = []
        c.on_request_drag_content = lambda **kw: calls.append(kw) or "provider"
        self.assertEqual(c._on_canvas_drag_prepare(None, x, y), "provider")
        self.assertEqual(calls, [{"use_selection": True}])

    def test_real_ctrl_press_still_subtracts(self):
        c = self.c
        c.select_tracks_stretch({VIOLIN}, 6.0, 10.0, notify=False)
        c._begin_pointer_interaction(c.time_to_x(8.0), lane_mid_y(c, VIOLIN), Gdk.ModifierType.CONTROL_MASK)
        self.assertTrue(c._drag_is_ctrl)
        self.assertEqual(c._drag_ctrl_mode, "subtract")
        self.assertFalse(c._drag_target_selection)

    def test_press_handled_once_when_both_gestures_fire(self):
        """The click and drag gestures both see each press; the press logic must run only once (e.g. star toggles)."""
        c = self.c
        seen = []
        c._begin_pointer_interaction = lambda x, y, state: seen.append((x, y))
        g = FakeGesture(t=1000)
        c._on_drag_begin(g, 100.0, 150.0)
        c._on_click_pressed(g, 1, 100.0, 150.0)
        self.assertEqual(len(seen), 1)
        g2 = FakeGesture(t=1300)
        c._on_click_pressed(g2, 1, 100.0, 150.0)
        c._on_drag_begin(g2, 100.0, 150.0)
        self.assertEqual(len(seen), 2)

    def test_double_click_selects_whole_track_and_arms_drag(self):
        c = self.c
        y = lane_mid_y(c, PIANO)
        x = c.time_to_x(20.0)
        g1 = FakeGesture(t=2000)
        c._on_drag_begin(g1, x, y)
        c._on_click_pressed(g1, 1, x, y)
        c._on_drag_end(g1, 0.0, 0.0)
        g2 = FakeGesture(t=2150)
        c._on_drag_begin(g2, x, y)
        c._on_click_pressed(g2, 2, x, y)
        self.assertEqual(c.selected_tracks, {PIANO})
        self.assertEqual(c.selection_range, (0.0, c.total_timeline_duration))
        self.assertIsNone(c._drag_start_time, "the marquee from the first click is cancelled")
        self.assertTrue(c._drag_target_selection, "a drag right after the double-click drags the track out")

    def test_right_click_keeps_selection_and_requests_menu(self):
        c = self.c
        c.select_tracks_stretch({VIOLIN}, 6.0, 10.0, notify=False)
        menus = []
        c.on_context_menu = lambda x, y: menus.append((x, y))
        x, y = c.time_to_x(8.0), lane_mid_y(c, VIOLIN)
        c._on_click_pressed(FakeGesture(t=3000, button=3), 1, x, y)
        self.assertEqual(menus, [(x, y)])
        self.assertEqual(c.selection_range, (6.0, 10.0))
        self.assertEqual(c.context_track_key, VIOLIN)
        self.assertTrue(c.context_in_selection)


class TestWholeTrackSelection(unittest.TestCase):
    def setUp(self):
        self.c = make_canvas()
        self.c.load_sessions([two_track_session(60.0)], is_archive_mode=False)
        self.c.multi_track_mode = True

    def test_select_whole_track(self):
        c = self.c
        c.select_whole_tracks({VIOLIN}, notify=False)
        self.assertEqual(c.selection_range, (0.0, 60.0))
        self.assertEqual(c.get_whole_selected_track_keys(), {VIOLIN})

    def test_add_whole_track_to_partial_selection(self):
        c = self.c
        c.select_tracks_stretch({VIOLIN}, 5.0, 9.0, notify=False)
        c.select_whole_tracks({PIANO}, add=True, notify=False)
        self.assertEqual(c.selected_tracks, {VIOLIN, PIANO})
        self.assertEqual(c.track_selections[VIOLIN], [(5.0, 9.0)])
        self.assertEqual(c.get_whole_selected_track_keys(), {PIANO})

    def test_unified_view_selects_everything(self):
        c = self.c
        c.multi_track_mode = False
        c.select_whole_tracks({VIOLIN}, notify=False)
        self.assertIsNone(c.selected_tracks)
        self.assertEqual(c.selection_range, (0.0, 60.0))

    def test_select_all_and_clear(self):
        c = self.c
        c.select_all(notify=False)
        self.assertEqual(c.selection_range, (0.0, 60.0))
        self.assertIsNone(c.selected_tracks)
        c.clear_selection()
        self.assertIsNone(c.selection_range)

    def test_whole_track_export_contains_only_that_track(self):
        c = self.c
        c.select_whole_tracks({VIOLIN}, notify=False)
        md = export_and_parse(c)
        self.assertEqual({n.pitch for n in md.notes}, {76})
        self.assertEqual(len(md.notes), 30)
        self.assertEqual([t.name for t in md.tracks], ["Violin"])
        self.assertAlmostEqual(min(n.start_time for n in md.notes), 0.0, places=2)


class TestExportSpec(unittest.TestCase):
    def setUp(self):
        self.c = make_canvas()
        self.c.load_sessions([two_track_session(60.0)], is_archive_mode=False)

    def test_per_track_slice_export(self):
        c = self.c
        c.multi_track_mode = True
        c.select_tracks_stretch({PIANO}, 10.0, 20.0, notify=False)
        spec = c.get_export_spec()
        self.assertEqual(spec["selected_tracks"], {PIANO})
        md = export_and_parse(c)
        self.assertEqual({n.pitch for n in md.notes}, {48})
        # Piano notes start at 10.5, 12.5, ... 18.5 -> 5 notes, times relative to the slice start
        self.assertEqual(len(md.notes), 5)
        self.assertAlmostEqual(md.notes[0].start_time, 0.5, places=2)
        self.assertLessEqual(max(n.end_time for n in md.notes), 10.0 + 1e-3)

    def test_unified_view_exports_what_is_highlighted(self):
        """A per-track selection made in Multi-Track view is drawn full-height in the unified view, so export all tracks."""
        c = self.c
        c.multi_track_mode = True
        c.select_tracks_stretch({VIOLIN}, 10.0, 20.0, notify=False)
        c.multi_track_mode = False
        spec = c.get_export_spec()
        self.assertIsNone(spec["selected_tracks"])
        md = export_and_parse(c)
        self.assertEqual({n.pitch for n in md.notes}, {48, 76})

    def test_disjoint_whole_range_intervals(self):
        c = self.c
        c.whole_range_intervals = [(0.0, 4.0), (30.0, 34.0)]
        c._recompute_selection_bounds()
        spec = c.get_export_spec()
        self.assertEqual(spec["track_intervals"][VIOLIN], [(0.0, 4.0), (30.0, 34.0)])
        md = export_and_parse(c)
        starts = sorted(round(n.start_time, 1) for n in md.notes)
        self.assertTrue(all(s < 4.0 or s >= 30.0 for s in starts), starts)
        self.assertTrue(any(s >= 30.0 for s in starts))


def smf_chunks(data: bytes):
    """(format, [track bytes]) of an SMF file."""
    import struct
    assert data[:4] == b"MThd"
    hlen, fmt, ntrks, _div = struct.unpack(">IHHH", data[4:14])
    pos, tracks = 8 + hlen, []
    while pos < len(data):
        cid, ln = data[pos:pos + 4], struct.unpack(">I", data[pos + 4:pos + 8])[0]
        if cid == b"MTrk":
            tracks.append(data[pos + 8:pos + 8 + ln])
        pos += 8 + ln
    assert len(tracks) == ntrks
    return fmt, tracks


class TestSliceFileLayout(unittest.TestCase):
    """Slices are written like the source file and DAW exports (Dorico ignored the old bare Type 0 slices)."""

    def _write(self, keys, ts=(3, 2, 24, 8), bpm=90.0):
        midi = two_track_session()._cached_midi
        notes = [n for n in midi.notes if (n.track_index, n.channel) in keys and n.start_time < 10]
        return MidiParser.write_notes_to_bytes(notes, tempo_bpm=bpm, tracks_info=midi.tracks, time_signature=ts)

    def test_single_track_slice_is_type1_with_conductor_track(self):
        fmt, tracks = smf_chunks(self._write({VIOLIN}))
        self.assertEqual(fmt, 1)
        self.assertEqual(len(tracks), 2)
        conductor, violin = tracks
        self.assertIn(b"\xFF\x51\x03", conductor)              # tempo
        self.assertIn(b"\xFF\x58\x04\x03\x02\x18\x08", conductor)  # source time signature (3/4)
        self.assertNotIn(b"\x91", conductor)                    # no notes in the conductor track
        self.assertIn(b"\xFF\x03\x06Violin", violin)
        self.assertIn(bytes([0xC1, 40]), violin)                # program change on the violin's channel
        self.assertTrue(conductor.endswith(b"\xFF\x2F\x00") and violin.endswith(b"\xFF\x2F\x00"))

    def test_multi_track_slice_has_one_track_per_instrument(self):
        fmt, tracks = smf_chunks(self._write({VIOLIN, PIANO}))
        self.assertEqual((fmt, len(tracks)), (1, 3))
        self.assertIn(b"Violin", tracks[1])
        self.assertIn(b"Piano", tracks[2])

    def test_default_time_signature_and_round_trip(self):
        data = self._write({PIANO}, ts=None, bpm=120.0)
        _, tracks = smf_chunks(data)
        self.assertIn(b"\xFF\x58\x04\x04\x02\x18\x08", tracks[0])  # 4/4 when the source has none
        tmp = tempfile.mkdtemp(prefix="pianola-ts-")
        try:
            path = os.path.join(tmp, "x.mid")
            with open(path, "wb") as f:
                f.write(self._write({PIANO}, ts=(6, 3, 24, 8), bpm=100.0))
            md = MidiParser.parse_file(path)
            self.assertEqual(md.time_signature, (6, 3, 24, 8))
            self.assertAlmostEqual(md.tempo_bpm, 100.0, places=1)
            self.assertEqual([t.name for t in md.tracks], ["Piano"])
            self.assertEqual(len(md.notes), 5)
            self.assertAlmostEqual(md.notes[0].start_time, 0.5, places=2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestHostVisiblePath(unittest.TestCase):
    """Inside Flatpak, files handed to other apps must use host paths, not the app's private bind mounts.

    Regression: in the Builder Flatpak the archive resolved to ~/.var/app/<id>/data/midikeep (Flatpak
    bind-mounts the granted ~/.local/share/midikeep there), so dragged slices carried a URI that is an
    empty directory outside the sandbox and Dorico ignored the drop. A symlink stands in for the bind
    mount here: os.path.samefile treats both the same way.
    """

    def setUp(self):
        from unittest import mock
        import src.archive as archive_mod
        self.archive_mod = archive_mod
        self.root = tempfile.mkdtemp(prefix="pianola-flatpak-")
        self.home = os.path.join(self.root, "home")
        self.host_mk = os.path.join(self.home, ".local", "share", "midikeep")
        os.makedirs(os.path.join(self.host_mk, "export"))
        open(os.path.join(self.host_mk, "index.db"), "w").close()
        self.slice = os.path.join(self.host_mk, "export", "Pianola_Slice_1s_8s_T2_Ch2.mid")
        open(self.slice, "wb").close()
        self.app_data = os.path.join(self.home, ".var", "app", "tech.redfoxlabs.Pianola", "data")
        self.app_cache = os.path.join(self.home, ".var", "app", "tech.redfoxlabs.Pianola", "cache")
        os.makedirs(self.app_data)
        os.makedirs(self.app_cache)
        os.symlink(self.host_mk, os.path.join(self.app_data, "midikeep"))  # stands in for the bind mount
        self.env = mock.patch.dict(os.environ, {
            "HOME": self.home, "FLATPAK_ID": "tech.redfoxlabs.Pianola",
            "XDG_DATA_HOME": self.app_data, "XDG_CACHE_HOME": self.app_cache,
            "XDG_CONFIG_HOME": os.path.join(self.home, ".var", "app", "tech.redfoxlabs.Pianola", "config"),
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_sandbox_path_maps_to_host_path(self):
        sandbox_slice = os.path.join(self.app_data, "midikeep", "export", "Pianola_Slice_1s_8s_T2_Ch2.mid")
        self.assertEqual(self.archive_mod.host_visible_path(sandbox_slice), self.slice)
        self.assertEqual(self.archive_mod.host_visible_path(os.path.join(self.app_data, "midikeep")), self.host_mk)

    def test_unrelated_or_unmatched_paths_are_unchanged(self):
        other = os.path.join(self.home, "Downloads", "x.mid")
        self.assertEqual(self.archive_mod.host_visible_path(other), other)
        # A private file with no host twin (a plain app-private cache file) stays as it is
        private = os.path.join(self.app_cache, "own.mid")
        open(private, "wb").close()
        self.assertEqual(self.archive_mod.host_visible_path(private), private)

    def test_not_mapped_when_host_file_is_a_different_file(self):
        os.makedirs(os.path.join(self.home, ".cache", "pianola"))
        open(os.path.join(self.home, ".cache", "pianola", "a.mid"), "wb").close()
        os.makedirs(os.path.join(self.app_cache, "pianola"))
        private = os.path.join(self.app_cache, "pianola", "a.mid")
        open(private, "wb").close()
        self.assertEqual(self.archive_mod.host_visible_path(private), private)

    def test_outside_flatpak_nothing_changes(self):
        del os.environ["FLATPAK_ID"]
        if os.path.exists("/.flatpak-info"):
            self.skipTest("running inside a Flatpak sandbox")
        sandbox_slice = os.path.join(self.app_data, "midikeep", "export", "Pianola_Slice_1s_8s_T2_Ch2.mid")
        self.assertEqual(self.archive_mod.host_visible_path(sandbox_slice), sandbox_slice)

    def test_archive_and_export_dirs_resolve_to_host_paths(self):
        data_dir = self.archive_mod.get_midikeep_dir(config_path=os.path.join(self.root, "no-config.yaml"))
        self.assertEqual(data_dir, self.host_mk)
        am = ArchiveManager.__new__(ArchiveManager)
        am.data_dir = os.path.join(self.app_data, "midikeep")  # as resolved by the unfixed code
        self.assertEqual(am.export_dir, os.path.join(self.host_mk, "export"))


class TestEdgeAutoscroll(unittest.TestCase):
    def test_speed_profile(self):
        speed = TimelineCanvas.edge_autoscroll_speed
        w = 1000.0
        zone = TimelineCanvas.EDGE_SCROLL_ZONE
        self.assertEqual(speed(500.0, w), 0.0)
        self.assertEqual(speed(zone + 1, w), 0.0)
        self.assertEqual(speed(w - zone - 1, w), 0.0)
        self.assertLess(speed(10.0, w), 0.0)
        self.assertGreater(speed(w - 10.0, w), 0.0)
        self.assertAlmostEqual(speed(10.0, w), -speed(w - 10.0, w))
        # Deeper into the zone and past the edge scrolls faster, up to the cap
        inner, edge, beyond, far = speed(w - zone + 1, w), speed(w, w), speed(w + 60, w), speed(w + 5000, w)
        self.assertGreaterEqual(inner, TimelineCanvas.EDGE_SCROLL_MIN_SPEED)
        self.assertLess(inner, edge)
        self.assertLess(edge, beyond)
        self.assertEqual(far, TimelineCanvas.EDGE_SCROLL_MAX_SPEED)
        self.assertEqual(speed(-5000, w), -TimelineCanvas.EDGE_SCROLL_MAX_SPEED)

    def test_marquee_grows_while_holding_at_edge(self):
        c = make_canvas(width=800.0)
        c.load_sessions([two_track_session(120.0)], is_archive_mode=False)
        self.assertGreater(c.hadj.get_upper(), c.hadj.get_page_size())
        c._begin_pointer_interaction(200.0, 150.0, Gdk.ModifierType(0))
        c._marquee_pointer = (795.0, 150.0)
        c._update_marquee_from_pointer()
        end_before = c.selection_range[1]
        moved = sum(c.step_edge_autoscroll(1 / 60) for _ in range(60))
        self.assertGreater(moved, 0.0)
        self.assertAlmostEqual(c.hadj.get_value(), moved)
        self.assertGreater(c.selection_range[1], end_before + 5.0)
        self.assertAlmostEqual(c.selection_range[0], 4.0, places=1)

    def test_no_autoscroll_before_the_marquee_moves(self):
        c = make_canvas(width=800.0)
        c.load_sessions([two_track_session(120.0)], is_archive_mode=False)
        c._begin_pointer_interaction(795.0, 150.0, Gdk.ModifierType(0))
        c._marquee_pointer = (795.0, 150.0)
        self.assertEqual(c.step_edge_autoscroll(1 / 60), 0.0)
        self.assertEqual(c.hadj.get_value(), 0.0)

    def test_shift_click_extends_from_anchor(self):
        c = make_canvas(width=800.0)
        c.load_sessions([two_track_session(120.0)], is_archive_mode=False)
        c._begin_pointer_interaction(c.time_to_x(4.0), 150.0, Gdk.ModifierType(0))
        c._marquee_pointer = (c.time_to_x(10.0), 150.0)
        c._update_marquee_from_pointer()
        c._on_drag_end(None, 0.0, 0.0)
        c.hadj.set_value(c.time_to_x(40.0))
        c._begin_pointer_interaction(c.time_to_x(45.0) - c.hadj.get_value(), 150.0, Gdk.ModifierType.SHIFT_MASK)
        self.assertAlmostEqual(c.selection_range[0], 4.0, places=1)
        self.assertAlmostEqual(c.selection_range[1], 45.0, places=1)


class TestKeybedLayout(unittest.TestCase):
    def test_octave_geometry(self):
        whites, blacks = keyboard_key_layout(48, 59)  # C3..B3
        heights = {p % 12: hi - lo for p, lo, hi in whites}
        self.assertEqual(heights, {0: 1.5, 2: 2.0, 4: 1.5, 5: 1.5, 7: 2.0, 9: 2.0, 11: 1.5})
        self.assertEqual(sum(heights.values()), 12.0)
        self.assertEqual([p % 12 for p, _, _ in blacks], [1, 3, 6, 8, 10])
        self.assertTrue(all(hi - lo == 1.0 for _, lo, hi in blacks))

    def test_white_keys_tile_the_range_and_wrap_black_keys(self):
        whites, blacks = keyboard_key_layout(45, 84)
        whites.sort(key=lambda w: w[1])
        self.assertAlmostEqual(whites[0][1], 44.5)
        self.assertAlmostEqual(whites[-1][2], 84.5)
        for (_, _, hi), (_, lo, _) in zip(whites, whites[1:]):
            self.assertAlmostEqual(hi, lo)  # no gaps, no overlaps between white keys
        for p, lo, hi in blacks:
            self.assertTrue(is_black_key(p))
            # Each black key sits on top of the seam between its two white neighbours
            self.assertTrue(any(w_lo < p < w_hi or abs(w_hi - p) < 1e-9 for _, w_lo, w_hi in whites))

    def test_range_edges_are_clipped(self):
        whites, _ = keyboard_key_layout(61, 66)  # starts on C#, ends on F#
        self.assertGreaterEqual(min(lo for _, lo, _ in whites), 60.5)
        self.assertLessEqual(max(hi for _, _, hi in whites), 66.5)


def _register_gresource():
    """window.py needs the compiled UI resource; use $PIANOLA_GRESOURCE or compile one if the tool exists."""
    path = os.environ.get("PIANOLA_GRESOURCE")
    if not path:
        tool = shutil.which("glib-compile-resources")
        if not tool:
            return False
        src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
        path = os.path.join(tempfile.mkdtemp(prefix="pianola-gres-"), "pianola.gresource")
        subprocess.run([tool, f"--sourcedir={src_dir}", f"--target={path}", os.path.join(src_dir, "pianola.gresource.xml")], check=True)
    Gio.resources_register(Gio.Resource.load(path))
    return True


class TestDragContentProvider(unittest.TestCase):
    """The window's drag/copy provider yields a valid .mid of exactly the selection."""

    @classmethod
    def setUpClass(cls):
        if not _register_gresource():
            raise unittest.SkipTest("glib-compile-resources not available (set PIANOLA_GRESOURCE to run)")
        import src.window as window_mod
        cls.W = window_mod.PianolaWindow

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pianola-drag-")
        self.c = make_canvas()
        self.c.load_sessions([two_track_session(60.0)], is_archive_mode=False)
        self.c.multi_track_mode = True
        am = ArchiveManager.__new__(ArchiveManager)
        am.data_dir = self.tmp
        win = types.SimpleNamespace(canvas=self.c, archive_mgr=am, player=self.c.player)
        for name in ("_collect_timeline_notes", "_export_active_selection_to_path", "_selection_file_name", "_create_drag_content_provider"):
            setattr(win, name, types.MethodType(getattr(self.W, name), win))
        self.win = win

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _serialize(provider, mime) -> bytes:
        """Read one format out of the provider the way a drop target or clipboard reader does."""
        stream = Gio.MemoryOutputStream.new_resizable()
        loop = GLib.MainLoop()
        result = {}

        def done(p, res):
            try:
                p.write_mime_type_finish(res)
            except Exception as e:  # pragma: no cover - reported below
                result["error"] = e
            loop.quit()

        provider.write_mime_type_async(mime, stream, GLib.PRIORITY_DEFAULT, None, done)
        GLib.timeout_add_seconds(5, loop.quit)
        loop.run()
        if "error" in result:
            raise result["error"]
        stream.close(None)
        return bytes(stream.steal_as_bytes().get_data())

    def _provider_file(self, provider):
        formats = provider.ref_formats()
        self.assertTrue(formats.contain_gtype(Gdk.FileList))
        for mime in ("text/uri-list", "audio/midi"):
            self.assertTrue(formats.contain_mime_type(mime), mime)
        uris = [u for u in self._serialize(provider, "text/uri-list").decode().splitlines() if u.strip()]
        self.assertEqual(len(uris), 1)
        path = Gio.File.new_for_uri(uris[0]).get_path()
        with open(path, "rb") as f:
            self.assertEqual(self._serialize(provider, "audio/midi"), f.read())
        return path

    def test_lane_selection_drag_yields_slice_file(self):
        self.c.select_tracks_stretch({VIOLIN}, 6.0, 10.0, notify=False)
        path = self._provider_file(self.win._create_drag_content_provider(use_selection=True))
        self.assertEqual(os.path.basename(path), "Pianola_Slice_6s_10s_T2_Ch2.mid")
        md = MidiParser.parse_file(path)
        self.assertEqual({n.pitch for n in md.notes}, {76})
        self.assertEqual(len(md.notes), 2)  # violin notes at 6.0 and 8.0

    def test_whole_track_drag_is_named_after_the_track(self):
        self.c.select_whole_tracks({PIANO}, notify=False)
        path = self._provider_file(self.win._create_drag_content_provider(use_selection=True))
        self.assertEqual(os.path.basename(path), "Pianola_Track_Piano_T3_Ch1.mid")
        md = MidiParser.parse_file(path)
        self.assertEqual({n.pitch for n in md.notes}, {48})
        self.assertEqual(len(md.notes), 30)


if __name__ == '__main__':
    unittest.main()
