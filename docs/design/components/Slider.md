A slider sets a number within a range by dragging; in Mosaic it sets score weights.

**Anatomy.** A `frame-bg` track with a `slider-grab` handle and the current value in `body` text centred on the track. `radius-md` on both.

**States.** Dragging uses `slider-grab-active`. A slider for a score that is off stays visible and keeps its value.

**Sizing.** A weight slider needs about 160 to 240px. The current app stretches it to the full column (about 500px), which makes small changes hard and wastes the row.

**Known issue.** The handle is 3.1:1 against the track in dark and 2.9:1 in light.

The consumer provides the range, the value and a label. Say what the number means once per group, not on every row.
