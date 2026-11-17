"""Visuals: where cards go and what is saved. No display needed."""
from __future__ import annotations

from mosaic.gui import visuals as V

ROW = (300, 700, 900, 200)


def test_catalogue_covers_the_design():
    titles = {v.title for v in V.CATALOG}
    assert {"Likely seats", "Districts by party share", "Win chance", "Score",
            "What is driving the score", "Temperature", "Entropy",
            "Comet plot", "District table", "Batch histogram",
            "Batch scatterplot"} == titles
    assert all(v.shows.endswith(".") and "\n" not in v.shows for v in V.CATALOG)
    assert V.DEFAULT_ROW == ("score", "party_share", "likely_seats")
    assert all(V.BY_KEY[k].dockable for k in V.DEFAULT_ROW)


def test_missing_says_what_is_needed():
    seats = V.BY_KEY["likely_seats"]
    assert V.missing(seats, {"elections": True}) is None
    assert V.missing(seats, {}) is None                 # no map yet: unknown
    assert "election" in V.missing(seats, {"elections": False})
    assert "batch" in V.missing(V.BY_KEY["batch_hist"], {"batch": False})
    assert V.missing(V.BY_KEY["score"], {"elections": False}) is None


def test_slots_fill_the_row_without_overlap():
    rects = V.slot_rects(ROW, 3)
    assert rects[0][0] == 300 and rects[-1][0] + rects[-1][2] <= 300 + 900
    for a, b in zip(rects, rects[1:], strict=False):
        assert a[0] + a[2] + V.GAP == b[0]
    assert all(r[1] == 700 and r[3] == 200 and r[2] >= V.MIN_W for r in rects)
    assert V.slot_rects(ROW, 0) == []


def test_capacity_respects_the_minimum_width():
    assert V.capacity(900) == 4
    assert V.capacity(100) == 1
    n = V.capacity(900)
    last = V.slot_rects(ROW, n)[-1]
    assert last[0] + last[2] <= ROW[0] + ROW[2]


def test_drop_slot():
    assert V.drop_slot(ROW, 3, (10, 10)) is None
    assert V.drop_slot(ROW, 3, (310, 750)) == 0
    assert V.drop_slot(ROW, 3, (750, 750)) == 1
    assert V.drop_slot(ROW, 3, (1190, 750)) == 2
    assert V.drop_slot(ROW, 3, (750, 950)) is None


def test_new_visual_docks_then_floats_when_the_row_is_full():
    s = V.VisualsState()
    s.add("temperature", row_capacity=4)
    assert s.row[-1] == "temperature"
    s.add("entropy", row_capacity=4)
    assert "entropy" in s.floating and "entropy" not in s.row
    s.add("entropy", row_capacity=9)                     # already shown
    assert "entropy" not in s.row
    s.add("batch_hist", row_capacity=9)                  # cannot dock
    assert "batch_hist" in s.floating


def test_drag_out_and_back():
    s = V.VisualsState()
    s.drop("party_share", ROW, (500, 300), (480, 290), (400, 200))
    assert s.row == ["score", "likely_seats"]
    assert s.floating["party_share"] == {"pos": [480, 290],
                                         "size": list(V.FLOAT_SIZE)}
    # Resized while floating, then moved: keeps its size.
    s.floating["party_share"]["size"] = [420, 310]
    s.drop("party_share", ROW, (60, 60), (40, 50), (420, 310))
    assert s.floating["party_share"] == {"pos": [40, 50], "size": [420, 310]}
    # Dropped at the left end of the row: first slot.
    s.drop("party_share", ROW, (305, 720), (300, 700), (420, 310))
    assert s.row == ["party_share", "score", "likely_seats"] and not s.floating


def test_reorder_within_the_row():
    s = V.VisualsState()
    s.drop("score", ROW, (1195, 750), (0, 0), (10, 10))
    assert s.row == ["party_share", "likely_seats", "score"]


def test_pinned_card_does_not_move():
    s = V.VisualsState()
    s.pinned.add("score")
    s.drop("score", ROW, (50, 50), (40, 40), (300, 240))
    assert s.row[0] == "score" and not s.floating
    s.float_at("score", (40, 40), (300, 240))
    s.drop("score", ROW, (310, 750), (300, 700), (300, 240))
    assert "score" in s.floating


def test_full_row_refuses_a_drop():
    s = V.VisualsState(row=["score", "party_share", "likely_seats", "entropy"])
    s.float_at("temperature", (10, 10), (300, 240))
    s.drop("temperature", ROW, (500, 750), (490, 740), (300, 240))
    assert "temperature" in s.floating and len(s.row) == 4


def test_overflow_floats_the_last_unpinned_card():
    s = V.VisualsState(row=["score", "party_share", "likely_seats"])
    s.pinned.add("likely_seats")
    assert s.overflow(2) == ["party_share"]
    assert s.row == ["score", "likely_seats"]
    assert s.overflow(2) == []


def test_remove_clears_everything_about_a_card():
    s = V.VisualsState()
    s.pinned.add("score")
    s.remove("score")
    assert not s.is_shown("score") and "score" not in s.pinned


def test_clamp_keeps_a_card_reachable_and_big_enough():
    pos, size = V.clamp_rect((5000, -40), (20, 20), (1440, 900))
    assert size == [V.MIN_W, V.MIN_H]
    assert 0 <= pos[0] <= 1440 - 60 and 0 <= pos[1] <= 900 - 30


def test_state_round_trips():
    s = V.VisualsState(row=["score", "entropy"])
    s.float_at("comet", (40, 50), (333, 222))
    s.pinned.update({"comet", "score"})
    back = V.VisualsState.from_json(s.to_json())
    assert (back.row, back.floating, back.pinned) == (s.row, s.floating, s.pinned)


def test_bad_saved_state_is_dropped_not_fatal():
    assert V.VisualsState.from_json(None).row == list(V.DEFAULT_ROW)
    assert V.VisualsState.from_json({"row": "score"}).row == list(V.DEFAULT_ROW)
    s = V.VisualsState.from_json({
        "row": ["score", "nope", "score", "batch_hist"],
        "floating": {"comet": {"pos": [1, 2], "size": [10, 10]},
                     "entropy": {"pos": "x"}, "score": {"pos": [0, 0],
                                                        "size": [300, 300]},
                     "gone": {}},
        "pinned": ["comet", "entropy", 7]})
    assert s.row == ["score"]
    assert s.floating == {"comet": {"pos": [1, 2],
                                    "size": [V.MIN_W, V.MIN_H]}}
    assert s.pinned == {"comet"}
    # An empty row is a real choice and is kept.
    assert V.VisualsState.from_json({"row": []}).row == []
