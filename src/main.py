# main.py
#
# Copyright 2026 Matthew Samson
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Application entry point and lifecycle manager for Pianola."""

import os
import sys

# Default to Cairo renderer to avoid GTK 4.14+ GSK GPU upload assertion crashes in sandboxed/Flatpak environments
if "GSK_RENDERER" not in os.environ:
    os.environ["GSK_RENDERER"] = "cairo"

import gi

from gettext import gettext as _

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Gio, Adw, Gdk
from .window import PianolaWindow


class PianolaApplication(Adw.Application):
    """The main application singleton class."""

    def __init__(self):
        super().__init__(
            application_id='tech.redfoxlabs.Pianola',
            flags=Gio.ApplicationFlags.HANDLES_OPEN,
            resource_base_path='/tech/redfoxlabs/Pianola'
        )
        self.create_action('quit', lambda *_: self.quit(), ['<control>q'])
        self.create_action('about', self.on_about_action)
        self.create_action('shortcuts', self.on_shortcuts_action, ['<control>question'])

        # Window accelerators
        self.set_accels_for_action("win.open", ['<control>o'])
        self.set_accels_for_action("win.export", ['<control>e'])
        self.set_accels_for_action("win.play_pause", ['space'])
        self.set_accels_for_action("win.play_selection", ['p'])
        self.set_accels_for_action("win.prev_section", ['bracketleft'])
        self.set_accels_for_action("win.next_section", ['bracketright'])
        self.set_accels_for_action("win.prev_day", ['<alt>Left', '<alt>bracketleft'])
        self.set_accels_for_action("win.next_day", ['<alt>Right', '<alt>bracketright'])
        self.set_accels_for_action("win.zoom_in", ['<control>plus', '<control>equal'])
        self.set_accels_for_action("win.zoom_out", ['<control>minus'])

    def do_startup(self):
        super().do_startup()
        self._load_css()

    def _load_css(self):
        provider = Gtk.CssProvider()
        try:
            provider.load_from_resource('/tech/redfoxlabs/Pianola/style.css')
        except Exception:
            css_path = os.path.join(os.path.dirname(__file__), 'style.css')
            if os.path.exists(css_path):
                provider.load_from_path(css_path)
        display = Gdk.Display.get_default()
        if display:
            Gtk.StyleContext.add_provider_for_display(
                display,
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = PianolaWindow(application=self)
        win.present()

    def do_open(self, files, n_files, hint):
        win = self.props.active_window
        if not win:
            win = PianolaWindow(application=self)
        if files:
            first_path = files[0].get_path()
            if first_path:
                win.load_file(first_path)
        win.present()

    def on_about_action(self, *args):
        about = Adw.AboutDialog(
            application_name='Pianola',
            application_icon='tech.redfoxlabs.Pianola',
            developer_name='Matthew Samson',
            version='0.1.0',
            translator_credits=_('translator-credits'),
            developers=['Matthew Samson'],
            copyright='© 2026 Matthew Samson'
        )
        about.present(self.props.active_window)

    def on_shortcuts_action(self, *args):
        builder = Gtk.Builder.new_from_resource('/tech/redfoxlabs/Pianola/shortcuts-dialog.ui')
        dialog = builder.get_object('shortcuts_dialog')
        if dialog:
            dialog.present(self.props.active_window)

    def create_action(self, name, callback, shortcuts=None):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)


def main(version):
    """The application's entry point."""
    app = PianolaApplication()
    return app.run(sys.argv)
