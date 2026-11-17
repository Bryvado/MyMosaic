"""Main-window geometry with no Dear PyGui import, so it can be tested bare.

The three columns used to be fixed constants (300 / rest / 468). Fixed outer
columns only fit one window size: on a narrower one the right-hand column ran
off the edge. They are derived from the window width here instead.
"""

from __future__ import annotations

# Shares of the usable width, and the range each outer column is held to. The
# map takes whatever is left, which is the point: it is the subject, and it is
# the only column that gains from every extra pixel.
SETUP_SHARE, SETUP_MIN, SETUP_MAX = 0.17, 280, 310
SCORES_SHARE, SCORES_MIN, SCORES_MAX = 0.29, 400, 500
MAP_MIN = 360

# Narrowest window all three columns fit in (with the default 36px of gaps);
# the viewport is not allowed to shrink below it. The design target is 1440.
MIN_WINDOW_WIDTH = SETUP_MIN + SCORES_MIN + MAP_MIN + 36


def column_widths(client_w: int, gaps: int = 36) -> tuple[int, int, int]:
    """(setup, map, scores) widths that together fit ``client_w``.

    ``gaps`` is everything that is not a column: the window padding on both
    sides and the spacing between the three. The outer columns scale with the
    window inside their ranges and the map takes the rest. The three fit any
    window at least MIN_WINDOW_WIDTH wide, which the app enforces as the
    viewport minimum; below it the map holds MAP_MIN and the row overflows.
    """
    usable = max(0, int(client_w) - gaps)
    setup = min(SETUP_MAX, max(SETUP_MIN, round(usable * SETUP_SHARE)))
    scores = min(SCORES_MAX, max(SCORES_MIN, round(usable * SCORES_SHARE)))
    return setup, max(MAP_MIN, usable - setup - scores), scores


def map_box_height(column_h: int, toolbar_h: int, strip_h: int,
                   charts_h: int, spacing: int) -> int:
    """Height left for the map once its neighbours in the column are placed.

    Everything else in the centre column has a fixed height, so the map box is
    the remainder. ``charts_h`` is 0 while the charts are hidden.
    """
    used = toolbar_h + strip_h + charts_h
    # One item-spacing gap after each block that is actually present.
    blocks = 2 + (1 if charts_h else 0)
    return max(160, int(column_h) - used - blocks * spacing)


def blend_fill(rgb, background, opacity: float):
    """Fade fill colours toward the map background.

    ``opacity`` 1 leaves them alone, 0 makes them the background. This is the
    fill layer's alpha composited over what lies beneath it, which until the
    basemap exists is the plain background. Works on any (..., 3) array.
    """
    import numpy as np

    a = min(1.0, max(0.0, float(opacity)))
    if a >= 1.0:
        return rgb
    base = np.asarray(background, dtype=np.float32)[:3]
    out = base + (np.asarray(rgb, dtype=np.float32) - base) * a
    return np.rint(out).astype(np.uint8)
