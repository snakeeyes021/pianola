# Pianola

<p align="center">
  <strong>A minimal MIDI timeline browser, acoustic scrubber, and Midikeep companion for GNOME.</strong>
</p>

<p align="center">
  <a href="https://gitlab.gnome.org/GNOME/libadwaita"><img src="https://img.shields.io/badge/GTK%204-Libadwaita-3584e4.svg" alt="GTK 4 / Libadwaita" /></a>
  <a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/badge/License-GPL%203.0-blue.svg" alt="License: GPLv3" /></a>
  <a href="https://github.com/snakeeyes021/pianola"><img src="https://img.shields.io/badge/Packaging-Flatpak-orange.svg" alt="Flatpak" /></a>
</p>

---

**Pianola** is a lightweight GTK 4 / Libadwaita application for previewing, scrubbing, and browsing MIDI recordings. It can be used as a standalone player for any Standard MIDI File, or as the graphical companion to Midikeep, the background MIDI capture service on [RedFox OS](https://github.com/snakeeyes021/redfox-os).

Instead of opening a full DAW just to find a take or audit an idea, Pianola provides fast acoustic scrubbing, quick section jumps, multi-track viewing, marquee selection, and direct drag-and-drop into DAWs and notation editors.

---

## Features

### Core MIDI Player
These features work with any standard `.mid` file opened via the file chooser, file manager, or command line:

* **Acoustic Scrubbing:** Hold `Ctrl + Space` and roll the mouse across notes to audition and sustain chords, similar to notation editors.
* **SoundFont Synthesis:** Built-in FluidSynth playback supporting standard General MIDI soundfonts.
* **Multi-Track View:** Toggle between a unified roll and separate instrument lanes (`Ctrl+T`), sized proportionally to each instrument's pitch range.
* **Silence Compacting:** Extended pauses are collapsed visually on the timeline to keep takes compact.
* **Section Jumps:** Jump between musical phrases and sections using `[` and `]`.
* **Pedal Sustain Display:** Visual distinction between key presses and damper pedal (CC 64) resonance tails.
* **Marquee Selection & DAW Drag-and-Drop:** Click and drag to select any time region, then drag the selection directly into DAWs (Bitwig, Reaper, Ardour), notation software, or file managers.
* **Export:** Save selected time slices or entire takes directly to `.mid` files (`Ctrl+E`).

### Midikeep Archive Companion
When running on RedFox OS or systems with the [Midikeep](https://github.com/snakeeyes021/redfox-os) recording daemon, Pianola automatically detects the archive and provides extended history navigation:

* **Automatic Archive Loading:** Opens directly into your recording timeline on launch, positioning the view at your latest take.
* **Continuous History Scroll:** Pan seamlessly across recording sessions spanning days, months, and years.
* **Date & Day Navigation:** Jump between recording days (`Alt+Left` / `Alt+Right`), inspect the day's activity overview minimap, or jump directly to any date with the calendar popover.
* **Clapper Markers & Starred Takes:** Visual markers for flagged takes with one-click navigation and favorite toggles.

---

## Keyboard Shortcuts

| Shortcut | Action |
| :--- | :--- |
| `Space` | Play / Pause from cursor |
| `p` | Play / Pause from start of selection |
| `Ctrl + Space` + mouse | Acoustic hover scrub |
| `]` / `[` | Jump to next / previous section |
| `Alt + Right` / `Alt + Left` | Jump to next / previous day *(archive mode)* |
| `Ctrl + T` | Toggle Multi-Track lanes |
| `Ctrl + +` / `Ctrl + -` | Zoom in / out |
| `Ctrl + Wheel` | Zoom at cursor (or scroll during audition) |
| `Middle Click` + drag | Smooth pan / autoscroll |
| `Ctrl + O` | Open MIDI file |
| `Ctrl + E` | Export selection or take |
| `Ctrl + Q` | Quit |
| `Ctrl + ?` | Show all keyboard shortcuts |

---

## Building & Installation

### Flatpak (Recommended)
Pianola is packaged as a Flatpak using the GNOME 47 runtime:

```bash
# Build and install locally
flatpak-builder --user --install --force-clean build-dir tech.redfoxlabs.Pianola.json

# Run
flatpak run tech.redfoxlabs.Pianola
```

### GNOME Builder
1. Clone the repository:
   ```bash
   git clone https://github.com/snakeeyes021/pianola.git
   cd pianola
   ```
2. Open the project in GNOME Builder.
3. Select the Flatpak runtime and run (`Ctrl+F5`).

### Local Meson Build
Requires `python3-gobject`, `gtk4`, `libadwaita-1`, `fluidsynth`, and `cairo`:

```bash
meson setup _build
meson compile -C _build
./_build/src/pianola
```

---

## Testing

Run unit tests covering MIDI parsing, section detection, multi-track routing, and playback logic:

```bash
python3 tests/test_archive.py
python3 tests/test_player.py
python3 tests/test_overview_minimap.py
python3 tests/test_timeline_canvas.py
```

---

## License

Copyright © 2026 Matthew Samson / Red Fox Labs. Licensed under [GPL-3.0-or-later](COPYING).
