"""Shared fixtures: a display for the tests that build the real window.

Dear PyGui needs an X display to create a viewport. On the Linux server there
is none, so when DISPLAY is unset and Xvfb is installed one is started for the
session; plain ``pytest`` then runs the GUI tests instead of skipping them,
which is the point -- a skipped test is how a removed widget slips through.
With neither a display nor Xvfb the GUI tests skip and say why.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

import pytest


@pytest.fixture(scope="session")
def display():
    if sys.platform != "linux" or os.environ.get("DISPLAY"):
        yield
        return
    xvfb = shutil.which("Xvfb")
    if xvfb is None:
        pytest.skip("needs a display: set DISPLAY or install Xvfb")
    number = 90 + os.getpid() % 400
    proc = subprocess.Popen(
        [xvfb, f":{number}", "-screen", "0", "1920x1200x24", "-nolisten", "tcp"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    if proc.poll() is not None:
        pytest.skip("Xvfb did not start")
    os.environ["DISPLAY"] = f":{number}"
    try:
        yield
    finally:
        os.environ.pop("DISPLAY", None)
        proc.terminate()
        proc.wait(timeout=10)


def _offline_store(folder):
    from mosaic import basemap

    def offline(url):
        raise OSError("tests run offline")

    return basemap.TileStore(folder, opener=offline)


@pytest.fixture(scope="session")
def app(display, tmp_path_factory):
    """The real application, built once: Dear PyGui allows one context per
    process, so every GUI test shares this window."""
    import dearpygui.dearpygui as dpg

    from mosaic.gui.app.core import MosaicApp

    application = MosaicApp()
    application.setup()
    # Keep the tests out of the user's recent files and saved settings.
    application._push_recent_shapefile = lambda *args, **kwargs: None
    application._save_place_unit = lambda: None
    application._save_map_style = lambda: None
    application._save_visuals = lambda: None
    # ... and out of the user's configuration history, which the app would
    # otherwise write to on every Start and on close. Pointed somewhere
    # temporary before the first frame, so the "restore last session" offer
    # finds an empty history and stays away.
    from mosaic.session_history import HistoryStore
    application.history = HistoryStore(tmp_path_factory.mktemp("history"))
    # No test reaches the network: the tile store fails as if offline unless a
    # test swaps in its own.
    application._basemap_store = _offline_store(tmp_path_factory.mktemp("tiles"))
    dpg.show_viewport()
    for _ in range(3):
        application.run_frame()
    yield application
    application.state.request_stop()
    dpg.destroy_context()
