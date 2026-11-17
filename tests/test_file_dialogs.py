"""Guard the Dear PyGui file-dialog convention.

In Dear PyGui 2.3 a modal window opened after a modal ``file_dialog`` is
dismissed the moment it is created: DPG clears its ``show`` flag and it never
appears, with no exception and nothing in the log. That silently broke the
Plan Continuity reference-plan loader, whose next step is a modal column
picker, and the "Discard & Open" confirm behind Open Shapefile.

Deferring the follow-up to a later frame does not help -- the block is not a
timing problem. Only keeping the file dialogs non-modal does, so this pins
that down: the failure is invisible at runtime, and would be reintroduced by
anyone copying an old call site.

Source text, not behaviour: asserting the real thing needs a display.
"""

from __future__ import annotations

import re
from pathlib import Path

GUI = Path(__file__).resolve().parents[1] / "src" / "mosaic" / "gui"

_OPEN = "dpg.file_dialog("


def _strip_comments(src: str) -> str:
    """Drop `#` comments. The call sites explain the modal rule in prose that
    names `modal=True`, which a plain search would read as code."""
    out = []
    for line in src.splitlines():
        cut = line.find("#")
        out.append(line if cut < 0 else line[:cut])
    return "\n".join(out)


def _file_dialog_calls():
    """(path, line, argument source) for each `dpg.file_dialog(...)` call.

    Parentheses are balanced rather than matched by regex: the argument lists
    hold nested calls and lambdas, so the first `)` is nowhere near the end.
    """
    for path in sorted(GUI.rglob("*.py")):
        text = _strip_comments(path.read_text(encoding="utf-8"))
        start = text.find(_OPEN)
        while start != -1:
            i = start + len(_OPEN)
            depth = 1
            while i < len(text) and depth:
                depth += (text[i] == "(") - (text[i] == ")")
                i += 1
            yield path, text[:start].count("\n") + 1, text[start + len(_OPEN):i - 1]
            start = text.find(_OPEN, i)


def test_there_are_file_dialogs_to_check():
    """Guard the guard: a renamed API would make every assertion vacuous."""
    assert len(list(_file_dialog_calls())) >= 4


def test_no_file_dialog_is_modal():
    offenders = [
        f"{path.relative_to(GUI.parents[2])}:{line}"
        for path, line, args in _file_dialog_calls()
        if re.search(r"\bmodal\s*=\s*True", args)
    ]
    assert not offenders, (
        "modal=True on a file dialog stops any modal opened from its callback "
        "from ever appearing (DPG 2.3): " + ", ".join(offenders)
    )


def test_every_file_dialog_states_its_modality():
    """An omitted `modal` defaults to False today, but say it out loud: the
    whole bug was a default nobody had reason to look at."""
    missing = [
        f"{path.relative_to(GUI.parents[2])}:{line}"
        for path, line, args in _file_dialog_calls()
        if not re.search(r"\bmodal\s*=", args)
    ]
    assert not missing, "file dialog without an explicit modal=: " + ", ".join(missing)
