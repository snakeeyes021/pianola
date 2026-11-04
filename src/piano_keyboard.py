# piano_keyboard.py
#
# Copyright 2026 Matthew Samson
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Visual piano keyboard gutter for Pianola timeline.

Provides a fixed vertical piano keybed anchored to the pitch axis,
highlighting Middle C (C4) and lighting up active sounding pitches in real time.
"""

import math
from typing import Callable, List, Optional, Set, Tuple, Any
import cairo
from .archive import get_track_color

from gettext import gettext as _
from gi.repository import Gtk, Gdk, GLib, Adw
from .timeline_canvas import TimelineCanvas
from .player import AudioPlayer


BLACK_PITCH_CLASSES = (1, 3, 6, 8, 10)
BLACK_KEY_WIDTH_RATIO = 0.62


def is_black_key(pitch: int) -> bool:
    return (pitch % 12) in BLACK_PITCH_CLASSES


def keyboard_key_layout(min_p: int, max_p: int) -> Tuple[List[Tuple[int, float, float]], List[Tuple[int, float, float]]]:
    """Vertical extent of every key, in pitch units (row p spans p-0.5 .. p+0.5 on the roll).

    White keys are full-width and wrap around their black neighbours: a white key reaches to the
    centre line of an adjacent black key's row, and to its own row edge where two white keys meet
    (E/F and B/C). Black keys cover exactly their own row and are drawn on top. Every key stays
    aligned with its note row on the roll. Returns (white_keys, black_keys) as (pitch, low, high).
    """
    lo_bound, hi_bound = min_p - 0.5, max_p + 0.5
    whites: List[Tuple[int, float, float]] = []
    blacks: List[Tuple[int, float, float]] = []
    for p in range(min_p, max_p + 1):
        if is_black_key(p):
            blacks.append((p, p - 0.5, p + 0.5))
            continue
        low = (p - 1.0) if is_black_key(p - 1) else (p - 0.5)
        high = (p + 1.0) if is_black_key(p + 1) else (p + 0.5)
        whites.append((p, max(lo_bound, low), min(hi_bound, high)))
    return whites, blacks


class PianoKeyboardGutter(Gtk.DrawingArea):
    """Vertical piano keyboard gutter pinned to the left of the timeline canvas.

    In Multi-Track view it shows one header per track lane instead: click a header to select the whole
    track (Ctrl adds/removes, Shift selects a range of tracks) and drag it out to export that track.
    """

    KEYBOARD_WIDTH = 50

    def __init__(self, canvas: TimelineCanvas, player: AudioPlayer):
        super().__init__()
        self.canvas = canvas
        self.player = player
        self.on_request_drag_content: Optional[Callable[..., Any]] = None
        self.on_context_menu: Optional[Callable[[float, float], None]] = None
        self._anchor_track_key: Optional[Tuple[int, int]] = None
        self._press_track_key: Optional[Tuple[int, int]] = None

        self.set_content_width(self.KEYBOARD_WIDTH)
        self.set_content_height(320)
        self.set_hexpand(False)
        self.set_vexpand(True)
        self.set_draw_func(self._on_draw)

        click = Gtk.GestureClick.new()
        click.set_button(0)
        click.connect("pressed", self._on_pressed)
        self.add_controller(click)

        drag = Gtk.DragSource.new()
        drag.set_actions(Gdk.DragAction.COPY)
        drag.connect("prepare", self._on_drag_prepare)
        drag.connect("drag-begin", self._on_drag_begin)
        self.add_controller(drag)

        motion = Gtk.EventControllerMotion.new()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", lambda *_: self.set_cursor_from_name("default"))
        self.add_controller(motion)

    # --- Track header interaction (Multi-Track view) ---

    def _lane_key_at_y(self, y: float) -> Optional[Tuple[int, int]]:
        if not self.canvas.is_multi_track_view:
            return None
        trk = self.canvas.get_track_at_y(y, height=float(self.get_height() or 0) or None)
        return (trk.track_index, trk.channel) if trk is not None else None

    def _on_motion(self, controller, x, y):
        key = self._lane_key_at_y(y)
        self.set_cursor_from_name("grab" if key is not None else "default")
        self.set_tooltip_text(
            _("Click to select the whole track, drag to export it") if key is not None else None
        )

    def select_track_from_header(self, key: Tuple[int, int], ctrl: bool = False, shift: bool = False):
        """Apply a click on a track header to the canvas selection."""
        canvas = self.canvas
        whole = canvas.get_whole_selected_track_keys()
        order = [(t.track_index, t.channel) for t in canvas.tracks]
        if shift and self._anchor_track_key in order and key in order:
            i, j = sorted((order.index(self._anchor_track_key), order.index(key)))
            canvas.select_whole_tracks(set(order[i:j + 1]), add=ctrl, notify=True)
        elif ctrl:
            if key in whole:
                remaining = whole - {key}
                if remaining:
                    canvas.select_whole_tracks(remaining, notify=True)
                else:
                    canvas.clear_selection(notify=True)
            else:
                canvas.select_whole_tracks({key}, add=True, notify=True)
            self._anchor_track_key = key
        elif key in whole and canvas.selected_tracks == whole:
            # Already part of a whole-track selection: keep it so a drag takes every selected track
            self._anchor_track_key = key
        else:
            canvas.select_whole_tracks({key}, notify=True)
            self._anchor_track_key = key
        canvas.queue_draw()
        self.queue_draw()

    def _on_pressed(self, gesture, n_press, x, y):
        key = self._lane_key_at_y(y)
        self._press_track_key = key
        btn = gesture.get_current_button()
        if btn == 3:
            self.canvas.context_time = self.canvas.x_to_time(self.canvas.hadj.get_value())
            self.canvas.context_track_key = key
            self.canvas.context_in_selection = key is not None and key in (self.canvas.selected_tracks or set())
            if self.on_context_menu:
                self.on_context_menu(x, y)
            return
        if btn != 1 or key is None:
            return
        state = gesture.get_current_event_state()
        self.select_track_from_header(
            key,
            ctrl=bool(state & Gdk.ModifierType.CONTROL_MASK),
            shift=bool(state & Gdk.ModifierType.SHIFT_MASK),
        )

    def _on_drag_prepare(self, source, x, y):
        key = self._lane_key_at_y(y)
        if key is None or not self.on_request_drag_content:
            return None
        if key not in self.canvas.get_whole_selected_track_keys():
            self.canvas.select_whole_tracks({key}, notify=True)
        return self.on_request_drag_content(use_selection=True)

    def _on_drag_begin(self, source, drag):
        display = Gdk.Display.get_default()
        if display:
            theme = Gtk.IconTheme.get_for_display(display)
            if theme and theme.has_icon("audio-x-generic-symbolic"):
                paintable = theme.lookup_icon("audio-x-generic-symbolic", None, 32, 1, Gtk.TextDirection.NONE, Gtk.IconLookupFlags.NONE)
                if paintable:
                    source.set_icon(paintable, 16, 16)

    @property
    def is_dark(self) -> bool:
        try:
            return Adw.StyleManager.get_default().get_dark()
        except Exception:
            return True

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
        if width <= 0 or height <= 0:
            return
        # 1. Background gutter
        is_dark = self.is_dark
        if is_dark:
            cr.set_source_rgb(0.11, 0.11, 0.12)
        else:
            cr.set_source_rgb(0.94, 0.94, 0.96)
        cr.paint()

        roll_top = self.canvas.HEADER_HEIGHT
        roll_bottom = height - self.canvas.FOOTER_HEIGHT
        roll_h = max(10.0, roll_bottom - roll_top)

        # Multi-Track Gutter Mode: render instrument badges, channel badges, and real-time sounding glow
        if self.canvas.multi_track_mode and len(self.canvas.tracks) > 1:
            lanes = self.canvas.get_track_lane_geometries(roll_top, roll_bottom)
            curr_t = self.canvas.scrub_cursor_time if (self.canvas.scrub_active and self.canvas.scrub_cursor_time is not None) else self.player.current_time
            whole_keys = self.canvas.get_whole_selected_track_keys()
            partial_keys = set(self.canvas.selected_tracks or ()) if self.canvas.selection_range else set()

            for trk, lane_top, lane_bottom in lanes:
                if trk is None:
                    continue
                lane_h = max(10.0, lane_bottom - lane_top)

                is_sounding = False
                for item in self.canvas.session_items:
                    for n in item.midi_data.notes:
                        if n.track_index == trk.track_index and n.channel == trk.channel:
                            if item.timeline_offset + n.start_time <= curr_t <= item.timeline_offset + n.end_time:
                                is_sounding = True
                                break
                    if is_sounding:
                        break

                base_r, base_g, base_b = get_track_color(program=trk.program, channel=trk.channel)
                key = (trk.track_index, trk.channel)
                if key in whole_keys or key in partial_keys:
                    cr.set_source_rgba(0.2, 0.5, 0.9, 0.35 if key in whole_keys else 0.16)
                    cr.rectangle(0, lane_top, width, lane_h)
                    cr.fill()
                if is_sounding:
                    cr.set_source_rgba(base_r, base_g, base_b, 0.28)
                    cr.rectangle(0, lane_top, width, lane_h)
                    cr.fill()

                if is_dark:
                    cr.set_source_rgba(0.25, 0.25, 0.28, 0.8)
                else:
                    cr.set_source_rgba(0.80, 0.80, 0.84, 0.9)
                cr.set_line_width(1.0)
                cr.move_to(0, lane_bottom)
                cr.line_to(width, lane_bottom)
                cr.stroke()

                cr.set_source_rgba(base_r, base_g, base_b, 0.95 if is_sounding else 0.70)
                cr.rectangle(0, lane_top + 4.0, 3.5, lane_h - 8.0)
                cr.fill()

                pill_x = 7.0
                pill_y = lane_top + (lane_h / 2.0) - 10.0
                pill_w = 34.0
                pill_h = 20.0
                r = 4.0

                # Elegant rounded badge background
                cr.set_source_rgba(base_r, base_g, base_b, 0.25 if not is_sounding else 0.45)
                cr.new_sub_path()
                cr.arc(pill_x + pill_w - r, pill_y + r, r, -math.pi/2, 0)
                cr.arc(pill_x + pill_w - r, pill_y + pill_h - r, r, 0, math.pi/2)
                cr.arc(pill_x + r, pill_y + pill_h - r, r, math.pi/2, math.pi)
                cr.arc(pill_x + r, pill_y + r, r, math.pi, 3*math.pi/2)
                cr.close_path()
                cr.fill()

                # Clean GNOME typography: CH <num>
                cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
                cr.set_font_size(9.0)
                if is_dark:
                    cr.set_source_rgb(0.95, 0.95, 0.98)
                else:
                    cr.set_source_rgb(0.12, 0.12, 0.18)
                ch_label = f"CH {trk.channel + 1}"
                ext = cr.text_extents(ch_label)
                cr.move_to(pill_x + ((pill_w - ext.width) / 2.0) - ext.x_bearing, pill_y + ((pill_h - ext.height) / 2.0) - ext.y_bearing)
                cr.show_text(ch_label)

                if is_sounding:
                    cr.set_source_rgba(0.20, 0.95, 0.65, 0.95)
                    cr.arc(width - 8.0, lane_top + (lane_h / 2.0), 3.0, 0, 2 * math.pi)
                    cr.fill()

            if is_dark:
                cr.set_source_rgba(0.25, 0.25, 0.28, 0.9)
            else:
                cr.set_source_rgba(0.80, 0.80, 0.84, 0.9)
            cr.set_line_width(1.0)
            cr.move_to(width - 0.5, 0)
            cr.line_to(width - 0.5, height)
            cr.stroke()
            return

        min_p, max_p, pitch_range = self.canvas._get_global_pitch_bounds()
        lane_h = (roll_h - 10.0) / pitch_range
        active_pitches = self._get_active_pitches()

        def y_of(pu: float) -> float:
            """Screen y of a (fractional) pitch position; matches TimelineCanvas._draw_pitch_grid rows."""
            return roll_bottom - ((pu - min_p) / pitch_range) * (roll_h - 10.0) - 8.0

        whites, blacks = keyboard_key_layout(min_p, max_p)
        key_w = width - 1.0
        black_w = round(key_w * BLACK_KEY_WIDTH_RATIO)

        # 2. White keys: full width, wrapping around the black keys drawn over them
        for p, low, high in whites:
            top, bottom = y_of(high), y_of(low)
            if p in active_pitches:
                cr.set_source_rgba(0.20, 0.85, 0.75, 0.95)
            elif is_dark:
                cr.set_source_rgba(0.85, 0.85, 0.88, 1.0)
            else:
                cr.set_source_rgba(0.99, 0.99, 1.0, 1.0)
            cr.rectangle(0.0, top, key_w, bottom - top)
            cr.fill()

        # Separators between neighbouring white keys
        if is_dark:
            cr.set_source_rgba(0.38, 0.38, 0.42, 0.95)
        else:
            cr.set_source_rgba(0.72, 0.72, 0.77, 0.95)
        cr.set_line_width(1.0)
        for p, low, high in whites[:-1]:
            sep_y = round(y_of(high)) + 0.5
            cr.move_to(0.0, sep_y)
            cr.line_to(key_w, sep_y)
            cr.stroke()

        # 3. Black keys on top, about 60% of the width, with a softly rounded tip
        for p, low, high in blacks:
            top, bottom = y_of(high), y_of(low)
            kh = bottom - top
            inset = min(0.6, kh * 0.08)
            top += inset
            kh -= 2 * inset
            if kh <= 0:
                continue
            r = min(2.0, kh / 2.0)
            cr.new_sub_path()
            cr.move_to(0.0, top)
            cr.line_to(black_w - r, top)
            cr.arc(black_w - r, top + r, r, -math.pi / 2, 0)
            cr.line_to(black_w, top + kh - r)
            cr.arc(black_w - r, top + kh - r, r, 0, math.pi / 2)
            cr.line_to(0.0, top + kh)
            cr.close_path()
            if p in active_pitches:
                cr.set_source_rgba(0.15, 0.75, 0.65, 1.0)
                cr.fill()
            else:
                grad = cairo.LinearGradient(0.0, 0.0, black_w, 0.0)
                if is_dark:
                    grad.add_color_stop_rgb(0.0, 0.10, 0.10, 0.12)
                    grad.add_color_stop_rgb(1.0, 0.22, 0.22, 0.25)
                else:
                    grad.add_color_stop_rgb(0.0, 0.14, 0.14, 0.16)
                    grad.add_color_stop_rgb(1.0, 0.30, 0.30, 0.34)
                cr.set_source(grad)
                cr.fill()

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
                if is_dark:
                    cr.set_source_rgba(0.35, 0.35, 0.40, 0.9)
                else:
                    cr.set_source_rgba(0.55, 0.55, 0.60, 0.9)
                cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
                cr.set_font_size(8.0)
                cr.move_to(width - 18.0, y + 3.0)
                cr.show_text(oct_name)

        # 5. Right border dividing keyboard from roll canvas
        if is_dark:
            cr.set_source_rgba(0.25, 0.25, 0.28, 0.9)
        else:
            cr.set_source_rgba(0.80, 0.80, 0.84, 0.9)
        cr.set_line_width(1.0)
        cr.move_to(width - 0.5, 0)
        cr.line_to(width - 0.5, height)
        cr.stroke()
