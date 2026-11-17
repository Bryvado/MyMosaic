"""The score list: twenty one-line rows, grouped, each opening in place.

Replaces the three scrolling columns of tall rows. A closed row is a checkbox,
the score's name, a short phrase saying what it favors, and its weight or
"off". Opening a row reveals the weight slider, the Aim choice where the score
has one, and a button that shows the score's existing chart panel.

The widgets the rest of the app already knows (``_w_*``, ``_*_enabled``,
``_*_dir``) are created here and stay the source of truth, so presets, Goals
and the runner work unchanged. Nothing here caches score state: the row is
redrawn from those widgets whenever they change, which is what keeps the Goals
panel and a hand-edited weight in step.
"""
from ._common import (
    _SCORE_GROUPS,
    _SCORE_NAME_W,
    _SCORE_NEED_PHRASE,
    _SCORE_PANELS,
    _SCORE_ROWS,
    _SCORE_VAL_W,
    dpg,
)

# Plain "+" and "-": Dear PyGui builds its font atlas from the Latin
# ranges, so a triangle (U+25B8) renders as the missing-glyph box.
_CHEVRON_CLOSED = "+"
_CHEVRON_OPEN = "-"
_AIM_ITEMS = ("Fair", "D", "R")


class ScoresMixin:
    """The grouped, collapsible score list in the right-hand column."""

    # ── Lookup ────────────────────────────────────────────────────────────────

    def _score_widgets(self, sec: str) -> tuple[str, str]:
        """(enabled-checkbox attribute, toggle callback name) for a section.

        Hinge is deliberately outside presets, so it is not in _PRESET_SCORES
        and is answered directly.
        """
        if sec == "":
            return "_hinge_enabled", "_on_hinge_toggle"
        for s, chk, cb, *_rest in self._PRESET_SCORES:
            if s == sec:
                return chk, cb
        raise KeyError(sec)

    def _score_phrase(self, entry) -> str:
        """The row's phrase, or what the score needs when it cannot be used."""
        sec, _label, phrase, _grp, _weights, _extras = entry
        chk_attr, _cb = self._score_widgets(sec)
        need = self._PRESET_NEEDS.get(sec)
        if need is not None and not self._goal_data_available().get(need, False):
            phrase = _SCORE_NEED_PHRASE[need]
        elif not self._preset_chk_usable(chk_attr):
            phrase = _SCORE_NEED_PHRASE[""]
        return phrase.replace("{unit}", self._place_unit)

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build_scores_column(self) -> None:
        """The right-hand column: a heading with the on-count, then the rows."""
        self._score_rows: dict[str, dict] = {}
        self._score_fields: dict[str, int | str] = {}
        self._scores_sig = None
        with self._heading_row("Scores"):
            self._scores_count = self.theme.text("0 of 20 on", "muted")
        self._preset_info = self.theme.text("Preset warning", "warning",
                                            show=False)
        with dpg.tooltip(self._preset_info):
            self._preset_info_detail = dpg.add_text("", wrap=320)
        dpg.add_separator()
        with dpg.child_window(width=-1, height=-1, border=False,
                              tag="scores_scroll"):
            for index, group in enumerate(_SCORE_GROUPS):
                if index:
                    dpg.add_spacer(height=6)
                self.theme.text(group, "subheading")
                for entry in _SCORE_ROWS:
                    if entry[3] == index:
                        self._build_score_row(entry)

    def _build_score_row(self, entry) -> None:
        """One score: a closed line, then the panel it opens into.

        The closed line is its own one-row table. Dear PyGui tables have no
        column spanning, so an open panel placed in a cell would be clipped to
        that column; keeping the panel outside the table gives it the full
        width. Fixed column widths are what line the rows up with each other.
        """
        sec, label, _phrase, _grp, weights, extras = entry
        chk_attr, cb_name = self._score_widgets(sec)
        row: dict = {"sec": sec, "label": label, "weights": weights,
                     "extras": extras, "open": False, "chk_attr": chk_attr}

        with dpg.table(header_row=False, policy=dpg.mvTable_SizingFixedFit,
                       borders_innerH=False, borders_outerH=False,
                       borders_innerV=False, borders_outerV=False, width=-1):
            # Switch and chevron share a cell: a column of their own would
            # cost the phrase another 26px, and the phrase is the part that
            # has to stay readable.
            dpg.add_table_column(width_fixed=True, init_width_or_weight=46)
            dpg.add_table_column(width_fixed=True,
                                 init_width_or_weight=_SCORE_NAME_W)
            dpg.add_table_column(width_stretch=True, init_width_or_weight=1.0)
            dpg.add_table_column(width_fixed=True,
                                 init_width_or_weight=_SCORE_VAL_W)
            with dpg.table_row():
                with dpg.group(horizontal=True):
                    setattr(self, chk_attr, dpg.add_checkbox(
                        default_value=(sec == "holistic_compactness"),
                        callback=getattr(self, cb_name)))
                    row["chk"] = getattr(self, chk_attr)
                    row["chevron"] = dpg.add_button(
                        label=_CHEVRON_CLOSED, width=16,
                        callback=lambda s, a, u: self._on_score_row_click(u),
                        user_data=sec)
                row["name"] = self.theme.text(label, "body")
                row["phrase"] = self.theme.text("", "muted")
                row["value"] = self.theme.text("off", "muted")
        self._tooltip(row["chevron"], "Show this score's settings.")
        row["phrase_tip"] = self._text_tooltip(row["phrase"])

        with dpg.group(show=False) as panel:
            row["panel"] = panel
            dpg.add_spacer(height=2)
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=46)
                with dpg.group():
                    self._build_score_weights(sec, label, weights)
                    if extras:
                        self._build_score_extras(sec, extras)
                    dpg.add_spacer(height=2)
                    panel_tag, item_attr = _SCORE_PANELS[sec]
                    dpg.add_button(
                        label="Open chart", width=92,
                        callback=lambda s, a, u: self._show_panel(
                            u[0], getattr(self, u[1])),
                        user_data=(panel_tag, item_attr))
                    dpg.add_spacer(height=4)
        self._score_rows[sec] = row

    def _build_score_weights(self, sec: str, label: str, weights) -> None:
        """A slider plus a number field for each of the score's weights.

        The design asks for about 200px of slider; the old row stretched it
        across a 500px column, which made small changes hard.
        """
        names = {"_w_county_excess": "Excess splits",
                 "_w_county_unified": "Whole counties"}
        for attr in weights:
            with dpg.group(horizontal=True):
                self.theme.text(names.get(attr, "Weight"), "subheading")
                is_float = attr == "_w_pop_deviation"
                # format="": the number field beside the slider shows the
                # value, so the slider does not print it a second time.
                common = dict(width=168, min_value=0, format="",
                              callback=self._on_score_edit)
                default = self._score_default(attr)
                if is_float:
                    setattr(self, attr, dpg.add_slider_float(
                        default_value=float(default), max_value=100.0,
                        **common))
                else:
                    setattr(self, attr, dpg.add_slider_int(
                        default_value=int(default), max_value=100, **common))
                field = (dpg.add_input_float if is_float else dpg.add_input_int)
                row_field = field(width=62, step=0, default_value=0,
                                  **({"format": "%.1f"} if is_float else {}),
                                  callback=self._on_score_field,
                                  user_data=attr)
                self._score_fields[attr] = row_field

    def _score_default(self, attr: str):
        """Startup weight for a slider, read from presets.SCHEMA.

        _PRESET_VALUES already maps every widget to its (section, key), so the
        default is looked up through it rather than written down again here.
        Hinge is outside presets and keeps its own startup value.
        """
        from mosaic.presets import SCHEMA
        for sec, key, widget_attr in self._PRESET_VALUES:
            if widget_attr == attr:
                return SCHEMA[sec][key][0]
        return 1

    def _build_score_extras(self, sec: str, extras: str) -> None:
        """The few controls that belong to one score and live in its row."""
        if extras.startswith("aim_"):
            from mosaic.gui.choice_radio import choice_radio
            attr = {"aim_mm": "_mm_dir", "aim_eg": "_eg_dir",
                    "aim_pb": "_pb_dir"}[extras]
            with dpg.group(horizontal=True):
                self.theme.text("Aim", "subheading")
                setattr(self, attr, choice_radio(
                    help_key="direction", items=list(_AIM_ITEMS),
                    default_value="Fair", horizontal=True,
                    callback=self._on_score_edit))
        elif extras == "seats_dir":
            from mosaic.gui.choice_radio import choice_radio
            with dpg.group(horizontal=True):
                self.theme.text("Aim", "subheading")
                self._dem_seats_dir = choice_radio(
                    help_key="seats", items=["D", "R"], default_value="D",
                    horizontal=True, callback=self._on_score_edit)
        elif extras == "majority":
            with dpg.group(horizontal=True):
                self.theme.text("Party", "subheading")
                self._majority_dem_chk = dpg.add_checkbox(
                    label="D", default_value=True,
                    callback=self._on_majority_dem_chk)
                self._majority_rep_chk = dpg.add_checkbox(
                    label="R", default_value=False,
                    callback=self._on_majority_rep_chk)
        elif extras == "hinge":
            with dpg.group(horizontal=True):
                self.theme.text("Party", "subheading")
                self._hinge_dem_chk = dpg.add_checkbox(
                    label="D", default_value=True,
                    callback=self._on_hinge_dem_chk)
                self._hinge_rep_chk = dpg.add_checkbox(
                    label="R", default_value=False,
                    callback=self._on_hinge_rep_chk)
            with dpg.group(horizontal=True):
                self.theme.text("Seats", "subheading")
                self._hinge_threshold = dpg.add_slider_int(
                    default_value=8, min_value=1, max_value=14, width=168)
        elif extras == "county_bias":
            # A move bias, not a score: it has no weight and is not in the
            # objective. It pairs with County Congruence, as the score's own
            # toggle already does, so it lives in that score's panel rather
            # than taking a row of its own in a list of twenty scores.
            with dpg.group(horizontal=True):
                self._county_bias_enabled = dpg.add_checkbox(
                    default_value=False, callback=self._on_county_bias_toggle)
                self._county_bias_lbl = self.theme.text(
                    "County-Edge Bias", "disabled")
                self._hint(self._county_bias_lbl, "county_edge_bias")
            with dpg.group(horizontal=True, tag="county_bias_controls",
                           show=False):
                self.theme.text("Factor", "subheading")
                self._county_bias = dpg.add_slider_int(
                    default_value=5, min_value=1, max_value=20, width=168)

    def _phrase_width(self) -> int:
        """Pixels the stretch column actually gets.

        Measured from the live list rather than from _SCORES_W: the scrollbar
        and the tables' cell padding take about 50px that a guess from the
        column constant would spend on text that then clips.
        """
        outer = dpg.get_item_rect_size("scores_scroll")[0] or self._columns[2]
        fixed = 46 + _SCORE_NAME_W + _SCORE_VAL_W
        return int(outer - fixed - 36)

    @staticmethod
    def _fit(text: str, width: int):
        """Trim to width and mark the cut, since a table cell clips silently.

        Returns None when Dear PyGui cannot measure text yet -- it has no font
        atlas before the first frame -- so the caller can leave the phrase
        unfitted and try again rather than caching an unmeasured string.
        """
        size = dpg.get_text_size(text)
        if width <= 0 or size is None:
            return None
        if size[0] <= width:
            return text
        cut = text
        while cut:
            cut = cut[:-1]
            measured = dpg.get_text_size(cut + "\u2026")
            if measured is not None and measured[0] <= width:
                break
        return cut.rstrip() + "\u2026"

    def _text_tooltip(self, widget):
        """A tooltip whose text is filled in later; table cells clip."""
        with dpg.tooltip(parent=widget, delay=0.5):
            return dpg.add_text("", wrap=300)

    # ── Interaction ───────────────────────────────────────────────────────────

    def _on_score_row_click(self, sec: str) -> None:
        """Open or close one row. Rows are independent of each other."""
        row = self._score_rows[sec]
        row["open"] = not row["open"]
        dpg.configure_item(row["panel"], show=row["open"])
        dpg.configure_item(row["chevron"],
                           label=_CHEVRON_OPEN if row["open"] else _CHEVRON_CLOSED)

    def _on_score_edit(self) -> None:
        """A weight or direction changed: refresh the rows that show it."""
        self._scores_sig = None
        self._goals_sig = None

    def _on_score_field(self, sender, value, attr) -> None:
        """The number field writes through to its slider, which is the value
        presets and Goals read."""
        slider = getattr(self, attr)
        if attr == "_w_pop_deviation":
            value = max(0.0, min(100.0, float(value)))
        else:
            value = max(0, min(100, int(value)))
        dpg.set_value(slider, value)
        self._on_score_edit()

    # ── Refresh ───────────────────────────────────────────────────────────────

    def _refresh_score_rows(self) -> None:
        """Redraw the rows from the widgets. Every write is behind a change
        check, so a steady screen costs one signature comparison a frame."""
        have = self._goal_data_available()
        state = []
        for entry in _SCORE_ROWS:
            sec = entry[0]
            row = self._score_rows[sec]
            on = bool(dpg.get_value(row["chk"]))
            usable = self._preset_chk_usable(row["chk_attr"])
            weight = dpg.get_value(getattr(self, row["weights"][0]))
            state.append((sec, on, usable, weight, self._score_phrase(entry)))
        width = self._phrase_width()
        sig = (tuple(state), self._place_unit, tuple(sorted(have.items())),
               width)
        if sig == self._scores_sig:
            return
        # Held back until every phrase has been measured and fitted, which
        # needs a font atlas and so cannot happen on the first frame.
        self._scores_sig = sig if dpg.get_text_size("x") is not None else None

        on_count = 0
        if width != getattr(self, "_phrase_px", None):
            self._phrase_px = width
            for row in self._score_rows.values():
                row.pop("phrase_full", None)
        for sec, on, usable, weight, phrase in state:
            row = self._score_rows[sec]
            on_count += on
            token = "accent_green" if on else ("body" if usable else "disabled")
            self.theme.retoken(row["name"], token)
            if row.get("phrase_full") != phrase:
                fitted = self._fit(phrase, width)
                dpg.set_value(row["phrase"], fitted if fitted else phrase)
                dpg.set_value(row["phrase_tip"], phrase)
                if fitted is not None:
                    row["phrase_full"] = phrase
            text = (f"{weight:g}" if on else "off")
            if dpg.get_value(row["value"]) != text:
                dpg.set_value(row["value"], text)
            self.theme.retoken(row["value"], "body" if on else "muted")
            for attr in row["weights"]:
                field = self._score_fields[attr]
                value = dpg.get_value(getattr(self, attr))
                if dpg.get_value(field) != value:
                    dpg.set_value(field, value)
        dpg.set_value(self._scores_count, f"{on_count} of {len(_SCORE_ROWS)} on")
