"""Terminal UI for the popup: fzf pickers, prompts, confirmations, progress lines.

Everything interactive goes through here so tests can drive the plugin with
HERDR_FEATURE_INPUTS (newline-separated prompt answers) and HERDR_FEATURE_FZF (a fake
fzf that echoes preselected keys).
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from collections import deque
from collections.abc import Callable, Iterable
from pathlib import Path

FZF_CANDIDATES = ("/opt/homebrew/bin/fzf", "/usr/local/bin/fzf", "~/.local/bin/fzf")

# Where progress lines go. The CLI mode points this at stderr so --json stays clean.
OUT = sys.stdout

# Non-interactive mode (CLI): answers keyed by question id. None means interactive.
_preset: dict[str, str] | None = None


def set_noninteractive(answers: dict[str, str]) -> None:
    global _preset, OUT
    _preset = dict(answers)
    OUT = sys.stderr


def noninteractive() -> bool:
    return _preset is not None


class Abort(Exception):
    """Something went wrong and the user needs to read about it before the popup closes."""


class Cancelled(Exception):
    """The user backed out (Esc, Ctrl-C, empty answer). Close silently."""


# --- scripted input seam ------------------------------------------------------

_UNSET = object()
_scripted: deque[str] | None | object = _UNSET


def _inputs() -> deque[str] | None:
    global _scripted
    if _scripted is _UNSET:
        raw = os.environ.get("HERDR_FEATURE_INPUTS")
        _scripted = deque(raw.split("\n")) if raw is not None else None
    return _scripted  # type: ignore[return-value]


def scripted() -> bool:
    return _inputs() is not None


def _read(label: str, key: str | None = None) -> str:
    if _preset is not None:
        if key is not None and key in _preset:
            return _preset[key]
        raise Abort(
            f"this step needs an answer ({label.strip().rstrip(':')}) and no flag provided one; "
            "run interactively or pass the matching option."
        )
    queue = _inputs()
    if queue is not None:
        if not queue:
            raise Abort(f"scripted input exhausted at prompt: {label!r}")
        answer = queue.popleft()
        print(f"{label}{answer}")
        return answer
    sys.stdout.flush()
    try:
        return input(label)
    except (EOFError, KeyboardInterrupt):
        print()
        raise Cancelled() from None


# --- output helpers -----------------------------------------------------------


def step(message: str) -> None:
    print(f"  {message}", file=OUT, flush=True)


def ok(message: str) -> None:
    print(f"  ✓ {message}", file=OUT, flush=True)


def fail(message: str) -> None:
    print(f"  ✗ {message}", file=OUT, flush=True)


def warn(message: str) -> None:
    print(f"  ! {message}", file=OUT, flush=True)


def heading(message: str) -> None:
    print(f"\n{message}", file=OUT, flush=True)


def pause(message: str = "Enter to close.") -> None:
    if scripted() or noninteractive():
        return
    try:
        input(message)
    except (EOFError, KeyboardInterrupt):
        pass


# --- prompts ------------------------------------------------------------------


def prompt(
    label: str,
    *,
    validator: Callable[[str], str | None] | None = None,
    allow_empty: bool = False,
    key: str | None = None,
) -> str:
    """Ask for a line of text. An empty answer cancels unless allow_empty."""
    while True:
        answer = _read(f"{label}: ", key).strip()
        if not answer:
            if allow_empty:
                return ""
            raise Cancelled()
        if validator:
            problem = validator(answer)
            if problem:
                warn(problem)
                continue
        return answer


def confirm(question: str, *, default: bool = False, key: str = "confirm") -> bool:
    hint = "Y/n" if default else "y/N"
    answer = _read(f"{question} [{hint}] ", key).strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes")


def confirm_typed(expected: str, question: str, *, key: str = "confirm-typed") -> bool:
    answer = _read(f"{question} Type '{expected}' to continue: ", key).strip()
    return answer == expected


def choose(question: str, options: dict[str, str], *, default: str, key: str = "choose") -> str:
    """Single-letter choice, e.g. options={"c": "continue", "a": "abort"}."""
    legend = ", ".join(f"[{key_}] {text}" for key_, text in options.items())
    while True:
        answer = _read(f"{question} {legend} ({default}): ", key).strip().lower()
        if not answer:
            return default
        if answer in options:
            return answer
        warn(f"answer with one of: {', '.join(options)}")


# --- fzf ----------------------------------------------------------------------


def find_fzf() -> str:
    override = os.environ.get("HERDR_FEATURE_FZF")
    if override:
        if not os.access(override, os.X_OK):
            raise Abort(f"HERDR_FEATURE_FZF={override} is not executable.")
        return override
    found = shutil.which("fzf")
    if found:
        return found
    for candidate in FZF_CANDIDATES:
        path = Path(candidate).expanduser()
        if os.access(path, os.X_OK):
            return str(path)
    raise Abort("fzf was not found. Install it (brew install fzf) or set HERDR_FEATURE_FZF.")


def encode_row(key: str, *columns: str) -> str:
    """One fzf line: hidden machine key, then display columns, tab separated."""
    parts = [key, *columns]
    return "\t".join(part.replace("\t", " ").replace("\n", " ") for part in parts)


def preview_command(*args: str) -> str:
    """A --preview command that calls back into this package; {1} is the row key."""
    python = shlex.quote(sys.executable)
    quoted = " ".join(shlex.quote(arg) for arg in args)
    return f"{python} -m herdr_feature {quoted} {{1}}"


def pick(
    rows: Iterable[str],
    *,
    prompt_text: str,
    header: str | None = None,
    multi: bool = False,
    preview: str | None = None,
    preview_size: str = "55%",
) -> list[str]:
    """Run fzf over `rows` (built with encode_row) and return the chosen keys.

    Raises Cancelled when the user escapes or nothing is selected.
    """
    if noninteractive():
        raise Abort("an interactive picker was reached in non-interactive mode; pass explicit options.")
    lines = [row for row in rows if row]
    if not lines:
        raise Abort("nothing to choose from.")

    args = [
        find_fzf(),
        "--delimiter=\t",
        "--with-nth=2..",
        "--accept-nth=1",
        "--layout=reverse",
        "--border",
        "--ansi",
        "--cycle",
        "--info=inline",
        f"--prompt={prompt_text}",
    ]
    if header:
        args += ["--header-first", f"--header={header}"]
    if multi:
        args += ["--multi", "--bind=ctrl-a:select-all,ctrl-d:deselect-all"]
    else:
        args.append("--no-multi")
    if preview:
        args += [f"--preview={preview}", f"--preview-window=right,{preview_size},border-left,wrap"]

    env = {
        **os.environ,
        "FZF_DEFAULT_OPTS": "",
        "FZF_DEFAULT_COMMAND": "",
        "FZF_DEFAULT_OPTS_FILE": "",
    }
    sys.stdout.flush()
    result = subprocess.run(
        args,
        input="\n".join(lines) + "\n",
        stdout=subprocess.PIPE,
        text=True,
        env=env,
        check=False,
    )
    if result.returncode in (1, 130):
        raise Cancelled()
    if result.returncode == 2:
        raise Abort("fzf failed to start; check the terminal size and FZF settings.")
    if result.returncode != 0:
        raise Cancelled()
    keys = [line for line in result.stdout.splitlines() if line]
    if not keys:
        raise Cancelled()
    return keys


def restore_terminal() -> None:
    """After fzf or a crash, make sure the terminal is usable for the final prompt."""
    if not sys.stdin.isatty():
        return
    try:
        subprocess.run(["stty", "sane"], stdin=sys.stdin, check=False, capture_output=True)
    except OSError:
        pass
