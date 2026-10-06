# piano_keyboard.py
#
# Copyright 2026 Matthew Samson
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Visual piano keyboard gutter for Pianola timeline.

Provides a fixed vertical piano keybed anchored to the pitch axis,
highlighting Middle C (C4) and lighting up active sounding pitches in real time.
"""

from typing import Set, Tuple
import cairo

from gi.repository import Gtk, Gdk, GLib
from .timeline_canvas import TimelineCanvas
from .player import AudioPlayer


class PianoKeyboardGutter(Gtk.DrawingArea):
    """Vertical piano keyboard gutter pinned to the left of the timeline canvas."""

    KEYBOARD_WIDTH = 50

    def __init__(self, canvas: TimelineCanvas, player: AudioPlayer):
        super().__init__()
        self.canvas = canvas
        self.player = player

        self.set_content_width(self.KEYBOARD_WIDTH)
        self.set_hexpand(False)
        self.set_vexpand(True)
        self.set_draw_func(self._on_draw)

    def _get_active_pitches(self) -> Set[int]:
        active = set()
        curr_t = self.player.current_time

        if self.canvas.scrub_active and self.canvas.scrub_cursor_time is not None:
            # Active scrub audition pitches
            t = self.canvas.scrub_cursor_time
            for item in self.canvas.session_items:
                for n in item.midi_data.notes:
                    if item.timeline_offset + n.start_time <= t <= item.timeline_offset + n.end_time:
                        active.add(n.pitch)
        elif self.player.is_playing:
            # Active playback pitches
            for item in self.canvas.session_items:
                for n in item.midi_data.notes:
                    if item.timeline_offset + n.start_time <= curr_t <= item.timeline_offset + n.end_time:
                        active.add(n.pitch)

        return active

    def _on_draw(self, drawing_area, cr: cairo.Context, width: int, height: int):
        # 1. Background gutter
        cr.set_source_rgb(0.11, 0.11, 0.12)
        cr.paint()

        roll_top = self.canvas.HEADER_HEIGHT
        roll_bottom = height - self.canvas.FOOTER_HEIGHT
        roll_h = max(10.0, roll_bottom - roll_top)

        min_p, max_p, pitch_range = self.canvas._get_global_pitch_bounds()
        lane_h = (roll_h - 10.0) / pitch_range
        active_pitches = self._get_active_pitches()

        # 2. Draw White Keys
        for p in range(min_p, max_p + 1):
            is_black = (p % 12) in (1, 3, 6, 8, 10)
            if is_black:
                continue

            norm_p = (p - min_p) / pitch_range
            y = roll_bottom - (norm_p * (roll_h - 10.0)) - 8.0
            kh = max(2.5, lane_h)

            if p in active_pitches:
                cr.set_source_rgba(0.20, 0.85, 0.75, 0.95)
            else:
                cr.set_source_rgba(0.85, 0.85, 0.88, 0.95)

            cr.rectangle(1.0, y - (kh / 2.0), width - 3.0, kh)
            cr.fill()

            # White key separator line
            cr.set_source_rgba(0.25, 0.25, 0.28, 0.8)
            cr.set_line_width(0.8)
            cr.rectangle(1.0, y - (kh / 2.0), width - 3.0, kh)
            cr.stroke()

        # 3. Draw Black Keys (overlayed on top of white keys)
        black_w = width * 0.62
        for p in range(min_p, max_p + 1):
            is_black = (p % 12) in (1, 3, 6, 8, 10)
            if not is_black:
                continue

            norm_p = (p - min_p) / pitch_range
            y = roll_bottom - (norm_p * (roll_h - 10.0)) - 8.0
            kh = max(2.0, lane_h * 0.9)

            if p in active_pitches:
                cr.set_source_rgba(0.15, 0.75, 0.65, 0.95)
            else:
                cr.set_source_rgba(0.13, 0.13, 0.15, 1.0)

            cr.rectangle(1.0, y - (kh / 2.0), black_w, kh)
            cr.fill()

            # Black key subtle highlight border
            cr.set_source_rgba(0.3, 0.3, 0.35, 0.9)
            cr.set_line_width(0.8)
            cr.rectangle(1.0, y - (kh / 2.0), black_w, kh)
            cr.stroke()

        # 4. Octave & Middle C (C4) labels
        for p in range(min_p, max_p + 1):
            if p % 12 != 0:
                continue

            norm_p = (p - min_p) / pitch_range
            y = roll_bottom - (norm_p * (roll_h - 10.0)) - 8.0

            if p == 60:
                # Middle C (C4) badge
                cr.set_source_rgba(0.18, 0.48, 0.85, 0.95)
                cr.rectangle(width - 24.0, y - 6.0, 22.0, 12.0)
                cr.fill()

                cr.set_source_rgb(1.0, 1.0, 1.0)
                cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
                cr.set_font_size(8.5)
                cr.move_to(width - 21.0, y + 3.0)
                cr.show_text("C4")
            else:
                # Other C octaves
                oct_name = f"C{(p // 12) - 1}"
                cr.set_source_rgba(0.35, 0.35, 0.40, 0.9)
                cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
                cr.set_font_size(8.0)
                cr.move_to(width - 18.0, y + 3.0)
                cr.show_text(oct_name)

        # 5. Right border dividing keyboard from roll canvas
        cr.set_source_rgba(0.25, 0.25, 0.28, 0.9)
        cr.set_line_width(1.0)
        cr.move_to(width - 0.5, 0)
        cr.line_to(width - 0.5, height)
        cr.stroke()
