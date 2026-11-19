"""Gathering and restoring a configuration through the interface.

The store itself is covered in tests/test_session_history.py; this is the half
that reads and writes widgets, so it drives the real window (the shared `app`
fixture starts Xvfb when there is no display).
"""

from __future__ import annotations

import time
from pathlib import Path

import dearpygui.dearpygui as dpg
import pytest

from mosaic import goals as G
from mosaic import session_history as SH
from mosaic.gui.state import AlgorithmStatus

ROOT = Path(__file__).resolve().parents[1]
SHAPEFILE = ROOT / "shapefiles" / "North_Carolina_Simplified.shp"


def _pump(app, until, timeout: float = 180.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.run_frame()
        if until():
            return True
    return False


@pytest.fixture(scope="module")
def loaded(app):
    """The sample map, loaded once for this module."""
    if app.runner is None or app.runner.graph is None:
        app._on_shapefile_selected(None, {"file_path_name": str(SHAPEFILE)})
        assert _pump(app, lambda: dpg.is_item_shown("shp_dialog")
                     or app.state.get("status")[0] == AlgorithmStatus.ERROR)
        app._shp_dialog._on_confirm_click()
        assert _pump(app, lambda: (app.runner.graph is not None
                                   and app.map_view is not None
                                   and app.map_view._loaded)
                     or app.state.get("status")[0] == AlgorithmStatus.ERROR), (
            app.state.error_message or "the map never loaded")
    assert app.state.get("status")[0] != AlgorithmStatus.ERROR
    return app


@pytest.fixture
def history(loaded, tmp_path):
    """A history of its own per test, so one test cannot see another's."""
    app = loaded
    previous = app.history
    app.history = SH.HistoryStore(tmp_path / "history", keep=4)
    app._history_sig = None
    app._history_selected = ""
    app._run_snapshot_id = ""
    yield app.history
    app.history = previous
    if dpg.does_item_exist("popup_history"):
        dpg.configure_item("popup_history", show=False)
    app.run_frame()


# ── Gathering ────────────────────────────────────────────────────────────────

def test_a_snapshot_carries_the_whole_configuration(loaded, history):
    app = loaded
    config = app._snapshot_config()
    assert config["version"] == SH.SCHEMA_VERSION
    # The map and its columns, in the same shape Open Recent stores.
    assert config["shapefile"]["path"] == str(SHAPEFILE)
    columns = config["shapefile"]["config"]
    assert columns["id_col"] == app.runner.id_col_name
    assert columns["pop_col"] and columns["county_col"]
    assert columns["elections"] and columns["demographics"]
    # The scores, as a preset: the same dict a preset file holds.
    from mosaic.presets import SCHEMA, validate_preset
    assert set(config["preset"]) == set(SCHEMA)
    assert validate_preset(config["preset"]) == config["preset"]
    # ... and everything a preset does not carry.
    assert set(config["scores"]) == {"hinge", "opportunity_targets"}
    assert config["run"]["num_districts"] == dpg.get_value(app._num_districts)
    assert config["run"]["max_iterations"] == dpg.get_value(app._iterations)
    assert config["annealing"]["enabled"] == dpg.get_value(app._ann_enabled)
    assert config["map_style"]["lines"]
    assert config["place_unit"] == app._place_unit


def test_starting_a_run_saves_a_snapshot(loaded, history):
    app = loaded
    dpg.set_value(app._num_districts, 4)
    dpg.set_value(app._iterations, 60)
    dpg.set_value(app._seed, 7)
    app._on_run()
    assert _pump(app, lambda: app.state.status in (
        AlgorithmStatus.RUNNING, AlgorithmStatus.PARTITIONING,
        AlgorithmStatus.COMPLETED, AlgorithmStatus.ERROR), 60.0)
    assert app.state.status is not AlgorithmStatus.ERROR, app.state.error_message
    rows = history.entries()
    assert len(rows) == 1
    assert rows[0]["trigger"] == "run"
    assert rows[0]["districts"] == 4
    assert rows[0]["iterations"] == 60
    assert app._run_snapshot_id == rows[0]["id"]

    # What the run exported is noted against that snapshot.
    app.state.request_stop()
    assert _pump(app, lambda: app.state.status in (
        AlgorithmStatus.IDLE, AlgorithmStatus.COMPLETED), 120.0)
    app._record_assignment_csv("/out/assignments_test.csv")
    assert history.entry(app._run_snapshot_id)["assignment_csv"] == (
        "/out/assignments_test.csv")
    assert history.load(app._run_snapshot_id).assignment_csv == (
        "/out/assignments_test.csv")


# ── Restoring ────────────────────────────────────────────────────────────────

def test_a_configuration_round_trips_through_a_restore(loaded, history):
    """The whole point: save, change a great deal, restore, and every setting
    is back where it was."""
    app = loaded
    dpg.set_value(app._num_districts, 9)
    dpg.set_value(app._iterations, 1234)
    dpg.set_value(app._tolerance, 1.5)
    app._on_tolerance_change()
    dpg.set_value(app._seed, 42)
    app._set_goal_strength("compact", 60.0)
    app._set_goal_strength("counties", 40.0)
    dpg.set_value(app._ann_enabled, False)
    app._on_ann_toggle()
    dpg.set_value(app._place_unit_input, "cities")
    app._on_place_unit_change()
    app.run_frame()

    before = app._snapshot_config()
    snapshot = history.save(before, trigger="run")
    assert snapshot is not None

    # Now change all of it.
    dpg.set_value(app._num_districts, 3)
    dpg.set_value(app._iterations, 77)
    dpg.set_value(app._tolerance, 4.5)
    app._on_tolerance_change()
    dpg.set_value(app._seed, 0)
    app._on_clear_scores()
    app._set_goal_strength("minority", 80.0)
    dpg.set_value(app._ann_enabled, True)
    app._on_ann_toggle()
    dpg.set_value(app._place_unit_input, "wards")
    app._on_place_unit_change()
    dpg.set_value(app._opportunity_target_auto["black"], False)
    dpg.set_value(app._opportunity_target_count["black"], 2)
    app._on_opportunity_target_change()
    app.run_frame()
    assert app._snapshot_config() != before

    app._restore_snapshot(snapshot.id)
    app.run_frame()

    after = app._snapshot_config()
    assert SH.diff(before, after) == [], (
        f"restored configuration differs: {SH.diff(before, after)}")
    # ... and the Goals panel reads the words it did, not just the weights.
    states = G.goal_states(after["preset"])
    assert states["compact"].label == "Strong"
    assert states["counties"].label == "Moderate"
    assert not states["minority"].on
    assert dpg.get_value(app._place_unit_input) == "cities"


def test_restoring_is_blocked_while_a_run_is_active(loaded, history):
    app = loaded
    snapshot = history.save(app._snapshot_config(), trigger="run")
    dpg.set_value(app._iterations, 4000)
    dpg.set_value(app._num_districts, 4)
    app._on_run()
    assert _pump(app, lambda: app.state.status is AlgorithmStatus.RUNNING, 60.0)
    assert app._restore_blocked()

    dpg.set_value(app._iterations, 123)
    app._restore_snapshot(snapshot.id)
    app.run_frame()
    assert dpg.get_value(app._iterations) == 123, "a restore ran during a run"
    assert "Stop the run" in app.state.status_message

    app.state.request_stop()
    assert _pump(app, lambda: app.state.status in (
        AlgorithmStatus.IDLE, AlgorithmStatus.COMPLETED), 120.0)


def test_a_missing_shapefile_is_reported_and_the_rest_applied(loaded, history):
    app = loaded
    dpg.set_value(app._iterations, 2468)
    config = app._snapshot_config()
    config["shapefile"]["path"] = "/nowhere/gone.shp"
    snapshot = history.save(config, trigger="exit")

    dpg.set_value(app._iterations, 11)
    app._restore_snapshot(snapshot.id)
    app.run_frame()

    message = app.state.status_message
    assert "map not found" in message and "gone.shp" in message
    # The open map is untouched...
    assert app.state.shapefile_path == str(SHAPEFILE)
    assert app.runner.graph is not None
    # ... and everything that could be applied was.
    assert dpg.get_value(app._iterations) == 2468


def test_a_missing_reference_plan_is_reported(loaded, history):
    app = loaded
    config = app._snapshot_config()
    config["reference_plan"] = {"path": "/nowhere/ref.csv",
                                "id_col": "GEOID20",
                                "district_col": "district"}
    config["starting_plan"] = {"path": "/nowhere/plan.csv",
                               "id_col": "GEOID20",
                               "district_col": "district"}
    snapshot = history.save(config, trigger="exit")

    app._restore_snapshot(snapshot.id)
    app.run_frame()

    message = app.state.status_message
    assert "reference plan not found at /nowhere/ref.csv" in message
    assert "starting plan not found at /nowhere/plan.csv" in message
    assert app.runner.alignment_data is None


def test_a_snapshot_from_a_newer_version_is_refused(loaded, history):
    app = loaded
    config = app._snapshot_config()
    config["version"] = SH.SCHEMA_VERSION + 1
    snapshot = history.save(config, trigger="exit")
    dpg.set_value(app._iterations, 31)

    app._restore_snapshot(snapshot.id)
    app.run_frame()
    assert "newer version" in app.state.status_message
    assert dpg.get_value(app._iterations) == 31


def test_the_columns_a_map_no_longer_has_are_named(loaded, history):
    app = loaded
    config = app._snapshot_config()
    config["shapefile"]["config"]["pop_col"] = "POP_FROM_2010"
    snapshot = history.save(config, trigger="exit")

    app._restore_snapshot(snapshot.id)
    app.run_frame()
    assert "columns gone from the map: POP_FROM_2010" in app.state.status_message


def test_a_different_map_defers_the_rest_of_the_restore(loaded, history):
    """A map takes a background load to open, so the configuration that goes
    with it waits rather than being applied to the map still on screen."""
    app = loaded
    config = app._snapshot_config()
    config["shapefile"]["path"] = str(SHAPEFILE)      # exists, so it is tried
    config["run"]["max_iterations"] = 4321
    snapshot = history.save(config, trigger="exit")

    # Stand in for the open itself: this test is about what is held back, and
    # really opening a second map would wipe the one this module shares.
    asked: list = []
    original = app._request_open_shapefile
    app._request_open_shapefile = lambda path, cfg=None: asked.append((path, cfg))
    try:
        app.state.update(shapefile_path="/elsewhere/other.shp")
        dpg.set_value(app._iterations, 5)
        app._restore_snapshot(snapshot.id)
    finally:
        app._request_open_shapefile = original
        app.state.update(shapefile_path=str(SHAPEFILE))

    assert asked and asked[0][0] == str(SHAPEFILE)
    assert asked[0][1].id_col == app.runner.id_col_name   # its saved columns
    assert app._pending_restore is not None
    assert dpg.get_value(app._iterations) == 5, "applied before its map arrived"

    # It applies once that map is the one open.
    app._finish_pending_restore()
    assert app._pending_restore is None
    assert dpg.get_value(app._iterations) == 4321


def test_a_held_restore_ignores_a_map_it_did_not_ask_for(loaded, history):
    app = loaded
    config = app._snapshot_config()
    config["run"]["max_iterations"] = 999
    app._pending_restore = ("/some/other.shp", config)
    dpg.set_value(app._iterations, 17)

    app._finish_pending_restore()
    assert dpg.get_value(app._iterations) == 17
    assert app._pending_restore is not None, "the wrong map consumed it"
    app._pending_restore = None


def test_opening_another_map_drops_a_held_restore(loaded, history):
    """What _begin_open_shapefile does on its way past: whatever started the
    open, a configuration waiting for a different map is not applied to this
    one. The one waiting for *this* map survives."""
    app = loaded
    config = app._snapshot_config()
    app._pending_restore = ("/some/other.shp", config)
    app._drop_stale_restore("/a/third.shp")
    assert app._pending_restore is None

    app._pending_restore = ("/some/other.shp", config)
    app._drop_stale_restore("/some/other.shp")
    assert app._pending_restore is not None
    app._pending_restore = None


# ── The window ───────────────────────────────────────────────────────────────

def test_the_window_lists_pins_and_renames(loaded, history):
    app = loaded
    dpg.set_value(app._iterations, 500)
    first = history.save(app._snapshot_config(), trigger="run")
    dpg.set_value(app._iterations, 900)
    second = history.save(app._snapshot_config(), trigger="exit")

    app._on_open_history()
    app.run_frame()
    assert dpg.is_item_shown("popup_history")
    # Newest first, and the newest is selected.
    assert app._history_selected == second.id
    assert dpg.does_item_exist(f"history_row_{first.id}")
    assert dpg.does_item_exist(f"history_row_{second.id}")
    assert "900 iters" in dpg.get_item_configuration(
        f"history_row_{second.id}")["label"]

    # The diff against the current settings is shown for the selection.
    app._on_history_select(first.id)
    app.run_frame()
    labels = [dpg.get_value(item) for item
              in dpg.get_item_children("history_diff", 1)]
    assert any("Run > max iterations: 900 → 500" in text
               for text in labels), labels

    # Pinning marks the row and survives pruning.
    app._on_history_pin()
    app.run_frame()
    assert history.entry(first.id)["pinned"] is True
    assert dpg.get_item_configuration(
        f"history_row_{first.id}")["label"].startswith("* ")

    # Renaming names it, and a named snapshot is kept.
    app._on_history_rename()
    dpg.set_value(app._history_name_input, "the good one")
    app._on_history_rename_save()
    app.run_frame()
    assert history.entry(first.id)["name"] == "the good one"
    assert '"the good one"' in dpg.get_item_configuration(
        f"history_row_{first.id}")["label"]


def test_the_window_restores_the_selected_snapshot(loaded, history):
    app = loaded
    dpg.set_value(app._iterations, 654)
    snapshot = history.save(app._snapshot_config(), trigger="run")
    dpg.set_value(app._iterations, 21)

    app._on_open_history()
    app.run_frame()
    app._on_history_select(snapshot.id)
    app._on_history_restore()
    app.run_frame()
    assert dpg.get_value(app._iterations) == 654


def test_saving_a_snapshot_as_a_preset_writes_its_own_scores(loaded, history,
                                                             tmp_path):
    app = loaded
    app._set_goal_strength("compact", 100.0)
    snapshot = history.save(app._snapshot_config(), trigger="run")
    stored_weight = snapshot.config["preset"]["holistic_compactness"]["weight"]
    # Change the live scores: the file must hold the snapshot's, not these.
    app._on_clear_scores()
    app.run_frame()

    path = tmp_path / "from_history.toml"
    app._save_preset_to(str(path), preset=snapshot.config["preset"])
    from mosaic.presets import read_preset
    written, _warnings = read_preset(path)
    assert written["holistic_compactness"]["weight"] == stored_weight
    assert written["holistic_compactness"]["enabled"] is True


def test_the_offer_appears_only_when_there_is_something_to_restore(loaded,
                                                                   history):
    app = loaded
    # Nothing saved yet: the offer stays away and marks itself done.
    app._history_offer_pending = True
    app._offer_restore_last_session()
    assert not dpg.does_item_exist("popup_history_offer")
    assert app._history_offer_pending is False

    dpg.set_value(app._iterations, 808)
    saved = history.save(app._snapshot_config(), trigger="exit")
    app._history_offer_pending = True
    app._offer_restore_last_session()
    app.run_frame()
    assert dpg.is_item_shown("popup_history_offer")
    # Dismissible, and not modal: it must not block the window behind it.
    assert not dpg.get_item_configuration("popup_history_offer")["modal"]
    assert app._history_offer_id == saved.id

    dpg.set_value(app._iterations, 3)
    app._on_history_offer_restore()
    app.run_frame()
    assert not dpg.is_item_shown("popup_history_offer")
    assert dpg.get_value(app._iterations) == 808
    dpg.delete_item("popup_history_offer")
    app.run_frame()
