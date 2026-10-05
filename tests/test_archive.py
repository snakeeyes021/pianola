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
                );
            """)
        conn.close()

        mgr = ArchiveManager(tmpdir)
        assert mgr.archive_exists(), "Archive should exist"

        sessions = mgr.load_sessions()
        assert len(sessions) == 2, f"Expected 2 sessions, got {len(sessions)}"
        assert sessions[0].note_count == 250
        assert sessions[1].starred is True
        assert "Clapper" in (sessions[1].notes or "")

        # Test latest session retrieval
        latest = mgr.get_latest_session()
        assert latest is not None
        assert latest.id == 2

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


if __name__ == "__main__":
    test_midi_write_and_parse()
    test_marquee_slice_export()
    test_sqlite_archive_operations()
    test_live_journal_detection()
    print("\n🎉 ALL TESTS PASSED SUCCESSFULLY!")
