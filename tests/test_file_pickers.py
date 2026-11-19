"""File pickers opened from inside a dialog.

Dear PyGui draws a modal window above every non-modal one, whatever the focus
order, and a file dialog here cannot be modal (a modal opened after a modal
file dialog is dismissed on creation in Dear PyGui 2.3, which is why every
picker passes modal=False). A picker opened from a modal dialog therefore
appeared underneath it and took no clicks -- Start from Plan's "Choose CSV..."
was unusable for that reason.

These drive the real window, so they need a display; the shared `app` fixture
starts Xvfb when there is none.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import dearpygui.dearpygui as dpg
import pytest

from mosaic.gui.state import AlgorithmStatus

ROOT = Path(__file__).resolve().parents[1]
SHAPEFILE = ROOT / "shapefiles" / "North_Carolina_Simplified.shp"
APP_SRC = ROOT / "src" / "mosaic" / "gui" / "app"

HOT_START = "hot_start_window"
PICKER = "__hot_start_file_dialog"


def _pump(app, until, timeout: float = 180.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.run_frame()
        if until():
            return True
    return False


def _shown_modals() -> list[str]:
    """Every modal window on screen, by alias or id.

    A shown modal is drawn above a file dialog whatever the focus order, so
    this is what "the picker is clickable" comes down to.
    """
    out = []
    for item in dpg.get_all_items():
        try:
            config = dpg.get_item_configuration(item)
        except Exception:                       # not a window / already gone
            continue
        if config.get("modal") and dpg.is_item_shown(item):
            out.append(dpg.get_item_alias(item) or str(item))
    return out


@pytest.fixture(scope="module")
def loaded(app):
    """The sample map, loaded once for this module."""
    if app.runner is not None and app.runner.graph is not None:
        return app
    app._on_shapefile_selected(None, {"file_path_name": str(SHAPEFILE)})
    assert _pump(app, lambda: dpg.is_item_shown("shp_dialog")
                 or app.state.get("status")[0] == AlgorithmStatus.ERROR), (
        "the column dialog never appeared")
    app._shp_dialog._on_confirm_click()
    # map_view._loaded, not the _map_ready pulse: the pulse is consumed by the
    # frame that sees it, so a poll can miss it entirely.
    assert _pump(app, lambda: (app.runner.graph is not None
                               and app.map_view is not None
                               and app.map_view._loaded)
                 or app.state.get("status")[0] == AlgorithmStatus.ERROR), (
        app.state.error_message or "the map never finished loading")
    assert app.state.get("status")[0] != AlgorithmStatus.ERROR, app.state.error_message
    return app


@pytest.fixture
def plan_csv(loaded, tmp_path):
    """A starting-plan CSV the column picker can read, with this map's own ID
    column so the defaults resolve the way they would for a real file."""
    app = loaded
    id_col = app.runner.id_col_name
    ids = app.runner.gdf[id_col].tolist()
    lines = [f"{id_col},district"]
    lines += [f"{value},{i % 6 + 1}" for i, value in enumerate(ids)]
    path = tmp_path / "plan.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def start_from_plan(loaded):
    """Start from Plan, open, and closed again afterwards.

    The teardown renders a frame after deleting: Dear PyGui defers a delete to
    the next frame, so a window deleted without one is still there -- and the
    next test would reopen it into the pending delete and get a window that
    never shows.
    """
    app = loaded
    app._on_load_hot_start()
    assert _pump(app, lambda: dpg.does_item_exist(HOT_START), 30.0), (
        "Start from Plan never opened")
    yield app
    for tag in (PICKER, HOT_START):
        if dpg.does_item_exist(tag):
            dpg.delete_item(tag)
    app.run_frame()


def test_the_starting_plan_picker_is_on_top_and_clickable(start_from_plan, plan_csv):
    """Choose CSV... leaves the file dialog as the top window, with Start from
    Plan out of the way, and the column picker still appears afterwards."""
    app = start_from_plan
    assert dpg.is_item_shown(HOT_START)
    assert dpg.get_item_configuration(HOT_START)["modal"], (
        "this test is about a modal parent; Start from Plan is no longer one")

    app._browse_hot_start()
    assert _pump(app, lambda: dpg.does_item_exist(PICKER), 30.0), (
        "the file picker never opened")
    app.run_frame()

    # The picker is up, and nothing modal is drawn over it.
    assert dpg.is_item_shown(PICKER)
    assert not dpg.is_item_shown(HOT_START), (
        "Start from Plan is still on screen, so it covers the picker")
    assert _shown_modals() == [], (
        f"a modal window is above the picker: {_shown_modals()}")
    # ... and it can take input. (dpg.get_active_window() is no use here: it
    # reports the last window that was activated and keeps reporting it after
    # that window is hidden, so it still names Start from Plan.)
    assert dpg.get_item_configuration(PICKER)["enabled"]

    # Choosing a file: the picker's own callback, as Dear PyGui calls it.
    callback = dpg.get_item_callback(PICKER)
    assert callback is not None
    callback(PICKER, {"file_path_name": str(plan_csv)})
    app.run_frame()

    # Start from Plan is back, before the column picker wrote to it...
    assert dpg.is_item_shown(HOT_START)
    # ... and the column picker is filled in, which is what the early return
    # in _show_hot_start_column_picker would have skipped.
    assert dpg.is_item_shown("hot_start_csv_controls")
    assert app._hot_start_pending_path == str(plan_csv)
    columns = dpg.get_item_configuration("hot_start_id_column")["items"]
    assert columns == [app.runner.id_col_name, "district"]
    assert dpg.get_value("hot_start_id_column") == app.runner.id_col_name
    assert dpg.get_value("hot_start_district_column") == "district"
    assert plan_csv.name in dpg.get_value("hot_start_csv_name")


def test_cancelling_the_picker_brings_start_from_plan_back(start_from_plan):
    app = start_from_plan
    assert dpg.is_item_shown(HOT_START)
    app._browse_hot_start()
    assert _pump(app, lambda: dpg.does_item_exist(PICKER), 30.0)
    assert not dpg.is_item_shown(HOT_START)

    # cancel_callback is not readable back off the item (Dear PyGui exposes
    # `callback` but not `cancel_callback`), so the handler it is wired to is
    # called the way Dear PyGui calls it.
    app._on_hot_start_file_cancelled(PICKER, None)
    app.run_frame()

    assert dpg.is_item_shown(HOT_START)
    # Nothing was chosen, so the column controls stay as they were.
    assert not dpg.is_item_shown("hot_start_csv_controls")


def test_the_reference_plan_picker_is_also_clear_of_modals(loaded):
    """Plan Continuity's picker opens from a non-modal settings popup, so it
    needs no hiding -- but nothing modal may be over it either."""
    app = loaded
    dpg.configure_item("popup_alignment_settings", show=True)
    app.run_frame()
    assert not dpg.get_item_configuration("popup_alignment_settings")["modal"]

    app._on_load_alignment()
    assert _pump(app, lambda: dpg.does_item_exist("__alignment_file_dialog"), 30.0)
    app.run_frame()
    assert dpg.is_item_shown("__alignment_file_dialog")
    assert _shown_modals() == [], (
        f"a modal window is above the reference-plan picker: {_shown_modals()}")

    dpg.delete_item("__alignment_file_dialog")
    dpg.configure_item("popup_alignment_settings", show=False)
    app.run_frame()


def test_the_file_shortcuts_stand_down_while_a_modal_is_up(start_from_plan):
    """Ctrl+O is global in Dear PyGui, so it could open the shapefile picker
    under a modal dialog -- the same failure by another route."""
    app = start_from_plan
    assert dpg.is_item_shown(HOT_START)
    assert app._modal_dialog_shown()
    assert HOT_START in app._modal_dialogs
    # Nothing modal up: the guard lets the shortcuts through.
    dpg.configure_item(HOT_START, show=False)
    app.run_frame()
    assert not app._modal_dialog_shown()
    dpg.configure_item(HOT_START, show=True)
    app.run_frame()


def test_every_modal_dialog_is_registered(app):
    """_modal_dialog_shown reads the registry rather than the item tree, so a
    modal built outside _dialog would be invisible to it."""
    for item in dpg.get_all_items():
        try:
            config = dpg.get_item_configuration(item)
        except Exception:
            continue
        if not config.get("modal"):
            continue
        alias = dpg.get_item_alias(item)
        # Dear PyGui's own file dialogs are windows too; they are never modal
        # here, which the next test holds to.
        assert alias in app._modal_dialogs, (
            f"modal window {alias or item} was not built by _dialog")


def test_every_file_dialog_in_the_app_is_non_modal():
    """The invariant the hide-and-restore pattern rests on.

    A modal file dialog stops any later modal from opening at all (Dear PyGui
    2.3), so every picker passes modal=False -- which is also why a picker can
    never be relied on to sit above a modal parent.
    """
    for path in sorted(APP_SRC.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"dpg\.file_dialog\((.*?)\n\s*\):", text,
                                 re.DOTALL):
            body = match.group(1)
            assert "modal=False" in body, (
                f"{path.name}: a file dialog does not pass modal=False")
