# window.py
#
# Copyright 2026 Matthew Samson
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Main application window for Pianola."""

import os
import tempfile
from datetime import datetime
from typing import Optional, List
from gettext import gettext as _

from gi.repository import Adw, Gtk, Gio, Gdk, GLib

from .archive import ArchiveManager, SessionRecord, NoteEvent
from .player import AudioPlayer
from .timeline_canvas import TimelineCanvas
from .overview_minimap import OverviewMinimap
from .piano_keyboard import PianoKeyboardGutter


@Gtk.Template(resource_path='/tech/redfoxlabs/Pianola/window.ui')
class PianolaWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'PianolaWindow'

    view_stack = Gtk.Template.Child()
    canvas_container = Gtk.Template.Child()
    window_title = Gtk.Template.Child()
    lbl_time = Gtk.Template.Child()
    lbl_selection = Gtk.Template.Child()
    btn_play = Gtk.Template.Child()
    btn_calendar = Gtk.Template.Child()
    minimap_container = Gtk.Template.Child()
    keyboard_container = Gtk.Template.Child()
    btn_prev_day = Gtk.Template.Child()
    btn_next_day = Gtk.Template.Child()
    btn_menu = Gtk.Template.Child()
    btn_multi_track = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.archive_mgr = ArchiveManager()
        self.player = AudioPlayer()
        self.canvas = TimelineCanvas(self.player)

        # Connect canvas callbacks
        self.canvas.on_star_toggled = self._on_star_toggled
        self.canvas.on_selection_changed = self._on_selection_changed

        # Put canvas into container
        self.canvas_container.append(self.canvas)

        # Setup Overview Minimap
        self.minimap = OverviewMinimap(self.canvas, self.player)
        self.minimap_container.append(self.minimap)
        self.btn_prev_day.connect("clicked", self._on_prev_day_clicked)
        self.btn_next_day.connect("clicked", self._on_next_day_clicked)

        # Setup Visual Piano Keyboard Gutter
        self.keyboard = PianoKeyboardGutter(self.canvas, self.player)
        self.keyboard_container.append(self.keyboard)

        # Player callbacks
        self.player.on_state_changed = self._on_player_state_changed
        self.player.on_tick = self._on_player_tick

        # Setup Calendar Popover
        self._setup_calendar_popover()

        # Setup Drag and Drop
        self._setup_drag_source()

        # Connect UI toggles
        self.btn_multi_track.connect("toggled", self._on_multi_track_toggled)

        # Register window actions
        self._setup_actions()

        # Setup Theme Selector inside Primary Menu Popover
        self._setup_theme_selector()

        # Connect style manager dark notification for light/dark Cairo repaints
        style_mgr = Adw.StyleManager.get_default()
        style_mgr.connect("notify::dark", self._on_style_dark_changed)

        # Add hardware-synchronized VSync tick callback
        self.canvas.add_tick_callback(self._on_ui_tick)

        # Auto-discover Midikeep archive on launch
        self.load_archive()

    def _setup_theme_selector(self):
        popover = self.btn_menu.get_popover()
        if popover:
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
            box.set_halign(Gtk.Align.CENTER)
            box.set_margin_top(6)
            box.set_margin_bottom(6)
            box.set_margin_start(12)
            box.set_margin_end(12)
            box.add_css_class("theme-selector")

            style_mgr = Adw.StyleManager.get_default()

            btn_system = Gtk.CheckButton()
            btn_system.add_css_class("follow")
            btn_system.set_tooltip_text(_("Follow System Style"))

            btn_light = Gtk.CheckButton()
            btn_light.add_css_class("light")
            btn_light.set_tooltip_text(_("Light Style"))
            btn_light.set_group(btn_system)

            btn_dark = Gtk.CheckButton()
            btn_dark.add_css_class("dark")
            btn_dark.set_tooltip_text(_("Dark Style"))
            btn_dark.set_group(btn_system)

            # Sync initial state
            curr_scheme = style_mgr.get_color_scheme()
            if curr_scheme == Adw.ColorScheme.FORCE_LIGHT:
                btn_light.set_active(True)
            elif curr_scheme == Adw.ColorScheme.FORCE_DARK:
                btn_dark.set_active(True)
            else:
                btn_system.set_active(True)

            def _on_theme_toggled(btn):
                if not btn.get_active():
                    return
                if btn == btn_system:
                    style_mgr.set_color_scheme(Adw.ColorScheme.DEFAULT)
                elif btn == btn_light:
                    style_mgr.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
                elif btn == btn_dark:
                    style_mgr.set_color_scheme(Adw.ColorScheme.FORCE_DARK)

            btn_system.connect("toggled", _on_theme_toggled)
            btn_light.connect("toggled", _on_theme_toggled)
            btn_dark.connect("toggled", _on_theme_toggled)

            box.append(btn_system)
            box.append(btn_light)
            box.append(btn_dark)

            popover.add_child(box, "theme_selector")

    def _on_style_dark_changed(self, *args):
        self.canvas.queue_draw()
        self.minimap.queue_draw()
        self.keyboard.queue_draw()

    def _setup_actions(self):
        actions = [
            ("open", self._on_action_open),
            ("export", self._on_action_export),
            ("play_pause", self._on_action_play_pause),
            ("play_selection", self._on_action_play_selection),
            ("prev_section", self._on_action_prev_section),
            ("next_section", self._on_action_next_section),
            ("prev_day", lambda *_: self._on_prev_day_clicked(None)),
            ("next_day", lambda *_: self._on_next_day_clicked(None)),
            ("zoom_in", lambda *_: self.canvas.zoom_in()),
            ("zoom_out", lambda *_: self.canvas.zoom_out()),
            ("toggle_multi_track", self._on_action_toggle_multi_track),
        ]
        for name, callback in actions:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

    def _setup_calendar_popover(self):
        popover = Gtk.Popover()
        calendar = Gtk.Calendar()
        calendar.connect("day-selected", self._on_calendar_day_selected)
        popover.set_child(calendar)
        self.btn_calendar.set_popover(popover)

    def _setup_drag_source(self):
        """Enable dragging marquee selection or active file directly from timeline into DAWs, Notation, or Nautilus."""
        self.canvas.on_request_drag_content = self._create_drag_content_provider

    def _create_drag_content_provider(self, use_selection: bool = True, target_item = None):
        """Prepare rock-solid MIDI file provider for drag operation supporting GdkFileList, text/uri-list, and text/plain."""
        if not self.canvas.session_items:
            return None

        export_dir = self.archive_mgr.export_dir
        sel = self.canvas.selection_range
        final_path = None

        if use_selection and sel:
            # Marquee slice export
            s_start, s_end = sel
            final_path = os.path.join(export_dir, f"Pianola_Slice_{int(s_start)}s_{int(s_end)}s.mid")
            all_notes: List[NoteEvent] = []
            for item in self.canvas.session_items:
                for n in item.midi_data.notes:
                    all_notes.append(NoteEvent(
                        pitch=n.pitch,
                        velocity=n.velocity,
                        start_time=n.start_time + item.timeline_offset,
                        end_time=n.end_time + item.timeline_offset,
                        channel=n.channel
                    ))
            self.archive_mgr.export_slice(all_notes, s_start, s_end, final_path)
        else:
            if not target_item:
                curr_t = self.player.current_time
                target_item = self.canvas.session_items[0]
                for item in self.canvas.session_items:
                    if item.timeline_offset <= curr_t <= item.end_timeline_offset:
                        target_item = item
                        break
                    elif item.timeline_offset <= curr_t:
                        target_item = item

            if target_item.session.file_path and os.path.exists(target_item.session.file_path):
                final_path = target_item.session.file_path
            else:
                final_path = os.path.join(export_dir, f"Pianola_Take_{int(target_item.timeline_offset)}s.mid")
                all_notes = [NoteEvent(
                    pitch=n.pitch,
                    velocity=n.velocity,
                    start_time=n.start_time,
                    end_time=n.end_time,
                    channel=n.channel
                ) for n in target_item.midi_data.notes]
                self.archive_mgr.export_slice(all_notes, 0.0, target_item.duration, final_path)

        if not final_path or not os.path.exists(final_path):
            return None

        # Build multi-format provider: GdkFileList for Nautilus, text/uri-list and text/plain for DAWs and Wine/Dorico
        gfile = Gio.File.new_for_path(os.path.abspath(final_path))
        uri = gfile.get_uri()
        uri_payload = (uri + chr(13) + chr(10)).encode("utf-8")

        providers = []
        if hasattr(Gdk, "FileList"):
            providers.append(Gdk.ContentProvider.new_for_value(Gdk.FileList.new_from_list([gfile])))
        else:
            providers.append(Gdk.ContentProvider.new_for_value(gfile))

        providers.append(Gdk.ContentProvider.new_for_bytes("text/uri-list", GLib.Bytes.new(uri_payload)))
        providers.append(Gdk.ContentProvider.new_for_bytes("text/plain", GLib.Bytes.new(uri_payload)))
        providers.append(Gdk.ContentProvider.new_for_bytes("text/plain;charset=utf-8", GLib.Bytes.new(uri_payload)))

        return Gdk.ContentProvider.new_union(providers)

    def load_archive(self):
        """Default launch: check ~/.local/share/midikeep/index.db."""
        if not self.archive_mgr.archive_exists():
            self.view_stack.set_visible_child_name("empty")
            self.window_title.set_subtitle("No Archive Found")
            return

        sessions = self.archive_mgr.load_sessions()
        live = self.archive_mgr.get_live_session()
        if live:
            sessions.append(live)

        if not sessions:
            self.view_stack.set_visible_child_name("empty")
            self.window_title.set_subtitle("Empty Archive")
            return

        self.view_stack.set_visible_child_name("timeline")
        self.canvas.load_sessions(sessions)

        # Default start position: beginning of the last session
        self.canvas.jump_to_latest_session()

        count = len(sessions)
        self.window_title.set_subtitle(f"Midikeep Archive • {count} {'take' if count == 1 else 'takes'}")

    def load_file(self, filepath: str):
        """Open an external single MIDI file directly."""
        try:
            session = self.archive_mgr.load_single_file(filepath)
            self.view_stack.set_visible_child_name("timeline")
            self.canvas.load_sessions([session])
            self.canvas.jump_to_archive_start()
            self.window_title.set_subtitle(os.path.basename(filepath))
        except Exception as e:
            dialog = Adw.AlertDialog(
                heading="Could Not Open File",
                body=str(e)
            )
            dialog.add_response("ok", "OK")
            dialog.present(self)

    # --- Callbacks & Action Handlers ---

    def _on_star_toggled(self, session: SessionRecord, new_state: bool):
        if session.id is not None:
            self.archive_mgr.set_starred(session.id, new_state)

    def _on_multi_track_toggled(self, btn):
        active = btn.get_active()
        self.canvas.set_multi_track_mode(active)
        self.keyboard.queue_draw()
        self.canvas.queue_draw()

    def _on_action_toggle_multi_track(self, action, param):
        self.btn_multi_track.set_active(not self.btn_multi_track.get_active())

    def _on_selection_changed(self, sel_range: Optional[tuple]):
        if sel_range:
            s1, s2 = sel_range
            dur = s2 - s1
            m1, s_1 = int(s1 // 60), int(s1 % 60)
            m2, s_2 = int(s2 // 60), int(s2 % 60)
            self.lbl_selection.set_text(f"Selected: {m1:02d}:{s_1:02d} – {m2:02d}:{s_2:02d} ({dur:.1f}s)")
        else:
            self.lbl_selection.set_text("No selection")

    def _on_player_state_changed(self, is_playing: bool):
        GLib.idle_add(lambda: self.btn_play.set_icon_name(
            "media-playback-pause-symbolic" if is_playing else "media-playback-start-symbolic"
        ))

    def _on_ui_tick(self, widget, frame_clock):
        if self.player.is_playing or self.canvas.scrub_active:
            self.keyboard.queue_draw()
        if self.player.is_playing:
            cur_t = self.player.current_time
            cur_m, cur_s = int(cur_t // 60), int(cur_t % 60)
            tot = self.canvas.total_timeline_duration
            tot_m, tot_s = int(tot // 60), int(tot % 60)
            self.lbl_time.set_text(f"{cur_m:02d}:{cur_s:02d} / {tot_m:02d}:{tot_s:02d}")

            self.canvas.queue_draw()
            self.minimap.queue_draw()

            # Auto-scroll canvas viewport if playhead approaches right edge
            # Only auto-scroll when user is not actively dragging and playhead is on-screen
            if not self.minimap.is_dragging and not self.canvas._drag_start_time:
                hadj = self.canvas.hadj
                cur_x = self.canvas.time_to_x(cur_t)
                page_size = hadj.get_page_size()
                val = hadj.get_value()
                max_val = max(0.0, hadj.get_upper() - page_size)
                # Only follow forward if the playhead is currently inside the visible viewport;
                # if user manually scrolled away to inspect another section, do not yank them back.
                if val <= cur_x <= val + page_size:
                    if cur_x > val + page_size - 80:
                        hadj.set_value(min(max_val, cur_x - 80))
        return GLib.SOURCE_CONTINUE

    def _on_player_tick(self, current_time: float):
        def _update():
            # Update time label
            cur_m, cur_s = int(current_time // 60), int(current_time % 60)
            tot = self.canvas.total_timeline_duration
            tot_m, tot_s = int(tot // 60), int(tot % 60)
            self.lbl_time.set_text(f"{cur_m:02d}:{cur_s:02d} / {tot_m:02d}:{tot_s:02d}")

            # Redraw canvas and minimap so playhead cursor follows playback in real time
            self.canvas.queue_draw()
            self.minimap.queue_draw()
            return False

        GLib.idle_add(_update)

    def _on_calendar_day_selected(self, calendar):
        gdate = calendar.get_date()
        target = datetime(gdate.get_year(), gdate.get_month(), gdate.get_day_of_month())
        self.canvas.jump_to_day(target)
        self.scroll_to_time(self.player.current_time)
        popover = self.btn_calendar.get_popover()
        if popover:
            popover.popdown()

    def _on_action_play_pause(self, action, param):
        if self.canvas.ctrl_held or self.canvas.scrub_active:
            return
        if not self.player.is_playing:
            # If starting playback and playhead is offscreen, frame it in the viewport
            hadj = self.canvas.hadj
            cur_x = self.canvas.time_to_x(self.player.current_time)
            val = hadj.get_value()
            page_size = hadj.get_page_size()
            if cur_x < val or cur_x > val + page_size:
                self.scroll_to_time(self.player.current_time)
        self.player.toggle_play_pause()

    def _on_action_play_selection(self, action, param):
        if self.player.is_playing:
            self.player.pause()
        else:
            if self.canvas.selection_range:
                start_t = self.canvas.selection_range[0]
                self.scroll_to_time(start_t)
                self.player.play(from_time=start_t)
            else:
                self.scroll_to_time(self.player.current_time)
                self.player.play()

    def scroll_to_time(self, t: float):
        """Scroll the viewport so time t is centered in view."""
        hadj = self.canvas.hadj
        target_x = self.canvas.time_to_x(t)
        page_size = hadj.get_page_size()
        max_val = max(0.0, hadj.get_upper() - page_size)
        hadj.set_value(max(0.0, min(max_val, target_x - (page_size / 3.0))))
        self.canvas.queue_draw()
        self.minimap.queue_draw()

    def _on_prev_day_clicked(self, btn):
        if not self.canvas.session_items:
            return
        curr_t = self.player.current_time
        curr_item = self.canvas.session_items[0]
        for item in self.canvas.session_items:
            if item.timeline_offset <= curr_t + 0.1:
                curr_item = item
            else:
                break
        curr_date = curr_item.session.start_time.date()

        all_days = sorted(list({item.session.start_time.date() for item in self.canvas.session_items}))
        prev_days = [d for d in all_days if d < curr_date]
        if prev_days:
            target_day = prev_days[-1]
            target_item = next(it for it in self.canvas.session_items if it.session.start_time.date() == target_day)
            self.player.seek(target_item.timeline_offset)
            self.scroll_to_time(target_item.timeline_offset)
        else:
            first = self.canvas.session_items[0]
            self.player.seek(first.timeline_offset)
            self.scroll_to_time(first.timeline_offset)
        self.minimap.queue_draw()

    def _on_next_day_clicked(self, btn):
        if not self.canvas.session_items:
            return
        curr_t = self.player.current_time
        curr_item = self.canvas.session_items[0]
        for item in self.canvas.session_items:
            if item.timeline_offset <= curr_t + 0.1:
                curr_item = item
            else:
                break
        curr_date = curr_item.session.start_time.date()

        all_days = sorted(list({item.session.start_time.date() for item in self.canvas.session_items}))
        next_days = [d for d in all_days if d > curr_date]
        if next_days:
            target_day = next_days[0]
            target_item = next(it for it in self.canvas.session_items if it.session.start_time.date() == target_day)
            self.player.seek(target_item.timeline_offset)
            self.scroll_to_time(target_item.timeline_offset)
        else:
            last = self.canvas.session_items[-1]
            self.player.seek(last.timeline_offset)
            self.scroll_to_time(last.timeline_offset)
        self.minimap.queue_draw()


    def _on_action_prev_section(self, action, param):
        t = self.player.jump_prev_section()
        self.scroll_to_time(t)

    def _on_action_next_section(self, action, param):
        t = self.player.jump_next_section()
        self.scroll_to_time(t)

    def _on_action_open(self, action, param):
        dialog = Gtk.FileDialog()
        dialog.set_title("Open MIDI File")
        filter_midi = Gtk.FileFilter()
        filter_midi.set_name("MIDI Files (*.mid, *.midi)")
        filter_midi.add_pattern("*.mid")
        filter_midi.add_pattern("*.midi")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filter_midi)
        dialog.set_filters(filters)

        def _open_cb(src, res):
            try:
                gfile = dialog.open_finish(res)
                if gfile:
                    self.load_file(gfile.get_path())
            except Exception:
                pass

        dialog.open(self, None, _open_cb)

    def _on_action_export(self, action, param):
        """Export selection (marquee slice), current file, or multiple files."""
        dialog = Gtk.FileDialog()
        dialog.set_title("Export MIDI")

        sel = self.canvas.selection_range
        if sel:
            dialog.set_initial_name("selection_slice.mid")
        elif self.canvas.session_items:
            dialog.set_initial_name(os.path.basename(self.canvas.session_items[0].session.file_path))

        def _save_cb(src, res):
            try:
                gfile = dialog.save_finish(res)
                if not gfile:
                    return
                dest_path = gfile.get_path()

                if sel:
                    s_start, s_end = sel
                    all_notes: List[NoteEvent] = []
                    for item in self.canvas.session_items:
                        for n in item.midi_data.notes:
                            all_notes.append(NoteEvent(
                                pitch=n.pitch,
                                velocity=n.velocity,
                                start_time=n.start_time + item.timeline_offset,
                                end_time=n.end_time + item.timeline_offset,
                                channel=n.channel
                            ))
                    self.archive_mgr.export_slice(all_notes, s_start, s_end, dest_path)
                elif self.canvas.session_items:
                    src_file = self.canvas.session_items[0].session.file_path
                    self.archive_mgr.export_file(src_file, dest_path)
            except Exception as e:
                err_dlg = Adw.AlertDialog(heading="Export Failed", body=str(e))
                err_dlg.add_response("ok", "OK")
                err_dlg.present(self)

        dialog.save(self, None, _save_cb)

    def do_close_request(self):
        self.player.close()
        return False
