"""Goals panel: plain-language scoring intents layered over the score rows.

A goal row says what the map should prioritize and how strongly; the detailed
score rows underneath stay the single source of truth. Every edit here is a
round trip through them: read the widgets into a preset, let ``mosaic.goals``
rewrite the sections one goal owns, push the result back through the same
``_apply_preset`` path a preset file takes. Nothing in the runner changes, and
a goal's strength is read back out of the weights rather than stored, so the
two directions cannot drift apart.
"""
import json

from mosaic import goals as G

from ._common import (
    _CONTRIB_BAR_METRICS,
    _CONTRIB_METRIC_SECTION,
    _GOAL_ORDER,
    _SETTINGS_DIR,
    _SETUP_W,
    dpg,
    log,
)

# The strength dropdown lists the ladder, plus Custom when the weights sit
# off it -- a value the user cannot pick, only arrive at.
_LADDER_ITEMS = tuple(name for name, _x in G.LADDER)
_CUSTOM = "Custom"
_PLACE_UNIT_FILE = _SETTINGS_DIR / "place_unit.json"

# Data a goal needs, phrased for the gap line. _PRESET_NEEDS supplies which
# need applies to which score, so only the wording lives here.
_NEED_TEXT = {
    "election": "election columns",
    "demographic": "demographic columns",
    "county": "a county/place column with more than one value",
    "reference plan": "a reference plan",
}


class GoalsMixin:
    """Goals panel: plain-language scoring intents over the score rows."""

    # ── Place unit ────────────────────────────────────────────────────────────
    # Mosaic's county column holds whatever place unit was loaded into it, so a
    # single-county map with city names in that column is scoring city splits.
    # The unit names itself in the goal's wording; the score, its preset key and
    # its row label ("County Congruence") are untouched.

    def _load_place_unit(self) -> None:
        self._place_unit = G.PLACE_UNIT_DEFAULT
        try:
            data = json.loads(_PLACE_UNIT_FILE.read_text(encoding="utf-8"))
            if isinstance(data, str) and data.strip():
                self._place_unit = data.strip()[:24]
        except Exception:
            pass

    def _save_place_unit(self) -> None:
        try:
            _SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
            _PLACE_UNIT_FILE.write_text(json.dumps(self._place_unit),
                                        encoding="utf-8")
        except Exception:
            pass

    def _on_place_unit_change(self) -> None:
        raw = (dpg.get_value(self._place_unit_input) or "").strip()[:24]
        self._place_unit = raw or G.PLACE_UNIT_DEFAULT
        self._save_place_unit()
        # Goal names, score phrases and the contributors table all name the
        # unit; make each redraw itself.
        self._goals_sig = None
        self._scores_sig = None
        self._contrib_sig = None

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build_goals_column(self) -> None:
        """Goals in the left column: one line per goal, name and a strength.

        The fine-tune slider moves into a small popup opened from the row, so
        the common case -- picking a named strength -- is one click and the
        whole set fits a 300px column.
        """
        self._goal_rows: dict[str, dict] = {}
        self._goal_tips: dict = {}
        self._goals_sig = None
        with dpg.group(horizontal=True):
            self.theme.text("Goals", "heading")
            dpg.add_spacer(width=_SETUP_W - 190)
            btn = dpg.add_button(label="Starting points\u2026", width=120,
                                 callback=lambda: dpg.configure_item(
                                     "popup_goal_starters", show=True))
            dpg.bind_item_theme(btn, self.theme.antinudge_theme)
        self._tooltip(btn, "A named set of goal strengths, applied together.")
        dpg.add_separator()
        self._goal_conflict = self.theme.text("", "warning",
                                              wrap=_SETUP_W - 24, show=False)
        for gid in _GOAL_ORDER:
            self._build_goal_row(G.GOALS_BY_ID[gid])
        self._build_goal_starters_popup()
        self._build_goal_tune_popup()

    def _build_goal_starters_popup(self) -> None:
        with self._dialog(
            "Starting points", "popup_goal_starters", (380, 180), show=False,
            primary=("Apply", self._on_goal_starter),
            secondary=("Cancel", lambda: dpg.configure_item(
                "popup_goal_starters", show=False)),
        ):
            self.theme.text(
                "Each one replaces every goal's strength, the same as setting "
                "them by hand.", "muted", wrap=350)
            dpg.add_spacer(height=4)
            self._goal_starter = dpg.add_combo(
                items=[name for _sid, name, _s in G.STARTERS],
                default_value=G.STARTERS[0][1], width=-1)

    def _build_goal_tune_popup(self) -> None:
        """One shared fine-tune popup; the row being tuned is held in
        _goal_tuning, so twenty sliders are not built for eight goals."""
        self._goal_tuning = ""
        with self._dialog(
            "Fine-tune", "popup_goal_tune", (360, 170), show=False,
            primary=("Done", lambda: dpg.configure_item(
                "popup_goal_tune", show=False)),
        ):
            self._goal_tune_title = self.theme.text("", "heading")
            self.theme.text(
                "Move between and past the named strengths. The label says "
                "where the weights actually sit.", "muted", wrap=330)
            dpg.add_spacer(height=4)
            self._goal_tune_slider = dpg.add_slider_int(
                default_value=0, min_value=0, max_value=G.STRENGTH_MAX,
                width=-1, format="Off", clamped=True,
                callback=lambda: self._on_goal_strength(self._goal_tuning))

    def _on_goal_tune_open(self, gid: str) -> None:
        self._goal_tuning = gid
        dpg.set_value(self._goal_tune_title,
                      G.GOALS_BY_ID[gid].title(self._place_unit))
        dpg.set_value(self._goal_tune_slider,
                      int(round(self._goal_rows[gid]["strength"])))
        dpg.configure_item("popup_goal_tune", show=True)

    def _build_goal_row(self, goal: "G.Goal") -> None:
        label, blurb = goal.text(self._place_unit)
        row: dict = {"strength": 0.0}
        with dpg.group(tag=f"goal_row_{goal.gid}"):
            with dpg.table(header_row=False, policy=dpg.mvTable_SizingFixedFit,
                           borders_innerH=False, borders_outerH=False,
                           borders_innerV=False, borders_outerV=False,
                           width=-1):
                dpg.add_table_column(width_stretch=True,
                                     init_width_or_weight=1.0)
                dpg.add_table_column(width_fixed=True, init_width_or_weight=108)
                dpg.add_table_column(width_fixed=True, init_width_or_weight=20)
                with dpg.table_row():
                    # "Minority representation" does not fit one line in a
                    # 300px column, so the name wraps as it does in the design.
                    row["title"] = self.theme.text(
                        label, "disabled", wrap=_SETUP_W - 180)
                    with dpg.group():
                        with dpg.group(tag=f"goal_ctl_{goal.gid}"):
                            row["combo"] = dpg.add_combo(
                                items=list(_LADDER_ITEMS), default_value="Off",
                                width=-1,
                                callback=lambda s, a, u: self._on_goal_combo(u),
                                user_data=goal.gid)
                        row["gap"] = self.theme.text("unavailable", "disabled",
                                                     show=False)
                    row["tune"] = dpg.add_button(
                        label="\u2026", width=18,
                        callback=lambda s, a, u: self._on_goal_tune_open(u),
                        user_data=goal.gid)
            self._build_goal_extras(goal, row)
        self._tooltip(row["title"], blurb)
        self._tooltip(row["tune"], "Fine-tune this goal's strength.")
        self._goal_rows[goal.gid] = row

    def _build_goal_extras(self, goal: "G.Goal", row: dict) -> None:
        """The non-weight options a goal owns, hidden while the goal is off."""
        if goal.gid == "favor_party":
            with dpg.group(tag="goal_extra_favor_party", show=False,
                           horizontal=True):
                self.theme.text("Favor", "subheading")
                row["party"] = dpg.add_radio_button(
                    items=["Democrats", "Republicans"], horizontal=True,
                    default_value="Democrats",
                    callback=lambda: self._on_goal_strength("favor_party"),
                )
        elif goal.gid == "continuity":
            with dpg.group(tag="goal_extra_continuity", show=False,
                           horizontal=True):
                self.theme.text("Keep together", "subheading")
                row["focus"] = dpg.add_combo(
                    items=list(self._FOCUS_LABELS.values()),
                    default_value=self._FOCUS_LABELS["none"], width=150,
                    callback=lambda: self._on_goal_strength("continuity"),
                )
            self._tooltip(row["focus"],
                          "Whose continuity Plan Continuity measures: everyone, "
                          "or one party's voters.")

    # ── Edits out: goal -> score widgets ──────────────────────────────────────

    def _apply_goal_preset(self, p: dict) -> None:
        """Push a goal-edited preset onto the score widgets.

        ``_apply_preset`` switches Hinge off, because a preset file carries no
        hinge settings and applying one should not leave a stale threshold
        behind. A goal edit is not a preset load and Hinge belongs to no goal,
        so its row is saved and put back around the call.
        """
        keep = (bool(dpg.get_value(self._hinge_enabled)),
                dpg.get_value(self._w_hinge),
                dpg.get_value(self._hinge_threshold),
                bool(dpg.get_value(self._hinge_dem_chk)),
                bool(dpg.get_value(self._hinge_rep_chk)))
        try:
            missing, other = self._apply_preset(p)
        except ValueError as e:
            log.exception("Goals: could not apply")
            self.state.update(status_message=f"Could not apply goal: {e}")
            return
        if keep[0] and not dpg.get_value(self._hinge_enabled):
            # Every score is always listed now, so there is no row to reveal.
            dpg.set_value(self._hinge_enabled, True)
            self._on_hinge_toggle()
        self._preset_set(self._w_hinge, keep[1])
        self._preset_set(self._hinge_threshold, keep[2])
        dpg.set_value(self._hinge_dem_chk, keep[3])
        dpg.set_value(self._hinge_rep_chk, keep[4])
        self._set_preset_info(missing, other)
        self._goals_sig = None
        self._contrib_sig = None

    def _goal_opts(self, gid: str) -> dict:
        """Party / focus arguments for one goal, read off its own row."""
        row = self._goal_rows.get(gid, {})
        if gid == "favor_party" and "party" in row:
            return {"party": "dem" if dpg.get_value(row["party"]) == "Democrats"
                    else "rep"}
        if gid == "continuity" and "focus" in row:
            inv = {v: k for k, v in self._FOCUS_LABELS.items()}
            return {"party_focus": inv[dpg.get_value(row["focus"])]}
        return {}

    def _on_goal_combo(self, gid: str) -> None:
        """A named strength was picked from the row's dropdown."""
        name = dpg.get_value(self._goal_rows[gid]["combo"])
        if name == _CUSTOM:
            # Custom is what the weights are, not something to apply; put the
            # dropdown back and send the user to the fine-tune popup instead.
            self._goals_sig = None
            self._on_goal_tune_open(gid)
            return
        want = dict(G.LADDER).get(name)
        if want is None:
            return
        self._set_goal_strength(gid, float(want))

    def _on_goal_strength(self, gid: str) -> None:
        """The fine-tune slider moved."""
        if gid:
            self._set_goal_strength(gid, float(dpg.get_value(
                self._goal_tune_slider)))

    def _set_goal_strength(self, gid: str, x: float) -> None:
        if self.runner is None or self.runner.gdf is None:
            self.state.update(status_message="Open a map before setting goals.")
            self._goals_sig = None       # put the dropdown back where it was
            return
        p = G.apply_goal(self._preset_from_widgets(), gid, x,
                         **self._goal_opts(gid))
        self._apply_goal_preset(p)

    def _on_goal_starter(self) -> None:
        dpg.configure_item("popup_goal_starters", show=False)
        name = dpg.get_value(self._goal_starter)
        pick = next((s for _sid, n, s in G.STARTERS if n == name), None)
        if pick is None:
            self.state.update(status_message="Pick a starting point first.")
            return
        if self.runner is None or self.runner.gdf is None:
            self.state.update(status_message="Open a map before setting goals.")
            return
        # A starting point names every goal, so it clears whatever the last one
        # set. Goals this map cannot support are forced off rather than left
        # weighted-but-inert, which would have the panel show "Not available"
        # over a score that is switched on underneath.
        have = self._goal_data_available()
        skipped = [G.GOALS_BY_ID[gid].title(self._place_unit)
                   for gid, x in pick.items()
                   if x > 0 and self._goal_gap(G.GOALS_BY_ID[gid], have)]
        pick = {gid: (0.0 if self._goal_gap(G.GOALS_BY_ID[gid], have) else x)
                for gid, x in pick.items()}
        opts = {**self._goal_opts("favor_party"), **self._goal_opts("continuity")}
        self._apply_goal_preset(
            G.apply_goals(self._preset_from_widgets(), pick, **opts))
        note = f" Skipped {', '.join(skipped)} (not available here)." if skipped else ""
        self.state.update(status_message=f"Applied '{name}'.{note}")

    # ── Edits back: score widgets -> goal labels ──────────────────────────────

    def _goal_data_available(self) -> dict:
        """What this map supplies, in _PRESET_NEEDS vocabulary."""
        r = self.runner
        return {
            "election": bool(getattr(r, "election_arrays", None)),
            "demographic": getattr(r, "vap_data", None) is not None,
            "county": self._has_place_column(),
            "reference plan": getattr(r, "alignment_data", None) is not None,
        }

    def _has_place_column(self) -> bool:
        """County scores apply when the place column has at least two values.

        One value and every county score is identically 0 for every plan -- its
        best -- so it reads as a top score while measuring nothing. Two or more
        and the scores are live, whether that column holds counties, the cities
        of a single county, or any other place unit.
        """
        arr = getattr(self.runner, "county_array", None)
        if arr is None or not len(arr):
            return False
        return int(arr.max()) >= 1          # county_array is dense from 0

    def _goal_gap(self, goal: "G.Goal", have: dict) -> str:
        """Why this goal cannot be used here, or "" when it can be.

        Needs come from ``_PRESET_NEEDS`` and usability from
        ``_preset_chk_usable``, the same two checks a preset load reports
        against, so there is no second copy of either rule.
        """
        secs = [s for s in goal.sections if s in self._goal_chk]

        def short(sec: str) -> str | None:
            """The data this score is missing, or None when it has what it needs."""
            need = self._PRESET_NEEDS.get(sec)
            return need if need is not None and not have[need] else None

        live = [s for s in secs if short(s) is None]
        if secs and not live:
            names = ", ".join(sorted({_NEED_TEXT.get(short(s), short(s))
                                      for s in secs}))
            return f"Not available for this map -- needs {names}."
        # Data is here but the scores have nothing to say about this map: the
        # same check a preset load reports as "not applicable here".
        if live and not any(self._preset_chk_usable(self._goal_chk[s])
                            for s in live):
            return "Not available for this map -- nothing here to measure."
        return ""

    def _goal_snapshot(self) -> tuple[dict, dict]:
        """(preset, goal states) read off the score widgets, once per frame.

        The Goals panel and the Score Contributors panel both want this, and
        either may be open; computing it per frame rather than per reader keeps
        a frame with both open the same cost as a frame with one.
        """
        frame = dpg.get_frame_count()
        if self._goal_snap_frame != frame or self._goal_snap is None:
            preset = self._preset_from_widgets()
            self._goal_snap = (preset, G.goal_states(preset))
            self._goal_snap_frame = frame
        return self._goal_snap

    def _sync_goals_panel(self) -> None:
        """Refresh the goal rows from the score widgets. The rows live in the
        left column and are always on screen, so every DPG write is behind a
        change check and a steady screen costs one signature comparison."""
        preset, states = self._goal_snapshot()
        have = self._goal_data_available()
        gaps = {g.gid: self._goal_gap(g, have) for g in G.GOALS}
        note = G.conflict_note(preset, self._place_unit)
        sig = (self._place_unit, tuple(sorted(
            (gid, round(s.strength, 3), s.exact, s.label) for gid, s in states.items())),
            tuple(sorted(gaps.items())), note,
            )
        if sig == self._goals_sig:
            return
        self._goals_sig = sig

        for gid in _GOAL_ORDER:
            goal = G.GOALS_BY_ID[gid]
            self._sync_goal_row(goal, states[gid], gaps[gid], preset)
        dpg.set_value(self._goal_conflict, note)
        dpg.configure_item(self._goal_conflict, show=bool(note))

    def _sync_goal_row(self, goal: "G.Goal", state: "G.GoalState", gap: str,
                       preset: dict) -> None:
        row = self._goal_rows[goal.gid]
        label, _blurb = goal.text(self._place_unit)
        if dpg.get_value(row["title"]) != label:
            dpg.set_value(row["title"], label)
        self.theme.retoken(row["title"],
                           "disabled" if gap else
                           "accent_green" if state.on else "body")
        dpg.configure_item(f"goal_ctl_{goal.gid}", show=not gap)
        dpg.configure_item(row["tune"], show=not gap)
        dpg.configure_item(row["gap"], show=bool(gap))
        row["gap_text"] = gap
        self._tooltip_set(row["gap"], gap)
        if gap:
            return

        row["strength"] = state.strength
        # "Custom" is only ever shown, never chosen: it is added to the list
        # while the weights sit off the ladder and taken away again after.
        items = list(_LADDER_ITEMS) + ([_CUSTOM] if not state.exact else [])
        if dpg.get_item_configuration(row["combo"])["items"] != items:
            dpg.configure_item(row["combo"], items=items)
        want = state.label if state.exact else _CUSTOM
        if dpg.get_value(row["combo"]) != want:
            dpg.set_value(row["combo"], want)
        self._tooltip_set(row["combo"],
                          state.label if not state.exact else "")

        extra = f"goal_extra_{goal.gid}"
        if dpg.does_item_exist(extra):
            dpg.configure_item(extra, show=state.on)
        if goal.gid == "favor_party" and state.on:
            want = ("Democrats" if preset["dem_seats"]["favor"] == "dem"
                    else "Republicans")
            if dpg.get_value(row["party"]) != want:
                dpg.set_value(row["party"], want)
        elif goal.gid == "continuity" and state.on:
            want = self._FOCUS_LABELS[preset["alignment"]["party_focus"]]
            if dpg.get_value(row["focus"]) != want:
                dpg.set_value(row["focus"], want)

    def _tooltip_set(self, widget, text: str) -> None:
        """Fill in a row tooltip created at build time, or hide it."""
        tip = self._goal_tips.get(widget)
        if tip is None:
            with dpg.tooltip(parent=widget, delay=0.4) as holder:
                tip = (holder, dpg.add_text("", wrap=280))
            self._goal_tips[widget] = tip
        dpg.configure_item(tip[0], show=bool(text))
        if text:
            dpg.set_value(tip[1], text)

    # ── Score Contributors grouping ───────────────────────────────────────────

    def _contrib_groups(self, bd: dict, preset: dict) -> list:
        """[(group key, [(metric, weighted value), ...])], contributors only.

        Each breakdown metric is placed by the section it came from: a goal's
        own section goes to that goal, Hinge to its own row, and the advanced
        sections no goal drives to "Other scores". The three directional metrics
        belong to two goals at once and are placed by their mode, the same rule
        that decides which goal's strength they count towards.
        """
        buckets: dict[str, list] = {k: [] for k in self.CONTRIB_GROUPS}
        for name, _rgba in _CONTRIB_BAR_METRICS:
            value = float(bd.get(name, 0.0) or 0.0)
            if value <= 0.0:
                continue
            section = _CONTRIB_METRIC_SECTION.get(name)
            if section is None:
                key = "_hinge"
            elif section in G.ADVANCED_SECTIONS:
                key = "_other"
            else:
                goal = G.goal_for_section_mode(
                    section, preset.get(section, {}).get("mode"))
                key = goal.gid if goal is not None else "_other"
            buckets[key].append((name, value))
        return [(k, buckets[k]) for k in self.CONTRIB_GROUPS if buckets[k]]

    def _set_contrib_row(self, i: int, name: str, strength: str,
                         share: str) -> None:
        row, cells = self._contrib_table_rows[i]
        for cell, text in zip(cells, (name, strength, share)):
            if dpg.get_value(cell) != text:
                dpg.set_value(cell, text)
        dpg.configure_item(row, show=True)

    def _render_score_contrib(self, groups: list, total: float, states: dict,
                              detail: bool) -> None:
        """Draw the grouped bars and the share table. Only called on a change."""
        scale = 100.0 / total if total > 0.0 else 0.0
        dpg.configure_item(self._contrib_empty, show=not groups)

        if detail:
            order = [(n, v) for _k, items in groups for n, v in items]
            index = {n: i for i, (n, _c) in enumerate(_CONTRIB_BAR_METRICS)}
            self._place_contrib_bars(
                self._contrib_bar_series,
                [(index[n], n, v) for n, v in order], scale)
            for series in self._contrib_goal_series:
                dpg.configure_item(series, show=False)
        else:
            index = {k: i for i, k in enumerate(self.CONTRIB_GROUPS)}
            self._place_contrib_bars(
                self._contrib_goal_series,
                [(index[k], self.contrib_group_title(k),
                  sum(v for _n, v in items)) for k, items in groups], scale)
            for series in self._contrib_bar_series:
                dpg.configure_item(series, show=False)

        row = 0
        for key, items in groups:
            state = states.get(key)
            word = "" if state is None else (
                state.label if state.exact else "Custom")
            self._set_contrib_row(row, self.contrib_group_title(key), word,
                                  f"{sum(v for _n, v in items) * scale:.1f}%")
            row += 1
            if not detail:
                continue
            for name, value in items:
                self._set_contrib_row(row, f"    {name}", "",
                                      f"{value * scale:.1f}%")
                row += 1
        for i in range(row, len(self._contrib_table_rows)):
            dpg.configure_item(self._contrib_table_rows[i][0], show=False)

    @staticmethod
    def _place_contrib_bars(series: list, rows: list, scale: float) -> None:
        """Lay out one bar set top-down and hide the rest of its series.

        Y ascends in the plot, so the first row is drawn at the highest slot.
        """
        n = len(rows)
        dpg.set_axis_ticks("contrib_y", tuple(
            (label, float(n - pos)) for pos, (_i, label, _v) in enumerate(rows)))
        dpg.set_axis_limits("contrib_y", 0.5, max(1, n) + 0.5)
        used = set()
        for pos, (i, _label, value) in enumerate(rows):
            dpg.set_value(series[i], [[value * scale], [float(n - pos)]])
            dpg.configure_item(series[i], show=True)
            used.add(i)
        for i, item in enumerate(series):
            if i not in used:
                dpg.configure_item(item, show=False)
