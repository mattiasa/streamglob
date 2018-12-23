import logging
# logger = logging.getLogger(__name__)
import os
from datetime import datetime, timedelta
from collections import namedtuple
import argparse
import subprocess
import select

import urwid
import urwid.raw_display
from urwid_utils.palette import *
from panwid.datatable import *
from panwid.listbox import ScrollingListBox
from panwid.dropdown import *
from panwid.dialog import *
from tonyc_utils.logging import *

import pytz
from orderedattrdict import AttrDict
import requests
import dateutil.parser
import yaml
import orderedattrdict.yamlutils
from orderedattrdict.yamlutils import AttrDictYAMLLoader

from .state import *
from .state import memo
from . import config
from . import model
from . import widgets
from . import utils
from . import session
from . import providers
from .exceptions import *

PACKAGE_NAME=__name__.split('.')[0]

class UrwidLoggingHandler(logging.Handler):

    pipe = None
    # def __init__(self, console):

    #     self.console = console
    #     super(UrwidLoggingHandler, self).__init__()

    def connect(self, pipe):
        self.pipe = pipe

    def emit(self, rec):

        if not self.pipe:
            return
        msg = self.format(rec)
        (ignore, ready, ignore) = select.select([], [self.pipe], [])
        if self.pipe in ready:
            os.write(self.pipe, (msg[:512]+"\n").encode("utf-8"))



class MainToolbar(urwid.WidgetWrap):

    signals = ["provider_change"]
    def __init__(self, default_provider):

        self.provider_dropdown = Dropdown(AttrDict(
            [ (p.NAME, n)
              for n, p in providers.PROVIDERS.items()]
        ) , label="Provider", default=default_provider, margin=1)

        urwid.connect_signal(
            self.provider_dropdown, "change",
            lambda w, b, v: self._emit("provider_change", v)
        )

        self.columns = urwid.Columns([
            # ('weight', 1, urwid.Padding(urwid.Edit("foo"))),
            ('weight', 1, self.provider_dropdown),
        ])
        self.filler = urwid.Filler(self.columns)
        super(MainToolbar, self).__init__(self.filler)

    @property
    def provider(self):
        return (self.provider_dropdown.selected_label)



class MainView(BaseView):

    def __init__(self, provider=None):

        self.provider = provider or providers.DEFAULT_PROVIDER
        self.toolbar = MainToolbar(self.provider)
        urwid.connect_signal(
            self.toolbar, "provider_change",
            lambda w, p: self.set_provider(p)
        )

        self.provider_view_placeholder = urwid.WidgetPlaceholder(urwid.Text(""))

        self.pile  = urwid.Pile([
            (1, self.toolbar),
            (1, urwid.Filler(urwid.Divider("-"))),
            ('weight', 1, self.provider_view_placeholder),
        ])
        self.pile.focus_position = 2
        super(MainView, self).__init__(self.pile)
        self.set_provider(self.provider)

    def set_provider(self, provider):

        logger.warning(f"set provider: {provider}")
        self.provider = provider
        # state.session = session.new(provider)
        # self.toolbar.set_resolutions(state.session.RESOLUTIONS)

        cfg = config.settings.profile.providers.get(provider, {})
        state.set_provider(provider, **cfg)
        self.provider_view_placeholder.original_widget = state.provider.view

        # self.provider_view = ScheduleView(provider)
        # self.provider_view_placeholder.original_widget = self.provider_view

        # self.table = GamesDataTable(self.provider, self.game_date) # preseason
        # self.table_placeholder.original_widget = self.table
        # urwid.connect_signal(self.table, "select",
        #                      lambda source, selection: self.open_watch_dialog(selection["game_id"]))



def main():

    global options
    global logger

    today = datetime.now(pytz.timezone('US/Eastern')).date()

    init_parser = argparse.ArgumentParser()
    init_parser.add_argument("-p", "--profile", help="use alternate config profile")
    options, args = init_parser.parse_known_args()

    config.settings.load()

    if options.profile:
        config.settings.set_profile(options.profile)

    parser = argparse.ArgumentParser()
    parser.add_argument("-r", "--resolution", help="stream resolution",
                        default=config.settings.profile.default_resolution)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-v", "--verbose", action="count", default=0,
                        help="verbose logging")
    group.add_argument("-q", "--quiet", action="count", default=0,
                        help="quiet logging")
    parser.add_argument("game", metavar="game",
                        help="game specifier", nargs="?")
    options, args = parser.parse_known_args()

    log_file = os.path.join(config.CONFIG_DIR, f"{PACKAGE_NAME}.log")


    # formatter = logging.Formatter(
    #     "%(asctime)s [%(module)16s:%(lineno)-4d] [%(levelname)8s] %(message)s",
    #     datefmt="%Y-%m-%d %H:%M:%S"
    # )

    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.DEBUG)
    # fh.setFormatter(formatter)

    logger = logging.getLogger(PACKAGE_NAME)

    ulh = UrwidLoggingHandler()

    setup_logging(options.verbose - options.quiet,
                  handlers=[fh, ulh],
                  quiet_stdout=True)

    providers.load()

    model.init()

    try:
        (provider, game_date) = options.game.split("/")
        game_date = dateutil.parser.parse(game_date)
    except (ValueError, AttributeError):
        try:
            game_date = dateutil.parser.parse(options.game)
            provider = list(config.settings.profile.providers.keys())[0]
        except (TypeError, ValueError):
            game_date = datetime.now().date()
            provider = options.game

    logger.debug(f"{PACKAGE_NAME} starting")


    entries = Dropdown.get_palette_entries()
    entries.update(ScrollingListBox.get_palette_entries())
    entries.update(DataTable.get_palette_entries())

    for (n, f, b) in  [
            ("reveal_focus",             "black",            "light green"),
           ("dp_barActive_focus",       "light gray",       "black"),
           ("dp_barActive_offFocus",    "black",            "black"),
           ("dp_barInactive_focus",     "dark gray",        "black"),
           ("dp_barInactive_offFocus",  "black",            "black"),
           ("dp_highlight_focus",       "black",            "yellow"),
           ("dp_highlight_offFocus",    "white",            "black"),
           ("text_highlight",           "yellow",           "black"),
           ("text_bold",                "white",             "black"),
           ("text_esc",                 "light red",   "black")
    ]:
        entries[n] = PaletteEntry(
            name=n,
            mono="white",
            foreground=f,
            background=b,
            foreground_high=f,
            background_high=b
        )

    # raise Exception(entries)
    palette = Palette("default", **entries)
    screen = urwid.raw_display.Screen()
    screen.set_terminal_properties(256)

    view = MainView(provider)

    log_console = widgets.ConsoleWindow()
    # log_box = urwid.BoxAdapter(urwid.LineBox(log_console), 10)
    pile = urwid.Pile([
        ("weight", 5, urwid.LineBox(view)),
        ("weight", 1, urwid.LineBox(log_console))
    ])

    def global_input(key):
        if key in ('q', 'Q'):
            raise urwid.ExitMainLoop()
        else:
            return False

    state.loop = urwid.MainLoop(
        pile,
        palette,
        screen=screen,
        unhandled_input=global_input,
        pop_ups=True
    )
    ulh.connect(state.loop.watch_pipe(log_console.log_message))
    if options.verbose:
        logger.setLevel(logging.DEBUG)

    state.loop.run()


if __name__ == "__main__":
    main()
