"""Run/pause/reset/revert control and district renumbering."""
from ._common import (
    _DIR_TO_MODE,
    AlgorithmStatus,
    AnnealingConfig,
    ScoreConfig,
    dpg,
    log,
    np,
    threading,
    time,
    warnings,
)


class RunnerMixin:
    """Run/pause/reset/revert control and district renumbering."""

    def _queue_session_action(self, action, *args, **kwargs):
        """Defer callbacks to the frame thread without wrapping their signatures.

        Dear PyGui's native dispatcher uses the callback's positional argument
        count, so registered methods must keep their explicit parameters.
        """
        if threading.get_ident() == self._gui_thread_id:
            return False
        self._session_requests.put(lambda: action(*args, **kwargs))
        return True

    def _session_action_pending(self):
        return self._pending_session_action is not None

    def _session_workers(self):
        return (self.algorithm_thread, self._data_thread, self._map_load_thread,
                getattr(self, "_calib_thread", None))

    def _session_workers_busy(self):
        return (getattr(self, "_saving", False)
                or any(not job[1].done() for job in getattr(self, "_ens_queries", {}).values())
                or any(t is not None and t.is_alive() for t in self._session_workers()))

    def _wait_for_workers(self, action):
        """Defer a state-changing action until all current writers have exited.

        The frame loop polls thread completion; it never blocks on join. Keeping
        the stop flag set until termination prevents a restarted run from
        reviving an old worker against the same shared state.
        """
        if self._session_action_pending():
            return True
        if self._session_workers_busy():
            self.state.request_stop()
            self._cancel_calibration()
            self._pending_session_action = action
            self.state.update(status_message="Waiting for current work to stop...")
            return True
        return False

    def _tick_session_action(self):
        if not self._session_action_pending():
            return
        if self._session_workers_busy():
            dpg.set_value(self._status_txt, "Waiting for current work to stop...")
            return
        action = self._pending_session_action
        self._pending_session_action = None
        self._perform_session_action(action)

    def _perform_session_action(self, action):
        """Report callback failures without ending the GUI frame loop."""
        try:
            action()
        except Exception as exc:
            log.exception("Session action failed")
            self.state.update(status_message=f"Could not complete action: {exc}")

    def _on_revert_to_best(self):
        """Rewind display to the best-scoring iteration seen so far."""
        if self._queue_session_action(self._on_revert_to_best):
            return
        if self._ensemble_active:
            return
        if self.state.best_assignment is None:
            return
        if self._wait_for_workers(self._on_revert_to_best):
            return
        with self.state._lock:
            if self.state.best_assignment is None:
                return
            best_assign = self.state.best_assignment.copy()
            best_score  = self.state.best_score
            best_iter   = self.state.best_iteration
            best_cuts   = self.state.best_cut_edges
            best_temp   = self.state.best_temperature
            best_breakdown = dict(self.state.best_score_breakdown)
            n_dist      = self.state.num_districts
            max_iter    = self.state.max_iterations
            _init       = (self.state.initial_assignment.copy()
                           if self.state.initial_assignment is not None else None)
            # Every series ends at the best iteration, or at the last point
            # recorded before it once the history has been thinned. Readers
            # notice the change and reload; nothing to trim by hand.
            self.state.history.truncate_after(best_iter)

        self.state.request_stop()

        self.state.update(
            status=AlgorithmStatus.IDLE,
            status_message=f"Reverted to best (iteration {best_iter:,})",
            current_assignment=best_assign,
            current_score=best_score,
            current_cut_edges=best_cuts,
            current_iteration=best_iter,
            current_temperature=best_temp,
            score_breakdown=best_breakdown,
        )
        if max_iter > 0:
            self._update_run_tiles(best_iter / max_iter)

        if self.map_view is not None and self.map_view._loaded:
            self.map_view.render_assignment(best_assign, n_dist, _init)

    def _on_run(self):
        if self._queue_session_action(self._on_run):
            return
        if self._ensemble_active:
            return
        if self.runner is None or self.runner.graph is None:
            self.state.update(status=AlgorithmStatus.ERROR,
                              error_message="Load a shapefile first.")
            return

        if self._wait_for_workers(self._on_run):
            return

        if not self._capture_run_settings():
            return
        # The whole configuration this run is about to use, kept so it can be
        # found again later. Saved after the settings validate, so a run that
        # could not start leaves no snapshot.
        self._save_history_snapshot("run")
        # The chain starts from exactly what was just captured, so there is no
        # edit outstanding against it.
        self._reweight_seen = None
        self._reweight_published = None
        self._reweight_at = 0.0
        self._clear_all_series()
        self.state.reset_run()

        self.algorithm_thread = threading.Thread(
            target=self.runner.run_algorithm, daemon=True, name="algo",
        )
        self.algorithm_thread.start()

    # ── Live reweighting ──────────────────────────────────────────────────────
    # A weight edited during a run used to do nothing until the next Start, so
    # steering a chain meant throwing it away. The scoring controls were
    # already editable mid-run (their edits simply waited); now the edit is
    # published to the running chain, which re-scores on it and carries on.

    # Seconds of quiet before an edit is published. A weight slider emits a
    # callback per pixel of drag, and each publish costs the run a re-score of
    # two plans, so the drag is allowed to finish first.
    _REWEIGHT_DEBOUNCE = 0.3

    # Settings a running chain cannot be handed. Each is frozen at run start --
    # the partition, the RNG seeding, the move mix, the cooling schedule and
    # the columns the scores read -- so changing one mid-run could not take
    # effect without restarting the chain. They are disabled while a single
    # run is in flight, so that "weights apply now" does not read as "anything
    # applies now". The starting plan has its own gate (_hot_start_editable)
    # and the column pickers are not reachable during a run.
    _RUN_LOCKED_WIDGETS = (
        "_num_districts", "_tolerance", "_tolerance_people",
        "_tolerance_ratchet_mode", "_iterations", "_seed",
        "_n3_pct", "_flip_enabled", "_flip_midpoint", "_fast_trees",
        "_county_bias_enabled", "_county_bias",
        "_ann_enabled", "_temp_factor", "_cool_mode", "_guide_frac",
        "_target_temp", "_cooling_rate", "_launch_watch_enabled",
        "_launch_watch_iter",
    )

    def _live_run_active(self) -> bool:
        """Is one interactive run in flight, running or paused?

        Batch Runs is excluded: its window disables the main columns and the
        chain belongs to the batch, not to the controls on screen.
        """
        return (not self._ensemble_active
                and not self._workflow_blocks_main
                and self.state.status in (AlgorithmStatus.RUNNING,
                                          AlgorithmStatus.PAUSED))

    def _tick_live_reweight(self) -> None:
        """Publish edited scoring to the running chain once the edits settle.

        Polled here rather than hooked onto each control: about thirty widgets
        feed a ScoreConfig, between the score rows, the Goals panel and four
        settings popups, and a list of them would go stale the first time one
        was added. Reading them costs nothing beyond a run, because this
        returns at once unless a single run is in flight.
        """
        if not self._live_run_active():
            self._reweight_seen = None
            self._reweight_published = None
            self._reweight_at = 0.0
            return
        config = self._build_score_config()
        live = (self._reweight_published if self._reweight_published is not None
                else self.state.score_config)
        if config == live:
            self._reweight_seen = None
            self._reweight_at = 0.0
            return
        now = time.monotonic()
        if config != self._reweight_seen:
            # Still moving: start the clock again from this value.
            self._reweight_seen = config
            self._reweight_at = now
            return
        if now - self._reweight_at < self._REWEIGHT_DEBOUNCE:
            return
        self.state.request_reweight(config)
        self._reweight_published = config
        self._reweight_seen = None
        self._reweight_at = 0.0

    def _sync_run_locked_controls(self) -> None:
        """Lock the settings a running chain cannot be handed; unlock after."""
        locked = self._live_run_active()
        if locked == getattr(self, "_run_locked", None):
            return
        self._run_locked = locked
        for name in self._RUN_LOCKED_WIDGETS:
            item = getattr(self, name, None)
            if item and dpg.does_item_exist(item):
                dpg.configure_item(item, enabled=not locked)
        if not locked:
            # The people slider has its own rule for when it can be used (a
            # range narrower than one person cannot), so hand it back rather
            # than leaving it enabled.
            self._sync_tolerance_people()

    def _build_score_config(self):
        """Every scoring widget, read into one ScoreConfig.

        Separated from the rest of the run settings because it is read twice:
        once when a run starts, and again on every edit while one is running,
        so the chain can be handed new weights without being restarted. The
        widgets stay the single source of truth either way.
        """
        cs_on      = dpg.get_value(self._cs_enabled)
        mm_on      = dpg.get_value(self._mm_enabled)
        eg_on      = dpg.get_value(self._eg_enabled)
        pb_on      = dpg.get_value(self._pb_enabled)
        pg_on      = dpg.get_value(self._pg_enabled)
        seats_on   = dpg.get_value(self._seats_enabled)
        maj_on     = dpg.get_value(self._majority_enabled)
        robust_eg = dpg.get_value(self._eg_mode) == "Robust (recommended)"

        w_cut  = (dpg.get_value(self._w_cut_edges)
                  if dpg.get_value(self._cut_enabled) else 0.0)
        w_cs_excess  = dpg.get_value(self._w_county_excess)  if cs_on else 0.0
        w_cs_unified = dpg.get_value(self._w_county_unified) if cs_on else 0.0
        w_pp   = (dpg.get_value(self._w_polsby_popper)
                  if dpg.get_value(self._pp_enabled) else 0.0)
        w_reock = (dpg.get_value(self._w_reock)
                   if dpg.get_value(self._reock_enabled) else 0.0)
        w_hc   = (dpg.get_value(self._w_holistic_compactness)
                  if dpg.get_value(self._hc_enabled) else 0.0)
        w_hsplit = (dpg.get_value(self._w_holistic_splitting)
                    if dpg.get_value(self._hsplit_enabled) else 0.0)
        w_hprop = (dpg.get_value(self._w_holistic_proportionality)
                   if dpg.get_value(self._hprop_enabled) else 0.0)
        w_hcmp  = (dpg.get_value(self._w_holistic_competitiveness)
                   if dpg.get_value(self._hcmp_enabled) else 0.0)
        w_repr = (dpg.get_value(self._w_representation)
                  if dpg.get_value(self._representation_enabled) else 0.0)
        w_mincoh = (dpg.get_value(self._w_minority_cohesion)
                    if dpg.get_value(self._minority_cohesion_enabled) else 0.0)
        w_cmtycong = (dpg.get_value(self._w_community_congruence)
                      if dpg.get_value(self._community_congruence_enabled) else 0.0)
        w_pd   = (dpg.get_value(self._w_pop_deviation)
                  if dpg.get_value(self._popdev_enabled) else 0.0)
        # Plan Continuity only counts when enabled AND a reference plan is loaded.
        _align_on = (dpg.get_value(self._alignment_enabled)
                     and self.runner is not None
                     and self.runner.alignment_data is not None)
        w_align = dpg.get_value(self._w_alignment) if _align_on else 0.0
        _align_focus = {
            "All residents": "none", "Republican": "rep", "Democratic": "dem",
        }[dpg.get_value(self._alignment_focus)]
        # Safe harbor cannot exceed population tolerance
        _tol    = dpg.get_value(self._tolerance) / 100.0
        _harbor = min(dpg.get_value(self._pop_dev_harbor) / 100.0, _tol)

        n_dist_run = dpg.get_value(self._num_districts)

        return ScoreConfig(
            weight_cut_edges=w_cut,
            weight_county_excess=w_cs_excess,
            weight_county_unified=w_cs_unified,
            weight_holistic_splitting=w_hsplit,
            holistic_splitting_unclipped=dpg.get_value(self._hsplit_unclipped),
            weight_polsby_popper=w_pp,
            weight_reock=w_reock,
            weight_holistic_compactness=w_hc,
            compactness_unclipped=dpg.get_value(self._hcompact_unclipped),
            weight_pop_deviation=w_pd,
            pop_deviation_safe_harbor=_harbor,
            weight_alignment=w_align,
            alignment_party_focus=_align_focus,
            alignment_restrict_to_party=dpg.get_value(self._alignment_restrict),
            alignment_win_threshold=dpg.get_value(self._alignment_win_threshold),
            weight_mean_median=dpg.get_value(self._w_mean_median) if mm_on else 0.0,
            mm_mode=_DIR_TO_MODE[dpg.get_value(self._mm_dir)],
            mm_bound=dpg.get_value(self._mm_bound),
            weight_efficiency_gap=dpg.get_value(self._w_efficiency_gap) if eg_on else 0.0,
            eg_mode=_DIR_TO_MODE[dpg.get_value(self._eg_dir)],
            eg_bound=dpg.get_value(self._eg_bound),
            partisan_quadratic_penalty=dpg.get_value(self._partisan_quadratic_penalty),
            use_robust_eg=robust_eg,
            weight_partisan_bias=dpg.get_value(self._w_partisan_bias) if pb_on else 0.0,
            pbias_mode=_DIR_TO_MODE[dpg.get_value(self._pb_dir)],
            pbias_bound=dpg.get_value(self._pbias_bound),
            weight_partisan_gini=dpg.get_value(self._w_partisan_gini) if pg_on else 0.0,
            weight_dem_seats=dpg.get_value(self._w_dem_seats) if seats_on else 0.0,
            dem_seats_favor_dem=(dpg.get_value(self._dem_seats_dir) == "D"),
            weight_holistic_proportionality=w_hprop,
            proportionality_unclipped=dpg.get_value(self._prop_unclipped),
            weight_holistic_competitiveness=w_hcmp,
            competitiveness_unclipped=dpg.get_value(self._comp_unclipped),
            weight_majority_chance_dem=(dpg.get_value(self._w_majority)
                                        if maj_on and dpg.get_value(self._majority_dem_chk)
                                        else 0.0),
            weight_majority_chance_rep=(dpg.get_value(self._w_majority)
                                        if maj_on and dpg.get_value(self._majority_rep_chk)
                                        else 0.0),
            election_win_prob_at_55=dpg.get_value(self._win_prob),
            election_swing_sigma=dpg.get_value(self._swing_sigma),
            weight_hinge=(dpg.get_value(self._w_hinge)
                          if dpg.get_value(self._hinge_enabled) else 0.0),
            hinge_threshold=max(1, min(dpg.get_value(self._hinge_threshold), n_dist_run)),
            hinge_dem=dpg.get_value(self._hinge_dem_chk),
            weight_representation=w_repr,
            representation_unclipped=dpg.get_value(self._representation_unclipped),
            opportunity_midpoint=dpg.get_value(self._opportunity_midpoint),
            opportunity_steepness=dpg.get_value(self._opportunity_steepness),
            opportunity_solid=dpg.get_value(self._opportunity_solid),
            opportunity_smart_targets=dpg.get_value(
                self._opportunity_smart_targets),
            opportunity_target_black=self._opportunity_target_value("black"),
            opportunity_target_latino=self._opportunity_target_value("latino"),
            opportunity_target_asian=self._opportunity_target_value("asian"),
            weight_minority_cohesion=w_mincoh,
            weight_community_congruence=w_cmtycong,
        )

    def _capture_run_settings(self) -> bool:
        """Validate the starting plan and copy widget settings into shared state.

        Return False if its district count or population balance prevents a run.
        """
        # Fine Tune reseeds each run from the live map, via the same seed slot the
        # runner already reads for Start from Plan (the two are mutually exclusive). No
        # map yet (e.g. right after Reset) -> clear the slot -> random seed.
        if self._fine_tune_active:
            with self.state._lock:
                cur = (self.state.current_assignment.copy()
                       if self.state.current_assignment is not None else None)
            self.state.update(hot_start_assignment=cur)

        # Seed sanity: validated when the seed was made, but the Districts
        # slider or the Population Tolerance could have moved since (Fine Tune
        # especially reseeds from a map built under the old settings). Re-check
        # both against the current values.
        hot_start, locked_districts = self.state.get(
            "hot_start_assignment", "hot_start_locked_districts")
        if locked_districts:
            from mosaic.io.hot_start import (
                DistrictCountMismatch,
                HotStartError,
                validate_district_locks,
            )
            try:
                validate_district_locks(
                    hot_start, locked_districts, self.runner.populations, self.runner.graph,
                    dpg.get_value(self._num_districts), dpg.get_value(self._tolerance) / 100.0)
            except DistrictCountMismatch as exc:
                self._show_hot_start_error(str(exc), district_count=exc.district_count)
                return False
            except HotStartError as exc:
                self._show_hot_start_error(str(exc))
                return False
        if hot_start is not None:
            what = "Fine Tune map" if self._fine_tune_active else "Starting plan"
            fix  = ("Stop Fine-Tuning" if self._fine_tune_active
                    else "Clear the starting plan")
            n_dist_slider = dpg.get_value(self._num_districts)
            n_dist_seed = int(hot_start.max()) + 1
            if n_dist_seed != n_dist_slider:
                self._show_hot_start_error(
                    f"{what} has {n_dist_seed} districts; {n_dist_slider} are selected. "
                    f"{fix} or change the district count.",
                    district_count=n_dist_seed if not self._fine_tune_active else None,
                )
                return False
            pops = self.runner.populations
            if pops is not None and n_dist_slider > 0:
                pop_f = pops.astype(np.float64)
                ideal = pop_f.sum() / n_dist_slider
                if ideal > 0:
                    dist_pop = np.bincount(
                        hot_start.astype(np.int64), weights=pop_f,
                        minlength=n_dist_slider)
                    max_dev = float(np.abs((dist_pop - ideal) / ideal).max())
                    tol = dpg.get_value(self._tolerance) / 100.0
                    if max_dev > tol:
                        self._show_hot_start_error(
                            f"{what} exceeds population tolerance: "
                            f"{max_dev * 100:.2f}% deviation, {tol * 100:.2f}% limit. "
                            f"Raise the limit "
                            f"or {fix}."
                        )
                        return False

        score_cfg = self._build_score_config()
        # Read again here: the run settings below record the tolerance too, and
        # the safe harbor inside the score config is capped against it.
        _tol = dpg.get_value(self._tolerance) / 100.0

        guided = dpg.get_value(self._cool_mode) == "Guided (recommended)"
        ann_cfg = AnnealingConfig(
            enabled=dpg.get_value(self._ann_enabled),
            initial_temp_factor=dpg.get_value(self._temp_factor),
            cooling_mode="GUIDED" if guided else "STATIC",
            guide_fraction=dpg.get_value(self._guide_frac),
            target_temp=dpg.get_value(self._target_temp),
            cooling_rate=dpg.get_value(self._cooling_rate),
            launch_watch=dpg.get_value(self._launch_watch_enabled),
            launch_watch_iter=dpg.get_value(self._launch_watch_iter),
        )
        seed = dpg.get_value(self._seed) or None

        cb_en  = dpg.get_value(self._county_bias_enabled)
        cb_val = dpg.get_value(self._county_bias)

        self.state.update(
            num_districts=dpg.get_value(self._num_districts),
            pop_tolerance=_tol,
            tolerance_ratchet_mode={
                "Off": "off", "Standard": "standard", "Strict": "strict",
            }[dpg.get_value(self._tolerance_ratchet_mode)],
            max_iterations=dpg.get_value(self._iterations),
            seed=seed,
            tree_mode="fast" if dpg.get_value(self._fast_trees) else "compiled",
            score_config=score_cfg,
            annealing_config=ann_cfg,
            map_render_interval=dpg.get_value(self._map_interval),
            county_bias_enabled=cb_en,
            county_bias=cb_val,
            n3_probability=dpg.get_value(self._n3_pct) / 100.0,
            flip_enabled=dpg.get_value(self._flip_enabled),
            flip_midpoint=dpg.get_value(self._flip_midpoint) / 100.0,
        )
        return True

    def _on_pause(self):
        if self._ensemble_active:
            return
        if self.state.status == AlgorithmStatus.PAUSED:
            self.state.request_resume()
        else:
            self.state.request_pause()

    def _on_reset(self):
        if self._queue_session_action(self._on_reset):
            return
        if self._ensemble_active or self._wait_for_workers(self._on_reset):
            return
        if getattr(self, "_ens_writer", None) is not None:
            if not self._on_ensemble_reset():
                return
        self._reset_run_state()

    def _reset_run_state(self):
        if self._queue_session_action(self._reset_run_state):
            return
        if self._ensemble_active:
            return
        if self._wait_for_workers(self._reset_run_state):
            return
        self.state.request_stop()
        with self.state._lock:
            self.state.history.reset()
            self.state.opportunity_targets = {}
            self.state.race_score_applicable = {}
        self.state.update(
            status=AlgorithmStatus.IDLE,
            status_message="",
            current_score=float("inf"),
            best_score=float("inf"),
            current_cut_edges=0,
            current_iteration=0,
            successful_steps=0,
            current_flip_rate=0.0,
            current_temperature=0.0,
            accepted_worse=0,
            rejected_worse=0,
            best_assignment=None,
            current_assignment=None,
            run_locked_districts=(),
            district_label_map=None,
            score_breakdown={},
            best_score_breakdown={},
        )
        # Reset wipes the current map, so a Fine Tune armed against it no longer
        # applies -- turn it off (restoring the pre-Fine Tune annealing settings).
        self._clear_fine_tune()
        # Reset status bar and score readouts
        dpg.set_value(self._status_txt, "Status: Idle")
        # The run strip and the stat tiles carry their own labels, so each
        # readout resets to the bare value. Progress is the strip's tiles; the
        # old progress-bar widget went with the redesign.
        dpg.set_value(self._iter_txt,   "0 / 0")
        dpg.set_value(self._time_txt,   "--")
        dpg.set_value(self._ips_txt,    "--")
        dpg.set_value(self._eta_txt,    "--")
        self._update_run_tiles(0.0)
        dpg.set_value(self._score_txt,  "--")
        dpg.set_value(self._best_txt,   "--")
        dpg.set_value(self._best_note,  "")
        dpg.set_value(self._temp_txt,   "Temperature: --")
        dpg.set_value(self._acc_txt,    "Entropy: --")
        dpg.set_value(self._succ_txt,   "Accepted steps: --")
        dpg.set_value(self._flip_txt,   "Flip rate: 0.0%")
        self._clear_all_series()
        if self.map_view is not None and self.map_view._loaded:
            self.map_view.draw_blank()

    def _stable_labeled_assignment(self, assignment) -> "np.ndarray | None":
        """`assignment` relabeled to match the live map / District Info labels.

        ReCom internally renumbers districts over the course of a run; the GUI
        uses ``stable_color_mapping`` against the initial partition to keep the
        same physical region under the same number across iterations. CSV
        exports must apply the same permutation, otherwise users see one label
        in Mosaic and a different one in their spreadsheet.
        """
        if assignment is None:
            return None
        initial = self.state.initial_assignment
        n_dist = self.state.num_districts or int(assignment.max()) + 1
        if initial is not None and len(initial) == len(assignment):
            from mosaic.gui.map_view import stable_color_mapping
            return stable_color_mapping(assignment, initial, n_dist)
        return assignment

    def _export_labeled_assignment(self, assignment) -> "np.ndarray | None":
        """`assignment` with any geographic renumber applied, 0-indexed.

        Exports add 1 to produce the district column, so we return the
        geographic number minus 1. With no renumber active this is just the
        stable-labeled assignment.
        """
        stable = self._stable_labeled_assignment(assignment)
        if stable is None:
            return None
        lm = self.state.district_label_map
        n_dist = self.state.num_districts or int(stable.max()) + 1
        if lm is not None and len(lm) == n_dist:
            return (lm[stable] - 1).astype(stable.dtype)
        return stable

    def _renumber_render(self, label_map) -> None:
        """Store label_map in state and re-render the static map with it."""
        self.state.update(district_label_map=label_map)
        with self.state._lock:
            assignment = (self.state.current_assignment.copy()
                          if self.state.current_assignment is not None else None)
            initial = (self.state.initial_assignment.copy()
                       if self.state.initial_assignment is not None else None)
            n_dist = self.state.num_districts
        if self.map_view is not None and assignment is not None and n_dist > 0:
            self.map_view.district_label_map = label_map
            self.map_view.fast_labels = False   # static map: precise labels
            self.map_view.render_assignment(assignment, n_dist, initial)

    def _renumber_centroids_xy(self):
        """Per-precinct centroid (x, y) in the gdf CRS, cached by gdf identity."""
        gdf = self.runner.gdf
        gid = id(gdf)
        cached = self._renumber_centroids
        if cached is not None and cached[0] == gid:
            return cached[1], cached[2]
        # Centroids are only used for relative geographic ordering of districts,
        # so the geographic-CRS "results likely incorrect" warning is moot here.
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", message=".*Results from 'centroid' are likely incorrect.*",
            )
            cents = gdf.geometry.centroid
            x = np.asarray(cents.x, dtype=np.float64)
            y = np.asarray(cents.y, dtype=np.float64)
        self._renumber_centroids = (gid, x, y)
        return x, y

    def _apply_renumber(self, rule: "str | None") -> None:
        """Apply (or clear, if rule is None) a geographic renumber to the
        current static map. Numbers only -- colors are untouched. No-op while a
        run is in progress. Does not write to the status bar.
        """
        if rule is None:
            self._renumber_render(None)
            return
        if self.runner is None or self.runner.gdf is None \
                or self.runner.populations is None:
            return
        with self.state._lock:
            status = self.state.status
            assignment = (self.state.current_assignment.copy()
                          if self.state.current_assignment is not None else None)
            initial = (self.state.initial_assignment.copy()
                       if self.state.initial_assignment is not None else None)
            n_dist = self.state.num_districts
        if status in (AlgorithmStatus.RUNNING, AlgorithmStatus.PARTITIONING):
            return
        if assignment is None or n_dist <= 0:
            return

        # Rank by stable color index so numbers line up with colors/panels.
        from mosaic.gui.map_view import stable_color_mapping
        if initial is not None and len(initial) == len(assignment):
            stable = stable_color_mapping(assignment, initial, n_dist)
        else:
            stable = assignment

        if rule == "infer":
            label_map = self._infer_label_map(stable, n_dist)
            if label_map is None:        # no alignment plan loaded -> no-op
                return
        elif rule == "proximity":
            from mosaic.renumber import proximity_label_map
            x, y = self._renumber_centroids_xy()
            pops = np.asarray(self.runner.populations, dtype=np.float64)
            label_map = proximity_label_map(stable, x, y, pops, n_dist)
        else:
            from mosaic.renumber import geographic_label_map
            x, y = self._renumber_centroids_xy()
            pops = np.asarray(self.runner.populations, dtype=np.float64)
            label_map = geographic_label_map(stable, x, y, pops, n_dist, rule=rule)
        from mosaic.renumber import preserve_locked_labels
        label_map = preserve_locked_labels(
            label_map, self.state.run_locked_districts, preserve_matches=rule == "infer")
        self._renumber_render(label_map)

    def _infer_label_map(self, stable: np.ndarray, n_dist: int):
        """Label map adopting the loaded reference plan's numbers, or None if no
        alignment plan is loaded. District counts need not match: the matching
        is rectangular and unmatched proposed districts get fresh numbers.

        The overlap is measured in whatever the alignment is set to align on:
        residents (population), D votes, or R votes -- mirroring the alignment
        score's party-focus setting.
        """
        ad = getattr(self.runner, "alignment_data", None)
        if ad is None or ad.alt_labels is None:
            return None
        from mosaic.renumber import infer_label_map_from_reference

        focus = self.state.score_config.alignment_party_focus
        dem = gop = None
        if self.runner.election_arrays:
            dem, gop = self.runner.election_arrays[0]
        if focus == "rep" and gop is not None:
            weights = np.asarray(gop, dtype=np.float64)
        elif focus == "dem" and dem is not None:
            weights = np.asarray(dem, dtype=np.float64)
        else:
            weights = np.asarray(self.runner.populations, dtype=np.float64)

        return infer_label_map_from_reference(
            stable, ad.alt_assignment, ad.alt_labels, weights,
            n_dist, ad.n_alt_districts,
        )

    # ── Renumber control sync (Advanced menu check + options radio) ──────────────

    _RENUMBER_RULE_TO_LABEL = {
        "nw_se":      "Northwest to Southeast",
        "n_s":        "North to South",
        "proximity":  "By proximity",
        "infer":      "Infer from reference plan",
    }
    _RENUMBER_LABEL_TO_RULE = {v: k for k, v in _RENUMBER_RULE_TO_LABEL.items()}

    def _alignment_loaded(self) -> bool:
        return (self.runner is not None
                and getattr(self.runner, "alignment_data", None) is not None)

    def _renumber_radio_items(self) -> list:
        items = ["By proximity", "Northwest to Southeast", "North to South", "None"]
        if self._alignment_loaded():
            items.append("Infer from reference plan")
        return items

    def _renumber_rule_label(self) -> str:
        """Radio label reflecting current renumber state."""
        if not self._renumber_enabled:
            return "None"
        return self._RENUMBER_RULE_TO_LABEL.get(self._renumber_rule, "By proximity")

    def _sync_renumber_widgets(self) -> None:
        dpg.set_value(self._renumber_after_run, self._renumber_enabled)
        if dpg.does_item_exist("renumber_rule_radio"):
            dpg.set_value("renumber_rule_radio", self._renumber_rule_label())

    def _maybe_live_renumber(self) -> None:
        """Reflect the current setting on the displayed static map immediately."""
        self._apply_renumber(self._renumber_rule if self._renumber_enabled else None)

    def _on_renumber_after_run_toggle(self, sender, app_data) -> None:
        self._renumber_enabled = bool(app_data)
        if self._renumber_enabled and self._renumber_rule not in ("nw_se", "n_s", "proximity"):
            self._renumber_rule = "proximity"
        self._sync_renumber_widgets()
        self._maybe_live_renumber()

    def _on_renumber_rule_change(self, sender, app_data) -> None:
        if app_data == "None":
            self._renumber_enabled = False
        else:
            self._renumber_enabled = True
            self._renumber_rule = self._RENUMBER_LABEL_TO_RULE.get(app_data, "proximity")
        self._sync_renumber_widgets()
        self._maybe_live_renumber()

    def _on_open_renumber_options(self) -> None:
        if self._ensemble_active:
            return
        # "Infer from reference plan" only appears when a reference plan is loaded;
        # if it was selected and the plan is gone, fall back to the diagonal.
        if self._renumber_rule == "infer" and not self._alignment_loaded():
            self._renumber_rule = "proximity"
        # Rebuilt each open (radio items depend on current state); the helper
        # deletes any prior instance. _sync_renumber_widgets() guards on the tag
        # existing, so deleting it on close is safe.
        with self._dialog(
            "Renumber Options", "renumber_options_window", (240, 170),
            secondary=("Close",
                       lambda: dpg.delete_item("renumber_options_window")),
        ):
            dpg.add_text("Renumber districts:")
            dpg.add_separator()
            dpg.add_radio_button(
                items=self._renumber_radio_items(),
                tag="renumber_rule_radio",
                default_value=self._renumber_rule_label(),
                callback=self._on_renumber_rule_change,
            )
        dpg.focus_item("renumber_options_window")
