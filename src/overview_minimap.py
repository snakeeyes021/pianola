# overview_minimap.py
#
# Copyright 2026 Matthew Samson
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Macro Overview Minimap for Pianola timeline navigation.

Renders a bird's-eye view of all recorded takes and note density with an
interactive translucent viewport lens replacing the default horizontal scrollbar.
"""

from typing import Optional
import cairo

from gi.repository import Gtk, Gdk, GLib
from .player import AudioPlayer


class OverviewMinimap(Gtk.DrawingArea):
    """Horizontal bird's-eye overview minimap and viewport navigation controller."""

    def __init__(self, canvas, player: AudioPlayer):
        super().__init__()
        self.canvas = canvas
        self.player = player
        self.hadj = self.canvas.hadj

        self.set_content_width(400)
        self.set_content_height(30)
        self.set_hexpand(True)
        self.set_vexpand(False)
        self.set_draw_func(self._on_draw)

        # Connect adjustments
        self.hadj.connect("value-changed", lambda *_: self.queue_draw())
        self.hadj.connect("changed", lambda *_: self.queue_draw())

        # Gesture: Click to jump viewport
        click = Gtk.GestureClick.new()
        click.connect("pressed", self._on_click_pressed)
        self.add_controller(click)

        # Gesture: Drag to pan viewport lens
        drag = Gtk.GestureDrag.new()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        self.add_controller(drag)

        # Gesture: Scroll wheel to pan viewport
        scroll = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.BOTH_AXES
        )
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

        self._drag_start_hadj_val = 0.0

    def _on_click_pressed(self, gesture, n_press, x, y):
        width = self.get_width()
        if width <= 0:
            return
        upper = max(1.0, self.hadj.get_upper())
        page_size = self.hadj.get_page_size()

        # Center viewport around clicked position
        target_canvas_x = (x / width) * upper - (page_size / 2.0)
        max_val = max(0.0, upper - page_size)
        self.hadj.set_value(max(0.0, min(max_val, target_canvas_x)))
        self.canvas.queue_draw()
        self.queue_draw()

    def _on_drag_begin(self, gesture, start_x, start_y):
        self._drag_start_hadj_val = self.hadj.get_value()

    def _on_drag_update(self, gesture, offset_x, offset_y):
        width = self.get_width()
        if width <= 0:
            return
        upper = max(1.0, self.hadj.get_upper())
        page_size = self.hadj.get_page_size()

        delta_canvas = (offset_x / width) * upper
        new_val = self._drag_start_hadj_val + delta_canvas
        max_val = max(0.0, upper - page_size)
        self.hadj.set_value(max(0.0, min(max_val, new_val)))
        self.canvas.queue_draw()
        self.queue_draw()

    def _on_scroll(self, controller, dx, dy):
        step = (dx if abs(dx) > abs(dy) else dy) * 45.0
        max_val = max(0.0, self.hadj.get_upper() - self.hadj.get_page_size())
        val = max(0.0, min(max_val, self.hadj.get_value() + step))
        self.hadj.set_value(val)
        self.canvas.queue_draw()
        self.queue_draw()
        return True

    def _on_draw(self, drawing_area, cr: cairo.Context, width: int, height: int):
        if width <= 0 or height <= 0:
            return

        # 1. Background tray
        cr.set_source_rgb(0.09, 0.09, 0.10)
        cr.paint()

        upper = max(1.0, self.hadj.get_upper())
        if not self.canvas.session_items or upper <= 1.0:
            return

        scale = width / upper

        # 2. Draw Session Take blocks & mini notes
        for item in self.canvas.session_items:
            start_cx = self.canvas.time_to_x(item.timeline_offset)
            end_cx = self.canvas.time_to_x(item.end_timeline_offset)

            sx = start_cx * scale
            ex = end_cx * scale
            sw = max(1.5, ex - sx)

            # Session background block
            cr.set_source_rgba(0.14, 0.14, 0.16, 0.6)
            cr.rectangle(sx, 1.0, sw, height - 2.0)
            cr.fill()

            # Take boundary separator line
            cr.set_source_rgba(0.32, 0.32, 0.36, 0.6)
            cr.set_line_width(1.0)
            cr.move_to(sx, 1.0)
            cr.line_to(sx, height - 1.0)
            cr.stroke()

            # Mini notes density
            notes = item.midi_data.notes
            if notes:
                cr.set_source_rgba(0.20, 0.78, 0.72, 0.65)
                for n in notes:
                    nx = self.canvas.time_to_x(item.timeline_offset + n.start_time) * scale
                    n_end_x = self.canvas.time_to_x(item.timeline_offset + n.end_time) * scale
                    nw = max(1.2, n_end_x - nx)
                    norm_p = max(0.0, min(1.0, (n.pitch - 36) / 52.0))
                    ny = height - 5.0 - (norm_p * (height - 10.0))
                    cr.rectangle(nx, ny, nw, 1.5)
                cr.fill()

            # Starred pip
            if item.session.starred:
                cr.set_source_rgba(0.95, 0.80, 0.20, 0.9)
                cr.arc(sx + 5.0, 6.0, 2.5, 0, 2 * 3.14159)
                cr.fill()

        # 3. Translucent Viewport Lens (active scroll view)
        lens_x = self.hadj.get_value() * scale
        lens_w = max(16.0, self.hadj.get_page_size() * scale)

        # Lens fill
        cr.set_source_rgba(0.35, 0.55, 0.85, 0.25)
        cr.rectangle(lens_x, 1.0, lens_w, height - 2.0)
        cr.fill()

        # Lens border
        cr.set_source_rgba(0.45, 0.75, 1.0, 0.85)
        cr.set_line_width(1.2)
        cr.rectangle(lens_x, 1.0, lens_w, height - 2.0)
        cr.stroke()

        # Grip handles on left and right borders of lens
        cr.set_source_rgba(0.75, 0.88, 1.0, 0.9)
        cr.set_line_width(1.5)
        cr.move_to(lens_x + 3.0, 7.0)
        cr.line_to(lens_x + 3.0, height - 7.0)
        cr.move_to(lens_x + lens_w - 3.0, 7.0)
        cr.line_to(lens_x + lens_w - 3.0, height - 7.0)
        cr.stroke()

        # 4. Playhead cursor
        play_cx = self.canvas.time_to_x(self.player.current_time)
        play_x = play_cx * scale
        if 0.0 <= play_x <= width:
            cr.set_source_rgba(0.95, 0.25, 0.25, 0.95)
            cr.set_line_width(1.5)
            cr.move_to(play_x, 0.0)
            cr.line_to(play_x, height)
            cr.stroke()

        # 5. Outer border tray
        cr.set_source_rgba(0.20, 0.20, 0.23, 0.9)
        cr.set_line_width(1.0)
        cr.rectangle(0.5, 0.5, width - 1.0, height - 1.0)
        cr.stroke()
