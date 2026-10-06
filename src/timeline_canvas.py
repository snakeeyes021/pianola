# timeline_canvas.py
#
# Copyright 2026 Matthew Samson
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Continuous horizontal timeline canvas for Pianola.

Renders:
- Multi-session continuous piano roll with Silence Compacting (gaps >= 3s collapsed to 2s).
- Distinct visualization of finger-held note durations vs damper pedal (CC 64) sustain tails.
- Session headers, clapper markers (0xFF 0x06), and collapsed pause indicators (// [pause]).
- Dorico-style acoustic scrubbing: Hold Ctrl + Space and glide mouse over notes.
- Marquee time range selection with Shift/Ctrl modifiers.
- Smooth horizontal zoom and native scrolling.
"""

import math
from datetime import datetime
from typing import List, Optional, Tuple, Callable
import cairo
from gi.repository import Gtk, Gdk, GLib

from .archive import NoteEvent, MarkerEvent, Section, SessionRecord, MidiData
from .player import AudioPlayer


class CollapsedGap:
    """Represents a silence gap >= 3s visually collapsed to a compact 2s fold."""

    def __init__(self, real_start: float, real_end: float, visual_duration: float = 2.0):
        self.real_start = real_start
        self.real_end = real_end
        self.real_duration = max(0.001, real_end - real_start)
        self.visual_duration = min(self.real_duration, visual_duration)
        self.saved = self.real_duration - self.visual_duration
        self.vis_start: float = 0.0
        self.vis_end: float = 0.0


class TimelineSessionItem:
    """Represents a session mapped onto the continuous timeline."""

    def __init__(self, session: SessionRecord, timeline_offset: float):
        self.session = session
        self.timeline_offset = timeline_offset
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
        self.collapsed_gaps: List[CollapsedGap] = []
        self.total_timeline_duration: float = 0.0
        self.total_visual_duration: float = 0.0
        self.px_per_sec: float = self.DEFAULT_PX_PER_SEC

        # Selection state: (start_sec, end_sec) on real timeline
        self.selection_range: Optional[Tuple[float, float]] = None
        self._drag_start_time: Optional[float] = None

        # Dorico scrub state: Hold Ctrl + Space while moving mouse
        self.ctrl_held: bool = False
        self.space_held: bool = False
        self.hover_mouse_time: float = 0.0
        self.scrub_active: bool = False
        self.scrub_cursor_time: Optional[float] = None

        # Callbacks
        self.on_star_toggled: Optional[Callable[[SessionRecord, bool], None]] = None
        self.on_selection_changed: Optional[Callable[[Optional[Tuple[float, float]]], None]] = None

        # Configuration
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_focusable(True)
        self.set_draw_func(self._on_draw)

        self._setup_event_controllers()
        self.player.on_tick = self._on_player_tick

    def _setup_event_controllers(self):
        # Motion controller
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

        # Click gesture for cursor positioning / markers / stars
        click = Gtk.GestureClick.new()
        click.connect("pressed", self._on_click_pressed)
        self.add_controller(click)

        # Key controller for Dorico Ctrl+Space scrubbing
        keys = Gtk.EventControllerKey.new()
        keys.connect("key-pressed", self._on_key_pressed)
        keys.connect("key-released", self._on_key_released)
        self.add_controller(keys)

        # Scroll controller for Ctrl + MouseWheel horizontal zoom
        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

    def load_sessions(self, sessions: List[SessionRecord]):
        """Build continuous timeline with Silence Compacting spanning all sessions."""
        self.session_items.clear()
        curr_offset = 0.0

        all_notes: List[NoteEvent] = []
        all_sections: List[Section] = []

        for s in sessions:
            item = TimelineSessionItem(s, curr_offset)
            self.session_items.append(item)

            for n in item.midi_data.notes:
                key_end = (n.played_end_time + curr_offset) if n.key_end_time is not None else None
                all_notes.append(NoteEvent(
                    pitch=n.pitch,
                    velocity=n.velocity,
                    start_time=n.start_time + curr_offset,
                    end_time=n.end_time + curr_offset,
                    channel=n.channel,
                    key_end_time=key_end
                ))

            for sec in item.midi_data.sections:
                all_sections.append(Section(
                    start_time=sec.start_time + curr_offset,
                    end_time=sec.end_time + curr_offset
                ))

            curr_offset += item.duration + self.INTER_SESSION_GAP

        self.total_timeline_duration = max(0.5, curr_offset - self.INTER_SESSION_GAP if self.session_items else 0.0)

        # Build Collapsed Gaps for all pauses >= 3.0 seconds
        self._build_collapsed_gaps(all_sections)

        unified_midi = MidiData(
            duration=self.total_timeline_duration,
            notes=all_notes,
            sections=all_sections
        )
        self.player.load_midi_data(unified_midi)

        self._update_dimensions()
        self.queue_draw()

    def _build_collapsed_gaps(self, sections: List[Section]):
        """Identify all silences >= 3.0s and configure them as compact 2s visual folds."""
        self.collapsed_gaps.clear()
        if not sections:
            return

        sorted_secs = sorted(sections, key=lambda s: s.start_time)
        raw_gaps = []

        # Leading silence
        if sorted_secs[0].start_time >= 3.0:
            raw_gaps.append((0.0, sorted_secs[0].start_time))

        # Gaps between sections
        for i in range(len(sorted_secs) - 1):
            s_end = sorted_secs[i].end_time
            next_start = sorted_secs[i+1].start_time
            if next_start - s_end >= 3.0:
                raw_gaps.append((s_end, next_start))

        # Trailing silence
        if self.total_timeline_duration - sorted_secs[-1].end_time >= 3.0:
            raw_gaps.append((sorted_secs[-1].end_time, self.total_timeline_duration))

        # Build collapsed gaps with running offsets
        cum_saved = 0.0
        for g_start, g_end in raw_gaps:
            gap = CollapsedGap(g_start, g_end, visual_duration=2.0)
            gap.vis_start = g_start - cum_saved
            gap.vis_end = gap.vis_start + gap.visual_duration
            cum_saved += gap.saved
            self.collapsed_gaps.append(gap)

        self.total_visual_duration = self.time_to_visual(self.total_timeline_duration)

    # --- Silence Compacting Coordinate Conversions ---

    def time_to_visual(self, t: float) -> float:
        """Map real time in seconds to visually collapsed time."""
        if not self.collapsed_gaps:
            return t
        cum_saved = 0.0
        for g in self.collapsed_gaps:
            if t < g.real_start:
                return t - cum_saved
            elif t <= g.real_end:
                ratio = (t - g.real_start) / g.real_duration
                return g.vis_start + (ratio * g.visual_duration)
            else:
                cum_saved += g.saved
        return t - cum_saved

    def visual_to_time(self, v: float) -> float:
        """Inverse mapping: convert visually collapsed time back to exact real time."""
        if not self.collapsed_gaps:
            return v
        cum_saved = 0.0
        for g in self.collapsed_gaps:
            if v < g.vis_start:
                return v + cum_saved
            elif v <= g.vis_end:
                ratio = (v - g.vis_start) / max(0.001, g.visual_duration)
                return g.real_start + (ratio * g.real_duration)
            else:
                cum_saved += g.saved
        return v + cum_saved

    def time_to_x(self, t: float) -> float:
        return self.time_to_visual(t) * self.px_per_sec

    def x_to_time(self, x: float) -> float:
        v = max(0.0, x / max(1.0, self.px_per_sec))
        t = self.visual_to_time(v)
        if self.total_timeline_duration > 0:
            return min(t, self.total_timeline_duration)
        return t

    def _update_dimensions(self):
        vis_dur = self.total_visual_duration if self.total_visual_duration > 0 else self.total_timeline_duration
        width = int(math.ceil(vis_dur * self.px_per_sec)) + 120
        self.set_content_width(max(800, width))
        self.set_content_height(320)

    def set_zoom(self, px_per_sec: float):
        self.px_per_sec = max(self.MIN_PX_PER_SEC, min(self.MAX_PX_PER_SEC, px_per_sec))
        self._update_dimensions()
        self.queue_draw()

    def zoom_in(self):
        self.set_zoom(self.px_per_sec * 1.3)

    def zoom_out(self):
        self.set_zoom(self.px_per_sec / 1.3)

    # --- Event Handlers ---

    def _on_player_tick(self, current_time: float):
        GLib.idle_add(self.queue_draw)

    def _on_key_pressed(self, controller, keyval, keycode, state):
        if keyval in (Gdk.KEY_Control_L, Gdk.KEY_Control_R):
            self.ctrl_held = True
            return False

        if keyval == Gdk.KEY_space:
            self.space_held = True
            # Dorico model: Space while Ctrl is down starts scrubbed audition
            is_ctrl = self.ctrl_held or bool(state & Gdk.ModifierType.CONTROL_MASK)
            if is_ctrl:
                self.ctrl_held = True
                self.scrub_active = True
                self.scrub_cursor_time = self.hover_mouse_time
                self.player.start_scrub()
                self.player.audit_at(self.hover_mouse_time)
                self.queue_draw()
                return True  # Consume event to prevent regular play/pause toggle

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

        if keyval == Gdk.KEY_space:
            self.space_held = False
            if self.scrub_active:
                # Releasing Space stops Dorico audition
                self.scrub_active = False
                self.scrub_cursor_time = None
                self.player.end_scrub()
                self.queue_draw()
                return True

        return False

    def _on_motion(self, controller, x, y):
        t = self.x_to_time(x)
        self.hover_mouse_time = t
        if self.scrub_active:
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

        # Header area: check stars and marker chips
        if y <= self.HEADER_HEIGHT:
            for item in self.session_items:
                item_x = self.time_to_x(item.timeline_offset)
                if item_x + 4 <= x <= item_x + 28 and 8 <= y <= 32:
                    new_star = not item.session.starred
                    item.session.starred = new_star
                    if self.on_star_toggled:
                        self.on_star_toggled(item.session, new_star)
                    self.queue_draw()
                    return

                for m in item.midi_data.markers:
                    mx = self.time_to_x(item.timeline_offset + m.time)
                    if abs(x - mx) <= 12:
                        self.player.seek(item.timeline_offset + m.time)
                        self.queue_draw()
                        return

        state = gesture.get_current_event_state()
        if not (state & Gdk.ModifierType.SHIFT_MASK):
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
            if dy < 0:
                self.zoom_in()
            elif dy > 0:
                self.zoom_out()
            return True
        elif abs(dy) > 0 and not (state & Gdk.ModifierType.SHIFT_MASK):
            # Allow regular vertical mouse wheel to scroll horizontally across timeline
            scrolled = self.get_ancestor(Gtk.ScrolledWindow)
            if scrolled:
                hadj = scrolled.get_hadjustment()
                step = dy * 45.0
                new_val = max(0.0, min(hadj.get_upper() - hadj.get_page_size(), hadj.get_value() + step))
                hadj.set_value(new_val)
                self.queue_draw()
                return True
        return False

    # --- Hierarchical Jump Functions ---

    def jump_to_archive_start(self):
        self.player.seek(0.0)
        self.queue_draw()

    def jump_to_latest_session(self):
        if not self.session_items:
            self.player.seek(0.0)
            return
        last_item = self.session_items[-1]
        self.player.seek(last_item.timeline_offset)
        self.queue_draw()

    def jump_to_prev_file(self):
        curr_t = self.player.current_time
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
        for item in self.session_items:
            st = item.session.start_time
            if st.year == target_date.year and st.month == target_date.month and st.day == target_date.day:
                self.player.seek(item.timeline_offset)
                self.queue_draw()
                return

    # --- Cairo Drawing ---

    def _get_global_pitch_bounds(self) -> Tuple[int, int, int]:
        all_notes = [n for item in self.session_items for n in item.midi_data.notes]
        if all_notes:
            min_p = min(min(n.pitch for n in all_notes) - 2, 57)
            max_p = max(max(n.pitch for n in all_notes) + 2, 63)
        else:
            min_p = 48
            max_p = 72
        pitch_range = max(12, max_p - min_p + 1)
        return min_p, max_p, pitch_range

    def _draw_pitch_grid(self, cr: cairo.Context, width: int, roll_top: float, roll_bottom: float, roll_h: float):
        min_p, max_p, pitch_range = self._get_global_pitch_bounds()
        lane_h = (roll_h - 10.0) / pitch_range

        for p in range(min_p, max_p + 1):
            norm_p = (p - min_p) / pitch_range
            y = roll_bottom - (norm_p * (roll_h - 10.0)) - 8.0

            is_black = (p % 12) in (1, 3, 6, 8, 10)
            if is_black:
                cr.set_source_rgba(0.08, 0.08, 0.09, 0.6)
                cr.rectangle(0, y - (lane_h / 2.0), width, lane_h)
                cr.fill()

            if p == 60:
                # Middle C (C4 = 60) highlight guide line
                cr.set_source_rgba(0.25, 0.65, 0.95, 0.5)
                cr.set_line_width(1.2)
                cr.set_dash([4.0, 4.0])
                cr.move_to(0, y)
                cr.line_to(width, y)
                cr.stroke()
                cr.set_dash([])

                # Badge label
                cr.set_source_rgba(0.15, 0.45, 0.75, 0.85)
                cr.rectangle(4.0, y - 7.0, 84.0, 14.0)
                cr.fill()
                cr.set_source_rgb(1.0, 1.0, 1.0)
                cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
                cr.set_font_size(9.0)
                cr.move_to(8.0, y + 3.0)
                cr.show_text("Middle C (C4)")
            elif p % 12 == 0:
                # Other C octaves
                cr.set_source_rgba(0.4, 0.4, 0.45, 0.25)
                cr.set_line_width(0.8)
                cr.set_dash([2.0, 4.0])
                cr.move_to(0, y)
                cr.line_to(width, y)
                cr.stroke()
                cr.set_dash([])

                octave_name = f"C{(p // 12) - 1}"
                cr.set_source_rgba(0.6, 0.6, 0.65, 0.7)
                cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
                cr.set_font_size(8.5)
                cr.move_to(6.0, y + 3.0)
                cr.show_text(octave_name)

    def _on_draw(self, drawing_area, cr: cairo.Context, width: int, height: int):
        cr.set_source_rgb(0.12, 0.12, 0.13)
        cr.paint()

        roll_top = self.HEADER_HEIGHT
        roll_bottom = height - self.FOOTER_HEIGHT
        roll_h = max(10.0, roll_bottom - roll_top)

        # Draw Pitch Grid lanes and Middle C (C4) guide line
        self._draw_pitch_grid(cr, width, roll_top, roll_bottom, roll_h)
        min_p, _, pitch_range = self._get_global_pitch_bounds()

        # 1. Draw sessions and notes
        for item in self.session_items:
            item_x = self.time_to_x(item.timeline_offset)
            item_end_x = self.time_to_x(item.end_timeline_offset)
            item_w = max(4.0, item_end_x - item_x)

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

            # Session Header badge
            self._draw_session_header(cr, item, item_x, item_w)

            # Draw Notes & Pedal Tails
            notes = item.midi_data.notes
            if notes:

                for n in notes:
                    nx = self.time_to_x(item.timeline_offset + n.start_time)
                    played_end_t = item.timeline_offset + n.played_end_time
                    key_x = self.time_to_x(played_end_t)
                    key_w = max(3.0, key_x - nx)

                    norm_p = (n.pitch - min_p) / pitch_range
                    ny = roll_bottom - (norm_p * (roll_h - 10.0)) - 8.0

                    vel_ratio = max(0.2, min(1.0, n.velocity / 127.0))

                    # 1. Solid bar: Actual played finger-held note
                    cr.set_source_rgba(0.15 * vel_ratio, 0.65 * vel_ratio, 0.95 * vel_ratio, 0.9)
                    cr.rectangle(nx, ny, key_w, 5.0)
                    cr.fill()

                    # 2. Translucent glowing tail: Damper pedal sustain extension (if held past key release)
                    if n.end_time > n.played_end_time + 0.05:
                        pedal_end_t = item.timeline_offset + n.end_time
                        pedal_x = self.time_to_x(pedal_end_t)
                        tail_w = max(2.0, pedal_x - key_x)

                        # Distinct soft sky-blue / lavender pedal color
                        cr.set_source_rgba(0.35, 0.85, 0.75, 0.45)
                        cr.rectangle(key_x, ny + 0.5, tail_w, 4.0)
                        cr.fill()

                        # Subtle dashed border for pedal extension
                        cr.set_source_rgba(0.4, 0.9, 0.8, 0.8)
                        cr.set_line_width(0.8)
                        cr.set_dash([2.0, 2.0])
                        cr.rectangle(key_x, ny + 0.5, tail_w, 4.0)
                        cr.stroke()
                        cr.set_dash([])

            # Clapper Marker Chips
            for m in item.midi_data.markers:
                mx = self.time_to_x(item.timeline_offset + m.time)
                self._draw_marker_chip(cr, m.text, mx, roll_top - 14.0)

        # 2. Draw Collapsed Silence Break Folds (// [pause])
        for gap in self.collapsed_gaps:
            gx1 = self.time_to_x(gap.real_start)
            gx2 = self.time_to_x(gap.real_end)
            gw = max(6.0, gx2 - gx1)

            # Shaded fold region
            cr.set_source_rgba(0.1, 0.1, 0.12, 0.85)
            cr.rectangle(gx1, roll_top, gw, roll_h)
            cr.fill()

            # Diagonal fold slashes (//)
            cr.set_source_rgba(0.45, 0.5, 0.55, 0.6)
            cr.set_line_width(1.5)
            # Left slash
            cr.move_to(gx1 + 2.0, roll_top + 4.0)
            cr.line_to(gx1 + 8.0, roll_bottom - 4.0)
            cr.stroke()
            # Right slash
            cr.move_to(gx2 - 8.0, roll_top + 4.0)
            cr.line_to(gx2 - 2.0, roll_bottom - 4.0)
            cr.stroke()

            # Center Pause Pill
            pause_sec = int(round(gap.real_duration))
            if pause_sec >= 60:
                p_text = f"// {pause_sec // 60}m {pause_sec % 60}s //"
            else:
                p_text = f"// {pause_sec}s //"

            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(9.0)
            cr.set_source_rgba(0.55, 0.6, 0.65, 0.8)
            mid_x = (gx1 + gx2) / 2.0
            cr.move_to(mid_x - 18.0, roll_top + (roll_h / 2.0) + 3.0)
            cr.show_text(p_text)

        # 3. Marquee Selection Overlay
        if self.selection_range:
            s_start, s_end = self.selection_range
            sel_x = self.time_to_x(s_start)
            sel_w = max(2.0, self.time_to_x(s_end) - sel_x)

            cr.set_source_rgba(0.2, 0.5, 0.9, 0.25)
            cr.rectangle(sel_x, roll_top, sel_w, roll_h)
            cr.fill()

            cr.set_source_rgba(0.35, 0.65, 1.0, 0.9)
            cr.set_line_width(1.5)
            cr.rectangle(sel_x, roll_top, sel_w, roll_h)
            cr.stroke()

        # 4. Time Ruler / Footer
        self._draw_footer_ruler(cr, width, height)

        # 5. Acoustic Scrub Line (Dorico Ctrl+Space audition)
        if self.scrub_active and self.scrub_cursor_time is not None:
            scrub_x = self.time_to_x(self.scrub_cursor_time)
            cr.set_source_rgba(1.0, 0.7, 0.1, 0.95)
            cr.set_line_width(2.0)
            cr.move_to(scrub_x, 0)
            cr.line_to(scrub_x, height)
            cr.stroke()

        # 6. Playhead Cursor
        play_x = self.time_to_x(self.player.current_time)
        cr.set_source_rgba(0.9, 0.25, 0.25, 0.95)
        cr.set_line_width(2.0)
        cr.move_to(play_x, roll_top - 6.0)
        cr.line_to(play_x, height)
        cr.stroke()

        cr.move_to(play_x - 5.0, roll_top - 6.0)
        cr.line_to(play_x + 5.0, roll_top - 6.0)
        cr.line_to(play_x, roll_top)
        cr.close_path()
        cr.fill()

    def _draw_session_header(self, cr: cairo.Context, item: TimelineSessionItem, x: float, w: float):
        star_char = "★" if item.session.starred else "☆"
        cr.set_source_rgb(0.95, 0.75, 0.15) if item.session.starred else cr.set_source_rgb(0.5, 0.5, 0.5)
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(14.0)
        cr.move_to(x + 6.0, 22.0)
        cr.show_text(star_char)

        cr.set_source_rgb(0.85, 0.85, 0.88)
        cr.set_font_size(11.0)
        date_str = item.session.start_time.strftime("%b %-d, %-I:%M %p")
        badge = f"{date_str} • {item.session.device_name}"
        if item.session.is_live:
            badge = f"🔴 LIVE • {badge}"
        cr.move_to(x + 24.0, 21.0)
        cr.show_text(badge)

        cr.set_source_rgb(0.6, 0.6, 0.65)
        cr.set_font_size(10.0)
        stats = f"{int(item.duration)}s • {item.session.note_count} notes"
        cr.move_to(x + 24.0, 35.0)
        cr.show_text(stats)

    def _draw_marker_chip(self, cr: cairo.Context, text: str, x: float, y: float):
        cr.set_source_rgba(0.2, 0.45, 0.3, 0.9)
        chip_w = min(80.0, max(24.0, len(text) * 6.5 + 8.0))
        cr.rectangle(x - 4.0, y - 10.0, chip_w, 15.0)
        cr.fill()

        cr.set_source_rgba(0.3, 0.8, 0.4, 0.9)
        cr.set_line_width(1.0)
        cr.move_to(x, y + 5.0)
        cr.line_to(x, self.HEADER_HEIGHT + 40.0)
        cr.stroke()

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

        # Step marks across timeline
        step_sec = 5.0 if self.px_per_sec >= 30.0 else (10.0 if self.px_per_sec >= 15.0 else 30.0)
        num_steps = int(self.total_timeline_duration / step_sec) + 1

        cr.set_source_rgb(0.5, 0.5, 0.55)
        cr.set_font_size(9.0)
        cr.set_line_width(1.0)

        for i in range(num_steps):
            t = i * step_sec
            tx = self.time_to_x(t)
            if tx > width:
                break
            cr.move_to(tx, ruler_y)
            cr.line_to(tx, ruler_y + 5.0)
            cr.stroke()

            mins = int(t // 60)
            secs = int(t % 60)
            cr.move_to(tx + 2.0, ruler_y + 13.0)
            cr.show_text(f"{mins}:{secs:02d}")
