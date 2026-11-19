"""The configuration history store.

``mosaic.session_history`` keeps whole configurations as timestamped JSON with
an index beside them. It imports no Dear PyGui and reads no widget, so all of
this runs without a display; the interface that gathers and restores a
configuration is covered in tests/test_history_panel.py.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from mosaic import session_history as H
from mosaic.presets import default_preset


def _config(districts: int = 14, iterations: int = 5000, **over) -> dict:
    """A configuration shaped like the one the interface builds."""
    preset = default_preset()
    config = {
        "version": H.SCHEMA_VERSION,
        "shapefile": {"path": "/maps/North_Carolina_Simplified.shp",
                      "config": {"pop_col": "POP", "id_col": "GEOID20",
                                 "county_col": "CTY",
                                 "elections": [["DEM", "REP"]],
                                 "demographics": {"total": "VAP_TOTAL"}}},
        "preset": preset,
        "scores": {"hinge": {"enabled": False, "weight": 1, "threshold": 8,
                             "party": "D"},
                   "opportunity_targets": {"black": -1, "latino": -1,
                                           "asian": -1}},
        "run": {"num_districts": districts, "tolerance_pct": 2.5,
                "max_iterations": iterations, "seed": 0,
                "tolerance_ratchet_mode": "Off", "tree_mode_fast": False,
                "n3_pct": 5, "flip_enabled": False, "flip_midpoint": 84},
        "annealing": {"enabled": True, "initial_temp_factor": 0.05},
        "reference_plan": None,
        "starting_plan": None,
        "map_style": {"fill_opacity": 1.0},
        "place_unit": "counties",
        "calibration": None,
    }
    config.update(over)
    return config


@pytest.fixture
def store(tmp_path):
    return H.HistoryStore(tmp_path / "history", keep=5)


# ── Saving ───────────────────────────────────────────────────────────────────

def test_an_empty_history_reads_as_empty(store):
    assert store.entries() == []
    assert store.latest() is None
    assert store.load("nothing") is None


def test_a_snapshot_round_trips_through_the_file(store):
    config = _config()
    saved = store.save(config, trigger="run", assignment_csv="/out/run_1.csv")
    assert saved is not None
    loaded = store.load(saved.id)
    assert loaded is not None
    assert loaded.config == config
    assert loaded.trigger == "run"
    assert loaded.assignment_csv == "/out/run_1.csv"
    assert loaded.readable
    # The file is JSON a person can read, under the folder it was given.
    on_disk = json.loads((store.folder / f"{saved.id}.json").read_text())
    assert on_disk["config"] == config


def test_the_index_summarises_without_reading_the_snapshots(store):
    store.save(_config(districts=13, iterations=800), trigger="exit")
    (row,) = store.entries()
    assert row["districts"] == 13
    assert row["iterations"] == 800
    assert row["trigger"] == "exit"
    assert row["shapefile"] == "North_Carolina_Simplified.shp"
    assert row["pinned"] is False
    assert row["digest"]


def test_enabled_goals_are_read_out_of_the_weights(store):
    from mosaic import goals as G
    preset = G.apply_goals(default_preset(), {"compact": 60, "equal_pop": 40})
    store.save(_config(preset=preset))
    (row,) = store.entries()
    assert "Compact districts" in row["goals"]
    assert "Equal population" in row["goals"]
    assert len(row["goals"]) == 2


def test_the_place_unit_names_the_counties_goal(store):
    from mosaic import goals as G
    preset = G.apply_goals(default_preset(), {"counties": 40})
    store.save(_config(preset=preset, place_unit="cities"))
    (row,) = store.entries()
    # Compactness ships enabled, so it is on here too; the unit is the point.
    assert "Keep cities whole" in row["goals"]
    assert not any("counties" in name for name in row["goals"])


def test_an_identical_configuration_is_not_saved_twice(store):
    config = _config()
    assert store.save(config) is not None
    assert store.save(config) is None, "the same configuration was saved again"
    assert len(store.entries()) == 1
    # A changed one is saved.
    assert store.save(_config(districts=7)) is not None
    assert len(store.entries()) == 2
    # ... and the first one can come back once something else is newest.
    assert store.save(config) is not None
    assert len(store.entries()) == 3


def test_two_snapshots_in_the_same_second_get_their_own_ids(store):
    when = datetime(2026, 11, 19, 2, 30, 45)
    first = store.save(_config(districts=5), trigger="run", when=when)
    second = store.save(_config(districts=6), trigger="run", when=when)
    assert first is not None and second is not None
    assert first.id != second.id
    assert {r["id"] for r in store.entries()} == {first.id, second.id}


def test_two_snapshots_in_the_same_second_stay_in_order(store):
    """Newest first has to mean saved last. Ids carry the timestamp to the
    second and the trigger, so sorting by id would order the two by the words
    "exit" and "run" instead."""
    when = datetime(2026, 11, 19, 2, 30, 45)
    first = store.save(_config(districts=5), trigger="run", when=when)
    second = store.save(_config(districts=6), trigger="exit", when=when)
    assert [r["id"] for r in store.entries()] == [second.id, first.id]
    assert store.latest()["id"] == second.id


def test_an_unknown_trigger_is_refused(store):
    with pytest.raises(ValueError):
        store.save(_config(), trigger="whenever")


def test_an_empty_configuration_is_not_stored(store):
    assert store.save({}) is None
    assert store.save(None) is None
    assert store.entries() == []


# ── Pruning ──────────────────────────────────────────────────────────────────

def _fill(store, count: int, start=datetime(2026, 1, 1, 12, 0, 0)):
    out = []
    for i in range(count):
        snap = store.save(_config(districts=i + 2), trigger="run",
                          when=start + timedelta(minutes=i))
        assert snap is not None
        out.append(snap)
    return out


def test_pruning_keeps_the_newest_and_drops_the_rest(store):
    saved = _fill(store, 8)                 # keep=5
    kept = [r["id"] for r in store.entries()]
    assert kept == [s.id for s in reversed(saved[-5:])]
    for snap in saved[:3]:
        assert not (store.folder / f"{snap.id}.json").exists()
        assert store.load(snap.id) is None


def test_pruning_keeps_a_pinned_snapshot_however_old(store):
    saved = _fill(store, 3)
    oldest = saved[0]
    assert store.pin(oldest.id)
    _fill(store, 8, start=datetime(2026, 2, 1, 12, 0, 0))

    ids = [r["id"] for r in store.entries()]
    assert oldest.id in ids, "a pinned snapshot was pruned"
    assert (store.folder / f"{oldest.id}.json").exists()
    assert store.load(oldest.id).config == _config(districts=2)
    # Pinning does not cost recent history: the newest `keep` unpinned ones
    # are still there alongside it.
    assert len([r for r in store.entries() if not r["pinned"]]) == store.keep


def test_unpinning_lets_an_old_snapshot_be_pruned(store):
    saved = _fill(store, 3)
    oldest = saved[0]
    store.pin(oldest.id)
    _fill(store, 8, start=datetime(2026, 2, 1, 12, 0, 0))
    assert oldest.id in [r["id"] for r in store.entries()]

    assert store.pin(oldest.id, False)
    assert oldest.id not in [r["id"] for r in store.entries()]
    assert not (store.folder / f"{oldest.id}.json").exists()


def test_pinning_and_renaming_an_unknown_snapshot_fails_quietly(store):
    assert store.pin("nothing") is False
    assert store.rename("nothing", "name") is False


def test_a_name_survives_and_is_bounded(store):
    snap = store.save(_config())
    assert store.rename(snap.id, "  the good one  ")
    assert store.entry(snap.id)["name"] == "the good one"
    assert store.load(snap.id).name == "the good one"
    assert store.rename(snap.id, "x" * 200)
    assert len(store.entry(snap.id)["name"]) == 80


def test_deleting_removes_the_row_and_the_file(store):
    snap = store.save(_config())
    assert store.delete(snap.id)
    assert store.entries() == []
    assert not (store.folder / f"{snap.id}.json").exists()


# ── Surviving a damaged history ──────────────────────────────────────────────

def test_an_unreadable_index_reads_as_empty_history(store):
    store.save(_config())
    store.index_path.write_text("{not json", encoding="utf-8")
    assert store.entries() == []
    assert store.latest() is None


def test_an_unreadable_snapshot_loads_as_none(store):
    snap = store.save(_config())
    (store.folder / f"{snap.id}.json").write_text("{", encoding="utf-8")
    assert store.load(snap.id) is None


def test_a_snapshot_from_a_newer_format_is_listed_but_not_readable(store):
    snap = store.save(_config(version=H.SCHEMA_VERSION + 1))
    assert store.entries()[0]["id"] == snap.id
    assert not store.load(snap.id).readable


# ── Diffing ──────────────────────────────────────────────────────────────────

def test_an_identical_configuration_has_no_differences():
    assert H.diff(_config(), _config()) == []


def test_a_changed_setting_is_reported_with_both_values():
    changes = H.diff(_config(districts=14), _config(districts=7))
    labels = {c.label: (c.before, c.after) for c in changes}
    assert labels == {"Run > num districts": ("14", "7")}


def test_a_setting_only_one_side_has_is_a_difference():
    before = _config()
    after = _config()
    after["run"]["warmup"] = 100
    changes = {c.label: (c.before, c.after) for c in H.diff(before, after)}
    assert changes["Run > warmup"] == ("", "100")
    changes = {c.label: (c.before, c.after) for c in H.diff(after, before)}
    assert changes["Run > warmup"] == ("100", "")


def test_values_are_formatted_for_a_person():
    before = _config()
    after = _config()
    after["annealing"]["enabled"] = False
    after["annealing"]["initial_temp_factor"] = 0.125
    after["shapefile"]["config"]["elections"] = [["DEM24", "REP24"]]
    after["reference_plan"] = {"path": "/plans/ref.csv", "id_col": "GEOID20",
                               "district_col": "district"}
    changes = {c.label: (c.before, c.after) for c in H.diff(before, after)}
    assert changes["Annealing > enabled"] == ("on", "off")
    assert changes["Annealing > initial temp factor"] == ("0.05", "0.125")
    assert changes["Map > config > elections"] == ("DEM, REP", "DEM24, REP24")
    # A whole block appearing reports each of its settings, not one blob.
    assert changes["Reference plan > path"] == ("", "/plans/ref.csv")


def test_the_diff_covers_the_preset_down_to_one_weight():
    before = _config()
    after = _config()
    after["preset"]["holistic_compactness"]["weight"] = 75.0
    changes = [c for c in H.diff(before, after)
               if "holistic compactness" in c.label]
    assert len(changes) == 1
    assert changes[0].after == "75"
