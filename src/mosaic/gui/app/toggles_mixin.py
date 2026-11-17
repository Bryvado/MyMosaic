"""Series/panel visibility toggles, tooltips, and hints."""
from ._common import _HINTS, dpg


class TogglesMixin:
    """Series/panel visibility toggles, tooltips, and hints."""

    def _on_ann_toggle(self):
        dpg.configure_item("ann_body",
                           show=dpg.get_value(self._ann_enabled))

    def _on_cool_mode(self):
        guided = dpg.get_value(self._cool_mode) == "Guided (recommended)"
        dpg.configure_item("guided_controls", show=guided)
        dpg.configure_item("static_controls", show=not guided)

    def _on_launch_watch_toggle(self):
        dpg.configure_item("launch_watch_controls",
                           show=dpg.get_value(self._launch_watch_enabled))

    # ── Score toggles ─────────────────────────────────────────────────────────
    # A score row draws its own name colour, phrase and value from the widgets
    # in _refresh_score_rows, so a toggle only has to say "something changed".
    # The three below keep a body because enabling them does more than that.

    def _score_changed(self):
        """Invalidate the row list and the Goals panel after a score edit."""
        self._scores_sig = None
        self._goals_sig = None
        self._goal_snap_frame = -1

    _on_cut_toggle = _score_changed
    _on_cs_toggle = _score_changed
    _on_pp_toggle = _score_changed
    _on_reock_toggle = _score_changed
    _on_representation_toggle = _score_changed
    _on_minority_cohesion_toggle = _score_changed
    _on_community_congruence_toggle = _score_changed
    _on_hc_toggle = _score_changed
    _on_hprop_toggle = _score_changed
    _on_hcmp_toggle = _score_changed
    _on_mm_toggle = _score_changed
    _on_eg_toggle = _score_changed
    _on_pb_toggle = _score_changed
    _on_pg_toggle = _score_changed
    _on_seats_toggle = _score_changed
    _on_majority_toggle = _score_changed
    _on_hinge_toggle = _score_changed
    _on_popdev_score_toggle = _score_changed
    _on_alignment_toggle = _score_changed

    def _on_hsplit_toggle(self):
        """County Congruence pairs with County-Edge Bias: switching the score
        on switches the bias on too, as it always has. Independent from then
        on."""
        self._score_changed()
        if dpg.get_value(self._hsplit_enabled):
            dpg.set_value(self._county_bias_enabled, True)
            self._on_county_bias_toggle()

    def _on_alignment_focus(self):
        # Party restrictions apply only to a party's voters.
        neutral = dpg.get_value(self._alignment_focus) == "All residents"
        if neutral:
            dpg.set_value(self._alignment_restrict, False)
        dpg.configure_item(self._alignment_restrict, enabled=not neutral)
        dpg.configure_item("alignment_party_controls", show=not neutral)

    def _on_majority_dem_chk(self):
        if dpg.get_value(self._majority_dem_chk):
            dpg.set_value(self._majority_rep_chk, False)
        self._score_changed()

    def _on_majority_rep_chk(self):
        if dpg.get_value(self._majority_rep_chk):
            dpg.set_value(self._majority_dem_chk, False)
        self._score_changed()

    def _on_hinge_dem_chk(self):
        if dpg.get_value(self._hinge_dem_chk):
            dpg.set_value(self._hinge_rep_chk, False)

    def _on_hinge_rep_chk(self):
        if dpg.get_value(self._hinge_rep_chk):
            dpg.set_value(self._hinge_dem_chk, False)

    def _on_county_bias_toggle(self):
        """County-Edge Bias lives inside the County Congruence row, so it keeps
        its own label colour and its own controls."""
        en = dpg.get_value(self._county_bias_enabled)
        self.theme.retoken(self._county_bias_lbl,
                           "accent_green" if en else "disabled")
        dpg.configure_item("county_bias_controls", show=en)

    def _hint(self, widget: int | str, key: str, delay: float = 0.6) -> None:
        """Attach a hover tooltip to ``widget`` using the named entry from ``_HINTS``.

        ``delay`` is the hover time (seconds) before the tooltip appears.
        """
        text = _HINTS.get(key)
        if text is None:
            return
        with dpg.tooltip(parent=widget, delay=delay):
            dpg.add_text(text, wrap=320)

    def _tooltip(self, widget: int | str, text: str, delay: float = 0.4) -> None:
        """Attach a hover tooltip with literal ``text`` (no _HINTS lookup)."""
        with dpg.tooltip(parent=widget, delay=delay):
            dpg.add_text(text, wrap=320)

    def _show_panel(self, panel_tag: str, menu_item) -> None:
        visible = dpg.is_item_shown(panel_tag)
        dpg.set_value(menu_item, not visible)
        dpg.configure_item(panel_tag, show=not visible)

    def _on_panel_temp_toggle(self):
        dpg.configure_item(
            "panel_temperature", show=dpg.get_value(self._panel_temp_item),
        )

    def _on_temp_panel_close(self):
        dpg.set_value(self._panel_temp_item, False)

    def _on_panel_cs_toggle(self):
        dpg.configure_item("panel_county_splits", show=dpg.get_value(self._panel_cs_item))

    def _on_panel_partisan_toggle(self):
        dpg.configure_item("panel_partisanship", show=dpg.get_value(self._panel_partisan_item))

    def _on_panel_win_chance_toggle(self):
        dpg.configure_item("panel_win_chance", show=dpg.get_value(self._panel_win_chance_item))

    def _on_panel_mm_toggle(self):
        dpg.configure_item("panel_mm", show=dpg.get_value(self._panel_mm_item))

    def _on_panel_eg_toggle(self):
        dpg.configure_item("panel_eg", show=dpg.get_value(self._panel_eg_item))

    def _on_panel_pb_toggle(self):
        dpg.configure_item("panel_pb", show=dpg.get_value(self._panel_pb_item))

    def _on_panel_pg_toggle(self):
        dpg.configure_item("panel_pg", show=dpg.get_value(self._panel_pg_item))

    def _on_panel_seats_toggle(self):
        dpg.configure_item("panel_dem_seats", show=dpg.get_value(self._panel_seats_item))

    def _on_panel_pp_toggle(self):
        dpg.configure_item("panel_pp", show=dpg.get_value(self._panel_pp_item))

    def _on_panel_reock_toggle(self):
        dpg.configure_item("panel_reock", show=dpg.get_value(self._panel_reock_item))

    def _on_panel_representation_toggle(self):
        dpg.configure_item("panel_representation",
                           show=dpg.get_value(self._panel_representation_item))

    def _on_panel_minority_cohesion_toggle(self):
        dpg.configure_item("panel_minority_cohesion",
                           show=dpg.get_value(self._panel_minority_cohesion_item))

    def _on_panel_community_congruence_toggle(self):
        dpg.configure_item(
            "panel_community_congruence",
            show=dpg.get_value(self._panel_community_congruence_item))

    def _on_panel_hc_toggle(self):
        dpg.configure_item("panel_hc", show=dpg.get_value(self._panel_hc_item))

    def _on_panel_hsplit_toggle(self):
        dpg.configure_item("panel_hsplit", show=dpg.get_value(self._panel_hsplit_item))

    def _on_panel_hprop_toggle(self):
        dpg.configure_item("panel_hprop", show=dpg.get_value(self._panel_hprop_item))

    def _on_panel_hcmp_toggle(self):
        dpg.configure_item("panel_hcmp", show=dpg.get_value(self._panel_hcmp_item))

    def _on_panel_popdev_toggle(self):
        dpg.configure_item("panel_popdev", show=dpg.get_value(self._panel_popdev_item))

    def _on_panel_alignment_toggle(self):
        dpg.configure_item("panel_alignment", show=dpg.get_value(self._panel_alignment_item))

    def _on_panel_cuts_toggle(self):
        dpg.configure_item("panel_cut_edges", show=dpg.get_value(self._panel_cuts_item))

    def _on_panel_majority_toggle(self):
        dpg.configure_item("panel_majority", show=dpg.get_value(self._panel_majority_item))

    def _on_panel_hinge_toggle(self):
        dpg.configure_item("panel_hinge", show=dpg.get_value(self._panel_hinge_item))

    def _on_panel_contrib_toggle(self):
        dpg.configure_item("panel_score_contrib", show=dpg.get_value(self._panel_contrib_item))

    def _on_panel_district_toggle(self):
        showing = dpg.get_value(self._panel_district_item)
        dpg.configure_item("panel_district_info", show=showing)
        # Force a fresh update next time we render the panel.
        self._dist_info_last_iter = -1

    def _on_district_panel_close(self):
        dpg.set_value(self._panel_district_item, False)
        self._dist_info_last_iter = -1

    def _on_dist_info_interval_change(self):
        # Lower threshold should take effect immediately.
        self._dist_info_last_iter = -1
