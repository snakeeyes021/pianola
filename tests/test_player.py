# test_player.py
#
# Copyright 2026 Matthew Samson
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for src/player.py audio synthesis and acoustic scrubbing."""

import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.archive import NoteEvent, Section, MidiData
from src.player import AudioPlayer, NullSynthEngine


def test_acoustic_scrubbing():
    print("[TEST] test_acoustic_scrubbing...")
    synth = NullSynthEngine()
    player = AudioPlayer(synth=synth)

    # Note 1: 1.0s to 3.0s (pitch 60)
    # Note 2: 2.0s to 4.0s (pitch 64)
    # Gap: 4.0s to 8.0s (4.0s silence)
    # Note 3: 8.0s to 10.0s (pitch 67)
    notes = [
        NoteEvent(pitch=60, velocity=100, start_time=1.0, end_time=3.0, channel=0),
        NoteEvent(pitch=64, velocity=90, start_time=2.0, end_time=4.0, channel=0),
        NoteEvent(pitch=67, velocity=110, start_time=8.0, end_time=10.0, channel=0),
    ]
    sections = [
        Section(start_time=1.0, end_time=4.0),
        Section(start_time=8.0, end_time=10.0)
    ]
    data = MidiData(duration=10.0, notes=notes, sections=sections)
    player.load_midi_data(data)

    # 1. Audit at 0.5s -> no notes sounding
    player.audit_at(0.5)
    assert len(synth.active_sounding_notes) == 0, f"Expected 0 sounding notes at 0.5s, got {synth.active_sounding_notes}"

    # 2. Audit at 1.5s -> only note 60 should sound
    player.audit_at(1.5)
    assert (0, 60) in synth.active_sounding_notes
    assert len(synth.active_sounding_notes) == 1

    # 3. Audit at 2.5s -> both note 60 and note 64 should sound (chord sustain)
    player.audit_at(2.5)
    assert (0, 60) in synth.active_sounding_notes
    assert (0, 64) in synth.active_sounding_notes
    assert len(synth.active_sounding_notes) == 2

    # 4. Audit at 3.5s -> note 60 damped, only note 64 sounding
    player.audit_at(3.5)
    assert (0, 60) not in synth.active_sounding_notes
    assert (0, 64) in synth.active_sounding_notes
    assert len(synth.active_sounding_notes) == 1

    # 5. Audit at 5.0s (silence gap) -> all damped
    player.audit_at(5.0)
    assert len(synth.active_sounding_notes) == 0

    # 6. Audit at 9.0s -> note 67 sounding
    player.audit_at(9.0)
    assert (0, 67) in synth.active_sounding_notes
    assert len(synth.active_sounding_notes) == 1

    # 7. End scrub -> damped
    player.end_scrub()
    assert len(synth.active_sounding_notes) == 0

    print("  ✓ Passed acoustic scrubbing (hover sustain and note damping).")


def test_bidirectional_gap_and_section_skipping():
    print("[TEST] test_bidirectional_gap_and_section_skipping...")
    synth = NullSynthEngine()
    player = AudioPlayer(synth=synth)

    # Notes:
    # Section 1: Note at 1.0s - 2.0s
    # Gap: 2.0s to 6.0s (4s silence)
    # Section 2: Note at 6.0s - 7.0s
    notes = [
        NoteEvent(pitch=60, velocity=100, start_time=1.0, end_time=2.0),
        NoteEvent(pitch=70, velocity=100, start_time=6.0, end_time=7.0),
    ]
    sections = [
        Section(start_time=1.0, end_time=2.0),
        Section(start_time=6.0, end_time=7.0)
    ]
    data = MidiData(duration=10.0, notes=notes, sections=sections)
    player.load_midi_data(data)

    # Forward gap skip from 2.5s (in the silence gap)
    target_fwd = player.skip_gap_forward(2.5)
    assert round(target_fwd, 1) == 6.0, f"Expected jump forward to 6.0s, got {target_fwd}"

    # Backward gap skip from 5.5s (in the silence gap)
    target_bwd = player.skip_gap_backward(5.5)
    assert round(target_bwd, 1) == 1.0, f"Expected jump backward to 1.0s, got {target_bwd}"

    # Section jumps
    player.seek(0.0)
    assert round(player.jump_next_section(), 1) == 1.0
    assert round(player.jump_next_section(), 1) == 6.0
    assert round(player.jump_prev_section(), 1) == 1.0
    assert round(player.jump_prev_section(), 1) == 0.0

    print("  ✓ Passed bidirectional gap skipping and section jumping.")


def test_transport_playback():
    print("[TEST] test_transport_playback...")
    synth = NullSynthEngine()
    player = AudioPlayer(synth=synth)

    notes = [
        NoteEvent(pitch=60, velocity=100, start_time=0.05, end_time=0.2),
    ]
    data = MidiData(duration=0.3, notes=notes, sections=[Section(0.05, 0.2)])
    player.load_midi_data(data)

    ticks = []
    player.on_tick = lambda t: ticks.append(t)

    # Start playback
    player.play(from_time=0.0)
    time.sleep(0.15)
    assert player.is_playing is True
    assert len(ticks) > 0

    # Pause
    player.pause()
    assert player.is_playing is False

    # Seek
    player.seek(0.05)
    assert round(player.current_time, 2) == 0.05

    player.stop()
    assert round(player.current_time, 2) == 0.0

    print("  ✓ Passed transport playback, pause, seek, and tick callbacks.")


def test_program_change_dispatch():
    print("[TEST] test_program_change_dispatch...")
    from src.archive import TrackInfo
    synth = NullSynthEngine()
    player = AudioPlayer(synth=synth)

    tracks = [
        TrackInfo(track_index=0, channel=0, name="Violin", program=40, instrument_name="Violin", min_pitch=60, max_pitch=72, note_count=5),
        TrackInfo(track_index=1, channel=1, name="Acoustic Grand Piano", program=0, instrument_name="Acoustic Grand Piano", min_pitch=40, max_pitch=80, note_count=10),
    ]
    program_changes = [
        (0.0, 0, 40),
        (0.0, 1, 0),
        (5.0, 0, 41), # Switch violin to viola at 5.0s
    ]

    notes = [
        NoteEvent(pitch=60, velocity=100, start_time=1.0, end_time=3.0, channel=0),
        NoteEvent(pitch=48, velocity=100, start_time=1.0, end_time=3.0, channel=1),
    ]

    data = MidiData(duration=10.0, notes=notes, tracks=tracks, program_changes=program_changes)
    player.load_midi_data(data)

    # In default unified mode, all channels are clamped to Acoustic Grand Piano (program 0)
    assert (0, 0) in synth.program_changes_received
    assert (0, 40) not in synth.program_changes_received

    # Enable multi-track mode
    synth.program_changes_received.clear()
    player.set_multi_track_mode(True)
    assert (0, 40) in synth.program_changes_received, "Initial violin program change not received in multi-track mode"
    assert (1, 0) in synth.program_changes_received, "Initial piano program change not received in multi-track mode"

    # Seek to 6.0s (past the program change at 5.0s)
    player.seek(6.0)
    assert (0, 41) in synth.program_changes_received, "Mid-stream program change on seek not received"

    # Switch back to unified mode
    synth.program_changes_received.clear()
    player.set_multi_track_mode(False)
    assert (0, 0) in synth.program_changes_received
    assert (1, 0) in synth.program_changes_received

    print("  ✓ Passed unified single-instrument vs multi-track program routing and seek synchronization.")


if __name__ == "__main__":
    test_acoustic_scrubbing()
    test_bidirectional_gap_and_section_skipping()
    test_transport_playback()
    test_program_change_dispatch()
    print("\n🎉 ALL PLAYER TESTS PASSED SUCCESSFULLY!")
