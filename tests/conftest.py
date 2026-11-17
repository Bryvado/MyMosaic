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
        [xvfb, f":{number}", "-screen", "0", "1600x1100x24", "-nolisten", "tcp"],
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


@pytest.fixture(scope="session")
def app(display):
    """The real application, built once: Dear PyGui allows one context per
    process, so every GUI test shares this window."""
    import dearpygui.dearpygui as dpg

    from mosaic.gui.app.core import MosaicApp

    application = MosaicApp()
    application.setup()
    # Keep the tests out of the user's recent-files list.
    application._push_recent_shapefile = lambda *args, **kwargs: None
    dpg.show_viewport()
    for _ in range(3):
        application.run_frame()
    yield application
    application.state.request_stop()
    dpg.destroy_context()
