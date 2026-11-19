"""
Batch Runs — repeat full runs (partition to finish) until an end condition is
met, writing each run's final map and map-wide metrics as it completes.

Output lives in its own ``output/batch_<timestamp>/`` folder:

* ``batch_assignments.csv`` — one row per precinct, one column per run
  (``run_1`` ...), joinable in GIS like Save Assignments. Written when the
  batch ends.
* ``batch_summaries.csv`` — one row per run of map-wide metrics.
* ``batch_settings.toml`` — the scoring preset and run settings used.

Live views and recovery files live in a separate ``cache/batches/`` folder.
While running, each run is appended to ``batch_assignments_in_progress.csv`` there
(one row per run), since a per-precinct file can't grow by appending. It is
removed once finalization succeeds. A crash or write failure leaves the working
file available for recovery.

The loop drives the GUI runner's own ``run_algorithm`` so a batch run is
exactly a normal run. It holds no GUI code.
"""

from __future__ import annotations

import csv
import dataclasses
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np

from mosaic.ensemble_store import EnsembleStore, RunSequence
from mosaic.paths import cache_dir
from mosaic.presets import _toml_value
from mosaic.scoring import score_plan
from mosaic.scoring.score import PlanScore, ScoreConfig

ENSEMBLE_VERSION = 1


@dataclass
class EnsembleSpec:
    mode: str = "count"               # "count" | "duration" | "unlimited" (until stopped)
    count: int = 10
    duration_s: float = 300.0
    base_seed: Optional[int] = None   # run i uses base_seed + i - 1; None = random
    keep_best: bool = False           # keep each run's best-scoring map, not its final one
    targeting: bool = False           # tune chain settings from the runs (mosaic.targeting)
    workers: int = 1                  # one preserves the serial execution path


@dataclass
class EnsembleProgress:
    """Shared between the batch thread and the GUI; guard with ``lock``."""
    lock: threading.Lock = field(default_factory=threading.Lock)
    runs_done: int = 0
    done_elapsed: float = 0.0         # seconds from start to the last completed run
    current_run: int = 0
    started: float = 0.0
    stop_requested: bool = False
    finished: bool = False
    error: str = ""
    targeter: Any = None              # mosaic.targeting.Targeter when targeting
    target_header: str = ""
    target_rows: list[dict[str, str]] = field(default_factory=list)
    workers: int = 1
    active_runs: int = 0
    starting_workers: bool = False
    notice: str = ""
    workers_ready: int = 0
    workers_requested: int = 1
    active_fraction: float = 0.0
    active_iterations: int = 0
    active_max_iterations: int = 0
    run_seconds_total: float = 0.0
    eta_seconds: Optional[float] = None
    expected_runs: Optional[int] = None


# ── Map-wide metrics ─────────────────────────────────────────────────────────


def _measure_config(cfg: ScoreConfig, have: dict[str, bool]) -> ScoreConfig:
    """Every available metric on, in one fixed form so summaries don't depend
    on what the run optimized. Holistic ratings use the unclipped default: the
    clipped scorecard saturates (Reock above 0.50 pins its half of Compactness),
    which would hide variation the optimizer is still acting on. EG is static.

    What a metric *measures* is kept, only its weight is forced: the manual
    Electoral Opportunity targets ride along on the config, so the batch
    summary counts the same districts per group the runs were scored over.
    """
    on = {f.name: 1.0 for f in dataclasses.fields(ScoreConfig)
          if f.name.startswith("weight_")}
    if not have.get("reference"):
        on["weight_alignment"] = 0.0
    if not have.get("hinge"):          # its threshold is the user's; no guessing one
        on["weight_hinge"] = 0.0
    return dataclasses.replace(
        cfg, **on,
        holistic_splitting_unclipped=True, compactness_unclipped=True,
        proportionality_unclipped=True, competitiveness_unclipped=True,
        representation_unclipped=True, use_robust_eg=False,
    )


def summary_columns(have: dict[str, bool], tuned: tuple = ()) -> list[str]:
    """Columns for batch_summaries.csv. `tuned`: targeting's knob names,
    recorded per run with the run's phase so tuning runs can be split out."""
    cols = ["run_id", "seed", "iterations", "seconds", "score",
            "cut_edges", "pop_dev_max_pct", "pop_dev_mean_pct"]
    if have["compactness"]:
        cols += ["compactness", "polsby_popper", "reock"]
    if have["county"]:
        cols += ["county_congruence_penalty", "county_excess_splits",
                 "county_unified_districts"]
    if have["elections"]:
        cols += ["mean_median", "efficiency_gap", "partisan_bias",
                 "partisan_gini_penalty", "expected_dem_seats", "proportionality",
                 "inversion_chance", "competitiveness", "dem_majority_chance"]
    if have.get("hinge"):
        cols += ["hinge_chance"]
    if have["demographics"]:
        cols += ["electoral_opportunity", "opportunity_black",
                 "opportunity_latino", "opportunity_asian",
                 "neighborhood_severance_penalty", "community_dispersion_penalty"]
    if have["reference"]:
        cols += ["alignment_mean_retention", "alignment_min_retention"]
    if tuned:
        cols += ["phase", *[k for k in tuned if k != "iterations"]]
    return cols


def summary_values(ps: PlanScore) -> dict[str, float]:
    """Map-wide values from a PlanScore scored with _measure_config."""
    return {
        "cut_edges": ps.cut_edges,
        "pop_dev_max_pct": ps.pop_dev_max,
        "pop_dev_mean_pct": ps.pop_dev_mean,
        "compactness": 100.0 - ps.holistic_compactness,
        "polsby_popper": 1.0 - ps.polsby_popper / 100.0,
        "reock": 1.0 - ps.reock / 100.0,
        "county_congruence_penalty": ps.holistic_splitting,
        "county_excess_splits": ps.county_excess_splits,
        "county_unified_districts": ps.county_unified_districts,
        "mean_median": ps.mean_median,
        "efficiency_gap": ps.efficiency_gap,
        "partisan_bias": ps.partisan_bias,
        "partisan_gini_penalty": ps.partisan_gini,
        "expected_dem_seats": ps.dem_seats,
        "proportionality": 100.0 - ps.holistic_proportionality,
        "inversion_chance": ps.inversion_chance,
        "competitiveness": 100.0 - ps.holistic_competitiveness,
        "dem_majority_chance": ps.majority_chance_dem,
        "hinge_chance": ps.hinge_chance,
        "electoral_opportunity": ps.representation_rating,
        "opportunity_black": ps.opportunity_black,
        "opportunity_latino": ps.opportunity_latino,
        "opportunity_asian": ps.opportunity_asian,
        "neighborhood_severance_penalty": ps.minority_cohesion,
        "community_dispersion_penalty": ps.community_congruence,
        "alignment_mean_retention": ps.alignment_mean_ret,
        "alignment_min_retention": ps.alignment_min_ret,
    }


def data_available(runner, score_config: Optional[ScoreConfig] = None) -> dict[str, bool]:
    """Which metric groups this map can report. Hinge also needs the run to use
    it: its threshold is a user choice, so there is none to measure otherwise."""
    elections = bool(runner.election_arrays)
    return {
        "compactness": runner.pp_data is not None and runner.reock_data is not None,
        "county": runner.county_array is not None,
        "elections": elections,
        "demographics": runner.vap_data is not None,
        "reference": runner.alignment_data is not None,
        "hinge": elections and bool(score_config and score_config.weight_hinge),
    }


# ── Writer ───────────────────────────────────────────────────────────────────


def dumps_toml(data: dict[str, Any]) -> str:
    """Scalars at top level, then tables (and one level of sub-tables).
    None values are dropped (TOML has no null)."""
    def _clean(d):
        return {k: (_clean(v) if isinstance(v, dict) else v)
                for k, v in d.items() if v is not None}
    data = _clean(data)
    lines = [f"{k} = {_toml_value(v)}" for k, v in data.items()
             if not isinstance(v, dict)]
    for name, table in data.items():
        if not isinstance(table, dict):
            continue
        scalars = {k: v for k, v in table.items() if not isinstance(v, dict)}
        if scalars:
            lines += ["", f"[{name}]"]
            lines += [f"{k} = {_toml_value(v)}" for k, v in scalars.items()]
        for sub, body in table.items():
            if isinstance(body, dict):
                lines += ["", f"[{name}.{sub}]"]
                lines += [f"{k} = {_toml_value(v)}" for k, v in body.items()]
    return "\n".join(lines) + "\n"


class EnsembleWriter:
    """Owns one batch folder and publishes completed, disk-backed runs."""

    def __init__(self, folder: Path, precinct_ids: list, id_col: str,
                 columns: list[str]):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=False)
        cache_root = cache_dir() / "batches"
        cache_root.mkdir(parents=True, exist_ok=True)
        self.internal_folder = Path(tempfile.mkdtemp(prefix=f"{self.folder.name}_",
                                                    dir=cache_root))
        self.precinct_ids = list(precinct_ids)
        self.id_col = id_col
        self.columns = columns
        self.store = EnsembleStore(self.internal_folder / "batch_results.sqlite3", columns,
                                   len(self.precinct_ids))
        self.assignments = RunSequence(self.store, assignments=True)
        self.rows = RunSequence(self.store)
        self._write_failed = False
        self._finalized = False
        self._cache_root = cache_root.resolve()
        self.settings: Optional[dict[str, Any]] = None
        with self.working_path.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["run_id", *self.precinct_ids])
        with self.summary_path.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(columns)

    @classmethod
    def create(cls, parent: Path, precinct_ids: list, id_col: str,
               columns: list[str], *, created_at: Optional[datetime] = None):
        """Reserve a timestamp folder, numbering collisions from 2."""
        stem = f"batch_{(created_at or datetime.now()):%Y%m%d_%H%M%S}"
        number = 1
        while True:
            folder = Path(parent) / (stem if number == 1 else f"{stem}_{number}")
            try:
                return cls(folder, precinct_ids, id_col, columns)
            except FileExistsError as exc:
                if exc.filename is None or Path(exc.filename) != folder:
                    raise
                number += 1

    @property
    def working_path(self) -> Path:
        """Row-per-run file appended during the batch; removed by finalize()."""
        return self.internal_folder / "batch_assignments_in_progress.csv"

    @property
    def summary_path(self) -> Path:
        return self.folder / "batch_summaries.csv"

    @property
    def assign_path(self) -> Path:
        return self.folder / "batch_assignments.csv"

    def write_settings(self, settings: dict[str, Any]) -> None:
        body = {"mosaic_batch": ENSEMBLE_VERSION, **settings}
        path = self.folder / "batch_settings.toml"
        staging = self.folder / "batch_settings.toml.tmp"
        staging.write_text(dumps_toml(body), encoding="utf-8")
        staging.replace(path)
        self.settings = settings

    def add_run(self, run_id: str, assignment: np.ndarray,
                row: dict[str, Any]) -> None:
        districts = assignment.astype(np.int32) + 1   # 1-indexed, as Save Assignments
        try:
            with self.working_path.open("a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow([run_id, *districts.tolist()])
            # The store publishes its count only after the disk transaction commits.
            self.store.add(run_id, districts, row)
            with self.summary_path.open("a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow([_fmt(row.get(c, "")) for c in self.columns])
        except Exception:
            self._write_failed = True
            raise

    def finalize(self) -> Optional[Path]:
        """Publish matching assignment/summary exports from committed records.
        Drop the recovery file only after successful export without write errors."""
        path = None
        summaries = self.folder / "batch_summaries.csv.tmp"
        with summaries.open("w", newline="", encoding="utf-8") as f:
            output = csv.writer(f)
            output.writerow(self.columns)
            for row in self.rows:
                output.writerow([_fmt(row.get(c, "")) for c in self.columns])
        if self.assignments:
            staging = self.folder / "batch_assignments.csv.tmp"
            self.store.export_assignments(staging, self.id_col, self.precinct_ids)
            staging.replace(self.assign_path)
            path = self.assign_path
        summaries.replace(self.summary_path)
        if not self._write_failed:
            self.working_path.unlink(missing_ok=True)
        self._finalized = True
        return path

    def clear_cache(self) -> bool:
        """Discard finalized browsing data; retain unfinished recovery data."""
        if not self._finalized or self._write_failed:
            return False
        folder = self.internal_folder.resolve()
        if folder.parent != self._cache_root:
            raise ValueError("Batch Runs cache is outside its owned cache directory")
        if folder.exists():
            shutil.rmtree(folder)
        return True


def _fmt(v: Any) -> Any:
    if isinstance(v, (float, np.floating)):
        return f"{float(v):.6g}"
    return v


# ── Loop ─────────────────────────────────────────────────────────────────────


def _apply_tuned(state, tuned: dict, base_ann) -> None:
    """Push one run's tuned settings into shared state before it starts."""
    upd = {"max_iterations": int(tuned["iterations"]),
           "n3_probability": tuned["n3_probability"]}
    if "flip_midpoint" in tuned:
        upd["flip_midpoint"] = tuned["flip_midpoint"]
    ann = {}
    if "temp_factor" in tuned:
        ann["initial_temp_factor"] = tuned["temp_factor"]
    if "guide_fraction" in tuned:
        ann["guide_fraction"] = tuned["guide_fraction"]
    if ann:
        upd["annealing_config"] = dataclasses.replace(base_ann, **ann)
    state.update(**upd)


def run_ensemble(runner, state, spec: EnsembleSpec, writer: EnsembleWriter,
                 progress: EnsembleProgress) -> None:
    """Run until the end condition, a stop request, or an error. Run settings
    must already be in ``state`` (the GUI's run-settings capture). ``spec`` is
    re-read before each run, so the GUI can extend it mid-ensemble."""
    num_districts, tolerance, max_iterations, score_config = state.get(
        "num_districts", "pop_tolerance", "max_iterations", "score_config")
    have = data_available(runner, score_config)
    measure_cfg = _measure_config(score_config, have)
    seed_rng = np.random.default_rng()
    targeter = progress.targeter
    base_ann = state.get("annealing_config")[0]
    with progress.lock:
        progress.started = time.time()
        if targeter is not None:
            progress.target_header = targeter.status_header()
            progress.target_rows = targeter.status_rows()

    def stopped():
        with progress.lock:
            return progress.stop_requested
    i = 0
    try:
        parallel_done = False
        if spec.workers > 1:
            from mosaic.ensemble_parallel import run_parallel
            parallel_done = run_parallel(runner, state, spec, writer, progress)
        while not parallel_done:
            with progress.lock:
                if progress.stop_requested:
                    break
                if spec.mode == "count" and i >= spec.count:
                    break
                if (spec.mode == "duration"
                        and time.time() - progress.started >= spec.duration_s):
                    break
                # Stop takes this lock too, so reset cannot erase a stop request.
                state.reset_run()
            i += 1
            seed = (spec.base_seed + i - 1 if spec.base_seed is not None
                    else int(seed_rng.integers(1, 2**31 - 1)))
            with progress.lock:
                progress.current_run = i
            state.update(seed=seed)
            tuned = targeter.propose() if targeter is not None else {}
            if tuned:
                _apply_tuned(state, tuned, base_ann)
            (run_iters,) = state.get("max_iterations")
            t0 = time.time()
            runner.run_algorithm()
            # Compared by name: importing mosaic.gui would pull in the whole app.
            status, err = state.get("status", "error_message")
            if status.name == "ERROR":
                with progress.lock:
                    progress.error = err or "run failed"
                break
            if status.name != "COMPLETED":
                break   # stopped mid-run; the partial run is dropped
            with state._lock:
                if spec.keep_best and state.best_assignment is not None:
                    final = state.best_assignment.copy()
                    total = state.best_score
                else:
                    final = state.current_assignment.copy()
                    total = state.current_score
            ps = score_plan(runner.graph_ctx.compute_cut_edges(final), measure_cfg,
                            assignment=final,
                            **runner.score_kwargs(num_districts, tolerance, measure_cfg))
            row = {"run_id": f"run_{i}", "seed": seed,
                   "iterations": run_iters,
                   "seconds": time.time() - t0, "score": total,
                   **summary_values(ps)}
            if targeter is not None:
                row.update(tuned, phase=targeter.phase(i))
            writer.add_run(f"run_{i}", final, row)
            with progress.lock:
                progress.runs_done = i
                progress.done_elapsed = time.time() - progress.started
            if targeter is not None:
                # Fitting may be expensive; the GUI reads published snapshots.
                targeter.observe(tuned, total, cancelled=stopped)
                with progress.lock:
                    progress.target_header = targeter.status_header()
                    progress.target_rows = targeter.status_rows()
    except Exception as e:   # surfaced in the batch window, not swallowed
        from mosaic.crash import write_crash_log
        path = write_crash_log(e, context={"phase": "batch", "run": i})
        with progress.lock:
            progress.error = f"{type(e).__name__}: {e}  (log: {path})"
    finally:
        if writer.settings is not None:
            try:
                settings = dict(writer.settings)
                settings["batch"] = dict(settings["batch"])
                with progress.lock:
                    settings["batch"].update(
                        workers_requested=spec.workers, workers_used=progress.workers,
                        worker_notice=progress.notice or None,
                        count=spec.count if spec.mode == "count" else None,
                        duration_minutes=spec.duration_s / 60 if spec.mode == "duration" else None)
                if targeter is not None:
                    settings["targeting"] = targeter.summary()
                writer.write_settings(settings)
            except Exception as e:
                writer._write_failed = True
                with progress.lock:
                    progress.error = progress.error or f"Settings not saved: {e}"
        try:
            writer.finalize()
        except Exception as e:   # the working file survives with every run in it
            with progress.lock:
                detail = f"Exports incomplete: {e}; recovery data: {writer.internal_folder}"
                progress.error = f"{progress.error}; {detail}" if progress.error else detail
        with progress.lock:
            progress.runs_done = writer.store.count
            if writer._write_failed and "recovery data:" not in progress.error:
                progress.error += f"; recovery data: {writer.internal_folder}"
            progress.finished = True
