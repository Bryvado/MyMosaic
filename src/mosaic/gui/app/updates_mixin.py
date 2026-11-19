"""Per-frame UI refresh: live plots, tables, and status labels."""
from mosaic import history as H
from mosaic.gui.choice_radio import RADIO_HELP

from ._common import (
    _PHASE_ATTR,
    _PHASE_BANDS,
    _PLOT_LIMIT,
    _RENDER_TARGET,
    _RUN_TILES,
    AlgorithmStatus,
    Path,
    _fmt_dur,
    _SeriesBuffer,
    dpg,
    np,
    threading,
    time,
)

_NAN = float("nan")


def _na_gap(vals):
    """Map the -1.0 "not applicable" sentinel to NaN for charting.

    The per-group demographic display values are 0-100, with -1.0 meaning the
    group is not applicable. ImPlot skips NaN, so an N/A group draws nothing
    instead of a flat line below the axis floor.
    """
    return np.where(vals < 0.0, _NAN, vals)


def _x100(vals):
    return vals * 100.0


def _rating(vals):
    """Penalty (0 = best) to rating (100 = best) for display."""
    return 100.0 - vals


# (buffer, history series, display units). Holistic charts show a rating,
# splitting stays the raw penalty (uncapped in unclipped mode, so charting it
# as 100 - x would be dishonest above 100). Per-group demographic values are
# 0-100 with -1 meaning "not applicable", which charts as a gap.
_FEEDS = (
    ("_buf_score", H.SCORE, None),
    ("_buf_acc", H.ACCEPTANCE, _x100),
    ("_buf_temp", H.TEMPERATURE, None),
    ("_buf_cs_score", "county_splits_score_history", None),
    ("_buf_cs_excess", "county_excess_splits_history", None),
    ("_buf_cs_clean", "county_unified_districts_history", None),
    ("_buf_mm", "mm_history", None),
    ("_buf_eg", "eg_history", None),
    ("_buf_pb", "partisan_bias_history", None),
    ("_buf_pg", "partisan_gini_history", None),
    ("_buf_seats", "dem_seats_history", None),
    ("_buf_pp", "pp_history", _x100),
    ("_buf_reock", "reock_history", _x100),
    ("_buf_hc", "holistic_compactness_history", _rating),
    ("_buf_hsplit", "holistic_splitting_history", None),
    ("_buf_hprop", "holistic_proportionality_history", _rating),
    ("_buf_hcmp", "holistic_competitiveness_history", _rating),
    ("_buf_rep_black", "representation_black_history", _na_gap),
    ("_buf_rep_latino", "representation_latino_history", _na_gap),
    ("_buf_rep_asian", "representation_asian_history", _na_gap),
    ("_buf_coh_black", "cohesion_black_history", _na_gap),
    ("_buf_coh_latino", "cohesion_latino_history", _na_gap),
    ("_buf_coh_asian", "cohesion_asian_history", _na_gap),
    ("_buf_rep_overall", "representation_overall_history", None),
    ("_buf_coh_overall", "minority_cohesion_overall_history", None),
    ("_buf_cong_black", "congruence_black_history", _na_gap),
    ("_buf_cong_latino", "congruence_latino_history", _na_gap),
    ("_buf_cong_asian", "congruence_asian_history", _na_gap),
    ("_buf_cong_overall", "community_congruence_overall_history", None),
    ("_buf_rep_black_seats", "representation_black_seats_history", None),
    ("_buf_rep_latino_seats", "representation_latino_seats_history", None),
    ("_buf_rep_asian_seats", "representation_asian_seats_history", None),
    ("_buf_popdev", "pop_deviation_history", None),
    ("_buf_popdev_max", "pop_dev_max_history", None),
    ("_buf_popdev_mean", "pop_dev_mean_history", None),
    ("_buf_align_penalty", "alignment_penalty_history", None),
    ("_buf_align_mean", "alignment_mean_ret_history", None),
    ("_buf_align_min", "alignment_min_ret_history", None),
    ("_buf_cuts", H.CUT_EDGES, None),
    ("_buf_maj_dem", "majority_dem_history", _x100),
    ("_buf_maj_rep", "majority_rep_history", _x100),
    ("_buf_hinge", "hinge_history", _x100),
    ("_buf_inversion", "inversion_history", _x100),
)

# Series each chart needs recorded while it is open, even if its score is off.
# Score, Temperature, Entropy and Cut Edges are always recorded.
_PANEL_SERIES = {
    "panel_county_splits": ("county_splits_score_history",
                            "county_excess_splits_history",
                            "county_unified_districts_history"),
    "panel_mm": ("mm_history",),
    "panel_eg": ("eg_history",),
    "panel_pb": ("partisan_bias_history",),
    "panel_pg": ("partisan_gini_history",),
    "panel_dem_seats": ("dem_seats_history",),
    "panel_majority": ("majority_dem_history", "majority_rep_history"),
    "panel_hinge": ("hinge_history",),
    "panel_pp": ("pp_history",),
    "panel_reock": ("reock_history",),
    "panel_representation": (
        "representation_black_history", "representation_latino_history",
        "representation_asian_history", "representation_black_seats_history",
        "representation_latino_seats_history",
        "representation_asian_seats_history", "representation_overall_history"),
    "panel_minority_cohesion": (
        "cohesion_black_history", "cohesion_latino_history",
        "cohesion_asian_history", "minority_cohesion_overall_history"),
    "panel_community_congruence": (
        "congruence_black_history", "congruence_latino_history",
        "congruence_asian_history", "community_congruence_overall_history"),
    "panel_hc": ("holistic_compactness_history",),
    "panel_hsplit": ("holistic_splitting_history",
                     "county_splits_score_history",
                     "county_excess_splits_history",
                     "county_unified_districts_history"),
    "panel_hprop": ("holistic_proportionality_history", "inversion_history"),
    "panel_hcmp": ("holistic_competitiveness_history",),
    "panel_popdev": ("pop_dev_max_history", "pop_dev_mean_history"),
    "panel_alignment": ("alignment_penalty_history",
                        "alignment_mean_ret_history",
                        "alignment_min_ret_history"),
}


_TARGET_LABELS = (("black", "Black"), ("latino", "Latino"), ("asian", "Asian"))


def _race_panel_msg(app, score_id: str = "") -> str:
    """Why a demographic panel is empty: no demographic columns, nothing for this score
    to measure in this state, or the score simply switched off."""
    if not getattr(app, "_has_race", False):
        return "Load demographic data to use this panel."
    if app.state.race_score_applicable.get(score_id, True) is False:
        return "Not available for the loaded demographic data."
    return "Turn on this score to chart it."


def _opportunity_target_lines(targets: dict,
                              provided=()) -> list[tuple[str, str]]:
    """Per-group opportunity-district targets as (text, theme token) pairs.

    The count is a heuristic reference, not a forecast or proof that a valid
    district can be drawn. A group with no column is zero-filled upstream, so it
    must be named as missing rather than treated as a substantive zero.
    """
    out: list[tuple[str, str]] = []
    for key, name in _TARGET_LABELS:
        t = targets.get(key)
        if not t:
            continue
        if provided and key not in provided:
            out.append((f"{name}: no column loaded", "disabled_deep"))
            continue
        drawable = int(round(t.get("ceiling", 0.0)))
        manual = bool(t.get("manual", False))
        counted = int(t.get("target", 0))
        note = f", {counted} counted by hand" if manual else ""
        if counted < 1:
            out.append(((f"{name}: left out by hand" if manual
                         else f"{name}: reference below 1 district"),
                        "disabled_deep"))
        elif not t.get("feasible", 0) or drawable < 1:
            # A manual target is scored whatever the heuristic found, so say
            # the group is counted rather than dropped.
            out.append(((f"{name}: no local reference found{note}" if manual
                         else f"{name}: no local reference found"),
                        "body" if manual else "disabled_deep"))
        else:
            out.append((f"{name}: {drawable}-district reference{note}", "body"))
    return out


class UpdatesMixin:
    """Per-frame UI refresh: live plots, tables, and status labels."""

    def _update_district_info_table(self) -> None:
        """Recompute the district info table from the live assignment.

        Called once per frame while the panel is visible.  Rows are rebuilt
        only when the district count changes; otherwise we just rewrite
        cell values via set_value so column widths stay stable.
        """
        if not dpg.is_item_shown("panel_district_info"):
            return
        if self.runner is None or self.runner.populations is None:
            dpg.configure_item("district_info_empty_lbl", show=True)
            return

        with self.state._lock:
            assignment = (self.state.current_assignment.copy()
                          if self.state.current_assignment is not None else None)
            initial = (self.state.initial_assignment.copy()
                       if self.state.initial_assignment is not None else None)
            n_dist = self.state.num_districts
            current_iter = self.state.current_iteration
            status = self.state.status
            label_map = self.state.district_label_map

        # Throttle: while running, only refresh every N iterations (per the
        # slider).  When idle/paused, always show fresh data.  An update is
        # also forced when the district count changes or the panel was just
        # re-opened (_dist_info_last_iter reset to -1).
        is_running = status in (AlgorithmStatus.RUNNING,
                                AlgorithmStatus.PARTITIONING)
        interval = max(1, int(dpg.get_value(self._dist_info_update_every)))
        force = (self._dist_info_last_iter < 0
                 or self._dist_table_built_rows != n_dist)
        if is_running and not force:
            if current_iter - self._dist_info_last_iter < interval:
                return

        if assignment is None or n_dist <= 0:
            dpg.configure_item("district_info_empty_lbl", show=True)
            return
        dpg.configure_item("district_info_empty_lbl", show=False)

        populations = self.runner.populations
        ideal_pop = float(populations.sum()) / n_dist if n_dist > 0 else 1.0

        # Per-district populations and deviation
        pop_d = np.bincount(assignment, weights=populations.astype(np.float64),
                            minlength=n_dist)
        pop_dev_pct = (pop_d - ideal_pop) / ideal_pop * 100.0

        # Polsby-Popper per district (mirror logic from io/export.py)
        pp_data = self.runner.pp_data
        if pp_data is not None and len(pp_data.areas) == len(assignment):
            _FOUR_PI = 4.0 * np.pi
            dist_area = np.bincount(assignment, weights=pp_data.areas,
                                    minlength=n_dist).astype(np.float64)
            dist_perim = np.bincount(assignment, weights=pp_data.ext_perimeters,
                                     minlength=n_dist).astype(np.float64)
            eu, ev, elen = pp_data.edge_u, pp_data.edge_v, pp_data.edge_len
            if len(eu) > 0:
                eu_d = assignment[eu]
                ev_d = assignment[ev]
                is_cut = eu_d != ev_d
                if is_cut.any():
                    cut_len = elen[is_cut]
                    np.add.at(dist_perim, eu_d[is_cut], cut_len)
                    np.add.at(dist_perim, ev_d[is_cut], cut_len)
            safe_perim = np.where(dist_perim > 0.0, dist_perim, 1.0)
            pp_per_dist = np.clip(_FOUR_PI * dist_area / (safe_perim ** 2),
                                  0.0, 1.0)
        else:
            pp_per_dist = None

        # Vote shares
        dem_pct = rep_pct = None
        if (self.runner.election_arrays
                and len(self.runner.election_arrays[0][0]) == len(assignment)):
            dem, gop = self.runner.election_arrays[0]
            dem_d = np.bincount(assignment, weights=dem.astype(np.float64),
                                minlength=n_dist)
            gop_d = np.bincount(assignment, weights=gop.astype(np.float64),
                                minlength=n_dist)
            total_d = dem_d + gop_d
            with np.errstate(invalid="ignore"):
                dem_pct = np.where(total_d > 0, dem_d / total_d * 100.0, np.nan)
                rep_pct = np.where(total_d > 0, gop_d / total_d * 100.0, np.nan)

        # Stable district -> displayed label (matches map labelling). A
        # geographic renumber maps the stable color index through label_map.
        use_lm = label_map is not None and len(label_map) == n_dist
        d_to_label = {}
        if initial is not None and len(initial) == len(assignment):
            from mosaic.gui.map_view import stable_color_mapping
            stable_colors = stable_color_mapping(assignment, initial, n_dist)
            for d in range(n_dist):
                mask = assignment == d
                if mask.any():
                    si = int(stable_colors[mask][0])
                    d_to_label[d] = int(label_map[si]) if use_lm else si + 1
        else:
            for d in range(n_dist):
                d_to_label[d] = int(label_map[d]) if use_lm else d + 1

        # Display order: rows sorted by displayed label ascending. Labels are
        # 1..k for the default/geographic numbering but can be non-contiguous
        # under "Infer from reference plan" (reference numbers + fresh ids), so we
        # order by the label value rather than assuming label == row + 1.
        ordered_ds = [d for d, _ in sorted(d_to_label.items(),
                                           key=lambda kv: kv[1])]

        # Rebuild rows only when district count changes
        if self._dist_table_built_rows != n_dist:
            dpg.delete_item("district_info_table", children_only=True, slot=1)
            for r in range(n_dist):
                with dpg.table_row(parent="district_info_table"):
                    for _, _, key in self._DIST_COLS:
                        dpg.add_text("", tag=f"di_r{r}_{key}")
            self._dist_table_built_rows = n_dist

        for r in range(n_dist):
            d = ordered_ds[r] if r < len(ordered_ds) else r
            lab = d_to_label.get(d, d + 1)
            dpg.set_value(f"di_r{r}_dist", str(lab))
            dpg.set_value(f"di_r{r}_pop", f"{int(pop_d[d]):,}")
            dpg.set_value(f"di_r{r}_pdev", f"{pop_dev_pct[d]:+.2f}%")
            dpg.set_value(
                f"di_r{r}_pp",
                f"{pp_per_dist[d] * 100:.1f}" if pp_per_dist is not None else "—",
            )
            if dem_pct is not None and not np.isnan(dem_pct[d]):
                dp = float(dem_pct[d])
                rp = float(rep_pct[d])
                dpg.set_value(f"di_r{r}_demp", f"{dp:.1f}%")
                dpg.set_value(f"di_r{r}_repp", f"{rp:.1f}%")
                m = dp - rp
                dpg.set_value(f"di_r{r}_margin", f"{m:+.1f}")
            else:
                dpg.set_value(f"di_r{r}_demp", "—")
                dpg.set_value(f"di_r{r}_repp", "—")
                dpg.set_value(f"di_r{r}_margin", "—")

        self._dist_info_last_iter = current_iter

    def _update_ui(self):
        snap = self.state.get_all()
        status  = snap["status"]
        is_running      = status == AlgorithmStatus.RUNNING
        is_paused       = status == AlgorithmStatus.PAUSED
        is_partitioning = status == AlgorithmStatus.PARTITIONING
        is_busy    = status in (
            AlgorithmStatus.RUNNING,
            AlgorithmStatus.PARTITIONING,
            AlgorithmStatus.PAUSED,
        )

        # ── Auto-renumber on run completion (Advanced checkbox) ──────────────────
        # Fire once when status enters COMPLETED.
        if (status == AlgorithmStatus.COMPLETED
                and getattr(self, "_last_seen_status", None)
                    != AlgorithmStatus.COMPLETED
                and self._renumber_enabled):
            self._apply_renumber(self._renumber_rule)
        self._last_seen_status = status

        # ── Inspection complete → show shapefile dialog (or skip for recent open) ─
        if snap["shp_inspect_ready"]:
            self.state.update(shp_inspect_ready=False)
            if self.runner and self.runner._pending_inspection is not None:
                if self._pending_recent_config is not None:
                    _cfg = self._pending_recent_config
                    self._pending_recent_config = None
                    self._on_shp_confirm(self.runner._pending_inspection, _cfg)
                else:
                    self._shp_dialog.populate(self.runner._pending_inspection)

        if self._session_action_pending():
            return

        # ── Load complete → update shapefile info label ────────────────────────
        if snap["gdf_ready"]:
            self.state.update(gdf_ready=False)
            self._update_shp_info_label()
            self._sync_tolerance_people()
            # A calibration already measured for this file, district count and
            # tolerance is good as it stands; reopening a map should not ask
            # for the same seconds twice.
            self._load_cached_calibration()
            # Mark this gdf as fully populated (all overlay arrays present) so
            # the map bg-load below may load it. complete_load sets gdf +
            # populations + county + elections + pp before pulsing gdf_ready,
            # so latching here guarantees the map never captures partial data.
            self._map_data_gdf_id = (id(self.runner.gdf)
                                     if self.runner and self.runner.gdf is not None
                                     else 0)

        # ── Map: trigger background load when new shapefile is ready ──────────
        loaded_path = self.state.shapefile_path
        loaded_gdf_id = (id(self.runner.gdf)
                         if self.runner and self.runner.gdf is not None
                         else 0)
        # Gate on the fully-loaded latch, not a status snapshot: on Open Recent
        # complete_load is kicked off in this same frame, so a stale IDLE
        # status (left by inspection) could otherwise let the map load a
        # runner that has only gdf+populations set — county/elections/pp still
        # None — and the same-identity gdf never re-triggers a reload.
        fully_loaded = (loaded_gdf_id != 0
                        and loaded_gdf_id == self._map_data_gdf_id)
        if (loaded_path
                and (loaded_path != self._map_loaded_path
                     or loaded_gdf_id != self._map_loaded_gdf_id)
                and not self._map_loading
                and fully_loaded
                and self.runner
                and self.runner.gdf is not None):
            self._map_loading = True
            self._reset_map_navigation()
            self._map_loaded_path = loaded_path
            self._map_loaded_gdf_id = loaded_gdf_id
            gdf_ref = self.runner.gdf
            county_array_ref = self.runner.county_array
            dem_ref = (self.runner.election_arrays[0][0]
                       if self.runner.election_arrays else None)
            gop_ref = (self.runner.election_arrays[0][1]
                       if self.runner.election_arrays else None)
            pp_data_ref  = self.runner.pp_data
            reock_data_ref = self.runner.reock_data
            pop_ref      = self.runner.populations
            vap_ref      = self.runner.vap_data
            mv = self.map_view
            def _bg_load():
                try:
                    mv.load(gdf_ref, county_array=county_array_ref,
                            dem_votes=dem_ref, gop_votes=gop_ref,
                            pp_data=pp_data_ref, reock_data=reock_data_ref,
                            populations=pop_ref, vap_data=vap_ref)
                    self._map_ready = True
                except Exception as exc:
                    import sys as _sys

                    from mosaic.crash import write_crash_log
                    crash_path = write_crash_log(
                        exc, context={"phase": "map_view_load"}
                    )
                    print(
                        f"\n[mosaic] Map rendering failed.\n"
                        f"        {type(exc).__name__}: {exc}\n"
                        f"        Log: {crash_path}\n",
                        file=_sys.stderr,
                    )
                    self.state.update(
                        error_message=(
                            f"Map render failed: {type(exc).__name__}: {exc}\n"
                            f"Log: {crash_path}"
                        ),
                        status_message="Map render failed — see crash log",
                    )
                finally:
                    self._map_loading = False
            self._map_load_thread = threading.Thread(target=_bg_load, daemon=True)
            self._map_load_thread.start()

        if self._map_ready:
            self._map_ready = False
            if self.map_view is not None:
                self.map_view.draw_blank()
                self._update_map_preview()
                with self.state._lock:
                    if self.state.current_assignment is not None:
                        self.state.map_needs_update = True

        self._tick_map_navigation()

        # ── Map: colour update triggered by runner ────────────────────────────
        with self.state._lock:
            map_needs = self.state.map_needs_update
            if map_needs:
                self.state.map_needs_update = False
                _assignment = (
                    self.state.current_assignment.copy()
                    if self.state.current_assignment is not None else None
                )
                _initial = (
                    self.state.initial_assignment.copy()
                    if self.state.initial_assignment is not None else None
                )
                _n_dist = self.state.num_districts
                _label_map = self.state.district_label_map
            else:
                _assignment = _initial = None
                _n_dist = 0
                _label_map = self.state.district_label_map

        # Label-mode tracking: cheap centroid while running, precise
        # pole-of-inaccessibility when idle.  On the transition running ->
        # idle, force one re-render so the precise labels actually appear
        # (otherwise the last frame's approximate labels persist).
        labels_should_be_fast = status in (
            AlgorithmStatus.RUNNING, AlgorithmStatus.PARTITIONING,
        )
        if (self._labels_were_fast and not labels_should_be_fast
                and self.map_view is not None
                and self.map_view.show_labels):
            map_needs = True
        self._labels_were_fast = labels_should_be_fast

        if self.map_view is not None:
            self.map_view.district_label_map = _label_map
        if (map_needs
                and _assignment is not None
                and self.map_view is not None):
            self.map_view.fast_labels = labels_should_be_fast
            self.map_view.render_assignment(_assignment, _n_dist, _initial)

        # ── Status / iteration ────────────────────────────────────────────────
        msg = snap["status_message"]
        if status == AlgorithmStatus.ERROR:
            err = self.state.error_message or msg
            dpg.set_value(
                self._status_txt,
                "ERROR — " + err if err else "ERROR",
            )
            self.theme.retoken(self._status_txt, "error")
        else:
            dpg.set_value(
                self._status_txt,
                "Status: " + status.value + (" -- " + msg if msg else ""),
            )
            self.theme.retoken(self._status_txt, "body")
        cur    = snap["current_iteration"]
        max_it = snap["max_iterations"]
        dpg.set_value(self._iter_txt, f"{cur:,} / {max_it:,}")
        self._update_run_tiles(cur / max_it if max_it > 0 else 0.0)

        # Timer / IPS
        st = self.state.start_time
        et = self.state.end_time
        pt = self.state.pause_time
        if st > 0 and cur > 0:
            if is_running:
                elapsed = time.time() - st
            elif is_paused and pt > 0:
                elapsed = pt - st          # frozen at the moment pause began
            else:
                elapsed = et - st if et > 0 else time.time() - st
            ips = cur / elapsed if elapsed > 0 else 0
            dpg.set_value(self._time_txt, _fmt_dur(elapsed))
            dpg.set_value(self._ips_txt, f"{ips:,.0f}")
            if is_running and ips > 0 and cur < max_it:
                dpg.set_value(self._eta_txt,
                              f"{_fmt_dur((max_it - cur) / ips)} left")
            else:
                dpg.set_value(self._eta_txt, "--")

        # Score
        score   = snap["current_score"]
        best    = snap["best_score"]
        best_it = snap["best_iteration"]
        # A stat tile carries its label above the value, so the value is the
        # number alone and the iteration goes in the tile's note line.
        if score < float("inf"):
            dpg.set_value(self._score_txt, f"{score:,.2f}")
        if best < float("inf"):
            dpg.set_value(self._best_txt, f"{best:,.2f}")
            dpg.set_value(self._best_note, f"at {best_it:,}")

        # Temperature & acceptance
        temp = snap["current_temperature"]
        dpg.set_value(
            self._temp_txt,
            f"Temperature: {temp:.4f}" if temp > 0
            else "Temperature: -- (annealing off)",
        )
        acc = snap["accepted_worse"]
        rej = snap["rejected_worse"]
        total_worse = acc + rej
        if total_worse > 0:
            rate = 100.0 * acc / total_worse
            dpg.set_value(self._acc_txt,
                          f"Entropy: {rate:.1f}%  ({acc:,} / {total_worse:,})")
        else:
            dpg.set_value(self._acc_txt, "Entropy: --")

        dpg.set_value(self._succ_txt,
                      f"Accepted steps: {snap['successful_steps']:,}")
        dpg.set_value(self._flip_txt,
                      f"Flip rate: {snap['current_flip_rate'] * 100.0:.1f}%")

        # ── Score rows, goals and panels ─────────────────────────────────────
        self._refresh_score_rows()
        self._update_plots_and_panels()

        # Keep district-count-dependent sliders bounded
        n_dist_val = dpg.get_value(self._num_districts)
        dpg.configure_item(self._hinge_threshold,  max_value=n_dist_val)
        self._clamp_opportunity_targets(n_dist_val)

        # ── Button states ─────────────────────────────────────────────────────
        # Scores stay editable during a run, and a change now reaches the
        # running chain: _tick_live_reweight publishes it once the edits
        # settle. The settings a chain cannot be handed are locked instead.
        self._sync_seed_controls()
        self._sync_preset_controls()
        self._sync_run_locked_controls()
        self._tick_live_reweight()
        self._tick_calibration()
        self._sync_goals_panel()
        self._sync_calibration_row()
        self._sync_ensemble_controls()
        # One primary action, whichever it is now. Start is unavailable only
        # while partitioning, since Pause and Resume share this button.
        dpg.configure_item(
            self._run_btn, enabled=not is_partitioning,
            label=("Pause" if is_running else "Resume" if is_paused else "Start"))
        dpg.configure_item(self._reset_btn,  enabled=True)
        has_result = self.state.best_assignment is not None
        # Allow saves while paused; block only during active running/partitioning.
        can_save = has_result and not is_running and not is_partitioning
        # Quick Save and Save Metrics left the left column with the redesign;
        # they live in File > Save Assignments / Save District Info, which the
        # same can_save rule gates.
        if self._file_save_asgn_item:
            dpg.configure_item(self._file_save_asgn_item,  enabled=can_save)
            dpg.configure_item(self._file_save_metrics_item, enabled=can_save)
        can_revert = has_result and status in (
            AlgorithmStatus.IDLE, AlgorithmStatus.PAUSED,
            AlgorithmStatus.COMPLETED, AlgorithmStatus.ERROR,
        )
        if can_revert:
            # Nothing to revert to when the plan on the map already IS the best
            # one -- e.g. just after Revert to Best, or the final iteration was
            # itself the best. Grey the button out in that case.
            with self.state._lock:
                cur  = self.state.current_assignment
                best = self.state.best_assignment
            if cur is not None and best is not None and np.array_equal(cur, best):
                can_revert = False
        dpg.configure_item(self._revert_btn, enabled=can_revert)

        # ── Button nudge / anti-nudge themes ─────────────────────────────────
        graph_ready = self.runner is not None and self.runner.graph is not None
        # The run strip's single primary action keeps the nudge theme whenever
        # it can be pressed; there is one nudge per view.
        dpg.bind_item_theme(
            self._run_btn,
            self.theme.nudge_theme
            if (is_running or is_paused or (graph_ready and not is_busy))
            else self.theme.antinudge_theme,
        )
        # Once a map exists, Reset is a live "start over" action -- give it the
        # neutral default button look so it reads as available, apart from the
        # dark inactive buttons. Before any result there is nothing to reset.
        dpg.bind_item_theme(
            self._reset_btn,
            0 if has_result else self.theme.antinudge_theme,
        )
        # Blue while it would do something; otherwise anti-nudge, which now
        # renders as the dark disabled grey in its disabled state.
        dpg.bind_item_theme(
            self._revert_btn,
            self.theme.nudge_theme if can_revert else self.theme.antinudge_theme,
        )

    def _set_chart_inactive(self, label, inactive):
        """Compact empty chart windows, preserving the user's active height."""
        dpg.configure_item(label, show=inactive)
        saved = getattr(self, "_inactive_chart_heights", None)
        if saved is None:
            saved = self._inactive_chart_heights = {}
        window = dpg.get_item_parent(label)
        if inactive and window not in saved:
            config = dpg.get_item_configuration(window)
            saved[window] = dpg.get_item_height(window) or config.get("height", 336)
            width = dpg.get_item_width(window) or config.get("width", 360)
            dpg.configure_item(label, wrap=max(100, width - 28))
            dpg.configure_item(window, height=110)
        elif not inactive and window in saved:
            dpg.configure_item(window, height=saved.pop(window))

    def _update_run_tiles(self, fraction: float) -> None:
        """Draw progress as _RUN_TILES filled squares.

        Only tiles that changed are recoloured, so a run repaints at most one
        tile per 1/20th of progress instead of a bar every frame.
        """
        lit = max(0, min(_RUN_TILES, int(fraction * _RUN_TILES + 1e-9)))
        if lit == self._run_tiles_lit:
            return
        on = self.theme.color("nudge_button")
        off = self.theme.color("frame_bg")
        for i in range(_RUN_TILES):
            colour = on if i < lit else off
            dpg.configure_item(f"run_tile_{i}", fill=colour, color=colour)
        self._run_tiles_lit = lit

    def _update_plots_and_panels(self) -> None:
        """Per-frame plot redraws and side panel refreshes."""
        self._publish_history_needs()
        self._update_memory_readout()
        # ── Plots ─────────────────────────────────────────────────────────────
        # One lock acquisition, copying only the points recorded since the last
        # frame. If the history rewrote itself (thinned, or reverted to best),
        # the delta is the whole, bounded series and the buffer starts over.
        buffers = [(getattr(self, attr), key, fn) for attr, key, fn in _FEEDS]
        with self.state._lock:
            hist = self.state.history
            deltas = [hist.delta(key, buf.gen, buf.pos)
                      for buf, key, _fn in buffers]
            # Iterations where the run's scoring changed, for the marker lines.
            marks = hist.marks()
        for (buf, key, fn), update in zip(buffers, deltas, strict=True):
            buf.feed(update, fn=fn, drop_nan=key == H.ACCEPTANCE)

        limit = dpg.get_value(self._limit_plots) if self._limit_plots else False

        def _render(buf: _SeriesBuffer, series_tag: str, x_tag: str, y_tag: str,
                    fit_y: bool = True) -> None:
            if not len(buf):
                dpg.set_value(series_tag, [[], []])
                return
            total = len(buf.ys)
            n = min(total, _PLOT_LIMIT) if limit else total   # == len(plot_data)
            # Render-time uniform subsample.  Buffer is untouched; we just
            # don't ship more than ~_RENDER_TARGET points to DPG per frame.
            # Three rules:
            #   1) Only subsample when "Limit plots" is ON.  When OFF, the
            #      user has explicitly asked for the full series and accepts
            #      the perf cost.
            #   2) Pin the stride to ABSOLUTE buffer position (not the
            #      window-relative start), so the set of sampled iterations
            #      is stable across frames as the window slides.  Without
            #      this, the strided sample rotates through `step` phases
            #      and the line flickers vertically each frame.
            #   3) Always include the indices of the windowed min, max, and
            #      last point so fit_axis_data has stable bounds and the
            #      leading edge tracks current data.
            if limit and n > _RENDER_TARGET:
                step = n // _RENDER_TARGET
                # Where this window starts in the full buffer.  Used to
                # offset the stride so absolute positions divisible by
                # `step` remain sampled even as the window slides.
                window_start = max(0, total - _PLOT_LIMIT)
                offset = (step - window_start % step) % step
                # Scan the cached numeric window; only newly appended values
                # need conversion from Python lists.
                xa, ya = buf.arrays()
                xa, ya = xa[total - n:], ya[total - n:]
                sel = np.zeros(n, dtype=bool)
                sel[offset::step] = True
                sel[n - 1] = True
                if not np.isnan(ya).any():              # first min / max, as list.index
                    sel[int(ya.argmin())] = sel[int(ya.argmax())] = True
                elif not np.isnan(ya).all():            # partly gaps: skip them
                    sel[int(np.nanargmin(ya))] = sel[int(np.nanargmax(ya))] = True
                else:                                   # all gaps: retain the first point
                    sel[0] = True
                idx = np.flatnonzero(sel)
                xs, ys = xa[idx].tolist(), ya[idx].tolist()
            else:
                xs, ys = buf.plot_data(limit)
            dpg.set_value(series_tag, [xs, ys])
            self._draw_reweight_marks(y_tag, marks)
            dpg.fit_axis_data(x_tag)
            # Skip y-fit for axes locked to a fixed range (e.g. probability
            # axes [0, 100]) — otherwise fit_axis_data overrides the lock
            # and the line vanishes when data sits on the boundary.
            if fit_y:
                dpg.fit_axis_data(y_tag)

        def _render_county_counts(excess_series, count_series, ceiling_series, x_axis, y_axis):
            buffers = (self._buf_cs_excess, self._buf_cs_clean)
            for buf, series in zip(buffers, (excess_series, count_series)):
                _render(buf, series, x_axis, y_axis, fit_y=False)
            hi = max((max(buf.plot_data(limit)[1], default=0.0) for buf in buffers), default=0.0)
            if len(self._buf_cs_clean):
                target = (self._buf_cs_score.ys[-1] / 10.0
                          - self._buf_cs_excess.ys[-1] + self._buf_cs_clean.ys[-1])
                xs, _ = self._buf_cs_clean.plot_data(limit)
                span = max(1.0, xs[-1] - xs[0])
                starts = xs[0] + np.arange(32) * span / 32
                dash_x = np.column_stack((starts, starts + span / 64)).ravel().tolist()
                dpg.set_value(ceiling_series, [dash_x, [target] * len(dash_x)])
                hi = max(hi, target)
            else:
                dpg.set_value(ceiling_series, [[], []])
            dpg.set_axis_limits(y_axis, 0.0, hi + max(1.0, hi * 0.05))

        if dpg.is_item_shown("panel_score"):
            _render(self._buf_score, "score_series", "score_x", "score_y")
        if dpg.is_item_shown("panel_entropy"):
            _render(self._buf_acc,   "acc_series",   "acc_x",   "acc_y")

        if dpg.is_item_shown("panel_temperature"):
            _render(self._buf_temp, "panel_temp_series", "panel_temp_x", "panel_temp_y")

        cs_on = dpg.get_value(self._cs_enabled)
        if dpg.is_item_shown("panel_county_splits"):
            dpg.configure_item("cs_charts_grp",   show=cs_on)
            self._set_chart_inactive("cs_inactive_lbl", not cs_on)
            if cs_on:
                _render_county_counts("cs_excess_series", "cs_clean_series", "cs_clean_max",
                                      "cs_excess_x", "cs_excess_y")

        pp_on    = dpg.get_value(self._pp_enabled)

        if dpg.is_item_shown("panel_mm"):
            dpg.configure_item("mm_plot_grp",     show=self._has_elections)
            self._set_chart_inactive("mm_inactive_lbl", not self._has_elections)
            if self._has_elections:
                _render(self._buf_mm, "mm_series", "mm_x", "mm_y")
        if dpg.is_item_shown("panel_eg"):
            dpg.configure_item("eg_plot_grp",     show=self._has_elections)
            self._set_chart_inactive("eg_inactive_lbl", not self._has_elections)
            if self._has_elections:
                _render(self._buf_eg, "eg_series", "eg_x", "eg_y")
        if dpg.is_item_shown("panel_pb"):
            dpg.configure_item("pb_plot_grp",     show=self._has_elections)
            self._set_chart_inactive("pb_inactive_lbl", not self._has_elections)
            if self._has_elections:
                _render(self._buf_pb, "pb_series", "pb_x", "pb_y")
        if dpg.is_item_shown("panel_pg"):
            dpg.configure_item("pg_plot_grp",     show=self._has_elections)
            self._set_chart_inactive("pg_inactive_lbl", not self._has_elections)
            if self._has_elections:
                _render(self._buf_pg, "pg_series", "pg_x", "pg_y", fit_y=False)
        if dpg.is_item_shown("panel_dem_seats"):
            dpg.configure_item("seats_plot_grp",     show=self._has_elections)
            self._set_chart_inactive("seats_inactive_lbl", not self._has_elections)
            if self._has_elections:
                _render(self._buf_seats, "seats_series", "seats_x", "seats_y")
        if dpg.is_item_shown("panel_majority"):
            dpg.configure_item("majority_plot_grp",     show=self._has_elections)
            self._set_chart_inactive("majority_inactive_lbl", not self._has_elections)
            if self._has_elections:
                _render(self._buf_maj_dem, "maj_dem_series", "maj_x", "maj_y", fit_y=False)
                _render(self._buf_maj_rep, "maj_rep_series", "maj_x", "maj_y", fit_y=False)
        if dpg.is_item_shown("panel_hinge"):
            # Hinge needs election data AND an established threshold (the Hinge
            # score enabled); otherwise it charts against a stale default.
            hinge_active = self._has_elections and dpg.get_value(self._hinge_enabled)
            dpg.configure_item("hinge_plot_grp",     show=hinge_active)
            self._set_chart_inactive("hinge_inactive_lbl", not hinge_active)
            if not hinge_active:
                dpg.set_value(
                    "hinge_inactive_lbl",
                    "Load election data to use this panel."
                    if not self._has_elections
                    else "Enable the Hinge score to set a threshold for this panel.")
            if hinge_active:
                _render(self._buf_hinge, "hinge_series", "hinge_x", "hinge_y", fit_y=False)
        if dpg.is_item_shown("panel_pp"):
            dpg.configure_item("pp_plot_grp",     show=pp_on)
            self._set_chart_inactive("pp_inactive_lbl", not pp_on)
            if pp_on:
                _render(self._buf_pp, "pp_series", "pp_x", "pp_y")
        if dpg.is_item_shown("panel_reock"):
            reock_on = dpg.get_value(self._reock_enabled)
            dpg.configure_item("reock_plot_grp",     show=reock_on)
            self._set_chart_inactive("reock_inactive_lbl", not reock_on)
            if reock_on:
                _render(self._buf_reock, "reock_series", "reock_x", "reock_y")
        if dpg.is_item_shown("panel_representation"):
            repr_on = dpg.get_value(self._representation_enabled)
            dpg.configure_item("representation_plot_grp",     show=repr_on)
            self._set_chart_inactive("representation_inactive_lbl", not repr_on)
            if not repr_on:
                dpg.set_value("representation_inactive_lbl",
                              _race_panel_msg(self, "representation"))
            if repr_on:
                mode = dpg.get_value(self._rep_chart_mode)
                overall = mode == "Overall"
                seats = mode == "Opportunity count"
                target_hi = 0.0
                active_groups = set()
                for group, buf in (("black", self._buf_rep_black_seats),
                                   ("latino", self._buf_rep_latino_seats),
                                   ("asian", self._buf_rep_asian_seats)):
                    tag = f"representation_{group}_target_series"
                    target = self.state.opportunity_targets.get(group, {})
                    provided = self.state.race_groups_provided
                    value = int(round(target.get("ceiling", 0.0)))
                    # A manual target stands in for the heuristic gate, the
                    # same rule the score itself applies.
                    applicable = ((not provided or group in provided)
                                  and target.get("target", 0) >= 1
                                  and bool(target.get("feasible", False)
                                           or target.get("manual", False)))
                    if applicable:
                        active_groups.add(group)
                    dpg.configure_item(f"representation_{group}_series",
                                       show=not overall and applicable)
                    visible = seats and applicable and value > 0 and len(buf) > 0
                    dpg.configure_item(tag, show=visible)
                    if visible:
                        xs, _ = buf.plot_data(limit)
                        span = max(1.0, xs[-1] - xs[0])
                        starts = xs[0] + np.arange(32) * span / 32
                        dash_x = np.column_stack((starts, starts + span / 64)).ravel().tolist()
                        dpg.set_value(tag, [dash_x, [value] * len(dash_x)])
                        target_hi = max(target_hi, value)
                    else:
                        dpg.set_value(tag, [[], []])
                dpg.configure_item("representation_overall_series", show=overall)
                if mode != getattr(self, "_rep_mode", None):
                    self._rep_mode = mode
                    dpg.set_value("representation_chart_help", RADIO_HELP["opportunity"][mode])
                    if overall:
                        dpg.configure_item("representation_y", label="Penalty (0 = best)")
                        dpg.set_axis_limits("representation_y", 0.0, 100.0)
                    elif seats:
                        dpg.configure_item("representation_y",
                                           label="Opportunity count")
                    else:
                        dpg.configure_item("representation_y",
                                           label="Rating (100 = proportional)")
                        dpg.set_axis_limits("representation_y", 0.0, 100.0)
                if overall:
                    # The combined penalty actually minimized in annealing.
                    _render(self._buf_rep_overall, "representation_overall_series",
                            "representation_x", "representation_y", fit_y=False)
                else:
                    bufs = ((self._buf_rep_black_seats, self._buf_rep_latino_seats,
                             self._buf_rep_asian_seats) if seats else
                            (self._buf_rep_black, self._buf_rep_latino, self._buf_rep_asian))
                    _render(bufs[0], "representation_black_series",
                            "representation_x", "representation_y", fit_y=False)
                    _render(bufs[1], "representation_latino_series",
                            "representation_x", "representation_y", fit_y=False)
                    _render(bufs[2], "representation_asian_series",
                            "representation_x", "representation_y", fit_y=False)
                    if seats:
                        # Variable y-axis: fit to the largest opportunity count across the
                        # three groups in the current window, with a little
                        # headroom.  Seats top out at the district count, so the
                        # fixed 0-100 rating scale would bury the lines at the
                        # bottom.
                        hi = target_hi
                        for group, _b in zip(("black", "latino", "asian"), bufs):
                            if group in active_groups and len(_b):
                                hi = max(hi, max(_b.plot_data(limit)[1]))
                        dpg.set_axis_limits("representation_y", 0.0, hi + max(0.1, hi * 0.05))
        if dpg.is_item_shown("popup_representation"):
            self._sync_opportunity_target_rows()
            # Targets are run-constant, so rebuild the text only when they change
            # rather than every frame the popup happens to be open.
            _tg = self.state.opportunity_targets
            if _tg is not getattr(self, "_repr_targets_shown", None):
                self._repr_targets_shown = _tg
                _lines = _opportunity_target_lines(
                    _tg, self.state.race_groups_provided)
                for _item, _row in zip(self._repr_target_lbls,
                                       _lines + [None] * len(self._repr_target_lbls)):
                    dpg.configure_item(_item, show=_row is not None)
                    if _row is not None:
                        dpg.set_value(_item, _row[0])
                        self.theme.retoken(_item, _row[1])
                dpg.configure_item(self._repr_counts_lbl, show=not _lines)
        if dpg.is_item_shown("panel_minority_cohesion"):
            mc_on = dpg.get_value(self._minority_cohesion_enabled)
            dpg.configure_item("minority_cohesion_plot_grp", show=mc_on)
            self._set_chart_inactive("minority_cohesion_inactive_lbl", not mc_on)
            if not mc_on:
                dpg.set_value("minority_cohesion_inactive_lbl",
                              _race_panel_msg(self, "minority_cohesion"))
            if mc_on:
                overall = dpg.get_value(self._cohesion_chart_mode) == "Overall"
                dpg.configure_item("minority_cohesion_overall_series", show=overall)
                for _s in ("minority_cohesion_black_series",
                           "minority_cohesion_latino_series",
                           "minority_cohesion_asian_series"):
                    dpg.configure_item(_s, show=not overall)
                if overall != getattr(self, "_coh_overall_mode", None):
                    self._coh_overall_mode = overall
                    dpg.set_value("minority_cohesion_chart_help",
                                  RADIO_HELP["severance"]["Overall" if overall else "By group"])
                    dpg.configure_item(
                        "minority_cohesion_y",
                        label="Penalty (0 = best)" if overall else "Neighborhood intact (%)")
                    dpg.set_axis_limits("minority_cohesion_y", -4.0, 104.0)
                if overall:
                    # The combined penalty actually minimized in annealing.
                    _render(self._buf_coh_overall, "minority_cohesion_overall_series",
                            "minority_cohesion_x", "minority_cohesion_y", fit_y=False)
                else:
                    _render(self._buf_coh_black,  "minority_cohesion_black_series",
                            "minority_cohesion_x", "minority_cohesion_y", fit_y=False)
                    _render(self._buf_coh_latino, "minority_cohesion_latino_series",
                            "minority_cohesion_x", "minority_cohesion_y", fit_y=False)
                    _render(self._buf_coh_asian,  "minority_cohesion_asian_series",
                            "minority_cohesion_x", "minority_cohesion_y", fit_y=False)
        if dpg.is_item_shown("panel_community_congruence"):
            cc_on = dpg.get_value(self._community_congruence_enabled)
            dpg.configure_item("community_congruence_plot_grp", show=cc_on)
            self._set_chart_inactive("community_congruence_inactive_lbl", not cc_on)
            if not cc_on:
                dpg.set_value("community_congruence_inactive_lbl",
                              _race_panel_msg(self, "community_congruence"))
            if cc_on:
                overall = dpg.get_value(self._congruence_chart_mode) == "Overall"
                dpg.configure_item("community_congruence_overall_series", show=overall)
                for _s in ("community_congruence_black_series",
                           "community_congruence_latino_series",
                           "community_congruence_asian_series"):
                    dpg.configure_item(_s, show=not overall)
                if overall != getattr(self, "_cong_overall_mode", None):
                    self._cong_overall_mode = overall
                    dpg.set_value("community_congruence_chart_help",
                                  RADIO_HELP["dispersion"]["Overall" if overall else "By group"])
                    dpg.configure_item(
                        "community_congruence_y",
                        label="Penalty (0 = best)" if overall else "Congruence (%)")
                    dpg.set_axis_limits("community_congruence_y", 0.0, 100.0)
                if overall:
                    # The combined penalty actually minimized in annealing.
                    _render(self._buf_cong_overall,
                            "community_congruence_overall_series",
                            "community_congruence_x", "community_congruence_y",
                            fit_y=False)
                else:
                    _render(self._buf_cong_black,  "community_congruence_black_series",
                            "community_congruence_x", "community_congruence_y",
                            fit_y=False)
                    _render(self._buf_cong_latino, "community_congruence_latino_series",
                            "community_congruence_x", "community_congruence_y",
                            fit_y=False)
                    _render(self._buf_cong_asian,  "community_congruence_asian_series",
                            "community_congruence_x", "community_congruence_y",
                            fit_y=False)
        if dpg.is_item_shown("panel_hc"):
            hc_on = dpg.get_value(self._hc_enabled)
            dpg.configure_item("hc_plot_grp",     show=hc_on)
            self._set_chart_inactive("hc_inactive_lbl", not hc_on)
            if hc_on:
                _render(self._buf_hc, "hc_series", "hc_x", "hc_y")
        if dpg.is_item_shown("panel_hsplit"):
            hs_on = dpg.get_value(self._hsplit_enabled)
            dpg.configure_item("hsplit_plot_grp",     show=hs_on)
            self._set_chart_inactive("hsplit_inactive_lbl", not hs_on)
            if hs_on:
                counts = dpg.get_value(self._hsplit_chart_mode) == "Counts"
                dpg.configure_item("hsplit_series", show=not counts)
                dpg.configure_item("hsplit_excess_series", show=counts)
                dpg.configure_item("hsplit_scd_series", show=counts)
                dpg.configure_item("hsplit_target_series", show=counts)
                dpg.configure_item("hsplit_y", label="Count" if counts else "Penalty (0 = best)")
                if counts:
                    _render_county_counts("hsplit_excess_series", "hsplit_scd_series",
                                          "hsplit_target_series", "hsplit_x", "hsplit_y")
                else:
                    _render(self._buf_hsplit, "hsplit_series", "hsplit_x", "hsplit_y",
                            fit_y=False)
                    hi = max(self._buf_hsplit.plot_data(limit)[1], default=0.0)
                    dpg.set_axis_limits("hsplit_y", 0.0, hi + max(0.01, hi * 0.05))
        if dpg.is_item_shown("panel_hprop"):
            dpg.configure_item("hprop_plot_grp",     show=self._has_elections)
            self._set_chart_inactive("hprop_inactive_lbl", not self._has_elections)
            if self._has_elections:
                _render(self._buf_hprop, "hprop_series", "hprop_x", "hprop_y")
                _render(self._buf_inversion, "hprop_inversion_series", "hprop_x", "hprop_y")
        if dpg.is_item_shown("panel_hcmp"):
            dpg.configure_item("hcmp_plot_grp",     show=self._has_elections)
            self._set_chart_inactive("hcmp_inactive_lbl", not self._has_elections)
            if self._has_elections:
                _render(self._buf_hcmp, "hcmp_series", "hcmp_x", "hcmp_y")
        if dpg.is_item_shown("panel_popdev"):
            # The ratchet records deviation each iteration (force_pop_components),
            # so the chart has real data even when the score weight is 0.
            popdev_on = (dpg.get_value(self._popdev_enabled)
                         or dpg.get_value(self._tolerance_ratchet_mode) != "Off")
            dpg.configure_item("popdev_plot_grp",     show=popdev_on)
            self._set_chart_inactive("popdev_inactive_lbl", not popdev_on)
            if popdev_on:
                _render(self._buf_popdev_max,  "popdev_max_series",  "popdev_x", "popdev_y")
                _render(self._buf_popdev_mean, "popdev_mean_series", "popdev_x", "popdev_y")
                # Fit x-axis to data; set y-axis with floor at 0 and reasonable headroom.
                dpg.fit_axis_data("popdev_x")
                y_max = (float(np.nanmax(self._buf_popdev_max.ys))
                         if len(self._buf_popdev_max) else 1.0)
                dpg.set_axis_limits("popdev_y", 0.0, max(y_max * 1.15, 2.0))
        if dpg.is_item_shown("panel_alignment"):
            align_on = (dpg.get_value(self._alignment_enabled)
                        and self.runner is not None
                        and self.runner.alignment_data is not None)
            values = dpg.get_value(self._alignment_chart_mode) == "Districts"
            dpg.configure_item("alignment_plot_grp", show=align_on and not values)
            self._set_chart_inactive("alignment_inactive_lbl", not align_on)
            dpg.configure_item("alignment_values_plot_grp", show=align_on and values)
            if align_on:
                _render(self._buf_align_penalty, "alignment_penalty_series",
                        "alignment_x", "alignment_y")
                _render(self._buf_align_mean, "alignment_mean_series",
                        "alignment_values_x", "alignment_value_y")
                _render(self._buf_align_min, "alignment_min_series",
                        "alignment_values_x", "alignment_value_y")
                dpg.set_axis_limits("alignment_y", 0.0, 100.0)
                dpg.set_axis_limits("alignment_value_y", 0.0, 100.0)
        if dpg.is_item_shown("panel_cut_edges"):
            _render(self._buf_cuts, "cuts_series", "cuts_x", "cuts_y")

        # Score Contributors panel — live bars grouped by goal. score_breakdown
        # holds raw weighted contributions; shares are derived here over every
        # contributor, so the goal shares on screen sum to 100.
        if dpg.is_item_shown("panel_score_contrib"):
            with self.state._lock:
                bd = dict(self.state.score_breakdown)
            preset, states = self._goal_snapshot()
            groups = self._contrib_groups(bd, preset)
            total = sum(v for _k, items in groups for _n, v in items)
            detail = bool(dpg.get_value(self._contrib_detail))
            sig = (detail, self._place_unit, round(total, 4),
                   tuple((k, tuple((n, round(v, 5)) for n, v in items))
                         for k, items in groups),
                   tuple(sorted((g, s.label, s.exact)
                                for g, s in states.items())))
            if sig != self._contrib_sig:
                self._contrib_sig = sig
                self._render_score_contrib(groups, total, states, detail)

        # Partisanship panel — live bar chart from current assignment
        if dpg.is_item_shown("panel_partisanship"):
            from mosaic.gui.map_view import _PARTISAN_BREAKS
            with self.state._lock:
                _pa = (self.state.current_assignment.copy()
                       if self.state.current_assignment is not None else None)
                _pnd = self.state.num_districts
            if (_pa is not None and self.runner is not None
                    and self.runner.election_arrays
                    and len(self.runner.election_arrays[0][0]) == len(_pa)):
                _dem, _gop = self.runner.election_arrays[0]
                _dem_d = np.bincount(_pa, weights=_dem.astype(np.float64), minlength=_pnd)
                _gop_d = np.bincount(_pa, weights=_gop.astype(np.float64), minlength=_pnd)
                _tot_d = _dem_d + _gop_d
                _shares = np.where(_tot_d > 0, _dem_d / _tot_d, 0.5)
                sorted_shares = np.sort(_shares)
                ranks = np.arange(1, len(sorted_shares) + 1, dtype=float)
                bucket_idx = np.searchsorted(_PARTISAN_BREAKS, sorted_shares, side="right") - 1
                bucket_idx = np.clip(bucket_idx, 0, len(_PARTISAN_BREAKS) - 1)
                for bi in range(len(_PARTISAN_BREAKS)):
                    mask = bucket_idx == bi
                    dpg.set_value(
                        self._partisan_bar_series[bi],
                        [ranks[mask].tolist(), (sorted_shares[mask] * 100.0).tolist()],
                    )
                n = len(sorted_shares)
                dpg.set_value("partisan_ref", [[0.5, n + 0.5], [50.0, 50.0]])
                median_x = (n + 1) / 2.0
                dpg.set_value("partisan_median", [[median_x, median_x], [0.0, 100.0]])
                dpg.fit_axis_data("partisan_x")
                dpg.set_axis_limits("partisan_y", 0.0, 100.0)

        # Win Chance panel — same layout, Y = P(D wins) via unified Gaussian model
        if dpg.is_item_shown("panel_win_chance"):
            import math

            from mosaic.gui.map_view import _PARTISAN_BREAKS
            from mosaic.scoring.partisan import k_to_sigma, p_win_gaussian
            with self.state._lock:
                _wa = (self.state.current_assignment.copy()
                       if self.state.current_assignment is not None else None)
                _wnd = self.state.num_districts
            if (_wa is not None and self.runner is not None
                    and self.runner.election_arrays
                    and len(self.runner.election_arrays[0][0]) == len(_wa)):
                _dem, _gop = self.runner.election_arrays[0]
                _dem_d = np.bincount(_wa, weights=_dem.astype(np.float64), minlength=_wnd)
                _gop_d = np.bincount(_wa, weights=_gop.astype(np.float64), minlength=_wnd)
                _tot_d = _dem_d + _gop_d
                _shares = np.where(_tot_d > 0, _dem_d / _tot_d, 0.5)
                _sigma_d    = k_to_sigma(dpg.get_value(self._win_prob))
                _sigma_comb = math.sqrt(dpg.get_value(self._swing_sigma) ** 2 + _sigma_d ** 2)
                _p_win = p_win_gaussian(_shares, _sigma_comb)
                sorted_p = np.sort(_p_win)
                wranks = np.arange(1, len(sorted_p) + 1, dtype=float)
                wbucket = np.searchsorted(_PARTISAN_BREAKS, sorted_p, side="right") - 1
                wbucket = np.clip(wbucket, 0, len(_PARTISAN_BREAKS) - 1)
                for bi in range(len(_PARTISAN_BREAKS)):
                    mask = wbucket == bi
                    dpg.set_value(
                        self._win_chance_bar_series[bi],
                        [wranks[mask].tolist(), (sorted_p[mask] * 100.0).tolist()],
                    )
                wn = len(sorted_p)
                dpg.set_value("win_chance_ref", [[0.5, wn + 0.5], [50.0, 50.0]])
                wmedian_x = (wn + 1) / 2.0
                dpg.set_value("win_chance_median", [[wmedian_x, wmedian_x], [0.0, 100.0]])
                dpg.fit_axis_data("win_chance_x")
                dpg.set_axis_limits("win_chance_y", 0.0, 100.0)

        self._update_likely_seats()

        # District Info table — per-district metrics for the current plan
        self._update_district_info_table()

        # Phase plot (metric-vs-metric trajectory); no-op unless its panel is open
        self._update_phase_plot()

        # ── Keyboard shortcuts (Ctrl+N/O/S/W) ─────────────────────────────────
        _ctrl = (dpg.is_key_down(dpg.mvKey_LControl)
                 or dpg.is_key_down(dpg.mvKey_RControl))
        if _ctrl:
            if dpg.is_key_pressed(dpg.mvKey_N):
                self._on_new()
            elif dpg.is_key_pressed(dpg.mvKey_O):
                self._on_import_shapefile()
            elif dpg.is_key_pressed(dpg.mvKey_S):
                # Gate the Save shortcut like the Save button: a result exists
                # and no run is active (see _update_ui's can_save).
                _status = self.state.get_all()["status"]
                if (self.state.best_assignment is not None
                        and _status != AlgorithmStatus.RUNNING
                        and _status != AlgorithmStatus.PARTITIONING):
                    self._on_file_save_assignments()
            elif dpg.is_key_pressed(dpg.mvKey_W):
                self._on_close()

    def _update_memory_readout(self) -> None:
        """Run details: this process's memory, once a second and only while
        the popup is open (reading it is a system call)."""
        if not dpg.is_item_shown("popup_run_details"):
            return
        now = time.monotonic()
        if now - self._mem_at < 1.0:
            return
        self._mem_at = now
        from mosaic.memory import format_bytes, rss_bytes
        with self.state._lock:
            held = self.state.history.nbytes
            stride = self.state.history.stride
        text = f"Memory: {format_bytes(rss_bytes())}"
        text += f"  (chart history {format_bytes(held)}"
        text += f", one point per {stride:,} iterations)" if stride > 1 else ")"
        dpg.set_value(self._mem_txt, text)

    def _publish_history_needs(self) -> None:
        """Tell the run which series the open charts need recorded. Only
        written when it changes; the run reads it when a point is due."""
        need = set()
        for panel, keys in _PANEL_SERIES.items():
            if dpg.does_item_exist(panel) and dpg.is_item_shown(panel):
                need.update(keys)
        if dpg.does_item_exist("panel_phase") and dpg.is_item_shown("panel_phase"):
            need.update((_PHASE_ATTR[self._phase_x_label],
                         _PHASE_ATTR[self._phase_y_label]))
        need = frozenset(need)
        if need != self._history_need:
            self._history_need = need
            with self.state._lock:
                self.state.history.extra = need

    def _reweight_line_theme(self):
        """Theme for the reweight marker: one thin line in the separator
        colour, which is this palette's quietest rule. Rebuilt after a theme
        swap so the marker follows light and dark like everything else."""
        name = self.theme.palette.name
        if getattr(self, "_reweight_theme_for", None) != name:
            with dpg.theme() as theme:
                with dpg.theme_component(dpg.mvInfLineSeries):
                    dpg.add_theme_color(dpg.mvPlotCol_Line,
                                        self.theme.color("separator"),
                                        category=dpg.mvThemeCat_Plots)
                    dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 1.0,
                                        category=dpg.mvThemeCat_Plots)
            self._reweight_theme = theme
            self._reweight_theme_for = name
        return self._reweight_theme

    def _draw_reweight_marks(self, y_tag: str, marks) -> None:
        """A thin vertical line on one chart at each reweight.

        The series before and after such a line were not produced by the same
        objective, which is worth saying on the chart rather than only in the
        log. Created on the chart's own axis the first time that chart is
        drawn, so no panel builder has to know about it, and rewritten only
        when the markers change.
        """
        if not marks and y_tag not in getattr(self, "_marks_drawn", {}):
            return
        drawn = self._marks_drawn
        if drawn.get(y_tag) == marks:
            return
        tag = f"{y_tag}_reweight"
        if not dpg.does_item_exist(tag):
            if not dpg.does_item_exist(y_tag):
                return
            dpg.add_inf_line_series([], tag=tag, parent=y_tag,
                                    label="##reweight")
            dpg.bind_item_theme(tag, self._reweight_line_theme())
        dpg.set_value(tag, [list(marks)])
        drawn[y_tag] = marks

    def _clear_all_series(self) -> None:
        """Clear local history buffers and blank all DPG plot series."""
        for buf in (
            self._buf_score, self._buf_acc, self._buf_temp,
            self._buf_cs_score, self._buf_cs_excess, self._buf_cs_clean,
            self._buf_mm, self._buf_eg, self._buf_seats,
            self._buf_pb, self._buf_pg,
            self._buf_pp, self._buf_reock, self._buf_hc,
            self._buf_hsplit, self._buf_hprop, self._buf_hcmp,
            self._buf_popdev,
            self._buf_popdev_max, self._buf_popdev_mean, self._buf_cuts,
            self._buf_align_penalty, self._buf_align_mean, self._buf_align_min,
            self._buf_maj_dem, self._buf_maj_rep, self._buf_hinge,
            self._buf_inversion,
            self._buf_rep_black, self._buf_rep_latino, self._buf_rep_asian,
            self._buf_rep_black_seats, self._buf_rep_latino_seats, self._buf_rep_asian_seats,
            self._buf_coh_black, self._buf_coh_latino, self._buf_coh_asian,
            self._buf_cong_black, self._buf_cong_latino, self._buf_cong_asian,
            self._buf_rep_overall, self._buf_coh_overall, self._buf_cong_overall,
        ):
            buf.clear()
        empty = [[], []]
        for tag in (
            "score_series", "acc_series", "panel_temp_series",
            "cs_excess_series", "cs_clean_series",
            "mm_series", "eg_series", "seats_series",
            "pb_series", "pg_series",
            "pp_series", "reock_series",
            "hc_series", "hsplit_series", "hprop_series", "hprop_inversion_series",
            "hsplit_excess_series", "hsplit_scd_series",
            "hsplit_target_series",
            "hcmp_series", "cs_clean_max", "partisan_median", "win_chance_median",
            "popdev_max_series", "popdev_mean_series", "cuts_series",
            "alignment_penalty_series", "alignment_mean_series", "alignment_min_series",
            "maj_dem_series", "maj_rep_series", "hinge_series",
            "representation_black_series",
            "representation_latino_series",
            "representation_asian_series",
            "representation_black_target_series", "representation_latino_target_series",
            "representation_asian_target_series",
            "minority_cohesion_black_series",
            "minority_cohesion_latino_series",
            "minority_cohesion_asian_series",
            "community_congruence_black_series",
            "community_congruence_latino_series",
            "community_congruence_asian_series",
            "representation_overall_series",
            "minority_cohesion_overall_series",
            "community_congruence_overall_series",
        ):
            dpg.set_value(tag, empty)
        for ax in (
            "score_x", "score_y", "acc_x", "acc_y",
            "panel_temp_x", "panel_temp_y",
            "cs_excess_x", "cs_excess_y",
            "mm_x", "mm_y", "eg_x", "eg_y",
            "pb_x", "pb_y", "pg_x", "pg_y",
            "seats_x", "seats_y",
            "pp_x", "pp_y", "popdev_x", "popdev_y", "cuts_x", "cuts_y",
            "reock_x", "reock_y", "hc_x", "hc_y", "hsplit_x", "hsplit_y",
            "hprop_x", "hprop_y", "hcmp_x", "hcmp_y",
            "representation_x", "representation_y",
            "minority_cohesion_x", "minority_cohesion_y",
            "community_congruence_x", "community_congruence_y",
            "partisan_x", "win_chance_x",
            "alignment_x", "alignment_y", "alignment_value_y", "alignment_values_x",
            "maj_x", "maj_y", "hinge_x", "hinge_y",
        ):
            dpg.set_axis_limits_auto(ax)
        # Re-pin axes whose ranges should always be locked (probability bands).
        # Without this, set_axis_limits_auto above releases them and the next
        # render auto-fits to whatever the data happens to be, causing the
        # axis to "stick" at a small range like [0, 0.1].
        dpg.set_axis_limits("pp_y",     0.0, 100.0)
        dpg.set_axis_limits("maj_y",    0.0, 100.0)
        dpg.set_axis_limits("hinge_y",  0.0, 100.0)
        dpg.set_axis_limits("popdev_y", 0.0, 5.0)
        dpg.set_axis_limits("alignment_y", 0.0, 100.0)
        dpg.set_axis_limits("alignment_value_y", 0.0, 100.0)
        dpg.set_axis_limits("pg_y", 0.0, 100.0)
        for ax in ("reock_y", "hc_y", "hprop_y", "hcmp_y"):
            dpg.set_axis_limits(ax, 0.0, 100.0)
        for ax in ("representation_y", "community_congruence_y"):
            dpg.set_axis_limits(ax, 0.0, 100.0)
        dpg.set_axis_limits("minority_cohesion_y", -4.0, 104.0)
        for tag in self._marks_drawn:
            marker = f"{tag}_reweight"
            if dpg.does_item_exist(marker):
                dpg.set_value(marker, [[]])
        self._marks_drawn = {}
        self._rep_mode = None
        self._phase_prev_n = 0
        self._phase_lim = None
        for k in range(_PHASE_BANDS):
            dpg.set_value(f"phase_band_{k}", empty)
        dpg.set_value("phase_head", empty)
        for s in self._partisan_bar_series:
            dpg.set_value(s, empty)
        for s in self._win_chance_bar_series:
            dpg.set_value(s, empty)
        for i, s in enumerate(self._contrib_bar_series):
            dpg.set_value(s, [[0.0], [float(i + 1)]])   # horizontal: value, slot
        for i, s in enumerate(self._contrib_goal_series):
            dpg.set_value(s, [[0.0], [float(i + 1)]])
        self._contrib_sig = None

    # ── Shapefile info label ──────────────────────────────────────────────────

    def _update_shp_info_label(self) -> None:
        """Refresh the shapefile status line after a successful load."""
        if self.runner is None:
            return
        # Prefer inspection path; fall back to state for the display label.
        insp = self.runner._pending_inspection
        path = (getattr(insp, "path", None) or self.state.shapefile_path or "")
        stem = Path(path).stem if path else "shapefile"
        # The row already carries the "Loaded" label, so the value is the name.
        dpg.set_value(self._shp_info, stem)
        self.theme.retoken(self._shp_info, "body")

        # Read actual loaded state from runner — avoids races on _loaded_config.
        # A score row draws its own name colour and phrase from these checkboxes
        # in _refresh_score_rows, so all this has to do is say which scores this
        # map can support. An unusable score stays listed and unchecked.
        has_county = self.runner.county_array is not None
        # The place scores need the column to hold more than one place, not a
        # column named for counties: with a single value every one of them is
        # identically 0 for every plan, which reads as a perfect score while
        # measuring nothing. Map overlays still key off has_county, since
        # drawing one outline is harmless.
        has_place = self._has_place_column()
        has_elections = bool(self.runner.election_arrays)
        self._has_elections = has_elections
        has_race = self.runner.vap_data is not None
        self._has_race = has_race
        _applic = self.state.race_score_applicable

        usable = {
            "_alignment_enabled": self.runner.alignment_data is not None,
            "_cs_enabled": has_place,
            "_hsplit_enabled": has_place,
            "_county_bias_enabled": has_place,
            "_mm_enabled": has_elections,
            "_eg_enabled": has_elections,
            "_pb_enabled": has_elections,
            "_pg_enabled": has_elections,
            "_hprop_enabled": has_elections,
            "_hcmp_enabled": has_elections,
            "_seats_enabled": has_elections,
            "_majority_enabled": has_elections,
            "_hinge_enabled": has_elections,
            "_representation_enabled":
                has_race and _applic.get("representation", True) is not False,
            "_minority_cohesion_enabled":
                has_race and _applic.get("minority_cohesion", True) is not False,
            "_community_congruence_enabled":
                has_race and _applic.get("community_congruence", True) is not False,
        }
        for attr, ok in usable.items():
            chk = getattr(self, attr)
            dpg.configure_item(chk, enabled=ok)
            if not ok and dpg.get_value(chk):
                dpg.set_value(chk, False)
        self._scores_sig = None

        dpg.configure_item(self._county_overlay, enabled=has_county)
        dpg.configure_item(self._splits_view, enabled=has_county)
        if not has_county and dpg.get_value(self._county_overlay):
            dpg.set_value(self._county_overlay, False)
        if not has_county and dpg.get_value(self._splits_view):
            dpg.set_value(self._splits_view, False)
            if self.map_view:
                self.map_view.splits_view = False
        # Enable/disable county splits panel menu item; close if now unavailable
        dpg.configure_item(self._panel_cs_item, enabled=has_county)
        if not has_county and dpg.is_item_shown("panel_county_splits"):
            dpg.set_value(self._panel_cs_item, False)
            dpg.configure_item("panel_county_splits", show=False)

        # Map-fill availability relabels combo entries and clears a selection
        # whose required data is absent.
        # PP alone is enough to shade compactness -- the overlay blends in Reock
        # when reock_data is present and falls back to PP-only when it isn't, so
        # don't gate on Reock.
        has_compact = self.runner.pp_data is not None
        has_pops = self.runner.populations is not None
        self._sync_fill_availability(
            elections=has_elections, race=has_race,
            compact=has_compact, pops=has_pops,
        )
        if has_elections and self._restore_partisan_on_load:
            self._restore_partisan_on_load = False
            # Elections loaded from Recent; the user picks the fill manually to
            # avoid an unintended overlay.

        # Enable/disable partisan panel menu items; close any open ones when unavailable
        for item_tag, panel_tag in [
            (self._panel_mm_item,         "panel_mm"),
            (self._panel_eg_item,         "panel_eg"),
            (self._panel_pb_item,         "panel_pb"),
            (self._panel_pg_item,         "panel_pg"),
            (self._panel_hprop_item,      "panel_hprop"),
            (self._panel_seats_item,      "panel_dem_seats"),
            (self._panel_hcmp_item,       "panel_hcmp"),
            (self._panel_majority_item,   "panel_majority"),
            (self._panel_hinge_item,      "panel_hinge"),
        ]:
            dpg.configure_item(item_tag, enabled=has_elections)
            if not has_elections and dpg.is_item_shown(panel_tag):
                dpg.set_value(item_tag, False)
                dpg.configure_item(panel_tag, show=False)
        # Same for the demographic panels, gated on race data instead.
        for item_tag, panel_tag in [
            (self._panel_representation_item,       "panel_representation"),
            (self._panel_minority_cohesion_item,    "panel_minority_cohesion"),
            (self._panel_community_congruence_item, "panel_community_congruence"),
        ]:
            dpg.configure_item(item_tag, enabled=has_race)
            if not has_race and dpg.is_item_shown(panel_tag):
                dpg.set_value(item_tag, False)
                dpg.configure_item(panel_tag, show=False)
        self._set_preset_info([])   # notes described the previous map

    # ── Popup toggle callbacks ────────────────────────────────────────────────
