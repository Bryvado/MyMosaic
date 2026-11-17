A panel groups related controls or one chart under a heading.

**Anatomy.** A `heading` label, then a `child-bg` box with a 1px `border`, `radius-sm` and 10px padding. Rules inside a panel use `separator`.

**Charts.** A chart panel uses `plot-bg` for the plotting area, `plot-border` for its frame, `plot-axis-grid` for gridlines and `plot-axis-text` for ticks.

**Rules.** One heading per panel. Do not nest panels more than one level. Floating panels (score detail, Goals, Score Contributors) use `popup-bg` and `radius-lg`.

The consumer provides the heading and the content.
