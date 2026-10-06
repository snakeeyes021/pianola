# Pianola 🎹

<p align="center">
  <strong>A modern, lightweight MIDI timeline browser, acoustic scrubber, and Midikeep companion for GNOME.</strong>
</p>

<p align="center">
  <a href="https://gitlab.gnome.org/GNOME/libadwaita"><img src="https://img.shields.io/badge/GTK%204-Libadwaita-3584e4.svg" alt="GTK 4 / Libadwaita" /></a>
  <a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/badge/License-GPL%203.0-blue.svg" alt="License: GPLv3" /></a>
  <a href="https://github.com/snakeeyes021/pianola"><img src="https://img.shields.io/badge/Packaging-Flatpak-orange.svg" alt="Flatpak" /></a>
</p>

---

**Pianola** (`tech.redfoxlabs.Pianola`) is an HIG-compliant GNOME/Libadwaita application designed to preview, audit, and timeline-browse MIDI recordings. Built as the graphical companion to [Midikeep](https://github.com/snakeeyes021/midikeep) (the background MIDI capture daemon on RedFoxOS), Pianola eliminates the friction of opening a heavy DAW just to audition ideas, find a take, or slice out a musical motif.

Pianola focuses strictly on **audition and timeline retrieval**: continuous infinite scroll, acoustic scrubbing, silence compacting, multi-track instrument lanes, marquee selection, and native drag-and-drop into DAWs or score editors. Zero bloat, no score formatting, no heavy mixing consoles.

---

## ✨ Features

### 🔍 Auto-Discovery & Single-File Player
* **Midikeep Companion:** On launch, Pianola automatically detects `~/.local/share/midikeep/index.db` and loads your entire archive into a continuous timeline, positioning the playhead right at your latest take (or live recording journal).
* **Standalone MIDI Player:** Pass any `.mid` file on the command line, double-click a file in Nautilus, or press `Ctrl+O` to open and audition any Standard MIDI File.

### 🎧 Acoustic Scrubbing & General MIDI Synthesis
* **Dorico-Style Acoustic Scrub:** Hold `Ctrl + Space` and glide the mouse anywhere across the piano roll to instantly audit and sustain whatever notes or chords the cursor rolls over.
* **FluidSynth Engine:** Bundled with FluidSynth and the high-fidelity `FluidR3_GM` SoundFont, delivering clean General MIDI playback without clicks or pops.
* **Multi-Instrument Program Change Routing:** Automatically routes General MIDI program changes across all 16 MIDI channels, so orchestral, pop, and chamber MIDI files (e.g. Violin + Piano) play with their authentic instrument sounds simultaneously.

### 📜 Continuous Timeline & Silence Compacting
* **Infinite Continuous Scroll:** Seamlessly pan across hundreds of takes and multiple recording sessions along a single horizontal axis.
* **Silence Compacting:** Silences $\ge 3$ seconds are automatically collapsed into compact 2-second visual folds (`// [pause] //`), so you never get lost scrolling through empty silence.
* **Middle C (C4) Guide:** Dedicated guide lines anchor the pitch axis across both light and dark themes.
* **Pedal Sustain Tails:** Distinct visual distinction between actual finger-held key durations (solid bars) and damper pedal (CC 64) sustain resonance (glowing translucent tails).

### 🎛️ Multi-Track & Instrument View (`Ctrl+T`)
* **Dynamic Lane Heights:** Press `Ctrl+T` or click the toggle button in the bottom bar to switch from unified roll mode to multi-track mode. Pianola allocates vertical lane heights dynamically proportional to the vertical pitch spread of each instrument's material.
* **Color-Coded Instrument Families:** Solo strings, pianos, brass, reeds, guitars, and percussion each receive curated visual palettes.
* **Left-Pinned Gutter Badges:** Instrument icons (🎻, 🎹, 🎺, 🎸, 🥁), channel badges, and real-time sounding indicator lights tell you exactly what instrument is playing.

### 🖱️ Marquee Selection & Native Drag-and-Drop
* **Time Span Selection:** Click and drag across the timeline canvas to marquee-select a time region across takes or within a single take.
* **Direct DAW Drag-and-Drop:** Drag your selection directly out of Pianola and drop it into **Bitwig, Reaper, Ardour, Dorico, MuseScore, or Nautilus**. Multi-format drag sources (`GdkFileList`, `text/uri-list`, `text/plain`) ensure seamless compatibility even under Wine.
* **Direct Export (`Ctrl+E`):** Export selected marquee slices or whole takes to standalone `.mid` files with one click.

### 📌 Navigation & Clapper Markers
* **Section Jumps:** Silences $\ge 3$ seconds demarcate musical sections; jump between section boundaries instantly using `[` and `]`.
* **Hierarchical Navigation:** Jump between files, recording days (`Alt+Left` / `Alt+Right`), and archive boundaries.
* **Calendar Date Picker:** Jump directly to any date in your recording history.
* **Musician Clappers:** Standard MIDI Marker meta-events (`0xFF 0x06`) are highlighted as interactive green chips. Star takes directly from session headers.

---

## ⌨️ Keyboard Shortcuts

| Shortcut | Action |
| :--- | :--- |
| `Space` | Play / Pause from playhead cursor |
| `p` | Play / Pause from marquee selection |
| `Ctrl + Space` + mouse | Acoustic hover sustain scrub (Dorico audition) |
| `]` | Jump to next musical section ($\ge 3$s silence) |
| `[` | Jump to previous musical section |
| `Alt + Right` / `Alt + ]` | Jump to next recording day |
| `Alt + Left` / `Alt + [` | Jump to previous recording day |
| `Ctrl + T` | Toggle Multi-Track instrument lanes |
| `Ctrl + +` / `Ctrl + =` | Zoom in timeline |
| `Ctrl + -` | Zoom out timeline |
| `Ctrl + Wheel` | Horizontal zoom under cursor |
| `Ctrl + O` | Open external MIDI file |
| `Ctrl + E` | Export selection slice or take |
| `Ctrl + Q` | Quit application |
| `Ctrl + ?` | View all keyboard shortcuts |

---

## 🏗️ Architecture

```
Pianola (GTK 4 / Libadwaita / Python 3)
├── src/archive.py          # Midikeep SQLite index reader, SMF parser, sections, GM metadata
├── src/player.py           # FluidSynth ctypes binding, PulseAudio fallback, scrub engine
├── src/timeline_canvas.py   # Cairo 2D continuous timeline, silence folding, multi-track lanes
├── src/piano_keyboard.py    # Vertical keybed gutter & real-time multi-track sounding lights
├── src/overview_minimap.py  # Global archive timeline minimap & day density navigation
└── src/window.py           # Application window, drag-and-drop provider, actions
```

---

## 🚀 Building & Installation

### Option 1: Flatpak (Recommended)
Pianola is packaged as a standard Flatpak sandbox using GNOME 47 Runtime and includes FluidSynth and FluidR3_GM:

```bash
# Build and install locally
flatpak-builder --user --install --force-clean build-dir tech.redfoxlabs.Pianola.json

# Run Pianola
flatpak run tech.redfoxlabs.Pianola
```

### Option 2: GNOME Builder
1. Clone the repository:
   ```bash
   git clone https://github.com/snakeeyes021/pianola.git
   cd pianola
   ```
2. Open the project in **GNOME Builder**.
3. Select the Flatpak runtime and click **Run** (`Ctrl+F5`).

### Option 3: Local Meson Setup (Development)
Ensure system dependencies are installed (`python3-gobject`, `gtk4`, `libadwaita-1`, `fluidsynth`, `cairo`):

```bash
meson setup _build
meson compile -C _build
./_build/src/pianola
```

---

## 🧪 Testing

Run unit tests covering SMF parsing, 3-second silence section boundaries, CC 64 sustain pedal extensions, multi-track GM routing, and transport scrubbing:

```bash
python3 tests/test_archive.py
python3 tests/test_player.py
```

---

## 📄 License & Attribution

* **Pianola:** Copyright © 2026 Matthew Samson / RedFoxLabs. Licensed under [GPL-3.0-or-later](LICENSE).
* **FluidR3_GM SoundFont:** Frank Wen / SoundFont Community (MIT-style soundfont license).
