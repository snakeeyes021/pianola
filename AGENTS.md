# AGENTS.md: Pianola Engineering Guide & Architecture

## 1. Project Overview & Identity
**Pianola** (`tech.redfoxlabs.Pianola`) is a modern, lightweight, HIG-compliant GNOME/Libadwaita application designed to preview, scrub, and timeline-browse MIDI recordings.

* **Core Language:** Python 3 (PyGObject, GTK 4, Libadwaita).
* **Build System:** Meson (`meson.build`) with GResource and Gettext (`po/`).
* **Packaging Target:** Flatpak (`tech.redfoxlabs.Pianola.json`), targeted for distribution via RedFoxLabs GitHub Container Registry (`ghcr.io/snakeeyes021/pianola`) and GNOME Circle standards.
* **App Philosophy:** Focused and frictionless. Play, pause, acoustic scrub, continuous infinite scroll, jump dates & sections, skip silences, marquee select, and drag-and-drop into DAWs. No heavy DAW editing, no score editing, zero bloat.

---

## 2. Backend Storage Context: Midikeep
Pianola is the official graphical companion to **Midikeep** (`midikeep-daemon`), the background MIDI recording engine on RedFoxOS.

### Storage Layout (XDG Compliant)
* **Root Data Directory:** `~/.local/share/midikeep/` (or `$XDG_DATA_HOME/midikeep`)
* **Index Database:** `~/.local/share/midikeep/index.db`
* **Recorded Takes:** `~/.local/share/midikeep/sessions/YYYY/MM/DD/session_HH-MM-SS.mid`
* **Crash & Live Journal:** `~/.local/share/midikeep/journal/`

### SQLite Schema (`index.db`)
```sql
CREATE TABLE IF NOT EXISTS sessions (
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

CREATE INDEX IF NOT EXISTS idx_sessions_start ON sessions(start_time);
CREATE INDEX IF NOT EXISTS idx_sessions_starred ON sessions(starred);
```

### MIDI Markers ("Clappers" / Signposts)
Takes flagged by the musician contain Standard MIDI Marker Meta-Events:
* **Byte sequence:** `0xFF 0x06 <len> <text>` (e.g. `Marker: Clapper ★` or custom user notes).
* In SQLite, flagged takes have `starred = 1` and `notes` containing the marker label.

---

## 3. User Experience & Core Requirements

### 3.1 Launch & Initial State
1. **Default View (Auto-Discovery):**
   * On launch, Pianola checks for the existence of `~/.local/share/midikeep/index.db`.
   * **If the Midikeep archive exists and no specific file was passed on the command line:** Pianola immediately opens into the **Archive Timeline View** by default, as if the entire archive was the file the user intended to open.
   * **Default Start Location:** Timeline positions at the beginning of the **last session** (if a session is actively being recorded in `~/.local/share/midikeep/journal/`, jump to that active session; otherwise, jump to the most recent complete session in `index.db`).
2. **Single File Player Mode:**
   * If launched with a file argument (e.g. `pianola /path/to/take.mid` or double-clicking a `.mid` in Nautilus), Pianola opens that single file directly in the player view.
   * A "Open File..." menu action / shortcut (`Ctrl+O`) allows loading any external `.mid` file at any time.
3. **Empty State:**
   * The application only shows a blank/empty placeholder screen if **no Midikeep archive exists** AND **no file was opened**.

### 3.2 Timeline & Acoustic Scrubbing
* **Infinite Continuous Scroll:** The archive view allows continuous, infinite scrolling across file boundaries along the timeline.
* **Acoustic Scrubbing (Hover Sustain):** With a key combo (e.g. `Ctrl+Space`), the mouse audits whatever spot it rolls over, sounding and sustaining the active notes at that point in time.
* **Timeline Zoom:** Standard horizontal zoom controls (e.g. `Ctrl + Wheel`, `Ctrl+=` / `Ctrl+-`, zoom slider).

### 3.3 Playback Controls & Navigation
* **Play from Cursor:** `Space` (toggles play/pause from the current playhead cursor).
* **Play from Selection:** `p` (toggles play/pause starting from the start of the marquee selection).
* **Pause:** `Space` or `p`.
* **Section Jump Navigation:**
  * Silences of **3 seconds or longer** demarcate a new musical section.
  * Shortcuts to jump cursor to previous/next section boundary.
* **Hierarchical Jumps:**
  * Jump to beginning of file / take.
  * Jump to beginning of day.
  * Jump to beginning of month.
  * Jump to beginning of year.
  * Jump to beginning of archive.
* **Calendar / Date Picker:** Popover to jump directly to any date.
* **Silence Skipping Toggle:** Fast-forwards past periods with no note events.
* **Clapper Chips:** Visual markers on the timeline scrub bar; clicking jumps to the marker.
* **Starring:** Toggles `starred = 1 / 0` in `index.db`.

### 3.4 Marquee Selection, Drag & Drop, and Export
* **Marquee Selection:** Click and drag across the timeline with standard `Shift` (extend) and `Ctrl` (modify) shortcuts to select a time span across files or within a take.
* **Native Drag & Drop:** Musician can click and drag the selected range or session card directly into Bitwig, Reaper, Ardour, Dorico/MuseScore, or Nautilus (`text/uri-list`).
* **Export Action:** Export button (`Ctrl+E`) to export the selected time range (marquee slice), the active file, or multiple selected files at once into standalone `.mid` file(s).

---

## 4. Flatpak Manifest & Sandboxing (`tech.redfoxlabs.Pianola.json`)

To work inside the Flatpak sandbox, ensure the following permissions are present in `finish-args`:
```json
"finish-args": [
    "--share=ipc",
    "--socket=fallback-x11",
    "--socket=wayland",
    "--device=dri",
    "--socket=pulseaudio",
    "--filesystem=xdg-data/midikeep:rw",
    "--filesystem=home:ro"
]
```
And add modules for `fluidsynth` and a GM SoundFont (e.g. `soundfont-fluid-gm` or `GeneralUser GS`).

---

## 5. Development & Build Commands

### Local Meson Build
```bash
meson setup _build
ninja -C _build
./_build/src/pianola
```

### Flatpak Local Build & Test
```bash
flatpak-builder --user --install --force-clean build-dir tech.redfoxlabs.Pianola.json
flatpak run tech.redfoxlabs.Pianola
```

---

## 6. Implementation Checklist for the Agent

- [ ] **Archive Manager (`src/archive.py`):**
  - Read `index.db` sessions with SQLite and inspect live journal in `~/.local/share/midikeep/journal/`.
  - Parse markers, note events, and silences (>= 3s section boundaries) from Standard MIDI Files.
- [ ] **Player & Synthesis Engine (`src/player.py`):**
  - Integrate FluidSynth playback with seek, pause, and time tracking.
  - Implement acoustic scrubbing (hover note sustain on `Ctrl+Space`).
  - Implement silence detection to jump ahead when "Skip Silence" is active.
- [ ] **Continuous Timeline View (`src/archive_view.py` / `src/timeline_canvas.py`):**
  - Infinite scrollable timeline across session boundaries.
  - Marquee time range selection (`Shift`/`Ctrl` support).
  - Note density / piano roll visualization with Clapper chips.
  - Horizontal zoom controls.
- [ ] **Transport & Navigation Controls:**
  - `Space` (play/pause from cursor), `p` (play/pause from selection).
  - Jump to section (>= 3s silence), file, day, month, year, archive.
  - Date picker popover.
- [ ] **Drag & Drop and Export:**
  - Setup `Gtk.DragSource` for selected ranges and sessions.
  - Export selected range / session to `.mid` file.
- [ ] **Flatpak Packaging:**
  - Bundle `fluidsynth` and soundfont in `tech.redfoxlabs.Pianola.json`.
  - Add GitHub Actions workflow `.github/workflows/flatpak.yml` to publish OCI bundle to GHCR.
