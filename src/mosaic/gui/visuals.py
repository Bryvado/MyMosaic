"""Visuals: the catalogue, where cards sit, and what is saved.

No Dear PyGui here. The app's VisualsMixin turns these answers into windows.

A visual is either *docked* (the app places it in a slot of the Visuals row)
or *floating* (the person placed it; it has its own position and size).
"""
from __future__ import annotations

from dataclasses import dataclass, field

MIN_W, MIN_H = 180, 140          # smallest card, from map.md
ROW_H = 190                      # height of the docked row's slots
GAP = 8
FLOAT_SIZE = (300, 240)          # a card's size when first pulled out of the row

NEEDS = {
    "elections": "Needs election results: a Democratic and a Republican "
                 "vote column.",
    "batch": "Needs a batch of runs. Start one from Advanced > Batch Runs.",
}


@dataclass(frozen=True)
class Visual:
    key: str
    title: str
    shows: str                   # the one line under the title
    panel: str                   # tag of the window that holds the chart
    needs: str | None = None     # key into NEEDS
    dockable: bool = True


CATALOG: tuple[Visual, ...] = (
    Visual("likely_seats", "Likely seats",
           "The chance (%) of each Democratic seat count.",
           "panel_likely_seats", "elections"),
    Visual("party_share", "Districts by party share",
           "Districts ordered by Democratic two-party share, with a 50% line.",
           "panel_partisanship", "elections"),
    Visual("win_chance", "Win chance",
           "Modeled Democratic win probability per district.",
           "panel_win_chance", "elections"),
    Visual("score", "Score", "The weighted total over the run.", "panel_score"),
    Visual("contrib", "What is driving the score",
           "Each goal's share of the total beside its set strength.",
           "panel_score_contrib"),
    Visual("temperature", "Temperature",
           "Annealing temperature over the run.", "panel_temperature"),
    Visual("entropy", "Entropy",
           "The rolling rate of accepted worse steps.", "panel_entropy"),
    Visual("comet", "Comet plot",
           "Two chosen measures traced against each other.", "panel_phase"),
    Visual("district_table", "District table",
           "Population, deviation, shares and compactness per district.",
           "panel_district_info"),
    Visual("batch_hist", "Batch histogram",
           "Any summary metric across all runs, with the median marked.",
           "popup_ens_hist", "batch", dockable=False),
    Visual("batch_scatter", "Batch scatterplot",
           "Two summary metrics across runs.",
           "popup_ens_scatter", "batch", dockable=False),
)
BY_KEY = {v.key: v for v in CATALOG}
DEFAULT_ROW = ("score", "party_share", "likely_seats")


def missing(visual: Visual, has: dict) -> str | None:
    """What ``visual`` needs that is absent, as a sentence, or None.

    ``has`` maps a need to True/False; a need not in it is not yet known
    (no map loaded) and does not block."""
    if visual.needs and has.get(visual.needs) is False:
        return NEEDS[visual.needs]
    return None


# ── Geometry ─────────────────────────────────────────────────────────────────

Rect = tuple[int, int, int, int]            # x, y, w, h


def capacity(row_w: int, gap: int = GAP) -> int:
    """How many cards fit in the row at the minimum width."""
    return max(1, (int(row_w) + gap) // (MIN_W + gap))


def slot_rects(row: Rect, n: int, gap: int = GAP) -> list[Rect]:
    """Equal slots for ``n`` docked cards across the row, left to right."""
    x, y, w, h = row
    if n <= 0:
        return []
    width = max(MIN_W, (w - gap * (n - 1)) // n)
    return [(x + i * (width + gap), y, width, h) for i in range(n)]


def drop_slot(row: Rect, n_docked: int, point: tuple[float, float],
              gap: int = GAP) -> int | None:
    """Where a card dropped at ``point`` lands in the row: an index into the
    docked order once the card is in it, or None when the point is outside the
    row. ``n_docked`` counts the cards in the row after the drop."""
    x, y, w, h = row
    px, py = point
    if not (x <= px <= x + w and y <= py <= y + h) or n_docked <= 0:
        return None
    rects = slot_rects(row, n_docked, gap)
    for i, (sx, _sy, sw, _sh) in enumerate(rects):
        if px < sx + sw + gap / 2:
            return i
    return n_docked - 1


def clamp_rect(pos, size, bounds: tuple[int, int]) -> tuple[list[int], list[int]]:
    """A floating card's position and size, kept at least the minimum size and
    with its header reachable inside a window of ``bounds``."""
    bw, bh = bounds
    w = max(MIN_W, min(int(size[0]), max(MIN_W, bw)))
    h = max(MIN_H, min(int(size[1]), max(MIN_H, bh)))
    x = max(0, min(int(pos[0]), max(0, bw - 60)))
    y = max(0, min(int(pos[1]), max(0, bh - 30)))
    return [x, y], [w, h]


# ── State ────────────────────────────────────────────────────────────────────

@dataclass
class VisualsState:
    """Which visuals are on screen and where. ``row`` is the docked order;
    ``floating`` maps a key to {"pos": [x, y], "size": [w, h]}."""
    row: list[str] = field(default_factory=lambda: list(DEFAULT_ROW))
    floating: dict[str, dict] = field(default_factory=dict)
    pinned: set[str] = field(default_factory=set)

    def shown(self) -> list[str]:
        return list(self.row) + list(self.floating)

    def is_shown(self, key: str) -> bool:
        return key in self.row or key in self.floating

    def add(self, key: str, row_capacity: int, float_at=(120, 120)) -> None:
        """A new visual docks; it floats when the row is full or it cannot
        dock."""
        if self.is_shown(key) or key not in BY_KEY:
            return
        if BY_KEY[key].dockable and len(self.row) < row_capacity:
            self.row.append(key)
        else:
            self.floating[key] = {"pos": list(float_at),
                                  "size": list(FLOAT_SIZE)}

    def remove(self, key: str) -> None:
        if key in self.row:
            self.row.remove(key)
        self.floating.pop(key, None)
        self.pinned.discard(key)

    def float_at(self, key: str, pos, size) -> None:
        if key in self.row:
            self.row.remove(key)
        self.floating[key] = {"pos": [int(pos[0]), int(pos[1])],
                              "size": [int(size[0]), int(size[1])]}

    def dock(self, key: str, index: int) -> None:
        if not BY_KEY[key].dockable:
            return
        self.floating.pop(key, None)
        if key in self.row:
            self.row.remove(key)
        self.row.insert(max(0, min(index, len(self.row))), key)

    def drop(self, key: str, row: Rect, point, pos, size,
             gap: int = GAP) -> None:
        """A card let go at ``point`` (the pointer) with its window at
        ``pos``/``size``: into the row if over it, floating otherwise. A
        pinned card does not move."""
        if key in self.pinned or not self.is_shown(key):
            return
        was_docked = key in self.row
        n_after = len(self.row) + (0 if was_docked else 1)
        slot = (drop_slot(row, n_after, point, gap)
                if BY_KEY[key].dockable and n_after <= capacity(row[2], gap)
                else None)
        if slot is not None:
            self.dock(key, slot)
        else:
            self.float_at(key, pos, FLOAT_SIZE if was_docked else size)

    def overflow(self, row_capacity: int, origin=(120, 120)) -> list[str]:
        """Float unpinned cards the row no longer has room for; returns them."""
        moved = []
        while len(self.row) > row_capacity:
            movable = [k for k in reversed(self.row) if k not in self.pinned]
            if not movable:
                break
            key = movable[0]
            step = 24 * len(moved)
            self.float_at(key, (origin[0] + step, origin[1] + step), FLOAT_SIZE)
            moved.append(key)
        return moved

    def to_json(self) -> dict:
        return {"row": list(self.row),
                "floating": {k: {"pos": list(v["pos"]), "size": list(v["size"])}
                             for k, v in self.floating.items()},
                "pinned": sorted(self.pinned)}

    @classmethod
    def from_json(cls, data) -> VisualsState:
        """Saved state, dropping anything unknown or malformed. Bad input
        gives the default row."""
        if not isinstance(data, dict) or not isinstance(data.get("row"), list):
            return cls()
        row = []
        for key in data["row"]:
            if key in BY_KEY and BY_KEY[key].dockable and key not in row:
                row.append(key)
        floating = {}
        raw = data.get("floating")
        for key, item in (raw.items() if isinstance(raw, dict) else ()):
            if key not in BY_KEY or key in row or not isinstance(item, dict):
                continue
            try:
                pos = [int(item["pos"][0]), int(item["pos"][1])]
                size = [max(MIN_W, int(item["size"][0])),
                        max(MIN_H, int(item["size"][1]))]
            except (KeyError, IndexError, TypeError, ValueError):
                continue
            floating[key] = {"pos": pos, "size": size}
        pinned = data.get("pinned")
        pins = {k for k in (pinned if isinstance(pinned, list) else ())
                if k in row or k in floating}
        return cls(row=row, floating=floating, pinned=pins)
