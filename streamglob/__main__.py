import logging
# logger = logging.getLogger(__name__)
import sys
import os
import traceback
from datetime import datetime, timedelta
from collections import namedtuple
import argparse
import subprocess
import select
import time
import re
import asyncio
import functools
import signal

import urwid
import urwid.raw_display
from urwid_utils.palette import *
from panwid.datatable import *
from panwid.listbox import ScrollingListBox
from panwid.dropdown import *
from panwid.dialog import *
from pony.orm import db_session
from tonyc_utils.logging import *

import pytz
from orderedattrdict import AttrDict
import requests
import dateutil.parser
import yaml
import orderedattrdict.yamlutils
from orderedattrdict.yamlutils import AttrDictYAMLLoader
from aiohttp.web import Application, AppRunner, TCPSite
from aiohttp_json_rpc import JsonRpc

from .state import *
from .widgets import *
from .views import *

from . import config
from . import model
from . import utils
from . import session
from . import providers
from . import player
from . import tasks
from .exceptions import *

urwid.AsyncioEventLoop._idle_emulation_delay = 1/20

PACKAGE_NAME=__name__.split('.')[0]

def load_palette():

    state.palette_entries = {}
    # FIXME: move to provider config
    for (n, f, b) in  [
            ("unread", "white", "black"),
    ]:
        state.palette_entries[n] = PaletteEntry(
            name=n,
            mono="white",
            foreground=f,
            background=b,
            foreground_high=f,
            background_high=b
        )

    for k, v in config.settings.profile.attributes.items():
        state.palette_entries[k] = PaletteEntry.from_config(v)

    for pname, p in providers.PROVIDERS.items():
        if not hasattr(p.config, "attributes"):
            continue
        for gname, group in p.config.attributes.items():
            for k, v in group.items():
                ename = f"{pname}.{gname}.{k}"
                state.palette_entries[ename] = PaletteEntry.from_config(v)

    state.palette_entries.update(DataTable.get_palette_entries(
        user_entries=state.palette_entries
    ))
    state.palette_entries.update(Dropdown.get_palette_entries())
    state.palette_entries.update(
        ScrollingListBox.get_palette_entries()
    )
    state.palette_entries.update(TabView.get_palette_entries())

    # raise Exception(state.palette_entries)
    return Palette("default", **state.palette_entries)


def reload_config():

    logger.info("reload config")
    profiles = config.settings.profile_names
    config.load(options.config_dir, merge_default=True)
    providers.load_config()
    for p in profiles:
        config.settings.include_profile(p)

    for k in list(state.screen._palette.keys()):
        del state.screen._palette[k]
    state.palette = load_palette()
    state.screen.register_palette(state.palette)


def run_gui(action, provider, **kwargs):

    state.palette = load_palette()
    state.screen = urwid.raw_display.Screen()

    def get_colors():
        if config.settings.profile.colors == "true":
            return 2**24
        elif isinstance(config.settings.profile.colors, int):
            return config.settings.profile.colors
        else:
            return 16

    state.screen.set_terminal_properties(get_colors())

    state.listings_view = ListingsView(provider)
    state.files_view = FilesView()
    state.tasks_view = TasksView()

    state.views = [
        Tab("Listings", state.listings_view, locked=True),
        # Tab("Files", state.files_view, locked=True),
        # Tab("Tasks", state.tasks_view, locked=True)
    ]

    state.main_view = BaseTabView(state.views)

    set_stdout_level(logging.CRITICAL)

    state.log_buffer = LogBuffer()
    log_console = LogViewer(state.event_loop, state.log_buffer)

    add_log_handler(state.log_buffer)

    left_column = urwid.Pile([
        ("weight", 3, state.listings_view),
        (1, urwid.SolidFill(u"\N{BOX DRAWINGS LIGHT HORIZONTAL}")),
        ("weight", 3, state.tasks_view)
    ])

    right_column = urwid.Pile([
        ("weight", 3, urwid.Filler(urwid.Text(""))),
        (1, urwid.SolidFill(u"\N{BOX DRAWINGS LIGHT HORIZONTAL}")),
        ("weight", 3, state.files_view)
    ])

    columns = urwid.Columns([
        ("weight", 1, left_column),
        (1, urwid.SolidFill(u"\N{BOX DRAWINGS LIGHT VERTICAL}")),
        ("weight", 1, right_column),
    ])

    pile = urwid.Pile([
        ("weight", 5, columns)
    ])

    if options.verbose:
        left_column.contents.append(
            (urwid.LineBox(log_console), pile.options("weight", 1))
            # (log_console, pile.options("given", 20))
        )


    def global_input(key):
        if key in ('q', 'Q'):
            state.listings_view.quit_app()
        elif key == "meta C":
            reload_config()
        else:
            return False

    state.loop = urwid.MainLoop(
        pile,
        state.palette,
        screen=state.screen,
        event_loop = urwid.AsyncioEventLoop(loop=state.event_loop),
        unhandled_input=global_input,
        pop_ups=True
    )

    if options.verbose:
        logger.setLevel(logging.DEBUG)

    def activate_view(loop, user_data):
        state.listings_view.activate()


    def start_server(loop, user_data):

        app = Application()

        async def start_server_async():
            runner = AppRunner(app)
            await runner.setup()
            site = TCPSite(runner, 'localhost', 8080)
            try:
                await site.start()
            except OSError as e:
                logger.warning(e)

        rpc = JsonRpc()

        methods = []
        for pname, p in providers.PROVIDERS.items():
            methods += [
                (pname, func)
                for name, func in p.RPC_METHODS
            ]

        rpc.add_methods(*methods)
        app.router.add_route("*", "/", rpc.handle_request)
        asyncio.create_task(start_server_async())

    state.loop.set_alarm_in(0, start_server)
    state.loop.set_alarm_in(0, activate_view)
    state.loop.run()


def run_cli(action, provider, selection, **kwargs):

    try:
        method = getattr(provider, action)
    except AttributeError:
        raise Exception(f"unknown action: {action}")

    try:
        task = method(
            selection,
            progress=False,
            stdout=sys.stdout, stderr=sys.stderr, **kwargs
        )
        loop_result = state.event_loop.run_until_complete(task.result)
        result = task.result.result()
        if isinstance(result, Exception):
            logger.exception(traceback.print_exception(type(result), result, result.__traceback__))
        if task.proc.done():
            proc = task.proc.result()
        else:
            proc = None
    except KeyboardInterrupt:
        logger.info("Exiting on keyboard interrupt")
    if proc:
        rc = proc.returncode
    else:
        rc = -1
    return rc


def main():

    global options
    global logger

    today = datetime.now(pytz.timezone('US/Eastern')).date()

    init_parser = argparse.ArgumentParser()
    init_parser.add_argument("-c", "--config-dir", help="use alternate config directory")
    init_parser.add_argument("-p", "--profile", help="use alternate config profile")
    options, args = init_parser.parse_known_args()

    config.load(options.config_dir, merge_default=True)
    if options.profile:
        for p in options.profile.split(","):
            config.settings.include_profile(p)
    player.Player.load()

    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-v", "--verbose", action="count", default=0,
                        help="verbose logging")
    group.add_argument("-q", "--quiet", action="count", default=0,
                        help="quiet logging")
    parser.add_argument("uri", metavar="URI",
                        help="media URI", nargs="?")

    options, args = parser.parse_known_args(args)

    state.options = AttrDict(vars(options))

    logging.captureWarnings(True)
    logger = logging.getLogger()
    sh = logging.StreamHandler()
    state.logger = setup_logging(options.verbose - options.quiet, quiet_stdout=False)

    providers.load()
    model.init()
    providers.load_config()

    spec = None

    logger.debug(f"{PACKAGE_NAME} starting")
    state.task_manager = tasks.TaskManager()

    state.task_manager_task = state.event_loop.create_task(state.task_manager.start())

    log_file = os.path.join(config.settings.CONFIG_DIR, f"{PACKAGE_NAME}.log")
    fh = logging.FileHandler(log_file)
    add_log_handler(fh)
    logging.getLogger("panwid.keymap").setLevel(logging.INFO)
    logging.getLogger("panwid.datatable").setLevel(logging.INFO)
    logging.getLogger("aio_mpv_jsonipc").setLevel(logging.INFO)

    action, provider, selection, opts = providers.parse_uri(options.uri)

    if selection:
        rc = run_cli(action, provider, selection, **opts)
    else:
        rc = run_gui(action, provider, **opts)
    return rc

if __name__ == "__main__":
    main()
