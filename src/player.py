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
import ctypes.util
import threading
from typing import List, Optional, Set, Callable
from dataclasses import dataclass

from .archive import NoteEvent, Section, MidiData


# Standard SoundFont search paths
DEFAULT_SOUNDFONT_PATHS = [
    "/app/share/soundfonts/default.sf2",
    "/app/share/soundfonts/FluidR3_GM.sf2",
    os.path.expanduser("~/.local/share/soundfonts/default.sf2"),
    os.path.expanduser("~/.local/share/soundfonts/FluidR3_GM.sf2"),
    os.path.expanduser("~/.local/share/midikeep/soundfonts/default.sf2"),
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

    def program_change(self, channel: int, program: int):
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
        # Search for libfluidsynth across standard system and Flatpak /app/lib paths
        candidates = [
            "/app/lib/libfluidsynth.so.3",
            "/app/lib/libfluidsynth.so",
            ctypes.util.find_library("fluidsynth") if hasattr(ctypes.util, "find_library") else None,
            "libfluidsynth.so.3",
            "libfluidsynth.so.2",
            "libfluidsynth.so",
        ]
        self._lib = None
        for c in candidates:
            if not c:
                continue
            try:
                self._lib = ctypes.CDLL(c)
                if self._lib:
                    break
            except OSError:
                continue

        if not self._lib:
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

        if hasattr(self._lib, "fluid_synth_program_change"):
            self._lib.fluid_synth_program_change.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
            self._lib.fluid_synth_program_change.restype = ctypes.c_int

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

    def program_change(self, channel: int, program: int):
        if self._lib and self._synth and hasattr(self._lib, "fluid_synth_program_change"):
            self._lib.fluid_synth_program_change(self._synth, int(channel), int(program))

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
        self.program_changes_received: List[Tuple[int, int]] = []

    def program_change(self, channel: int, program: int):
        self.program_changes_received.append((channel, program))

    def note_on(self, channel: int, pitch: int, velocity: int):
        self.active_sounding_notes.add((channel, pitch))

    def note_off(self, channel: int, pitch: int):
        self.active_sounding_notes.discard((channel, pitch))

    def all_notes_off(self):
        self.active_sounding_notes.clear()


class PulseWaveSynth(BaseSynth):
    """Ultra-low-latency real-time PCM synthesizer via libpulse-simple (shared memory / socket)."""

    def __init__(self):
        import ctypes, math, struct
        self.ctypes = ctypes
        self.math = math
        self.struct = struct
        self.rate = 44100
        self.active_notes = {}
        self.lock = threading.Lock()
        self.running = True
        self.pa = None
        self.is_available = False

        self._init_pulse()
        if self.is_available:
            self.thread = threading.Thread(target=self._audio_loop, daemon=True)
            self.thread.start()

    def _init_pulse(self):
        try:
            self.lib = self.ctypes.CDLL('libpulse-simple.so.0')

            class SampleSpec(self.ctypes.Structure):
                _fields_ = [
                    ('format', self.ctypes.c_int),      # PA_SAMPLE_S16LE = 3
                    ('rate', self.ctypes.c_uint32),
                    ('channels', self.ctypes.c_uint8),
                ]

            class BufferAttr(self.ctypes.Structure):
                _fields_ = [
                    ('maxlength', self.ctypes.c_uint32),
                    ('tlength', self.ctypes.c_uint32),
                    ('prebuf', self.ctypes.c_uint32),
                    ('minreq', self.ctypes.c_uint32),
                    ('fragsize', self.ctypes.c_uint32),
                ]

            self.lib.pa_simple_new.restype = self.ctypes.c_void_p
            self.lib.pa_simple_new.argtypes = [
                self.ctypes.c_char_p, self.ctypes.c_char_p, self.ctypes.c_int,
                self.ctypes.c_char_p, self.ctypes.c_char_p, self.ctypes.POINTER(SampleSpec),
                self.ctypes.c_void_p, self.ctypes.POINTER(BufferAttr), self.ctypes.POINTER(self.ctypes.c_int)
            ]
            self.lib.pa_simple_write.restype = self.ctypes.c_int
            self.lib.pa_simple_write.argtypes = [
                self.ctypes.c_void_p, self.ctypes.c_char_p, self.ctypes.c_size_t, self.ctypes.POINTER(self.ctypes.c_int)
            ]
            self.lib.pa_simple_free.argtypes = [self.ctypes.c_void_p]

            ss = SampleSpec(format=3, rate=self.rate, channels=1)
            # Ultra low-latency target buffer: ~20ms (44100 * 2 bytes * 0.02 = ~1764 bytes)
            ba = BufferAttr(
                maxlength=self.ctypes.c_uint32(-1),
                tlength=int(self.rate * 2 * 0.02),
                prebuf=self.ctypes.c_uint32(-1),
                minreq=self.ctypes.c_uint32(-1),
                fragsize=self.ctypes.c_uint32(-1)
            )

            err = self.ctypes.c_int(0)
            self.pa = self.lib.pa_simple_new(
                None, b'Pianola', 1, None, b'Pianola Playback',
                self.ctypes.byref(ss), None, self.ctypes.byref(ba), self.ctypes.byref(err)
            )
            if self.pa:
                self.is_available = True
        except Exception:
            self.is_available = False

    def note_on(self, channel: int, pitch: int, velocity: int):
        with self.lock:
            freq = 440.0 * (2.0 ** ((pitch - 69) / 12.0))
            self.active_notes[(channel, pitch)] = {
                'freq': freq, 'vel': velocity, 'phase': 0.0, 'age': 0.0
            }

    def note_off(self, channel: int, pitch: int):
        with self.lock:
            self.active_notes.pop((channel, pitch), None)

    def all_notes_off(self):
        with self.lock:
            self.active_notes.clear()

    def _audio_loop(self):
        chunk_size = int(self.rate * 0.015)  # 15ms slice
        dt = 1.0 / self.rate
        err = self.ctypes.c_int(0)

        while self.running:
            if not self.pa:
                time.sleep(0.05)
                continue

            with self.lock:
                if not self.active_notes:
                    time.sleep(0.01)
                    continue
                notes_snapshot = list(self.active_notes.values())

            samples = []
            for _ in range(chunk_size):
                sample_val = 0.0
                for n in notes_snapshot:
                    f = n['freq']
                    p = n['phase']
                    decay = self.math.exp(-2.2 * n['age'])
                    v = n['vel'] / 127.0
                    val = (self.math.sin(p) + 0.3 * self.math.sin(2.0 * p) + 0.15 * self.math.sin(3.0 * p)) * decay * v
                    sample_val += val
                    n['phase'] = (p + 2.0 * self.math.pi * f * dt) % (2.0 * self.math.pi)
                    n['age'] += dt

                scaled = int(max(-32767.0, min(32767.0, sample_val * 14000.0)))
                samples.append(self.struct.pack('<h', scaled))

            data = b''.join(samples)
            self.lib.pa_simple_write(self.pa, data, len(data), self.ctypes.byref(err))

    def close(self):
        self.running = False
        self.all_notes_off()
        if self.pa:
            try:
                self.lib.pa_simple_free(self.pa)
            except Exception:
                pass
            self.pa = None


class AudioPlayer:
    """Manages audio auditioning, acoustic scrubbing, playback, and navigation."""

    def __init__(self, synth: Optional[BaseSynth] = None):
        if synth:
            self.synth = synth
        else:
            fluid = FluidSynthEngine()
            if fluid.is_ready:
                self.synth = fluid
            else:
                pulse = PulseWaveSynth()
                self.synth = pulse if pulse.is_available else NullSynthEngine()

        self.notes: List[NoteEvent] = []
        self.sections: List[Section] = []
        self.duration: float = 0.0
        self.tracks: list = []
        self.program_changes: list = []
        self._last_applied_prog: dict = {}
        self.multi_track_mode: bool = False

        # Playback transport state
        self.is_playing: bool = False
        self.current_time: float = 0.0

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

    def set_multi_track_mode(self, enabled: bool):
        """Set unified (single instrument / grand piano) or multi-track playback mode."""
        with self._lock:
            self.multi_track_mode = enabled
            self.sync_program_changes()

    def sync_program_changes(self):
        """Synchronize synth instruments to current playback mode and playhead position."""
        with self._lock:
            if not self.multi_track_mode:
                # Unified single-instrument mode: clamp all channels to GM 0 (Acoustic Grand Piano)
                for ch in range(16):
                    self.synth.program_change(ch, 0)
                self._last_applied_prog = {ch: 0 for ch in range(16)}
            else:
                # Multi-track mode: assign each track's instrument to its channel
                for trk in self.tracks:
                    self.synth.program_change(trk.channel, trk.program)
                    self._last_applied_prog[trk.channel] = trk.program
                # Apply any program changes up to current_time
                for t, ch, prog in self.program_changes:
                    if t <= self.current_time:
                        self.synth.program_change(ch, prog)
                        self._last_applied_prog[ch] = prog

    def load_midi_data(self, midi_data: MidiData):
        """Load notes and sections from parsed MIDI data."""
        with self._lock:
            self.stop()
            self.notes = sorted(midi_data.notes, key=lambda n: n.start_time)
            self.sections = midi_data.sections
            self.duration = midi_data.duration
            self.tracks = getattr(midi_data, "tracks", [])
            self.program_changes = sorted(getattr(midi_data, "program_changes", []), key=lambda x: x[0])
            self.current_time = 0.0
            self.sync_program_changes()

    def play(self, from_time: Optional[float] = None):
        """Start or resume playback from specified position or current position."""
        with self._lock:
            if from_time is not None:
                self.current_time = max(0.0, min(from_time, self.duration))
            elif self.current_time >= self.duration - 0.05:
                # Auto-rewind to start if playhead is at or near the end
                self.current_time = 0.0

            if self.is_playing:
                return

            self.is_playing = True
            self._start_monotonic = time.monotonic()
            self._start_seek_offset = self.current_time
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
            self._start_monotonic = time.monotonic()
            self._start_seek_offset = target_time
            self._silence_all_playback_notes()

            # Apply program changes according to playback mode
            if self.multi_track_mode:
                progs = dict(self._last_applied_prog)
                for t, ch, prog in self.program_changes:
                    if t <= target_time:
                        progs[ch] = prog
                for ch, prog in progs.items():
                    self.synth.program_change(ch, prog)
            else:
                for ch in range(16):
                    self.synth.program_change(ch, 0)

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
        """Jump to the start of the previous section if within 1.2s of current section start, else start of current section."""
        curr = self.current_time
        prev_starts = [s.start_time for s in self.sections if s.start_time < curr - 1.2]
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
        tick_interval = 0.010  # 10ms high-precision clock
        try:
            while not self._stop_event.is_set():
                time.sleep(tick_interval)
                now = time.monotonic()

                with self._lock:
                    if not self.is_playing:
                        break

                    prev_t = self.current_time
                    elapsed = now - self._start_monotonic
                    self.current_time = self._start_seek_offset + elapsed

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

                    # Dispatch mid-playback program changes only in multi-track mode
                    if self.multi_track_mode:
                        for t, ch, prog in self.program_changes:
                            if prev_t < t <= self.current_time:
                                self.synth.program_change(ch, prog)

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
        except Exception:
            with self._lock:
                self.is_playing = False
                self._stop_event.set()
                self._silence_all_playback_notes()
            if self.on_state_changed:
                self.on_state_changed(False)

    def close(self):
        self.stop()
        self.end_scrub()
        self.synth.close()
