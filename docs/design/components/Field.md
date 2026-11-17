A field holds one typed value: a district count, an iteration count, a file name.

**Anatomy.** A `subheading` label above, then a `frame-bg` box with `body` text. No border. 8px by 5px padding, `radius-md`.

**States.** Hover uses `frame-bg-hovered`; editing uses `frame-bg-active`. An invalid value keeps the box unchanged and shows an `error` message directly beneath it.

**Known issue.** The box is 1.3:1 against the panel in dark and 1.1:1 in light, so an empty field is hard to see. Always give a field a label and a value or placeholder.

The consumer provides the label, the value and the unit. Put the unit in the label ("Tolerance (%)"), not inside the box.
