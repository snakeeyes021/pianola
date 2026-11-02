# Pianola Specification & Architecture

Pianola (`tech.redfoxlabs.Pianola`) is a modern, lightweight, HIG-compliant GNOME/Libadwaita application designed to audition, scrub, browse, and export MIDI recordings from the Midikeep archive (`midikeep-daemon`) or standalone MIDI files.

---

## 1. Core Interaction Model & Timeline Browsing

### 1.1 Continuous Infinite Archive Timeline
* **Seamless Cross-Boundary Scrolling:** The Archive Timeline View renders sessions continuously along a single coherent time dimension. Users can scroll infinitely backwards and forwards across file boundaries without having to manually open or switch sessions.
* **Default Launch Position:** On opening with an archive, the timeline jumps directly to the beginning of the **last session** (if an active session is currently being journaled in `~/.local/share/midikeep/journal/`, jump to that active session; otherwise, jump to the most recent completed take in `index.db`).
* **Zooming:** Standard horizontal timeline zoom controls (e.g. `Ctrl + MouseWheel`, zoom slider/shortcuts `Ctrl+=` / `Ctrl+-`), allowing smooth transitions from bird's-eye archive views down to individual note-level fidelity.

### 1.2 Acoustic Scrubbing (Hover Sustain)
* **Audio Scrubbing Mode:** Scrubbing is acoustic auditioning. When engaging the scrub combo (e.g. `Ctrl + Space` held, or scrub tool mode), moving the mouse along the timeline dynamically sounds and sustains the MIDI note(s) active at that specific instant in time.
* Moving away or releasing the scrub gesture dampens the notes cleanly.

### 1.3 Playback & Jump Controls
* **Play from Cursor (`Space`):** Starts playback from the current cursor position. Pressing `Space` again pauses.
* **Play from Selection (`p`):** Starts playback from the start of the marquee selection. Pressing `p` again pauses.
* **Section Jump Navigation:**
  * **Section definition:** Any period of silence **3 seconds or longer** demarcates a new musical section.
  * **Jump to Section:** Move playback/cursor instantly to the beginning of the previous or next section.
* **Hierarchical Boundary Jumps:**
  * Jump to beginning of **File / Take**
  * Jump to beginning of **Day**
  * Jump to beginning of **Month**
  * Jump to beginning of **Year**
  * Jump to beginning of **Archive** (earliest recorded take)

### 1.4 Marquee Selection, Per-Track Selection, Copy & Exporting
* **Whole-Range & Per-Track Selection:**
  * In unified view (or when dragging along the bottom time ruler), click and drag selects a time range across all tracks.
  * In Multi-Track view (`Ctrl+T`), dragging inside a single track lane selects that time stretch on only that track. Dragging vertically across several lanes selects that stretch on each spanned track.
* **Selection Modifiers:**
  * `Shift + Click/Drag`: Extend or contract selection boundaries.
  * `Ctrl + Drag` / `Ctrl + Click`: Add or subtract individual track stretches or non-contiguous tracks for fine-grained multi-track selection.
* **Copy (`Ctrl+C`) & DAW Drag & Drop:** Musician can press `Ctrl+C` or click and drag the selected stretch (or whole session card) directly out of Pianola and drop/paste into Bitwig Studio, Reaper, Ardour, Dorico, MuseScore, or Nautilus (`GdkFileList`, `text/uri-list`, `audio/midi`).
* **Direct Export:** An **Export** button (`Ctrl+E`) exports the selected whole-range or per-track slice (SMF Type 0 for a single track, SMF Type 1 preserving track names and program changes for multiple tracks) or the active file into a standalone `.mid` file.

---

### 1.5 Piano Roll Pitch Guide & Middle C (C4)
* A vertical piano keyboard gutter / ruler on the pitch axis with white/black key markers.
* Explicit pitch guide highlighting **Middle C (C4 / MIDI note 60)** across the canvas.

## 2. Midikeep Storage & Journaling Integration

* **Shared Configuration File:** `~/.config/midikeep/config.yaml` (or `$XDG_CONFIG_HOME/midikeep/config.yaml`, overridable via `$MIDIKEEP_CONFIG`)
  * Shared between `midikeep-daemon`, the `midikeep` CLI, and Pianola.
  * Reads `data_dir` (with `~` and environment variable expansion) to locate the Midikeep archive, falling back to `$XDG_DATA_HOME/midikeep` / `~/.local/share/midikeep/` if unset or if the config file is absent.
* **Data Directory:** `<data_dir>` (default `~/.local/share/midikeep/` or `$XDG_DATA_HOME/midikeep`)
* **Index Database:** `<data_dir>/index.db` (`sessions` table)
* **Crash & Live Journal:** `<data_dir>/journal/` (live-recording state detection)
* **Session Takes:** `<data_dir>/sessions/YYYY-MM/YYYY-MM-DD_HHMMSS_<device>.mid` (or `sessions/YYYY/MM/DD/...`)
* **Clapper Markers:** Standard MIDI Marker Meta-Events (`0xFF 0x06 <len> <text>`). Rendered as visual chips on the timeline; clicking a chip seeks playback directly to the marker.
* **Starring:** Toggles `starred` boolean directly in `index.db`.

---

## 3. Audio Engine & Synthesis

* **Synthesis:** Embedded FluidSynth engine (`libfluidsynth.so.3` via ctypes / dynamic portal) with GM SoundFont (`soundfont-fluid-gm` / `GeneralUser GS`).
* **PipeWire / PulseAudio:** Native portal support (`--socket=pulseaudio`).
* **Silence Skipping:** Automatically fast-forwards through silence gaps when "Skip Silence" is enabled.


---

## 4. Known Issues & Platform Integration Notes

### 4.1 External Drag-and-Drop Interoperability (File Explorer, Wine/Dorico)
* **Status:** Logged known issue for future investigation / enhancement.
* **Observed Behavior:** Dragging out from Pianola (via `text/uri-list`) does not currently drop into certain external applications, including the system File Explorer (Nautilus) and Dorico (running through Wine).
* **Root Causes / Architecture Analysis:**
  1. **Sandbox Namespace Isolation:** When running inside Flatpak, temporary export slices written to `/tmp/pianola_export.mid` reside inside Flatpak's private mount namespace (`/tmp`), which is inaccessible to host applications outside the sandbox.
  2. **Host Accessibility:** Writing cache files to a host-accessible path (e.g. `~/.local/share/midikeep/exports/`) or using XDG Desktop Portal `org.freedesktop.portal.FileTransfer` is required for host applications to read the URI.
  3. **Wine / Windows Bridge:** Wine applications running on Wayland/X11 have special OLE/DND clipboard and virtual drive path translation requirements (e.g. `Z:\path\to\file`).
