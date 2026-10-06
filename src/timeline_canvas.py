# timeline_canvas.py
#
# Copyright 2026 Matthew Samson
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Continuous horizontal timeline canvas for Pianola.

Renders:
- Multi-session continuous piano roll / note density across file boundaries.
- Session headers, clapper markers (0xFF 0x06), and >= 3.0s silence section boundaries.
- Interactive playhead cursor and acoustic scrubbing hover sustain (Ctrl+Space).
- Marquee time range selection with Shift/Ctrl modifiers.
- Smooth horizontal zoom and native scrolling.
"""

import math
from datetime import datetime
from typing import List, Optional, Tuple, Callable
import cairo
from gi.repository import Gtk, Gdk, GLib, Pango, PangoCairo

from .archive import NoteEvent, MarkerEvent, Section, SessionRecord, MidiData
from .player import AudioPlayer


class TimelineSessionItem:
    """Represents a session mapped onto the continuous timeline."""

    def __init__(self, session: SessionRecord, timeline_offset: float):
        self.session = session
        self.timeline_offset = timeline_offset  # Start seconds on the continuous timeline
        self.midi_data: MidiData = session.get_midi_data()
        self.duration: float = max(self.session.duration_seconds, self.midi_data.duration, 0.5)

    @property
    def end_timeline_offset(self) -> float:
        return self.timeline_offset + self.duration


class TimelineCanvas(Gtk.DrawingArea):
    """Interactive custom canvas rendering the continuous MIDI timeline."""

    INTER_SESSION_GAP = 3.0  # seconds gap between takes on the continuous timeline
    HEADER_HEIGHT = 44.0     # Pixels for session name, date, and clappers
    FOOTER_HEIGHT = 20.0     # Pixels for time ruler
    DEFAULT_PX_PER_SEC = 50.0
    MIN_PX_PER_SEC = 8.0
    MAX_PX_PER_SEC = 500.0

    def __init__(self, player: AudioPlayer):
        super().__init__()
        self.player = player
        self.session_items: List[TimelineSessionItem] = []
        self.total_timeline_duration: float = 0.0
        self.px_per_sec: float = self.DEFAULT_PX_PER_SEC

        # Selection state: (start_sec, end_sec) on continuous timeline or None
        self.selection_range: Optional[Tuple[float, float]] = None
        self._drag_start_time: Optional[float] = None

        # Acoustic scrub state
        self.ctrl_held: bool = False
        self.scrub_active: bool = False
        self.scrub_cursor_time: Optional[float] = None

        # Star toggle callback: on_star_toggled(session_record, new_state)
        self.on_star_toggled: Optional[Callable[[SessionRecord, bool], None]] = None
        self.on_selection_changed: Optional[Callable[[Optional[Tuple[float, float]]], None]] = None

        # Configuration
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_focusable(True)
        self.set_draw_func(self._on_draw)

        # Event controllers
        self._setup_event_controllers()

        # Connect player callbacks
        self.player.on_tick = self._on_player_tick

    def _setup_event_controllers(self):
        # Motion controller for acoustic scrub hover
        motion = Gtk.EventControllerMotion.new()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_motion_leave)
        self.add_controller(motion)

        # Drag gesture for marquee selection
        drag = Gtk.GestureDrag.new()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)

        # Click gesture for cursor positioning / markers
        click = Gtk.GestureClick.new()
        click.connect("pressed", self._on_click_pressed)
        self.add_controller(click)

        # Key controller for Ctrl+Space scrubbing and modifier tracking
        keys = Gtk.EventControllerKey.new()
        keys.connect("key-pressed", self._on_key_pressed)
        keys.connect("key-released", self._on_key_released)
        self.add_controller(keys)

        # Scroll controller for Ctrl + MouseWheel horizontal zoom
        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

    def load_sessions(self, sessions: List[SessionRecord]):
        """Build continuous timeline spanning all sessions."""
        self.session_items.clear()
        curr_offset = 0.0

        all_notes: List[NoteEvent] = []
        all_sections: List[Section] = []

        for s in sessions:
            item = TimelineSessionItem(s, curr_offset)
            self.session_items.append(item)

            # Map notes into unified continuous timeline coordinates
            for n in item.midi_data.notes:
                all_notes.append(NoteEvent(
                    pitch=n.pitch,
                    velocity=n.velocity,
                    start_time=n.start_time + curr_offset,
                    end_time=n.end_time + curr_offset,
                    channel=n.channel
                ))

            # Map sections into unified timeline
            for sec in item.midi_data.sections:
                all_sections.append(Section(
                    start_time=sec.start_time + curr_offset,
                    end_time=sec.end_time + curr_offset
                ))

            curr_offset += item.duration + self.INTER_SESSION_GAP

        self.total_timeline_duration = max(0.5, curr_offset - self.INTER_SESSION_GAP if self.session_items else 0.0)

        # Feed unified timeline notes and sections to audio player
        unified_midi = MidiData(
            duration=self.total_timeline_duration,
            notes=all_notes,
            sections=all_sections
        )
        self.player.load_midi_data(unified_midi)

        self._update_dimensions()
        self.queue_draw()

    def _update_dimensions(self):
        width = int(math.ceil(self.total_timeline_duration * self.px_per_sec)) + 200
        self.set_content_width(max(800, width))
        self.set_content_height(320)

    def set_zoom(self, px_per_sec: float):
        """Set horizontal zoom factor in pixels per second."""
        self.px_per_sec = max(self.MIN_PX_PER_SEC, min(self.MAX_PX_PER_SEC, px_per_sec))
        self._update_dimensions()
        self.queue_draw()

    def zoom_in(self):
        self.set_zoom(self.px_per_sec * 1.3)

    def zoom_out(self):
        self.set_zoom(self.px_per_sec / 1.3)

    # --- Coordinate conversions ---

    def time_to_x(self, t: float) -> float:
        return t * self.px_per_sec

    def x_to_time(self, x: float) -> float:
        t = max(0.0, x / max(1.0, self.px_per_sec))
        if self.total_timeline_duration > 0:
            return min(t, self.total_timeline_duration)
        return t

    # --- Event Handlers ---

    def _on_player_tick(self, current_time: float):
        GLib.idle_add(self.queue_draw)

    def _on_key_pressed(self, controller, keyval, keycode, state):
        if keyval in (Gdk.KEY_Control_L, Gdk.KEY_Control_R):
            self.ctrl_held = True
        return False

    def _on_key_released(self, controller, keyval, keycode, state):
        if keyval in (Gdk.KEY_Control_L, Gdk.KEY_Control_R):
            self.ctrl_held = False
            if self.scrub_active:
                self.scrub_active = False
                self.scrub_cursor_time = None
                self.player.end_scrub()
                self.queue_draw()
        return False

    def _on_motion(self, controller, x, y):
        # If Ctrl held or scrub_active: acoustic scrub at x
        if self.ctrl_held or self.scrub_active:
            t = self.x_to_time(x)
            self.scrub_active = True
            self.scrub_cursor_time = t
            self.player.audit_at(t)
            self.queue_draw()

    def _on_motion_leave(self, controller):
        if self.scrub_active:
            self.scrub_active = False
            self.scrub_cursor_time = None
            self.player.end_scrub()
            self.queue_draw()

    def _on_click_pressed(self, gesture, n_press, x, y):
        self.grab_focus()
        t = self.x_to_time(x)

        # Check if click was on header area (for star or marker chips)
        if y <= self.HEADER_HEIGHT:
            for item in self.session_items:
                item_x = self.time_to_x(item.timeline_offset)
                # Check star button click: 18px box near item_x + 6
                if item_x + 4 <= x <= item_x + 28 and 8 <= y <= 32:
                    new_star = not item.session.starred
                    item.session.starred = new_star
                    if self.on_star_toggled:
                        self.on_star_toggled(item.session, new_star)
                    self.queue_draw()
                    return

                # Check marker chip clicks
                for m in item.midi_data.markers:
                    mx = self.time_to_x(item.timeline_offset + m.time)
                    if abs(x - mx) <= 12:
                        self.player.seek(item.timeline_offset + m.time)
                        self.queue_draw()
                        return

        # Regular timeline click seeks playhead
        state = gesture.get_current_event_state()
        if not (state & Gdk.ModifierType.SHIFT_MASK):
            # Clear marquee selection unless Shift is held
            self.selection_range = None
            if self.on_selection_changed:
                self.on_selection_changed(None)

        self.player.seek(t)
        self.queue_draw()

    def _on_drag_begin(self, gesture, start_x, start_y):
        self._drag_start_time = self.x_to_time(start_x)

    def _on_drag_update(self, gesture, offset_x, offset_y):
        if self._drag_start_time is None:
            return
        success, start_x, _ = gesture.get_start_point()
        if not success:
            return
        curr_x = start_x + offset_x
        curr_t = self.x_to_time(curr_x)

        t1 = min(self._drag_start_time, curr_t)
        t2 = max(self._drag_start_time, curr_t)

        if abs(t2 - t1) > 0.05:
            self.selection_range = (t1, t2)
            if self.on_selection_changed:
                self.on_selection_changed(self.selection_range)
            self.queue_draw()

    def _on_drag_end(self, gesture, offset_x, offset_y):
        self._drag_start_time = None

    def _on_scroll(self, controller, dx, dy):
        state = controller.get_current_event_state()
        if state & Gdk.ModifierType.CONTROL_MASK:
            # Zoom horizontally
            if dy < 0:
                self.zoom_in()
            elif dy > 0:
                self.zoom_out()
            return True
        return False

    # --- Hierarchical Jump Functions ---

    def jump_to_archive_start(self):
        self.player.seek(0.0)
        self.queue_draw()

    def jump_to_latest_session(self):
        """Default start position: beginning of the last/live session."""
        if not self.session_items:
            self.player.seek(0.0)
            return

        last_item = self.session_items[-1]
        self.player.seek(last_item.timeline_offset)
        self.queue_draw()

    def jump_to_prev_file(self):
        curr_t = self.player.current_time
        # Find preceding session
        for item in reversed(self.session_items):
            if item.timeline_offset < curr_t - 0.2:
                self.player.seek(item.timeline_offset)
                self.queue_draw()
                return
        self.player.seek(0.0)
        self.queue_draw()

    def jump_to_next_file(self):
        curr_t = self.player.current_time
        for item in self.session_items:
            if item.timeline_offset > curr_t + 0.1:
                self.player.seek(item.timeline_offset)
                self.queue_draw()
                return
        self.player.seek(self.total_timeline_duration)
        self.queue_draw()

    def jump_to_day(self, target_date: datetime):
        """Jump to the first session recorded on target_date."""
        for item in self.session_items:
            st = item.session.start_time
            if st.year == target_date.year and st.month == target_date.month and st.day == target_date.day:
                self.player.seek(item.timeline_offset)
                self.queue_draw()
                return

    # --- Cairo Drawing Function ---

    def _on_draw(self, drawing_area, cr: cairo.Context, width: int, height: int):
        # Background
        cr.set_source_rgb(0.12, 0.12, 0.13)
        cr.paint()

        roll_top = self.HEADER_HEIGHT
        roll_bottom = height - self.FOOTER_HEIGHT
        roll_h = max(10.0, roll_bottom - roll_top)

        # Draw session columns and note events
        for item in self.session_items:
            item_x = self.time_to_x(item.timeline_offset)
            item_w = self.time_to_x(item.duration)

            # Session background tint
            if item.session.is_live:
                cr.set_source_rgba(0.25, 0.12, 0.12, 0.4)
            else:
                cr.set_source_rgba(0.16, 0.16, 0.18, 0.5)
            cr.rectangle(item_x, roll_top, item_w, roll_h)
            cr.fill()

            # Session boundary line
            cr.set_source_rgba(0.3, 0.3, 0.35, 0.8)
            cr.set_line_width(1.0)
            cr.move_to(item_x, 0)
            cr.line_to(item_x, height)
            cr.stroke()

            # Session Header (Badge)
            self._draw_session_header(cr, item, item_x, item_w)

            # Section Dividers (>= 3.0s silence)
            for sec in item.midi_data.sections:
                sec_x = self.time_to_x(item.timeline_offset + sec.start_time)
                cr.set_source_rgba(0.4, 0.5, 0.6, 0.3)
                cr.set_dash([3.0, 3.0])
                cr.move_to(sec_x, roll_top)
                cr.line_to(sec_x, roll_bottom)
                cr.stroke()
            cr.set_dash([])  # Reset dash

            # Draw Notes in Piano Roll
            notes = item.midi_data.notes
            if notes:
                min_p = min(n.pitch for n in notes)
                max_p = max(n.pitch for n in notes)
                pitch_range = max(12, max_p - min_p + 1)

                for n in notes:
                    nx = self.time_to_x(item.timeline_offset + n.start_time)
                    nw = max(3.0, self.time_to_x(n.duration))
                    # Invert Y: higher pitch -> higher position
                    norm_p = (n.pitch - min_p) / pitch_range
                    ny = roll_bottom - (norm_p * (roll_h - 10.0)) - 8.0

                    # Color by velocity
                    vel_ratio = max(0.2, min(1.0, n.velocity / 127.0))
                    # Vibrant warm teal / cyan
                    cr.set_source_rgba(0.18 * vel_ratio, 0.65 * vel_ratio, 0.95 * vel_ratio, 0.85)

                    # Draw rounded note rectangle
                    cr.rectangle(nx, ny, nw, 5.0)
                    cr.fill()

            # Clapper Marker Chips
            for m in item.midi_data.markers:
                mx = self.time_to_x(item.timeline_offset + m.time)
                self._draw_marker_chip(cr, m.text, mx, roll_top - 14.0)

        # Draw Marquee Selection Overlay
        if self.selection_range:
            s_start, s_end = self.selection_range
            sel_x = self.time_to_x(s_start)
            sel_w = max(2.0, self.time_to_x(s_end - s_start))

            # Translucent selection fill
            cr.set_source_rgba(0.2, 0.5, 0.9, 0.25)
            cr.rectangle(sel_x, roll_top, sel_w, roll_h)
            cr.fill()

            # Selection border handles
            cr.set_source_rgba(0.35, 0.65, 1.0, 0.9)
            cr.set_line_width(1.5)
            cr.rectangle(sel_x, roll_top, sel_w, roll_h)
            cr.stroke()

        # Draw Time Ruler / Footer
        self._draw_footer_ruler(cr, width, height)

        # Draw Acoustic Scrub Line (if actively scrubbing)
        if self.scrub_active and self.scrub_cursor_time is not None:
            scrub_x = self.time_to_x(self.scrub_cursor_time)
            # Glowing amber scrub line
            cr.set_source_rgba(1.0, 0.7, 0.1, 0.9)
            cr.set_line_width(2.0)
            cr.move_to(scrub_x, 0)
            cr.line_to(scrub_x, height)
            cr.stroke()

        # Draw Playhead Cursor
        play_x = self.time_to_x(self.player.current_time)
        cr.set_source_rgba(0.9, 0.25, 0.25, 0.95)  # Vibrant playhead red
        cr.set_line_width(2.0)
        cr.move_to(play_x, roll_top - 6.0)
        cr.line_to(play_x, height)
        cr.stroke()

        # Playhead triangle head
        cr.move_to(play_x - 5.0, roll_top - 6.0)
        cr.line_to(play_x + 5.0, roll_top - 6.0)
        cr.line_to(play_x, roll_top)
        cr.close_path()
        cr.fill()

    def _draw_session_header(self, cr: cairo.Context, item: TimelineSessionItem, x: float, w: float):
        # Star icon
        star_char = "★" if item.session.starred else "☆"
        cr.set_source_rgb(0.95, 0.75, 0.15) if item.session.starred else cr.set_source_rgb(0.5, 0.5, 0.5)
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(14.0)
        cr.move_to(x + 6.0, 22.0)
        cr.show_text(star_char)

        # Date & Device text
        cr.set_source_rgb(0.85, 0.85, 0.88)
        cr.set_font_size(11.0)
        date_str = item.session.start_time.strftime("%b %d, %H:%M")
        badge = f"{date_str} • {item.session.device_name}"
        if item.session.is_live:
            badge = f"🔴 LIVE • {badge}"
        cr.move_to(x + 24.0, 21.0)
        cr.show_text(badge)

        # Duration & Notes count
        cr.set_source_rgb(0.6, 0.6, 0.65)
        cr.set_font_size(10.0)
        stats = f"{int(item.duration)}s • {item.session.note_count} notes"
        cr.move_to(x + 24.0, 35.0)
        cr.show_text(stats)

    def _draw_marker_chip(self, cr: cairo.Context, text: str, x: float, y: float):
        # Chip background pill
        cr.set_source_rgba(0.2, 0.45, 0.3, 0.9)
        chip_w = min(80.0, max(24.0, len(text) * 6.5 + 8.0))
        cr.rectangle(x - 4.0, y - 10.0, chip_w, 15.0)
        cr.fill()

        # Marker line
        cr.set_source_rgba(0.3, 0.8, 0.4, 0.9)
        cr.set_line_width(1.0)
        cr.move_to(x, y + 5.0)
        cr.line_to(x, self.HEADER_HEIGHT + 40.0)
        cr.stroke()

        # Text label
        cr.set_source_rgb(1.0, 1.0, 1.0)
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(9.0)
        cr.move_to(x, y + 2.0)
        label = text if len(text) <= 12 else text[:10] + "…"
        cr.show_text(label)

    def _draw_footer_ruler(self, cr: cairo.Context, width: int, height: int):
        ruler_y = height - self.FOOTER_HEIGHT
        cr.set_source_rgb(0.18, 0.18, 0.2)
        cr.rectangle(0, ruler_y, width, self.FOOTER_HEIGHT)
        cr.fill()

        # Tick marks every N seconds depending on zoom
        step_sec = 5.0 if self.px_per_sec >= 30.0 else (10.0 if self.px_per_sec >= 15.0 else 30.0)
        num_steps = int(self.total_timeline_duration / step_sec) + 1

        cr.set_source_rgb(0.5, 0.5, 0.55)
        cr.set_font_size(9.0)
        cr.set_line_width(1.0)

        for i in range(num_steps):
            t = i * step_sec
            tx = self.time_to_x(t)
            cr.move_to(tx, ruler_y)
            cr.line_to(tx, ruler_y + 5.0)
            cr.stroke()

            # Time text
            mins = int(t // 60)
            secs = int(t % 60)
            cr.move_to(tx + 2.0, ruler_y + 13.0)
            cr.show_text(f"{mins}:{secs:02d}")
