"""Fill opacity fades the fill and nothing drawn over it.

MapView composes a frame as an array without a window, so this runs bare. The
same compose path serves the screen and the PNG/PDF export, which copies the
setting onto its own offscreen MapView.
"""

from __future__ import annotations

import re
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest

from mosaic.gui.map_style import MapStyle
from mosaic.gui.map_view import MapView

ROOT = Path(__file__).resolve().parents[1]
SHAPEFILE = ROOT / "shapefiles" / "North_Carolina_Simplified.shp"


@pytest.fixture(scope="module")
def loaded():
    gdf = gpd.read_file(SHAPEFILE)
    _, county = np.unique(gdf["CTY"].values, return_inverse=True)
    view = MapView("unused", 400, 200)
    assert view.load(gdf, county_array=county.astype(np.int32),
                     dem_votes=gdf["DEM"].values.astype(np.int64),
                     gop_votes=gdf["REP"].values.astype(np.int64))
    # Six bands, west to east, so there are district borders to look at.
    order = np.argsort(gdf.geometry.bounds["minx"].values)
    assignment = np.empty(len(gdf), dtype=np.int32)
    assignment[order] = np.arange(len(gdf)) * 6 // len(gdf)
    return view, assignment


def _frame(view, assignment, opacity, **flags):
    # Whole-pixel widths, so every line pixel is solid colour and the tests
    # can tell a line from fill by its colour alone.
    view.style = MapStyle(widths={"district": 2.0, "place": 1.0, "precinct": 1.0})
    view.line_colors = {"district": (0, 0, 0), "place": (180, 180, 180),
                        "precinct": (255, 0, 255)}
    for name in ("county_overlay", "precinct_overlay", "show_labels",
                 "district_partisan_overlay", "partisan_overlay"):
        setattr(view, name, flags.get(name, False))
    view.fill_opacity = opacity
    return view.compose_rgba(assignment, 6).astype(np.int32)


def test_fill_fades_toward_the_background(loaded):
    view, assignment = loaded
    solid = _frame(view, assignment, 1.0)
    faded = _frame(view, assignment, 0.4)
    bg = view._bg_color[:3].astype(np.int32)
    inside = view._pixel_map >= 0
    lines = (solid[..., :3] == 0).all(axis=-1)          # district borders
    fill = inside & ~lines
    want = np.rint(bg + (solid[fill][:, :3] - bg) * 0.4)
    assert np.abs(faded[fill][:, :3] - want).max() <= 1
    # The page outside the map is untouched.
    assert (faded[~inside] == solid[~inside]).all()


def test_district_borders_stay_solid(loaded):
    view, assignment = loaded
    solid = _frame(view, assignment, 1.0)
    faded = _frame(view, assignment, 0.2)
    borders = (solid[..., :3] == 0).all(axis=-1)
    assert borders.sum() > 100
    assert (faded[borders][:, :3] == 0).all()


def test_place_lines_stay_solid(loaded):
    view, assignment = loaded
    plain = _frame(view, assignment, 1.0)
    solid = _frame(view, assignment, 1.0, county_overlay=True)
    faded = _frame(view, assignment, 0.3, county_overlay=True)
    lines = (solid != plain).any(axis=-1)
    assert lines.sum() > 200
    assert (faded[lines] == solid[lines]).all()


def test_precinct_lines_stay_solid(loaded):
    """Fading the fill must not fade the lines drawn over it."""
    view, assignment = loaded
    under = _frame(view, assignment, 0.3)
    over = _frame(view, assignment, 0.3, precinct_overlay=True)
    lines = (over != under).any(axis=-1)
    assert lines.sum() > 500
    assert (over[lines][:, :3] == (255, 0, 255)).all()


def test_labels_stay_solid(loaded):
    view, assignment = loaded
    plain = _frame(view, assignment, 1.0)
    solid = _frame(view, assignment, 1.0, show_labels=True)
    faded = _frame(view, assignment, 0.3, show_labels=True)
    # Fully covered glyph pixels; the antialiased rim blends with what is
    # under it by nature, at any fill opacity.
    changed = (solid != plain).any(axis=-1)
    core = changed & ((solid[..., :3] == 255).all(axis=-1)
                      | (solid[..., :3] == 0).all(axis=-1))
    assert core.sum() > 20
    assert (faded[core] == solid[core]).all()


@pytest.mark.parametrize("mode", ["district_partisan_overlay", "partisan_overlay"])
def test_opacity_applies_to_other_fill_modes(loaded, mode):
    view, assignment = loaded
    solid = _frame(view, assignment, 1.0, **{mode: True})
    faded = _frame(view, assignment, 0.5, **{mode: True})
    inside = view._pixel_map >= 0
    assert (faded[inside] != solid[inside]).any()
    lines = (solid[..., :3] == 0).all(axis=-1)
    assert (faded[lines][:, :3] == 0).all()


def test_exports_copy_the_setting():
    """The offscreen MapView behind PNG/PDF export copies a named list of
    attributes from the on-screen one; fill_opacity has to be on it."""
    text = (ROOT / "src/mosaic/gui/app/export_mixin.py").read_text(encoding="utf-8")
    block = re.search(r"for flag in \((.*?)\):\s*\n\s*setattr\(offscreen", text,
                      re.DOTALL)
    assert block, "the export's attribute copy moved; update this test"
    assert '"fill_opacity"' in block.group(1)


def test_presets_do_not_carry_fill_opacity():
    from mosaic.presets import SCHEMA
    assert not any("opacity" in key for keys in SCHEMA.values() for key in keys)
