# archive.py
#
# Copyright 2026 Matthew Samson
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Archive and MIDI processing module for Pianola.

Provides:
- Standalone zero-dependency Standard MIDI File (SMF 0/1) parsing and writing.
- Note-level event extraction, marker identification, and >= 3.0s silence section detection.
- Marquee slice and multi-file export tools.
- SQLite access to Midikeep's index.db and live journal detection in ~/.local/share/midikeep/journal/.
"""

import os
import io
import struct
import sqlite3
import shutil
import tempfile
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict, Any


def get_midikeep_dir() -> str:
    """Return the Midikeep storage root directory."""
    # 1. Check standard host/flatpak mounted path (~/.local/share/midikeep)
    user_midikeep = os.path.expanduser("~/.local/share/midikeep")
    if os.path.exists(os.path.join(user_midikeep, "index.db")):
        return user_midikeep

    # 2. Check XDG_DATA_HOME (if set to custom directory on host)
    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data:
        candidate = os.path.join(xdg_data, "midikeep")
        if os.path.exists(os.path.join(candidate, "index.db")):
            return candidate

    return user_midikeep


@dataclass
class TrackInfo:
    track_index: int
    channel: int = 0
    name: str = ""
    program: int = 0
    instrument_name: str = "Acoustic Grand Piano"
    min_pitch: int = 21
    max_pitch: int = 108
    note_count: int = 0

    @property
    def pitch_span(self) -> int:
        return max(12, self.max_pitch - self.min_pitch + 1)


@dataclass
class NoteEvent:
    pitch: int
    velocity: int
    start_time: float  # seconds
    end_time: float    # seconds
    channel: int = 0
    key_end_time: Optional[float] = None  # Physical key release time (before pedal sustain)
    track_index: int = 0

    @property
    def duration(self) -> float:
        return max(0.0, self.end_time - self.start_time)

    @property
    def played_end_time(self) -> float:
        return self.key_end_time if self.key_end_time is not None else self.end_time


@dataclass
class MarkerEvent:
    time: float  # seconds
    text: str


@dataclass
class Section:
    start_time: float  # seconds
    end_time: float    # seconds

    @property
    def duration(self) -> float:
        return max(0.0, self.end_time - self.start_time)


@dataclass
class MidiData:
    duration: float = 0.0
    active_play_seconds: float = 0.0
    notes: List[NoteEvent] = field(default_factory=list)
    markers: List[MarkerEvent] = field(default_factory=list)
    sections: List[Section] = field(default_factory=list)
    tempo_bpm: float = 120.0
    key_signature: Optional[str] = None
    tracks: List[TrackInfo] = field(default_factory=list)
    program_changes: List[Tuple[float, int, int]] = field(default_factory=list)  # (time_sec, channel, program)
GM_PROGRAM_NAMES = (
    # Piano (0-7)
    "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano", "Honky-tonk Piano",
    "Electric Piano 1", "Electric Piano 2", "Harpsichord", "Clavinet",
    # Chromatic Percussion (8-15)
    "Celesta", "Glockenspiel", "Music Box", "Vibraphone",
    "Marimba", "Xylophone", "Tubular Bells", "Dulcimer",
    # Organ (16-23)
    "Drawbar Organ", "Percussive Organ", "Rock Organ", "Church Organ",
    "Reed Organ", "Accordion", "Harmonica", "Tango Accordion",
    # Guitar (24-31)
    "Acoustic Guitar (nylon)", "Acoustic Guitar (steel)", "Electric Guitar (jazz)", "Electric Guitar (clean)",
    "Electric Guitar (muted)", "Overdriven Guitar", "Distortion Guitar", "Guitar Harmonics",
    # Bass (32-39)
    "Acoustic Bass", "Electric Bass (finger)", "Electric Bass (pick)", "Fretless Bass",
    "Slap Bass 1", "Slap Bass 2", "Synth Bass 1", "Synth Bass 2",
    # Strings (40-47)
    "Violin", "Viola", "Cello", "Contrabass",
    "Tremolo Strings", "Pizzicato Strings", "Orchestral Harp", "Timpani",
    # Ensemble (48-55)
    "String Ensemble 1", "String Ensemble 2", "Synth Strings 1", "Synth Strings 2",
    "Choir Aahs", "Voice Oohs", "Synth Choir", "Orchestra Hit",
    # Brass (56-63)
    "Trumpet", "Trombone", "Tuba", "Muted Trumpet",
    "French Horn", "Brass Section", "Synth Brass 1", "Synth Brass 2",
    # Reed (64-71)
    "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax",
    "Oboe", "English Horn", "Bassoon", "Clarinet",
    # Pipe (72-79)
    "Piccolo", "Flute", "Recorder", "Pan Flute",
    "Blown Bottle", "Shakuhachi", "Whistle", "Ocarina",
    # Synth Lead (80-87)
    "Lead 1 (square)", "Lead 2 (sawtooth)", "Lead 3 (calliope)", "Lead 4 (chiff)",
    "Lead 5 (charang)", "Lead 6 (voice)", "Lead 7 (fifths)", "Lead 8 (bass + lead)",
    # Synth Pad (88-95)
    "Pad 1 (new age)", "Pad 2 (warm)", "Pad 3 (polysynth)", "Pad 4 (choir)",
    "Pad 5 (bowed)", "Pad 6 (metallic)", "Pad 7 (halo)", "Pad 8 (sweep)",
    # Synth Effects (96-103)
    "FX 1 (rain)", "FX 2 (soundtrack)", "FX 3 (crystal)", "FX 4 (atmosphere)",
    "FX 5 (brightness)", "FX 6 (goblins)", "FX 7 (echoes)", "FX 8 (sci-fi)",
    # Ethnic (104-111)
    "Sitar", "Banjo", "Shamisen", "Koto",
    "Kalimba", "Bagpipe", "Fiddle", "Shanai",
    # Percussive (112-119)
    "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock",
    "Taiko Drum", "Melodic Tom", "Synth Drum", "Reverse Cymbal",
    # Sound Effects (120-127)
    "Guitar Fret Noise", "Breath Noise", "Seashore", "Bird Tweet",
    "Telephone Ring", "Helicopter", "Applause", "Gunshot"
)


def get_gm_instrument_name(program: int, channel: int = 0) -> str:
    """Return human-readable General MIDI instrument name."""
    if channel == 9:
        return "Standard Drums"
    return GM_PROGRAM_NAMES[max(0, min(127, program))]


def get_gm_instrument_icon(program: int, channel: int = 0) -> str:
    """Return friendly emoji icon for instrument family."""
    if channel == 9:
        return "🥁"
    p = max(0, min(127, program))
    if p < 8:
        return "🎹"
    elif p < 16:
        return "🔔"
    elif p < 24:
        return "⛪"
    elif p < 32:
        return "🎸"
    elif p < 40:
        return "🎸"
    elif p < 48:
        return "🎻"
    elif p < 56:
        return "👥"
    elif p < 64:
        return "🎺"
    elif p < 72:
        return "🎷"
    elif p < 80:
        return "🪈"
    elif p < 88:
        return "⚡"
    elif p < 96:
        return "🌊"
    elif p < 112:
        return "🪕"
    elif p < 120:
        return "🥁"
    return "🎵"


def get_track_color(program: int, channel: int = 0) -> Tuple[float, float, float]:
    """Curated color for instrument family in dark and light modes."""
    if channel == 9:
        return (0.92, 0.30, 0.35)  # Crimson / Drums
    p = max(0, min(127, program))
    if p < 8:
        return (0.12, 0.68, 0.92)  # Cyan / Piano
    elif p < 16:
        return (0.95, 0.75, 0.20)  # Gold / Chromatic
    elif p < 24:
        return (0.85, 0.55, 0.20)  # Amber / Organ
    elif p < 32:
        return (0.92, 0.45, 0.25)  # Terracotta / Guitar
    elif p < 40:
        return (0.65, 0.40, 0.88)  # Purple / Bass
    elif p < 48:
        return (0.95, 0.60, 0.20)  # Warm Gold / Solo Strings
    elif p < 56:
        return (0.30, 0.75, 0.85)  # Soft Teal / Ensemble
    elif p < 64:
        return (0.95, 0.50, 0.15)  # Orange / Brass
    elif p < 72:
        return (0.25, 0.80, 0.55)  # Emerald / Reeds
    elif p < 80:
        return (0.20, 0.85, 0.70)  # Mint / Pipes
    elif p < 88:
        return (0.95, 0.35, 0.65)  # Magenta / Synth Lead
    elif p < 96:
        return (0.45, 0.55, 0.95)  # Indigo / Synth Pad

    palette = [
        (0.12, 0.68, 0.92), (0.95, 0.60, 0.20), (0.25, 0.80, 0.55), (0.65, 0.40, 0.88),
        (0.95, 0.50, 0.15), (0.95, 0.35, 0.65), (0.45, 0.55, 0.95), (0.92, 0.30, 0.35)
    ]
    return palette[channel % len(palette)]



class MidiParser:
    """Zero-dependency Standard MIDI File (SMF 0 and 1) parser and exporter."""

    @staticmethod
    def _read_vlq(stream: io.BytesIO) -> int:
        value = 0
        while True:
            b = stream.read(1)
            if not b:
                break
            byte = b[0]
            value = (value << 7) | (byte & 0x7F)
            if not (byte & 0x80):
                break
        return value

    @staticmethod
    def _write_vlq(value: int) -> bytes:
        buffer = bytearray([value & 0x7F])
        value >>= 7
        while value > 0:
            buffer.insert(0, (value & 0x7F) | 0x80)
            value >>= 7
        return bytes(buffer)

    @classmethod
    def parse_file(cls, filepath: str) -> MidiData:
        """Parse a .mid file into MidiData."""
        with open(filepath, "rb") as f:
            return cls.parse_bytes(f.read())

    @classmethod
    def parse_bytes(cls, data: bytes) -> MidiData:
        """Parse raw MIDI bytes into structured MidiData."""
        stream = io.BytesIO(data)
        magic = stream.read(4)
        if magic != b"MThd":
            raise ValueError(f"Invalid MIDI header: {magic!r}")

        header_len = struct.unpack(">I", stream.read(4))[0]
        header_data = stream.read(header_len)
        fmt, num_tracks, division = struct.unpack(">HHH", header_data[:6])

        if division & 0x8000:
            # SMPTE time - approximate 120 ticks/sec default
            ticks_per_quarter = 120
        else:
            ticks_per_quarter = division

        # Track reading
        raw_events = []  # (tick, priority, event_type, payload)
        # priority ensures tempo / meta events are processed before notes at same tick

        for track_idx in range(num_tracks):
            track_magic = stream.read(4)
            if not track_magic:
                break
            if track_magic != b"MTrk":
                chunk_len = struct.unpack(">I", stream.read(4))[0]
                stream.seek(chunk_len, io.SEEK_CUR)
                continue

            track_len = struct.unpack(">I", stream.read(4))[0]
            track_data = stream.read(track_len)
            tstream = io.BytesIO(track_data)

            curr_tick = 0
            running_status = None

            while tstream.tell() < track_len:
                delta = cls._read_vlq(tstream)
                curr_tick += delta

                status_peek = tstream.read(1)
                if not status_peek:
                    break
                status_byte = status_peek[0]

                if status_byte < 0x80:
                    if running_status is None:
                        continue
                    status = running_status
                    first_byte = status_byte
                else:
                    status = status_byte
                    if status < 0xF0:
                        running_status = status
                    first_byte = None

                # Process event
                if status == 0xFF:
                    meta_type = tstream.read(1)[0]
                    meta_len = cls._read_vlq(tstream)
                    meta_bytes = tstream.read(meta_len)

                    if meta_type == 0x03:
                        # Track Name
                        text = meta_bytes.decode("utf-8", errors="replace").strip()
                        raw_events.append((curr_tick, 0, "track_name", (track_idx, text)))
                    elif meta_type == 0x04:
                        # Instrument Name
                        text = meta_bytes.decode("utf-8", errors="replace").strip()
                        raw_events.append((curr_tick, 0, "instrument_name", (track_idx, text)))
                    elif meta_type == 0x51 and meta_len == 3:
                        # Set tempo
                        mpqn = (meta_bytes[0] << 16) | (meta_bytes[1] << 8) | meta_bytes[2]
                        raw_events.append((curr_tick, 0, "tempo", mpqn))
                    elif meta_type == 0x06:
                        # Marker
                        text = meta_bytes.decode("utf-8", errors="replace")
                        raw_events.append((curr_tick, 1, "marker", text))
                    elif meta_type == 0x59 and meta_len >= 2:
                        # Key Signature
                        sf, mi = struct.unpack("bb", meta_bytes[:2])
                        raw_events.append((curr_tick, 1, "key_signature", (sf, mi)))
                elif status in (0xF0, 0xF7):
                    sysex_len = cls._read_vlq(tstream)
                    tstream.seek(sysex_len, io.SEEK_CUR)
                else:
                    event_type = status & 0xF0
                    channel = status & 0x0F

                    if first_byte is not None:
                        b1 = first_byte
                    else:
                        b1 = tstream.read(1)[0]

                    if event_type in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                        b2 = tstream.read(1)[0]
                    else:
                        b2 = 0

                    if event_type == 0xC0:
                        # Program Change
                        raw_events.append((curr_tick, 0, "program_change", (channel, b1, track_idx)))
                    elif event_type == 0xB0 and b1 == 64:
                        # CC 64 Damper / Sustain Pedal
                        raw_events.append((curr_tick, 1, "sustain", (channel, b2)))
                    elif event_type == 0x90:
                        if b2 > 0:
                            raw_events.append((curr_tick, 2, "note_on", (channel, b1, b2, track_idx)))
                        else:
                            raw_events.append((curr_tick, 2, "note_off", (channel, b1, 0, track_idx)))
                    elif event_type == 0x80:
                        raw_events.append((curr_tick, 2, "note_off", (channel, b1, b2, track_idx)))

        # Sort raw events chronologically
        raw_events.sort(key=lambda x: (x[0], x[1]))

        # Convert ticks to seconds using tempos
        current_mpqn = 500000  # Default 120 BPM (500,000 microseconds / quarter note)
        last_tick = 0
        current_time = 0.0

        notes: List[NoteEvent] = []
        markers: List[MarkerEvent] = []
        open_notes: Dict[Tuple[int, int, int], List[Tuple[float, int]]] = {}
        # (channel, pitch, track_idx) -> list of (start_time, velocity)
        sustain_pedal: Dict[int, bool] = {ch: False for ch in range(16)}
        pedaled_notes: Dict[int, List[Tuple[int, float, float, int, int]]] = {ch: [] for ch in range(16)}
        # channel -> list of (pitch, start_time, key_release_time, velocity, track_idx)

        track_names: Dict[int, str] = {}
        track_programs: Dict[int, int] = {}
        channel_programs: Dict[int, int] = {}
        program_changes: List[Tuple[float, int, int]] = []

        initial_bpm = 120.0
        bpm_set = False

        for tick, _, etype, payload in raw_events:
            delta_ticks = tick - last_tick
            if delta_ticks > 0:
                seconds_per_tick = (current_mpqn / 1_000_000.0) / ticks_per_quarter
                current_time += delta_ticks * seconds_per_tick
                last_tick = tick

            if etype == "tempo":
                current_mpqn = payload
                bpm = 60_000_000.0 / current_mpqn
                if not bpm_set:
                    initial_bpm = bpm
                    bpm_set = True
            elif etype == "track_name":
                trk_idx, name = payload
                track_names[trk_idx] = name
            elif etype == "instrument_name":
                trk_idx, name = payload
                if trk_idx not in track_names or not track_names[trk_idx]:
                    track_names[trk_idx] = name
            elif etype == "program_change":
                ch, prog, trk_idx = payload
                track_programs[trk_idx] = prog
                channel_programs[ch] = prog
                program_changes.append((current_time, ch, prog))
            elif etype == "marker":
                markers.append(MarkerEvent(time=current_time, text=payload))
            elif etype == "note_on":
                channel, pitch, vel, trk_idx = payload
                key = (channel, pitch, trk_idx)
                if key not in open_notes:
                    open_notes[key] = []
                open_notes[key].append((current_time, vel))
            elif etype == "sustain":
                channel, val = payload
                is_down = val >= 64
                sustain_pedal[channel] = is_down
                if not is_down:
                    for pitch, start_t, key_rel_t, vel, trk_idx in pedaled_notes[channel]:
                        end_t = max(start_t + 0.05, current_time)
                        notes.append(NoteEvent(
                            pitch=pitch, velocity=vel, start_time=start_t, end_time=end_t,
                            channel=channel, key_end_time=key_rel_t, track_index=trk_idx
                        ))
                    pedaled_notes[channel].clear()
            elif etype == "note_off":
                channel, pitch, _, trk_idx = payload
                key = (channel, pitch, trk_idx)
                if key in open_notes and open_notes[key]:
                    start_t, vel = open_notes[key].pop(0)
                    if sustain_pedal.get(channel, False):
                        pedaled_notes[channel].append((pitch, start_t, current_time, vel, trk_idx))
                    else:
                        end_t = max(start_t + 0.02, current_time)
                        notes.append(NoteEvent(
                            pitch=pitch, velocity=vel, start_time=start_t, end_time=end_t,
                            channel=channel, key_end_time=end_t, track_index=trk_idx
                        ))

        # Finalize lingering pedaled notes
        for channel, pnotes in pedaled_notes.items():
            for pitch, start_t, key_rel_t, vel, trk_idx in pnotes:
                end_t = max(start_t + 0.1, current_time)
                notes.append(NoteEvent(
                    pitch=pitch, velocity=vel, start_time=start_t, end_time=end_t,
                    channel=channel, key_end_time=key_rel_t, track_index=trk_idx
                ))

        # Close any lingering open keys
        for (channel, pitch, trk_idx), starts in open_notes.items():
            for start_t, vel in starts:
                end_t = max(start_t + 0.1, current_time)
                notes.append(NoteEvent(
                    pitch=pitch, velocity=vel, start_time=start_t, end_time=end_t,
                    channel=channel, track_index=trk_idx
                ))

        notes.sort(key=lambda n: n.start_time)
        duration = current_time if notes or markers else 0.0
        if notes:
            duration = max(duration, max(n.end_time for n in notes))

        # Build distinct TrackInfo list
        tracks: List[TrackInfo] = []
        track_groups: Dict[Tuple[int, int], List[NoteEvent]] = {}
        for n in notes:
            k = (n.track_index, n.channel)
            if k not in track_groups:
                track_groups[k] = []
            track_groups[k].append(n)

        if track_groups:
            for (trk_idx, ch), t_notes in sorted(track_groups.items(), key=lambda x: (x[0][0], x[0][1])):
                prog = track_programs.get(trk_idx, channel_programs.get(ch, 0))
                inst_name = get_gm_instrument_name(prog, channel=ch)
                custom_name = track_names.get(trk_idx, "")
                name = custom_name if custom_name else inst_name
                min_p = min(n.pitch for n in t_notes)
                max_p = max(n.pitch for n in t_notes)
                tracks.append(TrackInfo(
                    track_index=trk_idx,
                    channel=ch,
                    name=name,
                    program=prog,
                    instrument_name=inst_name,
                    min_pitch=min_p,
                    max_pitch=max_p,
                    note_count=len(t_notes)
                ))
        else:
            tracks.append(TrackInfo(
                track_index=0,
                channel=0,
                name="Acoustic Grand Piano",
                program=0,
                instrument_name="Acoustic Grand Piano",
                min_pitch=48,
                max_pitch=72,
                note_count=0
            ))

        # Compute sections: silences of >= 3.0 seconds define section boundaries
        sections = cls._detect_sections(notes, duration)

        # Compute active play seconds
        active_play_seconds = cls._compute_active_time(notes)

        return MidiData(
            duration=duration,
            active_play_seconds=active_play_seconds,
            notes=notes,
            markers=markers,
            sections=sections,
            tempo_bpm=initial_bpm,
            tracks=tracks,
            program_changes=program_changes
        )

    @staticmethod
    def _detect_sections(notes: List[NoteEvent], total_duration: float, silence_threshold: float = 3.0) -> List[Section]:
        """Silences >= 3.0 seconds demarcate section boundaries."""
        if not notes:
            return [Section(start_time=0.0, end_time=total_duration)] if total_duration > 0 else []

        sections: List[Section] = []
        section_start = notes[0].start_time
        latest_end = notes[0].end_time

        for i in range(1, len(notes)):
            curr = notes[i]
            # Gap between latest active note end and current note start
            gap = curr.start_time - latest_end
            if gap >= silence_threshold:
                # Demarcate section
                sections.append(Section(start_time=section_start, end_time=latest_end))
                section_start = curr.start_time
            if curr.end_time > latest_end:
                latest_end = curr.end_time

        sections.append(Section(start_time=section_start, end_time=latest_end))
        return sections

    @staticmethod
    def _compute_active_time(notes: List[NoteEvent]) -> float:
        """Compute union of all sounding intervals."""
        if not notes:
            return 0.0
        # Merge intervals
        intervals = sorted([(n.start_time, n.end_time) for n in notes])
        merged = []
        for start, end in intervals:
            if not merged or start > merged[-1][1]:
                merged.append([start, end])
            else:
                merged[-1][1] = max(merged[-1][1], end)
        return sum(end - start for start, end in merged)

    @classmethod
    def write_notes_to_file(cls, notes: List[NoteEvent], output_path: str, tempo_bpm: float = 120.0, time_offset: float = 0.0):
        """Write note events to a standard Type 0 MIDI file.

        Adjusts start times by subtracting time_offset.
        """
        ticks_per_quarter = 480
        mpqn = int(round(60_000_000.0 / max(10.0, tempo_bpm)))
        seconds_per_tick = (mpqn / 1_000_000.0) / ticks_per_quarter

        # Build raw events list: (tick, priority, bytes)
        raw = []
        # Tempo event at tick 0
        tempo_payload = struct.pack(">BBB", (mpqn >> 16) & 0xFF, (mpqn >> 8) & 0xFF, mpqn & 0xFF)
        raw.append((0, 0, b"\xFF\x51\x03" + tempo_payload))

        for n in notes:
            rel_start = max(0.0, n.start_time - time_offset)
            rel_end = max(rel_start + 0.01, n.end_time - time_offset)

            start_tick = int(round(rel_start / seconds_per_tick))
            end_tick = int(round(rel_end / seconds_per_tick))
            ch = n.channel & 0x0F

            # Note On
            raw.append((start_tick, 2, bytes([0x90 | ch, n.pitch & 0x7F, n.velocity & 0x7F])))
            # Note Off
            raw.append((end_tick, 1, bytes([0x80 | ch, n.pitch & 0x7F, 0])))

        # End of track meta event
        max_tick = max([e[0] for e in raw], default=0)
        raw.append((max_tick, 3, b"\xFF\x2F\x00"))

        raw.sort(key=lambda x: (x[0], x[1]))

        # Encode track data
        track_bytes = bytearray()
        last_tick = 0
        for tick, _, payload in raw:
            delta = tick - last_tick
            track_bytes.extend(cls._write_vlq(delta))
            track_bytes.extend(payload)
            last_tick = tick

        header = struct.pack(">4sIHHH", b"MThd", 6, 0, 1, ticks_per_quarter)
        track_chunk = struct.pack(">4sI", b"MTrk", len(track_bytes)) + bytes(track_bytes)

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(header + track_chunk)


@dataclass
class SessionRecord:
    id: Optional[int]
    start_time: datetime
    end_time: datetime
    duration_seconds: float
    active_play_seconds: float
    note_count: int
    device_name: str
    file_path: str
    key_signature: Optional[str] = None
    tempo_bpm: float = 120.0
    starred: bool = False
    notes: Optional[str] = None
    is_live: bool = False

    _cached_midi: Optional[MidiData] = field(default=None, repr=False)

    def get_midi_data(self) -> MidiData:
        if self._cached_midi is None:
            if os.path.exists(self.file_path):
                self._cached_midi = MidiParser.parse_file(self.file_path)
            else:
                self._cached_midi = MidiData()
        return self._cached_midi


class ArchiveManager:
    """Manages index.db access, live journals, and MIDI exports."""

    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = data_dir or get_midikeep_dir()
        self.root_dir = self.data_dir
        self.db_path = os.path.join(self.data_dir, "index.db")
        self.journal_dir = os.path.join(self.data_dir, "journal")
        self.sessions_dir = os.path.join(self.data_dir, "sessions")

    @property
    def export_dir(self) -> str:
        """Host-accessible directory for exported MIDI slices and drag-and-drop files."""
        from gi.repository import GLib
        candidates = [
            os.path.join(self.data_dir, "export"),
            os.path.join(GLib.get_user_cache_dir(), "pianola", "export"),
            os.path.join(GLib.get_user_data_dir(), "pianola", "export"),
            os.path.join(tempfile.gettempdir(), "pianola_export")
        ]
        for c in candidates:
            try:
                os.makedirs(c, exist_ok=True)
                test_f = os.path.join(c, ".writable_test")
                with open(test_f, "w") as f:
                    f.write("ok")
                os.remove(test_f)
                return c
            except Exception:
                continue
        return tempfile.gettempdir()

    def archive_exists(self) -> bool:
        """True if Midikeep index.db exists."""
        return os.path.exists(self.db_path)

    def load_sessions(self) -> List[SessionRecord]:
        """Load all recorded sessions from index.db."""
        if not self.archive_exists():
            return []

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT id, start_time, end_time, duration_seconds, active_play_seconds,
                       note_count, device_name, file_path, key_signature, tempo_bpm,
                       starred, notes
                FROM sessions
                ORDER BY start_time ASC
            """)
            records = []
            for row in cursor.fetchall():
                # Parse timestamps
                st_str = row["start_time"]
                et_str = row["end_time"]
                try:
                    st = datetime.fromisoformat(st_str)
                    if st.tzinfo is not None:
                        st = st.astimezone()  # Convert from UTC to local system time
                    else:
                        st = st.replace(tzinfo=timezone.utc).astimezone()
                except Exception:
                    st = datetime.now()
                try:
                    et = datetime.fromisoformat(et_str)
                    if et.tzinfo is not None:
                        et = et.astimezone()
                    else:
                        et = et.replace(tzinfo=timezone.utc).astimezone()
                except Exception:
                    et = st

                records.append(SessionRecord(
                    id=row["id"],
                    start_time=st,
                    end_time=et,
                    duration_seconds=float(row["duration_seconds"] or 0.0),
                    active_play_seconds=float(row["active_play_seconds"] or 0.0),
                    note_count=int(row["note_count"] or 0),
                    device_name=row["device_name"] or "Unknown Device",
                    file_path=row["file_path"],
                    key_signature=row["key_signature"],
                    tempo_bpm=float(row["tempo_bpm"] or 120.0),
                    starred=bool(row["starred"]),
                    notes=row["notes"]
                ))
            return records
        finally:
            conn.close()

    def get_live_session(self) -> Optional[SessionRecord]:
        """Check journal directory for an active, in-progress session."""
        if not os.path.exists(self.journal_dir):
            return None

        # Look for live session or journal file
        candidates = []
        for f in os.listdir(self.journal_dir):
            if f.endswith(".mid") or f.endswith(".journal"):
                full_path = os.path.join(self.journal_dir, f)
                if os.path.isfile(full_path) and os.path.getsize(full_path) > 0:
                    candidates.append(full_path)

        if not candidates:
            return None

        # Take newest candidate
        candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        live_path = candidates[0]

        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(live_path))
            midi_data = MidiParser.parse_file(live_path) if live_path.endswith(".mid") else MidiData()
            return SessionRecord(
                id=None,
                start_time=mtime,
                end_time=mtime,
                duration_seconds=midi_data.duration,
                active_play_seconds=midi_data.active_play_seconds,
                note_count=len(midi_data.notes),
                device_name="Live Session",
                file_path=live_path,
                tempo_bpm=midi_data.tempo_bpm,
                starred=False,
                is_live=True,
                _cached_midi=midi_data
            )
        except Exception:
            return None

    def get_latest_session(self) -> Optional[SessionRecord]:
        """Return the live session if active, else the most recent indexed session."""
        live = self.get_live_session()
        if live:
            return live

        sessions = self.load_sessions()
        if sessions:
            return sessions[-1]
        return None

    def set_starred(self, session_id: int, starred: bool) -> bool:
        """Toggle or set starred status in SQLite."""
        if not self.archive_exists() or session_id is None:
            return False

        conn = sqlite3.connect(self.db_path)
        try:
            with conn:
                conn.execute(
                    "UPDATE sessions SET starred = ? WHERE id = ?",
                    (1 if starred else 0, session_id)
                )
            return True
        except Exception:
            return False
        finally:
            conn.close()

    def export_slice(self, notes: List[NoteEvent], start_time: float, end_time: float,
                     dest_path: str, tempo_bpm: float = 120.0) -> str:
        """Slice notes overlapping [start_time, end_time] and export to a new MIDI file."""
        sliced_notes = []
        for n in notes:
            # Check overlap
            if n.end_time > start_time and n.start_time < end_time:
                # Clamp within the slice window
                s = max(start_time, n.start_time)
                e = min(end_time, n.end_time)
                sliced_notes.append(NoteEvent(
                    pitch=n.pitch,
                    velocity=n.velocity,
                    start_time=s,
                    end_time=e,
                    channel=n.channel,
                    track_index=n.track_index
                ))

        MidiParser.write_notes_to_file(sliced_notes, dest_path, tempo_bpm=tempo_bpm, time_offset=start_time)
        return dest_path

    def export_file(self, src_file: str, dest_path: str) -> str:
        """Copy a whole file to destination."""
        os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
        shutil.copy2(src_file, dest_path)
        return dest_path

    def export_multiple_files(self, src_files: List[str], dest_dir: str) -> List[str]:
        """Export multiple files into a directory."""
        os.makedirs(dest_dir, exist_ok=True)
        exported = []
        for src in src_files:
            filename = os.path.basename(src)
            target = os.path.join(dest_dir, filename)
            shutil.copy2(src, target)
            exported.append(target)
        return exported

    @classmethod
    def load_single_file(cls, filepath: str) -> SessionRecord:
        """Create a SessionRecord from any standalone .mid file on disk."""
        midi_data = MidiParser.parse_file(filepath)
        filename = os.path.basename(filepath)
        mtime = datetime.fromtimestamp(os.path.getmtime(filepath))

        return SessionRecord(
            id=None,
            start_time=mtime,
            end_time=mtime,
            duration_seconds=midi_data.duration,
            active_play_seconds=midi_data.active_play_seconds,
            note_count=len(midi_data.notes),
            device_name=filename,
            file_path=os.path.abspath(filepath),
            tempo_bpm=midi_data.tempo_bpm,
            starred=any("Clapper" in m.text for m in midi_data.markers),
            _cached_midi=midi_data
        )
