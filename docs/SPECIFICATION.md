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

### 1.4 Marquee Selection & Exporting
* **Range Selection:** Click and drag across the timeline to establish a time-range selection.
* **Selection Modifiers:** Support standard modifier keys:
  * `Shift + Click/Drag`: Extend or contract selection boundaries.
  * `Ctrl + Click`: Multi-region or union selection adjustments.
* **DAW Drag & Drop:** Musician can click and drag the selected range (or whole session card) directly out of Pianola and drop into Bitwig Studio, Reaper, Ardour, MuseScore, or Nautilus (`text/uri-list` with rendered `.mid`).
* **Direct Export:** An **Export** button (`Ctrl+E`) allows exporting the selected time range (marquee slice), the active file, or multiple selected files at once directly into standalone `.mid` file(s).

---

### 1.5 Piano Roll Pitch Guide & Middle C (C4)
* A vertical piano keyboard gutter / ruler on the pitch axis with white/black key markers.
* Explicit pitch guide highlighting **Middle C (C4 / MIDI note 60)** across the canvas.

## 2. Midikeep Storage & Journaling Integration

* **Data Directory:** `~/.local/share/midikeep/` (or `$XDG_DATA_HOME/midikeep`)
* **Index Database:** `~/.local/share/midikeep/index.db` (`sessions` table)
* **Crash & Live Journal:** `~/.local/share/midikeep/journal/` (live-recording state detection)
* **Session Takes:** `~/.local/share/midikeep/sessions/YYYY/MM/DD/session_HH-MM-SS.mid`
* **Clapper Markers:** Standard MIDI Marker Meta-Events (`0xFF 0x06 <len> <text>`). Rendered as visual chips on the timeline; clicking a chip seeks playback directly to the marker.
* **Starring:** Toggles `starred` boolean directly in `index.db`.

---

## 3. Audio Engine & Synthesis

* **Synthesis:** Embedded FluidSynth engine (`libfluidsynth.so.3` via ctypes / dynamic portal) with GM SoundFont (`soundfont-fluid-gm` / `GeneralUser GS`).
* **PipeWire / PulseAudio:** Native portal support (`--socket=pulseaudio`).
* **Silence Skipping:** Automatically fast-forwards through silence gaps when "Skip Silence" is enabled.


---

## 4. Known Issues & Platform Integration Notes

### 4.1 Flatpak Drag-and-Drop to Host / Wine Applications
* **Issue:** When running inside the Flatpak sandbox, temporary export slices written to `/tmp/` reside in Flatpak private mount namespace, rendering them inaccessible to host file managers or Wine-bridged applications (e.g. Dorico).
* **Fix Target:** Write drag-and-drop cache slices to `~/.local/share/midikeep/exports/` or implement the `org.freedesktop.portal.FileTransfer` portal to negotiate cross-sandbox file transfers.
