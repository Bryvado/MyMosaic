"""Window geometry and the fill-opacity blend: no display needed."""

from __future__ import annotations

import numpy as np
import pytest

from mosaic.gui import layout
from mosaic.gui.layout import blend_fill, column_widths, map_box_height


@pytest.mark.parametrize("width", [1080, 1440, 1600, 1843, 1920, 2560, 3840])
def test_the_three_columns_fit_the_window(width):
    setup, centre, scores = column_widths(width, gaps=36)
    assert setup + centre + scores + 36 <= width


@pytest.mark.parametrize("width", [1440, 1843, 2560])
def test_the_map_is_the_widest_column(width):
    setup, centre, scores = column_widths(width)
    assert centre > scores > setup


def test_outer_columns_stay_inside_their_ranges():
    for width in range(900, 4000, 37):
        setup, centre, scores = column_widths(width)
        assert layout.SETUP_MIN <= setup <= layout.SETUP_MAX
        assert layout.SCORES_MIN <= scores <= layout.SCORES_MAX
        assert centre >= layout.MAP_MIN


def test_extra_width_goes_to_the_map_once_the_outer_columns_are_full():
    a = column_widths(2400)
    b = column_widths(2800)
    assert (a[0], a[2]) == (b[0], b[2]) == (layout.SETUP_MAX, layout.SCORES_MAX)
    assert b[1] - a[1] == 400


def test_everything_fits_down_to_the_minimum_window():
    for width in range(layout.MIN_WINDOW_WIDTH, 1500):
        assert sum(column_widths(width)) + 36 <= width


def test_the_app_enforces_that_minimum():
    from mosaic.gui.app._common import _MIN_CLIENT_W
    assert _MIN_CLIENT_W >= layout.MIN_WINDOW_WIDTH


def test_at_1440_the_map_has_room_to_spare():
    setup, centre, scores = column_widths(1440)
    assert centre > layout.MAP_MIN + 200


def test_the_map_box_takes_the_height_the_rest_leaves():
    assert map_box_height(880, 60, 74, 0, 5) == 880 - 60 - 74 - 10
    with_charts = map_box_height(880, 60, 74, 160, 5)
    assert with_charts == 880 - 60 - 74 - 160 - 15


def test_the_map_box_never_collapses():
    assert map_box_height(200, 60, 74, 160, 5) == 160


# ── Fill opacity ─────────────────────────────────────────────────────────────

def test_full_opacity_changes_nothing():
    rgb = np.array([[200, 40, 40], [10, 20, 30]], dtype=np.uint8)
    assert blend_fill(rgb, (255, 255, 255, 255), 1.0) is rgb


def test_zero_opacity_is_the_background():
    rgb = np.array([[200, 40, 40], [10, 20, 30]], dtype=np.uint8)
    out = blend_fill(rgb, (28, 30, 34, 255), 0.0)
    assert (out == np.array([28, 30, 34])).all()


def test_half_opacity_is_halfway_to_the_background():
    out = blend_fill(np.array([[200, 100, 0]], dtype=np.uint8),
                     (0, 0, 0, 255), 0.5)
    assert out.tolist() == [[100, 50, 0]]
    assert out.dtype == np.uint8


def test_opacity_is_clamped():
    rgb = np.array([[200, 100, 0]], dtype=np.uint8)
    assert blend_fill(rgb, (0, 0, 0, 255), 7.0) is rgb
    assert (blend_fill(rgb, (9, 9, 9, 255), -1.0) == 9).all()
