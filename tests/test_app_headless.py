"""The real window, built headless: can it open a map, and does every widget
the code reaches for still exist?

Both tests answer a failure that was silent. Dear PyGui swallows an exception
raised inside a widget callback, so when the redesign removed two widgets that
the open-shapefile path still wrote to, choosing a file simply did nothing:
no error, no crash log.
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


def _pump(app, until, timeout: float = 180.0) -> bool:
    """Run real frames until `until()` holds; False on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.run_frame()
        if until():
            return True
    return False


def test_opening_a_shapefile_loads_it(app):
    """Through _on_shapefile_selected, the file dialog's own callback, so the
    whole path runs: reset, clear file state, inspect, confirm columns, load."""
    app._on_shapefile_selected(None, {"file_path_name": str(SHAPEFILE)})

    assert _pump(app, lambda: dpg.is_item_shown("shp_dialog")
                 or app.state.get("status")[0] == AlgorithmStatus.ERROR), (
        "the column dialog never appeared")
    assert app.state.get("status")[0] != AlgorithmStatus.ERROR, (
        app.state.error_message)
    inspection = app.runner._pending_inspection
    assert inspection is not None and inspection.n_precincts == 2666

    # Confirm with the columns the dialog detected, as a person would.
    app._shp_dialog._on_confirm_click()
    assert _pump(app, lambda: (app.runner.graph is not None and app._map_ready)
                 or app.state.get("status")[0] == AlgorithmStatus.ERROR), (
        "the map never finished loading")
    assert app.state.get("status")[0] != AlgorithmStatus.ERROR, (
        app.state.error_message)

    assert len(app.runner.populations) == 2666
    assert app.runner.graph.number_of_nodes() == 2666
    assert dpg.get_value(app._shp_info) == SHAPEFILE.stem
    # The readouts the reset path writes, in the redesigned layout.
    assert dpg.get_value(app._iter_txt).startswith("0 / ")
    assert dpg.get_value(app._alignment_info) == "none"
    assert app._run_tiles_lit == 0


def test_reset_and_revert_reach_only_widgets_that_exist(app):
    """The two other callers of the removed progress bar."""
    app._on_reset()
    app._on_clear_alignment()
    app._update_run_tiles(0.5)
    assert app._run_tiles_lit == 10
    app._on_reset()
    assert app._run_tiles_lit == 0


# ── Widget attribute audit ───────────────────────────────────────────────────

_REF = re.compile(r"\bself\.(_[A-Za-z0-9_]+)")
# Every way the app's source binds an attribute on itself.
_ASSIGN = (
    re.compile(r"\bself\.(_[A-Za-z0-9_]+)\s*(?::[^=\n]+)?=(?!=)"),   # self._x = / : T =
    re.compile(r"\bas\s+self\.(_[A-Za-z0-9_]+)"),                    # with ... as self._x
    re.compile(r"setattr\(\s*self\s*,\s*[\"'](_[A-Za-z0-9_]+)[\"']"),
)
# Deliberately no pattern for tuple targets (`self._a, self._b = ...`): the
# loose form also matches `f(self._a, ...)`, which would count every argument
# as an assignment and let the very orphans this hunts for through.


def _mixin_sources():
    # _common.py holds helper classes with their own `self`, not the app's.
    return [p for p in sorted(APP_SRC.glob("*.py")) if p.name != "_common.py"]


def test_every_referenced_attribute_exists_or_is_created_lazily(app):
    """No `self._x` in the app may point at nothing.

    Exists-after-setup() is the first test. It cannot be the only one: about a
    hundred attributes belong to windows built on first use (Batch Runs, the
    roster, the scatterplot) and legitimately do not exist yet. Those pass the
    second test instead -- some code that still exists assigns them. An
    attribute that fails both is one whose widget was deleted while a reader
    was left behind, which is exactly how the progress bar and the
    reference-plan status went missing.
    """
    referenced: dict[str, set[str]] = {}
    assigned: set[str] = set()
    for path in _mixin_sources():
        text = path.read_text(encoding="utf-8")
        for match in _REF.finditer(text):
            referenced.setdefault(match.group(1), set()).add(path.name)
        for pattern in _ASSIGN:
            assigned.update(pattern.findall(text))

    orphans = {name: sorted(files) for name, files in referenced.items()
               if not hasattr(app, name) and name not in assigned}
    assert not orphans, (
        "referenced but never created: "
        + "; ".join(f"{n} ({', '.join(f)})" for n, f in sorted(orphans.items())))


def test_the_audit_sees_the_app(app):
    """Guard the guard: a broken regex or an empty glob would pass vacuously."""
    names = set()
    for path in _mixin_sources():
        names.update(_REF.findall(path.read_text(encoding="utf-8")))
    assert len(names) > 300
    for known in ("_run_btn", "_iter_txt", "_alignment_info", "_shp_info"):
        assert known in names and hasattr(app, known)


def test_widget_attributes_hold_live_items(app):
    """An attribute can exist and still name a deleted item. Every attribute
    built by setup() that holds a Dear PyGui id must resolve."""
    dead = []
    for name, value in vars(app).items():
        if not name.startswith("_") or isinstance(value, bool):
            continue
        if isinstance(value, int) and value > 0 and name.endswith(
                ("_txt", "_btn", "_info", "_combo", "_enabled", "_item")):
            if not dpg.does_item_exist(value):
                dead.append(name)
    assert not dead, f"attributes naming items that no longer exist: {dead}"


# ── Layout on a real window (step 1 follow-up) ───────────────────────────────
# Each of these was wrong on a 1843 x 925 display: the map sat at a fixed
# height in a column twice as tall, the charts were clipped off the bottom, and
# the right-hand column ran off the edge.

SIZES = [(1843, 925), (1440, 900)]


def _resize(app, width, height):
    dpg.set_viewport_width(width)
    dpg.set_viewport_height(height)
    assert _pump(app, lambda: dpg.get_viewport_client_width() == width
                 and dpg.get_viewport_client_height() == height, timeout=20), (
        "the viewport would not take the requested size")
    # Two-stage settle: the columns, then the map raster redrawn to fit them.
    for _ in range(10):
        app.run_frame()
    assert _pump(app, lambda: (app._map_w, app._map_h) == app._map_target
                 and app._map_resize_job is None, timeout=60), (
        "the map never settled on the size its box asked for")
    for _ in range(5):
        app.run_frame()


def _column(tag):
    state = dpg.get_item_state(tag)
    return state["pos"], state["rect_size"]


@pytest.mark.parametrize("size", SIZES)
def test_all_three_columns_fit_the_window(app, size):
    _resize(app, *size)
    width, height = size
    right_edges = []
    for tag in ("setup_column", "map_column", "scores_column"):
        (x, y), (w, h) = _column(tag)
        assert x >= 0 and x + w <= width, f"{tag} runs past the window edge"
        assert y + h <= height, f"{tag} runs past the bottom of the window"
        right_edges.append(x + w)
    assert right_edges == sorted(right_edges)
    # The count in the scores heading was the first thing to be cut off.
    assert dpg.get_item_state(app._scores_count)["rect_max"][0] <= width
    # And no row's value column either.
    for row in app._score_rows.values():
        assert dpg.get_item_state(row["value"])["rect_max"][0] <= width


@pytest.mark.parametrize("size", SIZES)
def test_the_map_fills_the_centre_column(app, size):
    _resize(app, *size)
    sp = app.theme.palette.spacing
    (_, _), (col_w, col_h) = _column("map_column")
    (_, box_y), (box_w, box_h) = _column("map_container")
    canvas_w, canvas_h = dpg.get_item_rect_size("map_canvas")
    # The raster is the box, less the box's own padding and border.
    assert canvas_w == box_w - 2 * sp.window_padding[0] - 2 * sp.child_border_size
    assert canvas_h == box_h - 2 * sp.window_padding[1] - 2 * sp.child_border_size
    assert (app.map_view._w, app.map_view._h) == (canvas_w, canvas_h)
    # It is the largest thing in the column, not a strip at the top of it.
    assert box_h > 0.45 * col_h
    assert box_h > 1.5 * dpg.get_item_rect_size("visuals_row")[1]
    assert box_w >= col_w - 2
    # Nothing under the map is pushed out of the column.
    (_, strip_y), (_, strip_h) = _column("run_strip")
    assert strip_y + strip_h <= col_h


def test_the_map_refits_when_the_window_changes(app):
    _resize(app, 1843, 925)
    big = (app._map_w, app._map_h)
    _resize(app, 1440, 900)
    small = (app._map_w, app._map_h)
    assert small[0] < big[0] and small != big
    _resize(app, 1843, 925)
    assert (app._map_w, app._map_h) == big


@pytest.mark.parametrize("size", SIZES)
def test_the_toolbar_sits_above_the_map(app, size):
    _resize(app, *size)
    (_, box_y), _ = _column("map_container")
    for attr in ("_fill_combo", "_county_overlay", "_precinct_overlay",
                 "_show_labels", "_splits_view", "_fill_opacity", "_more_btn"):
        item = getattr(app, attr)
        state = dpg.get_item_state(item)
        assert dpg.is_item_shown(item) and state["visible"], attr
        assert state["rect_size"][0] > 0, attr
    # Relative to the same column: the toolbar's rows end where the map begins.
    assert dpg.get_item_rect_size("map_toolbar")[1] <= box_y
    assert dpg.get_y_scroll("map_column") == 0


def _frames(app, n=4):
    for _ in range(n):
        app.run_frame()


def _reset_visuals(app):
    from mosaic.gui.visuals import VisualsState
    app._visuals = VisualsState()
    app._visual_placed.clear()
    _frames(app)


@pytest.mark.parametrize("size", SIZES)
def test_the_default_visuals_dock_in_the_row(app, size):
    from mosaic.gui import visuals as V
    _resize(app, *size)
    _reset_visuals(app)
    assert app._visuals.row == list(V.DEFAULT_ROW)
    rx, ry, rw, rh = app._visuals_row_rect()
    last_right = rx
    for key in V.DEFAULT_ROW:
        panel = V.BY_KEY[key].panel
        assert dpg.is_item_shown(panel), key
        x, y = dpg.get_item_pos(panel)
        w, h = dpg.get_item_width(panel), dpg.get_item_height(panel)
        assert (y, h) == (int(ry), int(rh)) and w >= V.MIN_W
        assert x >= last_right - 1 and x + w <= rx + rw + 1, key
        last_right = x + w
    # The row is inside the window, under the run strip.
    assert ry + rh <= size[1]
    (_, strip_y), (_, strip_h) = _column("run_strip")
    assert dpg.get_item_state("visuals_row")["pos"][1] >= strip_y + strip_h
    # The plots that used to sit in the centre column are the Score card now.
    assert dpg.get_item_parent("score_half_plot") == "panel_score"


def test_a_visual_floats_docks_pins_and_closes(app):
    from mosaic.gui import visuals as V
    _resize(app, 1843, 925)
    _reset_visuals(app)
    row = app._visuals_row_rect()
    panel = V.BY_KEY["score"].panel

    # Let go over the map: it floats where it was dropped.
    dpg.configure_item(panel, pos=[700, 200])
    app._drop_visual("score", (720, 210), row)
    _frames(app)
    assert "score" in app._visuals.floating and "score" not in app._visuals.row
    assert dpg.get_item_pos(panel) == [700, 200]
    assert not dpg.get_item_configuration(panel)["no_resize"]
    assert dpg.get_item_configuration(panel)["min_size"] == [V.MIN_W, V.MIN_H]

    # A corner resize is picked up and kept.
    dpg.configure_item(panel, width=420, height=300)
    _frames(app)
    assert app._visuals.floating["score"]["size"] == [420, 300]

    # Pinned: it cannot be dropped anywhere else, or resized.
    app._on_visual_pin(None, None, "score")
    _frames(app)
    assert dpg.get_item_configuration(panel)["no_resize"]
    app._drop_visual("score", (row[0] + 5, row[1] + 5), row)
    _frames(app)
    assert "score" in app._visuals.floating
    app._on_visual_pin(None, None, "score")

    # Unpinned and dropped on the row: back in, at the slot under the pointer.
    app._drop_visual("score", (row[0] + row[2] - 5, row[1] + 5), row)
    _frames(app)
    assert app._visuals.row[-1] == "score" and not app._visuals.floating

    # Closed: gone from the row, and the map takes the height back when the
    # row is empty.
    before = dpg.get_item_rect_size("map_container")[1]
    for key in list(app._visuals.row):
        app._on_visual_close(None, None, key)
    _resize(app, 1843, 925)
    assert not dpg.is_item_shown(panel)
    assert dpg.get_item_rect_size("map_container")[1] > before + 150
    _reset_visuals(app)


def test_the_views_menu_and_the_picker_drive_the_same_cards(app):
    _resize(app, 1843, 925)
    _reset_visuals(app)
    dpg.set_value(app._panel_temp_item, True)
    app._on_panel_temp_toggle()
    _frames(app)
    assert app._visuals.is_shown("temperature")
    assert dpg.is_item_shown("panel_temperature")
    app._on_visual_pick(None, None, "temperature")       # picked again: removed
    _frames(app)
    assert not dpg.is_item_shown("panel_temperature")
    assert not dpg.get_value(app._panel_temp_item)
    # The picker lists every visual.
    app._open_visual_picker()
    from mosaic.gui.visuals import CATALOG
    assert len(dpg.get_item_children("visual_picker", 1)) == len(CATALOG)
    dpg.configure_item("visual_picker", show=False)
    _reset_visuals(app)


def test_a_visual_the_map_cannot_feed_is_not_added(app):
    _reset_visuals(app)
    assert app._visual_has().get("batch") is False
    app._set_visual("batch_hist", True)
    assert not dpg.does_item_exist("popup_ens_hist")
    arrays = app.runner.election_arrays
    app.runner.election_arrays = []
    try:
        app._visuals.remove("likely_seats")
        app._set_visual("likely_seats", True)
        assert not app._visuals.is_shown("likely_seats")
    finally:
        app.runner.election_arrays = arrays
        _reset_visuals(app)


def test_likely_seats_draws_the_plan_on_screen(app):
    import numpy as np
    _resize(app, 1843, 925)
    _reset_visuals(app)
    n = 14
    before = (app.state.current_assignment, app.state.num_districts)
    app.state.update(current_assignment=np.arange(2666) % n, num_districts=n)
    app._likely_at = 0.0
    app._likely_sig = None
    try:
        _frames(app)
    finally:
        app.state.update(current_assignment=before[0], num_districts=before[1])
    xs, ys = [], []
    for series in app._likely_series:
        data = dpg.get_value(series)
        xs += list(data[0])
        ys += list(data[1])
    assert sorted(xs) == list(range(n + 1))
    assert np.isclose(sum(ys), 100.0, atol=1e-6)
    assert "Most likely" in dpg.get_value(app._likely_note)


@pytest.mark.parametrize("size", SIZES)
def test_goals_and_map_panels_do_not_scroll(app, size):
    _resize(app, *size)
    for tag in ("panel_goals", "panel_map_setup"):
        assert dpg.get_y_scroll_max(tag) == 0, f"{tag} has a scrollbar"


def test_the_weight_shows_once(app):
    """A slider with no value text, and the number field beside it."""
    for attr, field in app._score_fields.items():
        assert dpg.get_item_configuration(getattr(app, attr))["format"] == "", attr
        assert dpg.get_value(field) == dpg.get_value(getattr(app, attr)), attr


def test_start_from_does_not_repeat_its_label(app):
    app._update_start_mode_display()
    assert dpg.get_value(app._hot_start_info) == "Random partition"


# ── Fill opacity and the basemap ─────────────────────────────────────────────

@pytest.fixture
def style_file(app, tmp_path, monkeypatch):
    """Point map-style saving at a temporary file, and put it back after."""
    from mosaic.gui.app import map_mixin
    from mosaic.gui.app.core import MosaicApp

    path = tmp_path / "map_style.json"
    monkeypatch.setattr(map_mixin, "_SETTINGS_DIR", tmp_path)
    monkeypatch.setattr(map_mixin, "_MAP_STYLE_FILE", path)
    saved = (app._fill_opacity_value, app._fill_opacity_basemap,
             dict(app._basemap_choice), dpg.get_value(app._basemap_chk))
    app._save_map_style = lambda: MosaicApp._save_map_style(app)
    yield path
    app._save_map_style = lambda: None
    app._fill_opacity_value, app._fill_opacity_basemap = saved[0], saved[1]
    app._basemap_choice = saved[2]
    dpg.set_value(app._basemap_chk, saved[3])
    app._apply_fill_opacity()


def test_fill_opacity_reaches_the_map_and_is_saved(app, style_file):
    assert dpg.get_item_configuration(app._fill_opacity)["max_value"] == 100
    dpg.set_value(app._basemap_chk, False)
    app._on_basemap_toggle()
    dpg.set_value(app._fill_opacity, 80)
    app._on_fill_opacity()
    assert app.map_view.fill_opacity == pytest.approx(0.80)
    assert '"fill_opacity": 0.8' in style_file.read_text()
    # Read back the way the next launch would.
    app._fill_opacity_value = 1.0
    app._load_map_style()
    assert app._fill_opacity_value == pytest.approx(0.80)


def test_opacity_is_45_over_a_basemap_and_100_without(app, style_file):
    """Two settings behind one slider, so switching the basemap off does not
    leave a washed-out map behind."""
    from mosaic.gui.app.basemap_mixin import BASEMAP_OPACITY

    app._fill_opacity_value, app._fill_opacity_basemap = 1.0, BASEMAP_OPACITY
    dpg.set_value(app._basemap_chk, True)
    app._on_basemap_toggle()
    assert dpg.get_value(app._fill_opacity) == 45
    assert app.map_view.fill_opacity == pytest.approx(0.45)
    dpg.set_value(app._basemap_chk, False)
    app._on_basemap_toggle()
    assert dpg.get_value(app._fill_opacity) == 100
    assert app.map_view.fill_opacity == 1.0
    # Changing one leaves the other alone.
    dpg.set_value(app._fill_opacity, 70)
    app._on_fill_opacity()
    dpg.set_value(app._basemap_chk, True)
    app._on_basemap_toggle()
    assert dpg.get_value(app._fill_opacity) == 45


def test_basemap_is_on_for_districts_and_off_for_other_fills(app, style_file):
    from mosaic.gui.app._common import _FILL_NONE, _FILL_OPTIONS

    app._basemap_choice = {}
    dpg.set_value(app._fill_combo, _FILL_NONE)
    app._on_fill_combo()
    assert dpg.get_value(app._basemap_chk) is True
    labels = dpg.get_item_configuration(app._fill_combo)["items"]
    other = next(label for label in labels
                 if any(label.startswith(name) for name, _a, _n in _FILL_OPTIONS)
                 and "(" not in label)
    dpg.set_value(app._fill_combo, other)
    app._on_fill_combo()
    assert dpg.get_value(app._basemap_chk) is False, other
    # The user can still turn it on for that fill, and it is remembered there.
    dpg.set_value(app._basemap_chk, True)
    app._on_basemap_toggle()
    dpg.set_value(app._fill_combo, _FILL_NONE)
    app._on_fill_combo()
    dpg.set_value(app._fill_combo, other)
    app._on_fill_combo()
    assert dpg.get_value(app._basemap_chk) is True
    dpg.set_value(app._fill_combo, _FILL_NONE)
    app._on_fill_combo()


def test_offline_draws_without_a_basemap_and_says_so(app, style_file):
    dpg.set_value(app._basemap_chk, True)
    app._on_basemap_toggle()
    app._basemap_key = None
    assert _pump(app, lambda: app._basemap_key is not None
                 and app._basemap_job is None, timeout=30)
    assert app.map_view.basemap is None
    message = app.state.get("status_message")[0]
    assert "Basemap unavailable" in message and "Drawing without it" in message
    # The map still draws.
    app._redraw_map_now()
    assert app.map_view._last_rgba is not None


def test_basemap_is_painted_off_the_frame_loop_and_credited(app, style_file, tmp_path):
    from test_basemap import FakeStore

    offline = app._basemap_store
    app._basemap_store = FakeStore(tmp_path / "tiles", (-79.0, 35.5))
    try:
        dpg.set_value(app._basemap_chk, True)
        app._on_basemap_toggle()
        app._basemap_key = None
        frames = 0
        while app.map_view.basemap is None and frames < 2000:
            app.run_frame()
            frames += 1
        raster = app.map_view.basemap
        assert raster is not None and raster.drawn
        assert raster.key == (app.map_view._w, app.map_view._h,
                              tuple(app.map_view._view_bounds))
        assert app._basemap_executor is not None      # painted by the worker
        assert dpg.is_item_shown(app._basemap_credit)
        assert "OpenFreeMap" in dpg.get_value(app._basemap_credit_text)
        # The road through the fake world is in the frame the map draws.
        app._redraw_map_now()
        frame = app.map_view._last_rgba[..., :3]
        outside = app.map_view._pixel_map < 0
        road = tuple(app.theme.palette.basemap_road_major[:3])
        ground = tuple(app.theme.palette.basemap_ground[:3])
        assert (frame[outside] == road).all(axis=-1).sum() > 100
        assert (frame[outside] == ground).all(axis=-1).mean() > 0.9
        # Switching it off removes the credit and the basemap from the frame.
        dpg.set_value(app._basemap_chk, False)
        app._on_basemap_toggle()
        for _ in range(3):
            app.run_frame()
        assert not dpg.is_item_shown(app._basemap_credit)
        assert app.map_view.basemap_on is False
    finally:
        app._basemap_store = offline
        app._basemap_key = None


def test_a_missing_or_broken_style_file_keeps_the_defaults(app, style_file):
    for content in (None, "not json", '{"fill_opacity": 7}', '{"other": 1}', "[1]"):
        if content is not None:
            style_file.write_text(content)
        app._fill_opacity_value, app._fill_opacity_basemap = 1.0, 0.45
        app._load_map_style()
        assert (app._fill_opacity_value, app._fill_opacity_basemap) == (1.0, 0.45)


# ── Map style row ────────────────────────────────────────────────────────────

def test_map_style_row_sits_under_the_map(app):
    _resize(app, 1440, 900)
    (_, box_y), (_, box_h) = _column("map_container")
    row_y = dpg.get_item_state("map_style_row")["pos"][1]
    (_, strip_y), (_, strip_h) = _column("run_strip")
    (_, _), (_, col_h) = _column("map_column")
    assert box_y + box_h <= row_y < strip_y
    assert strip_y + strip_h <= col_h
    assert dpg.get_item_rect_size("map_style_row")[0] <= _column("map_column")[1][0]


def test_width_and_colour_reach_the_map_and_are_saved(app, style_file):
    from mosaic.gui.map_style import MapStyle

    try:
        dpg.set_value(app._style_width["district"], 4.0)
        dpg.set_value(app._style_width["precinct"], 0.0)
        app._on_map_style()
        assert app.map_view.style.width("district") == 4.0
        assert app.map_view.style.width("precinct") == 0.0
        app._on_map_style_color(None, [10, 200, 30, 255], "place")
        assert app.map_view.style.colors["place"] == (10, 200, 30)
        saved = style_file.read_text()
        assert '"width": 4.0' in saved and "200" in saved
        # The next launch reads it back.
        app._map_style = MapStyle()
        app._load_map_style()
        assert app._map_style.width("district") == 4.0
        assert app._map_style.colors["place"] == (10, 200, 30)
    finally:
        app._on_map_style_reset()
    assert app.map_view.style.widths == {"district": 2.5, "place": 1.5,
                                         "precinct": 0.5}
    assert app.map_view.style.colors["place"] is None
    assert dpg.get_value(app._style_width["district"]) == pytest.approx(2.5)


def test_widths_are_limited_to_zero_through_eight(app):
    for kind in ("district", "place", "precinct"):
        cfg = dpg.get_item_configuration(app._style_width[kind])
        assert (cfg["min_value"], cfg["max_value"]) == (0.0, 8.0)
        assert cfg["min_clamped"] and cfg["max_clamped"]


def test_place_line_label_follows_the_place_unit(app):
    before = app._place_unit
    try:
        dpg.set_value(app._place_unit_input, "cities")
        app._on_place_unit_change()
        assert dpg.get_value(app._style_label["place"]) == "City lines"
        assert dpg.get_item_label(app._county_overlay) == "Cities"
        dpg.set_value(app._place_unit_input, "counties")
        app._on_place_unit_change()
        assert dpg.get_value(app._style_label["place"]) == "County lines"
    finally:
        dpg.set_value(app._place_unit_input, before)
        app._on_place_unit_change()


def test_default_line_colours_follow_the_theme(app):
    from mosaic.gui.map_style import default_colors
    from mosaic.gui.theme import PALETTES

    start = dpg.get_value(app._theme_radio)
    try:
        for name, label in (("dark", "Dark"), ("light", "Light")):
            dpg.set_value(app._theme_radio, label)
            app._on_theme_change()
            want = default_colors(PALETTES[name])
            assert app.map_view.line_colors == want
            shown = tuple(int(v) for v in dpg.get_value(app._style_color["district"])[:3])
            assert shown == want["district"]
    finally:
        dpg.set_value(app._theme_radio, start)
        app._on_theme_change()
