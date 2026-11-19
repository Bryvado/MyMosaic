"""Spawn-isolated Batch Runs runs, with one coordinator and bounded batches."""
from __future__ import annotations

import copy
import math
import multiprocessing as mp
import os
import pickle
import tempfile
import time
from multiprocessing.connection import wait
from pathlib import Path


def available_cpus():
    counts = []
    for probe in (getattr(os, "process_cpu_count", os.cpu_count),
                  lambda: len(os.sched_getaffinity(0))):
        try:
            value = probe()
            if value:
                counts.append(value)
        except (AttributeError, OSError, NotImplementedError):
            pass
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            kernel.GetProcessAffinityMask.argtypes = (
                wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t),
                ctypes.POINTER(ctypes.c_size_t))
            process_mask, system_mask = ctypes.c_size_t(), ctypes.c_size_t()
            if kernel.GetProcessAffinityMask(kernel.GetCurrentProcess(),
                                            ctypes.byref(process_mask),
                                            ctypes.byref(system_mask)) and process_mask.value:
                counts.append(process_mask.value.bit_count())
        except (AttributeError, OSError):
            pass
    return max(1, min(counts)) if counts else 1


def default_workers():
    return max(1, min(6, available_cpus() - 1))


_DATA = ("graph", "graph_ig", "populations", "county_array", "county_pops",
         "pp_data", "reock_data", "opportunity_coords", "alignment_data",
         "election_arrays", "vap_data")
# score_config travels whole, so settings carried on it -- the opportunity
# curve, the manual opportunity targets -- reach each worker with it.
_SETTINGS = ("num_districts", "pop_tolerance", "max_iterations", "score_config",
             "annealing_config", "county_bias_enabled", "county_bias",
             "n3_probability", "n3_max_attempts_per_stage", "flip_enabled",
             "flip_midpoint", "tolerance_ratchet_mode", "tree_mode",
             "hot_start_assignment", "hot_start_locked_districts")
_THREAD_ENV = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
               "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS", "NUMBA_NUM_THREADS")


def _worker(connection, stop, snapshot, slot):
    # Imports happen after spawn inherits the numerical thread limits.
    import numpy as np

    from mosaic.ensemble import _apply_tuned, _measure_config, data_available, summary_values
    from mosaic.gui.runner import AlgorithmRunner
    from mosaic.gui.state import SharedState
    from mosaic.recom.recombination import GraphContext
    from mosaic.scoring import score_plan

    class WorkerState(SharedState):
        def check_should_stop(self):
            return stop.is_set() or super().check_should_stop()

        def update(self, **kwargs):
            super().update(**kwargs)
            if not {"status", "current_iteration", "max_iterations"}.isdisjoint(kwargs):
                phase = {"PARTITIONING": 1, "RUNNING": 2, "COMPLETED": 3}.get(
                    self.status.name, 0)
                with slot.get_lock():
                    values = slot.get_obj()
                    values[1:4] = phase, self.current_iteration, self.max_iterations
                    if phase == 2 and not values[5]:
                        values[5] = time.monotonic()

    try:
        with open(snapshot, "rb") as source:
            data, settings = pickle.load(source)
        runner = AlgorithmRunner(WorkerState())
        for name, value in data.items():
            setattr(runner, name, value)
        runner.graph_ctx = GraphContext(runner.graph_ig)
        connection.send(("ready", None))
        while True:
            job = connection.recv()
            if job is None:
                break
            index, seed, tuned, phase, keep_best = job
            if stop.is_set():
                connection.send(("cancelled", index))
                continue
            with slot.get_lock():
                slot.get_obj()[:] = index, 0, 0, 0, time.monotonic(), 0
            state = WorkerState()
            state.update(**copy.deepcopy(settings), seed=seed)
            runner.state = state
            if tuned:
                _apply_tuned(state, tuned, settings["annealing_config"])
            started = time.monotonic()
            runner.run_algorithm()
            if state.status.name == "ERROR":
                raise RuntimeError(f"Run {index}: {state.error_message}")
            if state.status.name != "COMPLETED":
                connection.send(("cancelled", index))
                continue
            use_best = keep_best and state.best_assignment is not None
            final = state.best_assignment if use_best else state.current_assignment
            total = state.best_score if use_best else state.current_score
            cfg = _measure_config(state.score_config, data_available(runner, state.score_config))
            ps = score_plan(runner.graph_ctx.compute_cut_edges(final), cfg,
                            assignment=final, **runner.score_kwargs(
                                state.num_districts, state.pop_tolerance, cfg))
            row = dict(run_id=f"run_{index}", seed=seed, iterations=state.max_iterations,
                       seconds=time.monotonic() - started, score=total, **summary_values(ps))
            if tuned:
                row.update(tuned, phase=phase)
            connection.send(("result", (index, np.asarray(final).copy(), row)))
    except (EOFError, BrokenPipeError):
        pass
    except BaseException as exc:
        try:
            connection.send(("error", f"{type(exc).__name__}: {exc}"))
        except (OSError, EOFError):
            pass
    finally:
        connection.close()


class Workers:
    """Parent owns all pipes/processes, including partial startup cleanup."""
    def __init__(self, snapshot_dir=None):
        self.processes = []
        self.connections = []
        self.stop = None
        self.slots = {}
        self.last_readings = {}
        self.snapshot_dir = snapshot_dir
        self.snapshot = None
        self.on_ready = lambda count: None

    def start(self, count, runner, state, stopped):
        ctx = mp.get_context("spawn")
        self.stop = ctx.Event()
        data = {name: getattr(runner, name) for name in _DATA}
        with state._lock:
            settings = copy.deepcopy({name: getattr(state, name) for name in _SETTINGS})
        if self.snapshot_dir is None:
            from mosaic.paths import cache_dir
            self.snapshot_dir = cache_dir()
        Path(self.snapshot_dir).mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=self.snapshot_dir, prefix="worker-input-",
                                         suffix=".pickle", delete=False) as target:
            self.snapshot = Path(target.name)
            pickle.dump((data, settings), target, protocol=pickle.HIGHEST_PROTOCOL)
        old = {name: os.environ.get(name) for name in _THREAD_ENV}
        try:
            os.environ.update(dict.fromkeys(_THREAD_ENV, "1"))
            for _ in range(count):
                if stopped():
                    return False
                parent, child = ctx.Pipe()
                self.connections.append(parent)
                slot = ctx.Array("d", 6)
                self.slots[parent] = slot
                process = ctx.Process(target=_worker, args=(child, self.stop, self.snapshot, slot),
                                      name="mosaic-batch")
                try:
                    process.start()
                    self.processes.append(process)
                finally:
                    child.close()
        finally:
            for name, value in old.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        pending = set(self.connections)
        deadline = time.monotonic() + 120
        while pending:
            if stopped():
                return False
            if time.monotonic() > deadline:
                raise RuntimeError("Worker startup timed out")
            for connection in wait(pending, timeout=.05):
                kind, body = connection.recv()
                if kind != "ready":
                    raise RuntimeError(body)
                pending.remove(connection)
                self.on_ready(count - len(pending))
            if any(p.exitcode is not None for p in self.processes):
                raise RuntimeError("A worker exited during startup")
        self.snapshot.unlink()
        self.snapshot = None
        return True

    def readings(self, pending, batch):
        readings = []
        for connection in pending:
            slot = self.slots[connection]
            lock = slot.get_lock()
            values = self.last_readings.get(connection, (0, 0, 0, 0, 0, 0))
            if lock.acquire(False):
                try:
                    values = tuple(slot.get_obj())
                    self.last_readings[connection] = values
                finally:
                    lock.release()
            if int(values[0]) == batch[connection][0]:
                readings.append(values)
            else:
                readings.append((batch[connection][0], 0, 0, 0, 0, 0))
        return readings

    def close(self):
        if self.stop is not None:
            self.stop.set()
        for connection in self.connections:
            try:
                connection.send(None)
            except (OSError, EOFError):
                pass
        deadline = time.monotonic() + 5
        for process in self.processes:
            process.join(max(0, deadline - time.monotonic()))
        for process in self.processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join()
            process.close()
        for connection in self.connections:
            connection.close()
        if self.snapshot is not None:
            self.snapshot.unlink(missing_ok=True)
            self.snapshot = None


def publish_progress(progress, spec, readings, completed=None):
    """Combine live iteration fractions and predict remaining batch time."""
    now = time.monotonic()
    fractions, predictions = [], []
    for _, phase, iteration, total, started, chain_started in readings:
        fraction = min(.999, iteration / total) if total else 0.0
        fractions.append(fraction)
        chain_elapsed = now - chain_started if chain_started else 0
        if phase == 3:
            predictions.append((max(.01, now - started), 0.0))
        elif fraction >= .02 and chain_elapsed > .05:
            remaining = chain_elapsed * (total - iteration) / max(1, iteration)
            predictions.append((now - started + remaining, remaining))
        else:
            predictions.append(None)
    with progress.lock:
        if completed is not None:
            progress.runs_done += 1
            progress.run_seconds_total += completed["seconds"]
            progress.done_elapsed = time.time() - progress.started
        progress.active_runs = len(readings)
        progress.active_fraction = sum(fractions)
        progress.active_iterations = int(sum(r[2] for r in readings))
        progress.active_max_iterations = int(sum(r[3] for r in readings))
        measured = [p[0] for p in predictions if p is not None]
        mean = (progress.run_seconds_total / progress.runs_done if progress.runs_done
                else sum(measured) / len(measured) if measured else None)
        if mean is None:
            progress.eta_seconds = None
            progress.expected_runs = None
            return
        left = [p[1] if p is not None else max(.1, mean - (now - r[4] if r[4] else 0))
                for p, r in zip(predictions, readings)]
        current = max(left, default=0.0)
        if spec.mode == "count":
            batches = math.ceil(max(0, spec.count-progress.current_run) / progress.workers)
            progress.eta_seconds = current + batches * mean
        elif spec.mode == "duration":
            duration_left = max(0, spec.duration_s - (time.time() - progress.started))
            batches = math.ceil(max(0, duration_left-current) / max(.01, mean))
            progress.eta_seconds = current + batches * mean
            progress.expected_runs = progress.runs_done + len(readings) + batches * progress.workers


def run_parallel(runner, state, spec, writer, progress):
    """Return False only when startup failed before any jobs were submitted."""
    import numpy as np

    def stopped():
        with progress.lock:
            return progress.stop_requested

    workers = Workers(writer.internal_folder)
    try:
        count = min(max(1, spec.workers), available_cpus())
        if spec.mode == "count":
            count = min(count, max(1, spec.count))
        if count == 1:
            with progress.lock:
                progress.notice = "Using one worker."
            return False
        with progress.lock:
            progress.starting_workers = True
            progress.workers_requested = count
        def ready(count):
            with progress.lock:
                progress.workers_ready = count
        workers.on_ready = ready
        try:
            if not workers.start(count, runner, state, stopped):
                return True
        except Exception as exc:
            with progress.lock:
                progress.notice = f"Workers unavailable; using serial execution ({exc})."
            return False
        with progress.lock:
            progress.workers = count
            progress.starting_workers = False
        rng = np.random.default_rng()
        targeter = progress.targeter
        index = 0
        while not stopped():
            batch = {}
            with progress.lock:
                for connection in workers.connections:
                    if spec.mode == "count" and index >= spec.count:
                        break
                    if (spec.mode == "duration"
                            and time.time() - progress.started >= spec.duration_s):
                        break
                    index += 1
                    seed = spec.base_seed + index - 1 if spec.base_seed is not None else int(
                        rng.integers(1, 2**31 - 1))
                    tuned = targeter.propose() if targeter else {}
                    phase = targeter.phase(index) if targeter else ""
                    batch[connection] = (index, seed, tuned, phase, spec.keep_best)
                progress.current_run = index
                progress.active_runs = len(batch)
            if not batch:
                break
            for connection, job in batch.items():
                connection.send(job)
            pending = set(batch)
            observations = {}
            stopping_since = None
            failure = ""
            while pending:
                publish_progress(progress, spec, workers.readings(pending, batch))
                if stopped() or failure:
                    workers.stop.set()
                    if stopping_since is None:
                        stopping_since = time.monotonic()
                    if time.monotonic() - stopping_since > 5:
                        break
                for connection in wait(pending, timeout=.05):
                    completed = None
                    try:
                        kind, body = connection.recv()
                    except (OSError, EOFError):
                        kind, body = "error", "A Batch Runs worker exited unexpectedly."
                    pending.remove(connection)
                    if kind == "result":
                        i, assignment, row = body
                        writer.add_run(f"run_{i}", assignment, row)
                        observations[i] = (batch[connection][2], row["score"])
                        completed = row
                    elif kind == "error":
                        failure = str(body)
                    elif kind != "cancelled":
                        failure = "Unexpected Batch Runs worker response."
                    elif not stopped() and not failure:
                        failure = "A Batch Runs run ended before completing."
                    publish_progress(progress, spec, workers.readings(pending, batch), completed)
            if failure:
                raise RuntimeError(failure)
            if targeter:
                for i, (tuned, score) in sorted(observations.items()):
                    targeter.observe(tuned, score, cancelled=stopped)
                with progress.lock:
                    progress.target_header = targeter.status_header()
                    progress.target_rows = targeter.status_rows()
        return True
    finally:
        workers.close()
        with progress.lock:
            progress.active_runs = 0
            progress.active_fraction = 0.0
            progress.starting_workers = False
