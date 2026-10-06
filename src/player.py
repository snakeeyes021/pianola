# player.py
#
# Copyright 2026 Matthew Samson
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Audio synthesis and transport playback engine for Pianola.

Provides:
- Dynamic FluidSynth ctypes binding with graceful null/fallback engine.
- Acoustic scrubbing (hover note sustain on mouse scrub).
- Transport controls: Play, Pause, Seek, Stop.
- Bidirectional silence / gap skipping (forward and backward).
- Section jump navigation (utilizing >= 3.0s silence boundaries).
"""

import os
import sys
import time
import glob
import ctypes
import threading
from typing import List, Optional, Set, Callable
from dataclasses import dataclass

from .archive import NoteEvent, Section, MidiData


# Standard SoundFont search paths
DEFAULT_SOUNDFONT_PATHS = [
    "/app/share/soundfonts/default.sf2",
    "/app/share/soundfonts/FluidR3_GM.sf2",
    "/usr/share/soundfonts/default.sf2",
    "/usr/share/soundfonts/FluidR3_GM.sf2",
    "/usr/share/sounds/sf2/default.sf2",
    "/usr/share/sounds/sf2/FluidR3_GM.sf2",
    "/usr/share/soundfonts/freepats-general-midi.sf2",
]


class BaseSynth:
    """Interface for MIDI synthesis."""

    def note_on(self, channel: int, pitch: int, velocity: int):
        pass

    def note_off(self, channel: int, pitch: int):
        pass

    def all_notes_off(self):
        pass

    def close(self):
        pass


class FluidSynthEngine(BaseSynth):
    """Real FluidSynth engine loaded dynamically via ctypes."""

    def __init__(self, soundfont_path: Optional[str] = None):
        self._lib = None
        self._settings = None
        self._synth = None
        self._adriver = None
        self.is_ready = False

        self._init_fluid(soundfont_path)

    def _init_fluid(self, soundfont_path: Optional[str]):
        # Search for libfluidsynth
        libname = ctypes.util.find_library("fluidsynth") or "libfluidsynth.so.3"
        try:
            self._lib = ctypes.CDLL(libname)
        except OSError:
            try:
                self._lib = ctypes.CDLL("libfluidsynth.so.2")
            except OSError:
                try:
                    self._lib = ctypes.CDLL("libfluidsynth.so")
                except OSError:
                    self._lib = None
                    return

        # Setup ctypes signatures
        self._lib.new_fluid_settings.restype = ctypes.c_void_p
        self._lib.delete_fluid_settings.argtypes = [ctypes.c_void_p]

        self._lib.fluid_settings_setstr.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
        self._lib.fluid_settings_setstr.restype = ctypes.c_int

        self._lib.new_fluid_synth.argtypes = [ctypes.c_void_p]
        self._lib.new_fluid_synth.restype = ctypes.c_void_p
        self._lib.delete_fluid_synth.argtypes = [ctypes.c_void_p]

        self._lib.fluid_synth_sfload.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        self._lib.fluid_synth_sfload.restype = ctypes.c_int

        self._lib.new_fluid_audio_driver.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._lib.new_fluid_audio_driver.restype = ctypes.c_void_p
        self._lib.delete_fluid_audio_driver.argtypes = [ctypes.c_void_p]

        self._lib.fluid_synth_noteon.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self._lib.fluid_synth_noteon.restype = ctypes.c_int

        self._lib.fluid_synth_noteoff.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        self._lib.fluid_synth_noteoff.restype = ctypes.c_int

        self._lib.fluid_synth_all_notes_off.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._lib.fluid_synth_all_notes_off.restype = ctypes.c_int

        # Initialize settings
        self._settings = self._lib.new_fluid_settings()
        if not self._settings:
            return

        # Try PulseAudio / PipeWire audio drivers
        self._lib.fluid_settings_setstr(self._settings, b"audio.driver", b"pulseaudio")

        self._synth = self._lib.new_fluid_synth(self._settings)
        if not self._synth:
            return

        # Locate soundfont
        sf_to_load = soundfont_path
        if not sf_to_load:
            for p in DEFAULT_SOUNDFONT_PATHS:
                if os.path.exists(p):
                    sf_to_load = p
                    break

        if sf_to_load and os.path.exists(sf_to_load):
            self._lib.fluid_synth_sfload(self._synth, sf_to_load.encode("utf-8"), 1)

        # Audio driver
        self._adriver = self._lib.new_fluid_audio_driver(self._settings, self._synth)
        self.is_ready = True

    def note_on(self, channel: int, pitch: int, velocity: int):
        if self._lib and self._synth:
            self._lib.fluid_synth_noteon(self._synth, int(channel), int(pitch), int(velocity))

    def note_off(self, channel: int, pitch: int):
        if self._lib and self._synth:
            self._lib.fluid_synth_noteoff(self._synth, int(channel), int(pitch))

    def all_notes_off(self):
        if self._lib and self._synth:
            for ch in range(16):
                self._lib.fluid_synth_all_notes_off(self._synth, ch)

    def close(self):
        if self._lib:
            self.all_notes_off()
            if self._adriver:
                self._lib.delete_fluid_audio_driver(self._adriver)
                self._adriver = None
            if self._synth:
                self._lib.delete_fluid_synth(self._synth)
                self._synth = None
            if self._settings:
                self._lib.delete_fluid_settings(self._settings)
                self._settings = None
        self.is_ready = False


class NullSynthEngine(BaseSynth):
    """Fallback engine used when audio device/FluidSynth is not available."""

    def __init__(self):
        self.active_sounding_notes: Set[tuple] = set()

    def note_on(self, channel: int, pitch: int, velocity: int):
        self.active_sounding_notes.add((channel, pitch))

    def note_off(self, channel: int, pitch: int):
        self.active_sounding_notes.discard((channel, pitch))

    def all_notes_off(self):
        self.active_sounding_notes.clear()


class AudioPlayer:
    """Manages audio auditioning, acoustic scrubbing, playback, and navigation."""

    def __init__(self, synth: Optional[BaseSynth] = None):
        if synth:
            self.synth = synth
        else:
            fluid = FluidSynthEngine()
            self.synth = fluid if fluid.is_ready else NullSynthEngine()

        self.notes: List[NoteEvent] = []
        self.sections: List[Section] = []
        self.duration: float = 0.0

        # Playback transport state
        self.is_playing: bool = False
        self.current_time: float = 0.0
        self.skip_silence: bool = False

        # Active sounding notes during normal playback
        self._playing_active: Set[tuple] = set()

        # Acoustic scrub state (hover sustain)
        self._scrub_active_notes: Set[tuple] = set()
        self.is_scrubbing: bool = False

        # Periodic callback for UI updates: on_tick(current_time: float)
        self.on_tick: Optional[Callable[[float], None]] = None
        self.on_state_changed: Optional[Callable[[bool], None]] = None

        # Threading for playback timer
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()

    def load_midi_data(self, midi_data: MidiData):
        """Load notes and sections from parsed MIDI data."""
        with self._lock:
            self.stop()
            self.notes = sorted(midi_data.notes, key=lambda n: n.start_time)
            self.sections = midi_data.sections
            self.duration = midi_data.duration
            self.current_time = 0.0

    def play(self, from_time: Optional[float] = None):
        """Start or resume playback from specified position or current position."""
        with self._lock:
            if from_time is not None:
                self.current_time = max(0.0, min(from_time, self.duration))

            if self.is_playing:
                return

            self.is_playing = True
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._playback_loop, daemon=True)
            self._thread.start()

        if self.on_state_changed:
            self.on_state_changed(True)

    def pause(self):
        """Pause playback at current playhead position."""
        with self._lock:
            if not self.is_playing:
                return
            self.is_playing = False
            self._stop_event.set()
            self._silence_all_playback_notes()

        if self.on_state_changed:
            self.on_state_changed(False)

    def stop(self):
        """Stop playback and reset playhead to beginning."""
        with self._lock:
            self.is_playing = False
            self._stop_event.set()
            self._silence_all_playback_notes()
            self.current_time = 0.0

        if self.on_state_changed:
            self.on_state_changed(False)
        if self.on_tick:
            self.on_tick(0.0)

    def toggle_play_pause(self, from_time: Optional[float] = None):
        """Toggle play / pause."""
        if self.is_playing:
            self.pause()
        else:
            self.play(from_time)

    def seek(self, target_time: float):
        """Seek playhead to target_time in seconds."""
        with self._lock:
            target_time = max(0.0, min(target_time, self.duration))
            self.current_time = target_time
            self._silence_all_playback_notes()

        if self.on_tick:
            self.on_tick(self.current_time)

    def _silence_all_playback_notes(self):
        for ch, pitch in list(self._playing_active):
            self.synth.note_off(ch, pitch)
        self._playing_active.clear()

    # --- Bidirectional Gap / Silence Skipping ---

    def skip_gap_forward(self, current_time: Optional[float] = None) -> float:
        """Find the start of the next note event or active section forward."""
        t = self.current_time if current_time is None else current_time
        # Check notes starting after current_time
        next_note = next((n for n in self.notes if n.start_time > t + 0.05), None)
        if next_note:
            target = next_note.start_time
        else:
            target = self.duration
        self.seek(target)
        return target

    def skip_gap_backward(self, current_time: Optional[float] = None) -> float:
        """Find the end or start of the previous note event/section backward."""
        t = self.current_time if current_time is None else current_time
        # Find latest note starting before current_time - 0.05
        prev_notes = [n for n in self.notes if n.start_time < t - 0.05]
        if prev_notes:
            target = prev_notes[-1].start_time
        else:
            target = 0.0
        self.seek(target)
        return target

    # --- Section Jumps (>= 3.0s silence boundaries) ---

    def jump_next_section(self) -> float:
        """Jump to the start of the next section boundary."""
        curr = self.current_time
        for s in self.sections:
            if s.start_time > curr + 0.05:
                self.seek(s.start_time)
                return s.start_time
        self.seek(self.duration)
        return self.duration

    def jump_prev_section(self) -> float:
        """Jump to the start of the current or previous section."""
        curr = self.current_time
        # Find preceding section start
        prev_starts = [s.start_time for s in self.sections if s.start_time < curr - 0.2]
        if prev_starts:
            target = prev_starts[-1]
        else:
            target = 0.0
        self.seek(target)
        return target

    # --- Acoustic Scrubbing (Hover Note Sustain) ---

    def start_scrub(self):
        """Begin acoustic scrubbing session (e.g. on Ctrl+Space down)."""
        self.is_scrubbing = True
        if self.is_playing:
            self.pause()

    def audit_at(self, timestamp: float):
        """Sound and sustain whatever notes are active at this timeline point.

        Dampens notes that are no longer sounding at this point.
        """
        self.is_scrubbing = True
        timestamp = max(0.0, min(timestamp, self.duration))

        # Find notes active at timestamp
        active_now: Set[tuple] = set()
        for n in self.notes:
            if n.start_time <= timestamp <= n.end_time:
                active_now.add((n.channel, n.pitch, n.velocity))

        active_keys = {(ch, pitch) for ch, pitch, _ in active_now}
        old_keys = {(ch, pitch) for ch, pitch, _ in self._scrub_active_notes}

        # Turn off notes that stopped sounding
        to_stop = old_keys - active_keys
        for ch, pitch in to_stop:
            self.synth.note_off(ch, pitch)

        # Turn on new notes
        to_start = active_now - self._scrub_active_notes
        for ch, pitch, vel in to_start:
            self.synth.note_on(ch, pitch, vel)

        self._scrub_active_notes = active_now

    def end_scrub(self):
        """End acoustic scrubbing and damp all sustained notes."""
        self.is_scrubbing = False
        for ch, pitch, _ in list(self._scrub_active_notes):
            self.synth.note_off(ch, pitch)
        self._scrub_active_notes.clear()

    # --- Internal Playback Loop ---

    def _playback_loop(self):
        tick_interval = 0.015  # 15ms clock for low latency
        last_wall_time = time.monotonic()

        while not self._stop_event.is_set():
            time.sleep(tick_interval)
            now = time.monotonic()
            dt = now - last_wall_time
            last_wall_time = now

            with self._lock:
                if not self.is_playing:
                    break

                self.current_time += dt

                # Silence skipping check during playback
                if self.skip_silence:
                    # Check if any note is currently active
                    is_active = any(n.start_time <= self.current_time <= n.end_time for n in self.notes)
                    if not is_active:
                        # We are in silence. Find next note
                        next_note = next((n for n in self.notes if n.start_time > self.current_time), None)
                        if next_note and (next_note.start_time - self.current_time) >= 1.0:
                            self.current_time = next_note.start_time

                if self.current_time >= self.duration:
                    self.current_time = self.duration
                    self.is_playing = False
                    self._stop_event.set()
                    self._silence_all_playback_notes()
                    if self.on_state_changed:
                        self.on_state_changed(False)
                    if self.on_tick:
                        self.on_tick(self.current_time)
                    break

                # Dispatch notes
                sounding_now: Set[tuple] = set()
                for n in self.notes:
                    if n.start_time <= self.current_time <= n.end_time:
                        sounding_now.add((n.channel, n.pitch))

                # Note offs
                to_off = self._playing_active - sounding_now
                for ch, pitch in to_off:
                    self.synth.note_off(ch, pitch)

                # Note ons
                to_on = sounding_now - self._playing_active
                for ch, pitch in to_on:
                    vel = 100
                    for n in self.notes:
                        if n.channel == ch and n.pitch == pitch and n.start_time <= self.current_time <= n.end_time:
                            vel = n.velocity
                            break
                    self.synth.note_on(ch, pitch, vel)

                self._playing_active = sounding_now
                curr_t = self.current_time

            if self.on_tick:
                self.on_tick(curr_t)

    def close(self):
        self.stop()
        self.end_scrub()
        self.synth.close()
