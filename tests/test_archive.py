# test_archive.py
#
# Copyright 2026 Matthew Samson
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit and smoke tests for src/archive.py."""

import os
import sys
import tempfile
import sqlite3
from datetime import datetime

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.archive import (
    MidiParser, NoteEvent, MarkerEvent, ArchiveManager, SessionRecord
)


def test_midi_write_and_parse():
    print("[TEST] test_midi_write_and_parse...")
    with tempfile.TemporaryDirectory() as tmpdir:
        midi_path = os.path.join(tmpdir, "sample.mid")

        # Create notes:
        # Note 1: 0.0s to 1.0s (C4, 60)
        # Note 2: 1.0s to 2.0s (E4, 64)
        # GAP: 2.0s to 5.5s (3.5s silence -> section boundary!)
        # Note 3: 5.5s to 7.0s (G4, 67)
        notes = [
            NoteEvent(pitch=60, velocity=100, start_time=0.0, end_time=1.0, channel=0),
            NoteEvent(pitch=64, velocity=90, start_time=1.0, end_time=2.0, channel=0),
            NoteEvent(pitch=67, velocity=110, start_time=5.5, end_time=7.0, channel=0),
        ]

        # Write MIDI file
        MidiParser.write_notes_to_file(notes, midi_path, tempo_bpm=120.0)
        assert os.path.exists(midi_path), "MIDI file was not generated"

        # Parse MIDI file
        parsed = MidiParser.parse_file(midi_path)
        assert len(parsed.notes) == 3, f"Expected 3 notes, got {len(parsed.notes)}"
        assert parsed.notes[0].pitch == 60
        assert parsed.notes[1].pitch == 64
        assert parsed.notes[2].pitch == 67

        # Check section detection: silence between 2.0s and 5.5s is 3.5s (>= 3.0s threshold)
        assert len(parsed.sections) == 2, f"Expected 2 sections, got {len(parsed.sections)}"
        assert parsed.sections[0].start_time == 0.0
        assert round(parsed.sections[0].end_time, 1) == 2.0
        assert round(parsed.sections[1].start_time, 1) == 5.5

        # Check duration and active play time
        assert round(parsed.duration, 1) >= 7.0
        assert round(parsed.active_play_seconds, 1) == 3.5  # 1.0 + 1.0 + 1.5

        print("  ✓ Passed MIDI write, parse, and 3s silence section detection.")


def test_marquee_slice_export():
    print("[TEST] test_marquee_slice_export...")
    with tempfile.TemporaryDirectory() as tmpdir:
        archive_mgr = ArchiveManager(tmpdir)
        slice_path = os.path.join(tmpdir, "slice.mid")

        notes = [
            NoteEvent(pitch=60, velocity=100, start_time=1.0, end_time=3.0, channel=0),
            NoteEvent(pitch=62, velocity=95, start_time=2.0, end_time=4.0, channel=0),
            NoteEvent(pitch=64, velocity=90, start_time=5.0, end_time=6.0, channel=0),
        ]

        # Slice between 1.5s and 3.5s
        archive_mgr.export_slice(notes, start_time=1.5, end_time=3.5, dest_path=slice_path, tempo_bpm=120.0)
        assert os.path.exists(slice_path), "Slice file was not created"

        parsed = MidiParser.parse_file(slice_path)
        # Both note 60 and note 62 overlap the [1.5, 3.5] window; note 64 does not.
        assert len(parsed.notes) == 2, f"Expected 2 sliced notes, got {len(parsed.notes)}"
        # Offsets should be normalized relative to 1.5s
        assert round(parsed.notes[0].start_time, 2) == 0.0  # note 60 clamped at 1.5s -> 0.0s
        assert round(parsed.notes[1].start_time, 2) == 0.5  # note 62 started at 2.0s -> 0.5s

        print("  ✓ Passed marquee slice extraction and normalization.")


def test_sqlite_archive_operations():
    print("[TEST] test_sqlite_archive_operations...")
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "index.db")
        conn = sqlite3.connect(db_path)
        with conn:
            conn.execute("""
                CREATE TABLE sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_time TIMESTAMP NOT NULL,
                    end_time TIMESTAMP NOT NULL,
                    duration_seconds REAL NOT NULL,
                    active_play_seconds REAL NOT NULL,
                    note_count INTEGER NOT NULL,
                    device_name TEXT NOT NULL,
                    file_path TEXT NOT NULL UNIQUE,
                    key_signature TEXT,
                    tempo_bpm REAL DEFAULT 120.0,
                    starred BOOLEAN DEFAULT 0,
                    notes TEXT
                );
            """)
            conn.execute("""
                INSERT INTO sessions (
                    start_time, end_time, duration_seconds, active_play_seconds,
                    note_count, device_name, file_path, tempo_bpm, starred, notes
                ) VALUES (
                    '2026-10-05T12:00:00', '2026-10-05T12:05:00', 300.0, 180.0,
                    250, 'Keystation 88', '/tmp/fake1.mid', 120.0, 0, 'First take'
                ), (
                    '2026-10-05T14:00:00', '2026-10-05T14:10:00', 600.0, 420.0,
                    800, 'Keystation 88', '/tmp/fake2.mid', 128.0, 1, 'Marker: Clapper ★'
                ), (
                    '2026-10-06T09:30:00', '2026-10-06T09:40:00', 600.0, 400.0,
                    500, 'Keystation 88', '/tmp/fake3.mid', 120.0, 0, 'Next day take'
                );
            """)
        conn.close()

        mgr = ArchiveManager(tmpdir)
        assert mgr.archive_exists(), "Archive should exist"

        sessions = mgr.load_sessions()
        assert len(sessions) == 3, f"Expected 3 sessions, got {len(sessions)}"
        assert sessions[0].note_count == 250
        assert sessions[1].starred is True
        assert "Clapper" in (sessions[1].notes or "")

        # Test available dates discovery
        dates = mgr.get_available_dates()
        from datetime import date
        d1 = sessions[0].start_time.date()
        d2 = sessions[2].start_time.date()
        assert dates == [d1, d2], f"Expected {[d1, d2]}, got {dates}"

        # Test day-scoped session loading
        day1_sessions = mgr.load_sessions_for_date(d1)
        assert len(day1_sessions) == 2
        assert day1_sessions[0].id == 1
        assert day1_sessions[1].id == 2

        day2_sessions = mgr.load_sessions_for_date(d2)
        assert len(day2_sessions) == 1
        assert day2_sessions[0].id == 3

        empty_day_sessions = mgr.load_sessions_for_date(date(2026, 10, 20))
        assert len(empty_day_sessions) == 0

        # Test latest session retrieval
        latest = mgr.get_latest_session()
        assert latest is not None
        assert latest.id == 3

        # Test starring toggle
        assert mgr.set_starred(session_id=1, starred=True)
        sessions_updated = mgr.load_sessions()
        assert sessions_updated[0].starred is True

        print("  ✓ Passed SQLite session index queries and star toggling.")


def test_live_journal_detection():
    print("[TEST] test_live_journal_detection...")
    with tempfile.TemporaryDirectory() as tmpdir:
        journal_dir = os.path.join(tmpdir, "journal")
        os.makedirs(journal_dir, exist_ok=True)

        live_file = os.path.join(journal_dir, "live_recording.mid")
        notes = [NoteEvent(pitch=60, velocity=100, start_time=0.0, end_time=1.0)]
        MidiParser.write_notes_to_file(notes, live_file)

        mgr = ArchiveManager(tmpdir)
        live_session = mgr.get_live_session()
        assert live_session is not None, "Failed to detect live session"
        assert live_session.is_live is True
        assert live_session.device_name == "Live Session"

        # Check get_latest_session prefers live session
        latest = mgr.get_latest_session()
        assert latest is not None
        assert latest.is_live is True

        print("  ✓ Passed live journal detection.")


def test_sustain_pedal_parsing():
    print('[TEST] test_sustain_pedal_parsing...')
    import struct, tempfile
    # Note On at t=0, Note Off at t=100 ticks, CC 64 down at t=50 ticks, CC 64 up at t=250 ticks
    header = struct.pack('>4sIHHH', b'MThd', 6, 0, 1, 480)
    track_events = bytearray()
    track_events.extend(bytes([0x00, 0x90, 0x3C, 0x64]))       # t=0: Note On (pitch 60, vel 100)
    track_events.extend(bytes([0x32, 0xB0, 0x40, 0x7F]))       # t=50 (+50): CC 64 val 127 (Pedal Down)
    track_events.extend(bytes([0x32, 0x80, 0x3C, 0x00]))       # t=100 (+50): Note Off
    track_events.extend(bytes([0x81, 0x16, 0xB0, 0x40, 0x00])) # t=250 (+150): CC 64 val 0 (Pedal Up)
    track_events.extend(bytes([0x0A, 0xFF, 0x2F, 0x00]))       # t=260 (+10): End of track

    track = struct.pack('>4sI', b'MTrk', len(track_events)) + bytes(track_events)
    with tempfile.NamedTemporaryFile(suffix='.mid', delete=False) as f:
        f.write(header + track)
        tmp_name = f.name

    try:
        data = MidiParser.parse_file(tmp_name)
        assert len(data.notes) == 1, f'Expected 1 note, got {len(data.notes)}'
        note = data.notes[0]
        assert note.end_time > 0.20, f'Expected note extended by pedal past 0.20s, got end_time={note.end_time}'
        print('  ✓ Passed CC 64 damper/sustain pedal note extension.')
    finally:
        os.unlink(tmp_name)




def test_multi_track_parsing():
    print('[TEST] test_multi_track_parsing...')
    import struct, tempfile
    from src.archive import get_gm_instrument_name, get_track_color

    # Header: Type 1, 2 tracks, 480 ticks/quarter
    header = struct.pack('>4sIHHH', b'MThd', 6, 1, 2, 480)

    # Track 1: Violin (Program 40), Channel 0
    t1_events = bytearray()
    t1_events.extend(bytes([0x00, 0xC0, 40]))          # Program Change: Violin (40)
    t1_events.extend(bytes([0x00, 0x90, 60, 100]))     # Note On (pitch 60)
    t1_events.extend(bytes([0x81, 0x70, 0x80, 60, 0])) # Note Off at 240 ticks
    t1_events.extend(bytes([0x00, 0xFF, 0x2F, 0x00]))  # End of track
    t1_chunk = struct.pack('>4sI', b'MTrk', len(t1_events)) + bytes(t1_events)

    # Track 2: Grand Piano (Program 0), Channel 1
    t2_events = bytearray()
    t2_events.extend(bytes([0x00, 0xC1, 0]))           # Program Change: Grand Piano (0)
    t2_events.extend(bytes([0x00, 0x91, 48, 90]))      # Note On (pitch 48)
    t2_events.extend(bytes([0x81, 0x70, 0x81, 48, 0])) # Note Off at 240 ticks
    t2_events.extend(bytes([0x00, 0xFF, 0x2F, 0x00]))  # End of track
    t2_chunk = struct.pack('>4sI', b'MTrk', len(t2_events)) + bytes(t2_events)

    with tempfile.NamedTemporaryFile(suffix='.mid', delete=False) as f:
        f.write(header + t1_chunk + t2_chunk)
        tmp_name = f.name

    try:
        data = MidiParser.parse_file(tmp_name)
        assert len(data.tracks) == 2, f'Expected 2 tracks, got {len(data.tracks)}'
        assert data.tracks[0].program == 40
        assert 'Violin' in data.tracks[0].instrument_name
        assert data.tracks[0].min_pitch == 60 and data.tracks[0].max_pitch == 60
        assert data.tracks[1].program == 0
        assert 'Piano' in data.tracks[1].instrument_name
        assert data.tracks[1].min_pitch == 48 and data.tracks[1].max_pitch == 48

        color_v = get_track_color(program=40, channel=0)
        color_p = get_track_color(program=0, channel=1)
        assert color_v != color_p
        print('  ✓ Passed multi-track multi-instrument parsing and GM metadata.')
    finally:
        os.unlink(tmp_name)


def test_midikeep_shared_config_archive_location():
    print("[TEST] test_midikeep_shared_config_archive_location...")
    from src.archive import get_midikeep_dir, get_midikeep_config_path, load_midikeep_config

    with tempfile.TemporaryDirectory() as tmpdir:
        custom_archive = os.path.join(tmpdir, "custom_midikeep_archive")
        sessions_dir = os.path.join(custom_archive, "sessions", "2026-10")
        os.makedirs(sessions_dir, exist_ok=True)

        # Create a session MIDI file inside the relocated archive
        rel_midi_path = os.path.join(sessions_dir, "take1.mid")
        MidiParser.write_notes_to_file(
            [NoteEvent(pitch=60, velocity=100, start_time=0.0, end_time=1.0, channel=0)],
            rel_midi_path,
        )

        # Create index.db where file_path was recorded under an old ~/.local/share/midikeep path
        db_path = os.path.join(custom_archive, "index.db")
        conn = sqlite3.connect(db_path)
        with conn:
            conn.execute("""
                CREATE TABLE sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_time TIMESTAMP NOT NULL,
                    end_time TIMESTAMP NOT NULL,
                    duration_seconds REAL NOT NULL,
                    active_play_seconds REAL NOT NULL,
                    note_count INTEGER NOT NULL,
                    device_name TEXT NOT NULL,
                    file_path TEXT NOT NULL UNIQUE,
                    key_signature TEXT,
                    tempo_bpm REAL DEFAULT 120.0,
                    starred BOOLEAN DEFAULT 0,
                    notes TEXT
                );
            """)
            conn.execute("""
                INSERT INTO sessions (
                    start_time, end_time, duration_seconds, active_play_seconds,
                    note_count, device_name, file_path, tempo_bpm, starred, notes
                ) VALUES (
                    '2026-10-05T12:00:00', '2026-10-05T12:00:01', 1.0, 1.0,
                    1, 'Piano', '/old/home/.local/share/midikeep/sessions/2026-10/take1.mid', 120.0, 0, ''
                );
            """)
        conn.close()

        # 1. Explicit config_path with data_dir and other midikeep-daemon settings
        cfg_file = os.path.join(tmpdir, "config.yaml")
        with open(cfg_file, "w", encoding="utf-8") as f:
            f.write(
                f"# Shared Midikeep configuration\n"
                f"silence_threshold_seconds: 300\n"
                f"data_dir: \"{custom_archive}\"\n"
                f"device_blacklist:\n"
                f"  - Midi Through\n"
            )

        parsed_cfg = load_midikeep_config(cfg_file)
        assert parsed_cfg.get("data_dir") == custom_archive
        assert get_midikeep_dir(cfg_file) == os.path.abspath(custom_archive)

        mgr = ArchiveManager(config_path=cfg_file)
        assert mgr.data_dir == os.path.abspath(custom_archive)
        assert mgr.archive_exists()
        sessions = mgr.load_sessions()
        assert len(sessions) == 1
        # Relocated session file path should resolve into custom_archive/sessions/...
        assert sessions[0].file_path == os.path.abspath(rel_midi_path)
        assert len(sessions[0].get_midi_data().notes) == 1

        # 2. XDG_CONFIG_HOME discovery and fallback when config is absent or omits data_dir
        xdg_cfg = os.path.join(tmpdir, "xdg_config")
        xdg_data = os.path.join(tmpdir, "xdg_data")
        mk_cfg_dir = os.path.join(xdg_cfg, "midikeep")
        os.makedirs(mk_cfg_dir, exist_ok=True)
        xdg_cfg_file = os.path.join(mk_cfg_dir, "config.yaml")

        old_xdg_cfg = os.environ.get("XDG_CONFIG_HOME")
        old_xdg_data = os.environ.get("XDG_DATA_HOME")
        old_mk_cfg = os.environ.get("MIDIKEEP_CONFIG")
        try:
            os.environ["XDG_CONFIG_HOME"] = xdg_cfg
            os.environ["XDG_DATA_HOME"] = xdg_data
            os.environ.pop("MIDIKEEP_CONFIG", None)

            # Fallback when config.yaml does not exist yet
            assert get_midikeep_config_path() == xdg_cfg_file
            assert get_midikeep_dir() == os.path.abspath(os.path.join(xdg_data, "midikeep"))

            # Config file exists without data_dir -> still falls back to default data_dir
            with open(xdg_cfg_file, "w", encoding="utf-8") as f:
                f.write("silence_threshold_seconds: 300\n")
            assert get_midikeep_dir() == os.path.abspath(os.path.join(xdg_data, "midikeep"))

            # Config file specifies data_dir with environment variable expansion
            os.environ["TEST_MIDIKEEP_ARCHIVE"] = custom_archive
            with open(xdg_cfg_file, "w", encoding="utf-8") as f:
                f.write("data_dir: $TEST_MIDIKEEP_ARCHIVE\n")
            assert get_midikeep_dir() == os.path.abspath(custom_archive)
        finally:
            os.environ.pop("TEST_MIDIKEEP_ARCHIVE", None)
            if old_xdg_cfg is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = old_xdg_cfg
            if old_xdg_data is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = old_xdg_data
            if old_mk_cfg is not None:
                os.environ["MIDIKEEP_CONFIG"] = old_mk_cfg

        print("  ✓ Passed shared config.yaml archive location discovery and fallback.")


def test_per_track_slice_export():
    print("[TEST] test_per_track_slice_export...")
    from src.archive import TrackInfo

    with tempfile.TemporaryDirectory() as tmpdir:
        archive_mgr = ArchiveManager(tmpdir)
        tracks_info = [
            TrackInfo(track_index=0, channel=0, name="Violin", program=40, instrument_name="Violin"),
            TrackInfo(track_index=1, channel=1, name="Cello", program=42, instrument_name="Cello"),
            TrackInfo(track_index=2, channel=2, name="Flute", program=73, instrument_name="Flute"),
        ]
        notes = [
            NoteEvent(pitch=67, velocity=100, start_time=1.0, end_time=3.0, channel=0, track_index=0),
            NoteEvent(pitch=48, velocity=95, start_time=1.0, end_time=3.0, channel=1, track_index=1),
            NoteEvent(pitch=72, velocity=90, start_time=1.0, end_time=3.0, channel=2, track_index=2),
        ]

        # 1. Single-track selection export (only Violin on track 0, ch 0)
        single_path = os.path.join(tmpdir, "single_track.mid")
        archive_mgr.export_slice(
            notes,
            start_time=1.0,
            end_time=3.0,
            dest_path=single_path,
            selected_tracks={(0, 0)},
            tracks_info=tracks_info,
        )
        parsed_single = MidiParser.parse_file(single_path)
        assert len(parsed_single.notes) == 1
        assert parsed_single.notes[0].pitch == 67
        assert parsed_single.notes[0].channel == 0
        assert parsed_single.tracks[0].program == 40

        # 2. Multi-track selection export with per-track intervals (Violin [1.0, 2.0], Flute [2.0, 3.0], Cello excluded)
        multi_path = os.path.join(tmpdir, "multi_track.mid")
        archive_mgr.export_slice(
            notes,
            start_time=1.0,
            end_time=3.0,
            dest_path=multi_path,
            selected_tracks={(0, 0), (2, 2)},
            track_intervals={
                (0, 0): [(1.0, 2.0)],
                (2, 2): [(2.0, 3.0)],
            },
            tracks_info=tracks_info,
        )
        parsed_multi = MidiParser.parse_file(multi_path)
        assert len(parsed_multi.notes) == 2
        assert len(parsed_multi.tracks) == 2
        by_pitch = {n.pitch: n for n in parsed_multi.notes}
        assert round(by_pitch[67].start_time, 2) == 0.0
        assert round(by_pitch[67].end_time, 2) == 1.0
        assert round(by_pitch[72].start_time, 2) == 1.0
        assert round(by_pitch[72].end_time, 2) == 2.0

        print("  ✓ Passed single-track and multi-track slice export.")


if __name__ == "__main__":
    test_midi_write_and_parse()
    test_marquee_slice_export()
    test_sqlite_archive_operations()
    test_live_journal_detection()
    test_sustain_pedal_parsing()
    test_multi_track_parsing()
    test_midikeep_shared_config_archive_location()
    test_per_track_slice_export()
    print("\n🎉 ALL ARCHIVE TESTS PASSED SUCCESSFULLY!")
