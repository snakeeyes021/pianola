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
class NoteEvent:
    pitch: int
    velocity: int
    start_time: float  # seconds
    end_time: float    # seconds
    channel: int = 0
    key_end_time: Optional[float] = None  # Physical key release time (before pedal sustain)

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

        for _ in range(num_tracks):
            track_magic = stream.read(4)
            if not track_magic:
                break
            if track_magic != b"MTrk":
                # Unknown chunk, skip
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
                    # Running status
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
                    # Meta Event
                    meta_type = tstream.read(1)[0]
                    meta_len = cls._read_vlq(tstream)
                    meta_bytes = tstream.read(meta_len)

                    if meta_type == 0x51 and meta_len == 3:
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
                    # SysEx
                    sysex_len = cls._read_vlq(tstream)
                    tstream.seek(sysex_len, io.SEEK_CUR)
                else:
                    # Channel event
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

                    if event_type == 0xB0 and b1 == 64:
                        # CC 64 Damper / Sustain Pedal
                        raw_events.append((curr_tick, 1, "sustain", (channel, b2)))

                    if event_type == 0x90:
                        # Note on (if b2 == 0, note off)
                        if b2 > 0:
                            raw_events.append((curr_tick, 2, "note_on", (channel, b1, b2)))
                        else:
                            raw_events.append((curr_tick, 2, "note_off", (channel, b1, 0)))
                    elif event_type == 0x80:
                        raw_events.append((curr_tick, 2, "note_off", (channel, b1, b2)))

        # Sort raw events chronologically
        raw_events.sort(key=lambda x: (x[0], x[1]))

        # Convert ticks to seconds using tempos
        current_mpqn = 500000  # Default 120 BPM (500,000 microseconds / quarter note)
        last_tick = 0
        current_time = 0.0

        notes: List[NoteEvent] = []
        markers: List[MarkerEvent] = []
        open_notes: Dict[Tuple[int, int], List[Tuple[float, int]]] = {}
        # (channel, pitch) -> list of (start_time, velocity)
        sustain_pedal: Dict[int, bool] = {ch: False for ch in range(16)}
        pedaled_notes: Dict[int, List[Tuple[int, float, float, int]]] = {ch: [] for ch in range(16)}
        # channel -> list of (pitch, start_time, velocity) waiting for pedal release

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
            elif etype == "marker":
                markers.append(MarkerEvent(time=current_time, text=payload))
            elif etype == "note_on":
                channel, pitch, vel = payload
                key = (channel, pitch)
                if key not in open_notes:
                    open_notes[key] = []
                open_notes[key].append((current_time, vel))
            elif etype == "sustain":
                channel, val = payload
                is_down = val >= 64
                sustain_pedal[channel] = is_down
                if not is_down:
                    # Pedal released: finalize all notes that were released while pedal was held
                    for pitch, start_t, key_rel_t, vel in pedaled_notes[channel]:
                        end_t = max(start_t + 0.05, current_time)
                        notes.append(NoteEvent(
                            pitch=pitch, velocity=vel, start_time=start_t, end_time=end_t,
                            channel=channel, key_end_time=key_rel_t
                        ))
                    pedaled_notes[channel].clear()
            elif etype == "note_off":
                channel, pitch, _ = payload
                key = (channel, pitch)
                if key in open_notes and open_notes[key]:
                    start_t, vel = open_notes[key].pop(0)
                    if sustain_pedal.get(channel, False):
                        # Key released but sustain pedal is holding the dampers open
                        pedaled_notes[channel].append((pitch, start_t, current_time, vel))
                    else:
                        end_t = max(start_t + 0.02, current_time)
                        notes.append(NoteEvent(
                            pitch=pitch, velocity=vel, start_time=start_t, end_time=end_t,
                            channel=channel, key_end_time=end_t
                        ))

        # Finalize lingering pedaled notes
        for channel, pnotes in pedaled_notes.items():
            for pitch, start_t, key_rel_t, vel in pnotes:
                end_t = max(start_t + 0.1, current_time)
                notes.append(NoteEvent(
                    pitch=pitch, velocity=vel, start_time=start_t, end_time=end_t,
                    channel=channel, key_end_time=key_rel_t
                ))

        # Close any lingering open keys
        for (channel, pitch), starts in open_notes.items():
            for start_t, vel in starts:
                end_t = max(start_t + 0.1, current_time)
                notes.append(NoteEvent(pitch=pitch, velocity=vel, start_time=start_t, end_time=end_t, channel=channel))

        notes.sort(key=lambda n: n.start_time)
        duration = current_time if notes or markers else 0.0
        if notes:
            duration = max(duration, max(n.end_time for n in notes))

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
            tempo_bpm=initial_bpm
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
        self.db_path = os.path.join(self.data_dir, "index.db")
        self.journal_dir = os.path.join(self.data_dir, "journal")
        self.sessions_dir = os.path.join(self.data_dir, "sessions")

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
                    channel=n.channel
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
