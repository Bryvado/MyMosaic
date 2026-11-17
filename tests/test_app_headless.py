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
