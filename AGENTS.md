# AGENTS.md: Pianola Engineering Guide & Architecture

## 1. Project Overview & Identity
**Pianola** (`tech.redfoxlabs.Pianola`) is a modern, lightweight, HIG-compliant GNOME/Libadwaita application designed to preview, scrub, and timeline-browse MIDI recordings.

* **Core Language:** Python 3 (PyGObject, GTK 4, Libadwaita).
* **Build System:** Meson (`meson.build`) with GResource and Gettext (`po/`).
* **Packaging Target:** Flatpak (`tech.redfoxlabs.Pianola.json`), targeted for distribution via RedFoxLabs GitHub Container Registry (`ghcr.io/snakeeyes021/pianola`) and GNOME Circle standards.
* **App Philosophy:** Focused and frictionless. Play, pause, scrub, scroll, jump dates, skip silences, and drag-and-drop into DAWs. No heavy DAW editing, no score editing, zero bloat.

---

## 2. Backend Storage Context: Midikeep
Pianola is the official graphical companion to **Midikeep** (`midikeep-daemon`), the background MIDI recording engine on RedFoxOS.

### Storage Layout (XDG Compliant)
* **Root Data Directory:** `~/.local/share/midikeep/` (or `$XDG_DATA_HOME/midikeep`)
* **Index Database:** `~/.local/share/midikeep/index.db`
* **Recorded Takes:** `~/.local/share/midikeep/sessions/YYYY/MM/DD/session_HH-MM-SS.mid`
* **Crash Journal:** `~/.local/share/midikeep/journal/`

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
2. **Single File Player Mode:**
   * If launched with a file argument (e.g. `pianola /path/to/take.mid` or double-clicking a `.mid` in Nautilus), Pianola opens that single file directly in the player view.
   * A "Open File..." menu action / shortcut (`Ctrl+O`) allows loading any external `.mid` file at any time.
3. **Empty State:**
   * The application only shows a blank/empty placeholder screen if **no Midikeep archive exists** AND **no file was opened**.

### 3.2 Audio Engine & Auditioning
* **Synthesis:** Lightweight General MIDI playback via **FluidSynth** (`libfluidsynth` / `pyfluidsynth` or ctypes) using a bundled or system General MIDI SoundFont (`soundfont-fluid-gm` / `GeneralUser GS`).
* **Audio Backend:** PipeWire / PulseAudio via standard portals (`--socket=pulseaudio`).
* **Playback Controls:** Play / Pause (`Space`), Stop, Scrub Slider, Elapsed Time & Total Duration.

### 3.3 Navigation, Date Jump & Silence Skipping
1. **Calendar / Date Picker:**
   * A clean date picker popover to jump instantly to any recorded day and session.
2. **Silence Skipping Toggle ("Skip Silence"):**
   * Fast-forwards past periods with no note events—handling both large inter-session gaps and idle pauses inside a take.
3. **Marker / Clapper Chips:**
   * Takes with Clapper markers show visual chips/flags on the timeline scrub bar.
   * Clicking a marker chip seeks playback directly to that exact moment.
4. **Starring / Favoriting:**
   * Ability to toggle the star status (`starred = 1 / 0`) in `index.db` directly from the UI.

### 3.4 Native Drag & Drop
* Musician can click and drag any session card or the active take header directly out of Pianola and drop it into:
  * **Bitwig Studio**
  * **Reaper**
  * **Ardour**
  * **Dorico / MuseScore**
  * **Nautilus (File Manager)**
* Uses standard Wayland / X11 MIME drag-and-drop (`text/uri-list` with `file://...` URIs).

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
  - Read `index.db` sessions with SQLite.
  - Parse markers and note events from Standard MIDI Files.
- [ ] **Player Engine (`src/player.py`):**
  - Integrate FluidSynth playback with seek, pause, and time tracking.
  - Implement silence detection to jump ahead when "Skip Silence" is active.
- [ ] **Archive Timeline View (`src/archive_view.py` / `src/archive_view.ui`):**
  - Infinite scroll list of takes grouped by date.
  - Take cards showing start time, duration, note count, device name, and star toggle.
- [ ] **Player View & Mini Piano Roll (`src/player_view.py`):**
  - Scrub slider with note density visualizer.
  - Marker flags on the timeline.
- [ ] **Drag & Drop Source:**
  - Setup `Gtk.DragSource` on session cards and player header with `Gdk.ContentProvider.new_for_value(Gio.File)`.
- [ ] **Flatpak Packaging:**
  - Bundle `fluidsynth` and soundfont in `tech.redfoxlabs.Pianola.json`.
  - Add GitHub Actions workflow `.github/workflows/flatpak.yml` to publish OCI bundle to GHCR.
