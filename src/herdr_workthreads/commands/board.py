"""board: the landing screen. Every thread as a thread of work with a progress bar;
Enter opens or focuses it, hotkeys run the other commands on the selected thread."""

from __future__ import annotations

import shutil
import subprocess
import sys

from .. import manifest, prs, ui
from ..config import Config
from ..manifest import Thread
from . import add, close, common, drop, install_cli, new, remove

HOTKEYS = [
    ("enter", "open / focus (done: remove)"),
    ("ctrl-n", "new"),
    ("ctrl-a", "add repos"),
    ("ctrl-d", "drop worktrees"),
    ("ctrl-w", "close workspaces"),
    ("ctrl-x", "remove"),
    ("ctrl-r", "refresh PRs"),
    ("ctrl-o", "open PRs in browser"),
    ("ctrl-t", "install cli"),
    ("?", "help"),
]
EXPECT = [key for key, _ in HOTKEYS if key != "enter"] + ["f1"]

HELP = """\
Thread board

  A thread is one piece of work running through several repositories: a folder holding one Git
  worktree per repository, opened as Herdr workspaces. A worktree is done when its
  pull request is merged; a thread is done when every worktree is.

Row
  name   progress bar (█ merged  ░ not yet  ? not refreshed)   status   summary
  status: open (workspaces live)  closed (files only)  done (all PRs merged)

Keys, on the highlighted thread
  Enter    open or focus its workspaces; on a done thread, offer to remove it
  ctrl-w   close its workspaces (worktrees, folder and branches are kept)
  ctrl-n   new thread
  ctrl-a   add repositories to it
  ctrl-d   drop worktrees from it (branches kept)
  ctrl-x   remove it: worktrees, folder, workspaces, optionally its branches
  ctrl-r   refresh: look up every pull request with gh, fetch default branches
  ctrl-o   open its pull requests in the browser
  ctrl-t   install the herdr-workthreads command line (for agents and scripts)
  ?  F1    this help
  Esc      leave the board

Inside pickers: type to filter, Tab marks (ctrl-a all, ctrl-d none), Esc goes back.
Nothing is deleted by closing; only remove and drop delete, after confirmation.
"""

STATUS_DONE = "done"


def _status(thread: Thread, live: dict, repo_live: dict) -> str:
    word = common.status_word(thread, live, repo_live)
    if word in ("open", "closed") and common.thread_progress(thread).done:
        return STATUS_DONE
    return word


def _rank(status: str) -> int:
    return {"open": 0, "closed": 1, STATUS_DONE: 3}.get(status, 2)


def _rows(threads: list[Thread], live: dict, repo_live: dict) -> tuple[list[str], dict[str, Thread]]:
    items = [(thread, _status(thread, live, repo_live)) for thread in threads]
    items.sort(key=lambda item: (_rank(item[1]), -_updated(item[0])))
    rows, by_key = [], {}
    for thread, status in items:
        progress = common.thread_progress(thread)
        count = len(thread.worktrees) if thread.readable else 0
        open_prs = sum(
            common.worktree_progress(wt).kind == common.PROGRESS_OPEN_PR for wt in thread.worktrees
        )
        detail = f"{count} worktree{'s' if count != 1 else ''}"
        if open_prs:
            detail += f" · {open_prs} PR{'s' if open_prs != 1 else ''} open"
        if not thread.readable:
            detail = thread.error or "unreadable"
        key = str(thread.root)
        by_key[key] = thread
        rows.append(ui.encode_row(key, f"{thread.name:<28}", progress.bar(), f"{status:<8}", detail))
    return rows, by_key


def _updated(thread: Thread) -> float:
    try:
        from datetime import datetime

        return datetime.fromisoformat(thread.updated_at.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


def _header(threads: list[Thread], live: dict, repo_live: dict, notice: str | None) -> str:
    readable = [f for f in threads if f.readable]
    totals = [common.thread_progress(f) for f in readable]
    done = sum(1 for p in totals if p.done)
    merged = sum(p.merged for p in totals)
    total = sum(p.total for p in totals)
    open_count = sum(1 for f in readable if f.name in live or repo_live.get(f.name))
    summary = (
        f"{len(readable)} thread{'s' if len(readable) != 1 else ''} · {open_count} open · "
        f"{done} done · {merged}/{total} merged"
    )
    checked = prs.last_checked(threads)
    if notice:
        freshness = notice
    elif checked is None:
        freshness = "PRs never refreshed: ctrl-r"
    else:
        freshness = f"PRs refreshed {common.age(checked)}"
    legend = "   ".join(f"{key}: {text}" for key, text in HOTKEYS if key != "?") + "   ?: help"
    return f"{summary}   ·   {freshness}\n{legend}"


def _sub(action, *args, **kwargs) -> None:
    """Run a sub-command inside the board loop: Esc returns to the board, an Abort is
    shown and acknowledged, and the board redraws."""
    try:
        action(*args, **kwargs)
    except ui.Cancelled:
        return
    except ui.Abort as error:
        ui.restore_terminal()
        print(f"\n{error}\n", file=sys.stderr)
        ui.pause()


def _open_urls(thread: Thread) -> None:
    urls = [pr.url for wt in thread.worktrees if (pr := prs.from_dict(wt.pr)) and pr.url]
    if not urls:
        ui.warn(f"{thread.name} has no pull requests on record; refresh with ctrl-r.")
        ui.pause()
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    if not shutil.which(opener):
        for url in urls:
            print(url)
        ui.pause()
        return
    for url in urls:
        subprocess.Popen([opener, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run(config: Config) -> None:
    notice: str | None = None
    try:
        prs.gh_binary()
    except ui.Abort as error:
        notice = str(error)

    while True:
        threads = common.load_threads(config)
        live = common.live_map(threads)
        repo_live = common.repo_live_map(threads)
        rows, by_key = _rows(threads, live, repo_live)
        if not rows:
            rows = [ui.encode_row("", "(no threads yet)", "", "", "ctrl-n to start one")]
        key, keys = ui.pick_expect(
            rows,
            prompt_text="thread> ",
            header=_header(threads, live, repo_live, notice),
            preview=ui.preview_command("preview-thread"),
            preview_size="50%",
            expect=EXPECT,
        )
        selected = by_key.get(keys[0]) if keys else None

        if key == "ctrl-n" or (key == "" and selected is None):
            new.run(config)  # `new` focuses the workspace it opened, so the board is done
            return
        if key == "ctrl-t":
            _sub(install_cli.run)
            continue
        if key in ("?", "f1"):
            print(HELP)
            ui.pause("Enter to return to the board.")
            continue
        if key == "ctrl-r":
            try:
                common.refresh_progress(threads)
                notice = None
            except ui.Abort as error:
                notice = str(error)
                ui.warn(str(error))
                ui.pause()
            continue
        if selected is None:
            continue
        if key == "ctrl-a":
            _sub(add.run, config, selected)
            continue
        if key == "ctrl-d":
            _sub(drop.run, config, selected)
            continue
        if key == "ctrl-w":
            _sub(close.run, config, selected)
            continue
        if key == "ctrl-x":
            _sub(remove.run, config, selected)
            continue
        if key == "ctrl-o":
            _open_urls(selected)
            continue
        if key != "":
            continue

        # Enter.
        if not selected.readable:
            _sub(_abort, f"{selected.name} cannot be opened: {selected.error}")
            continue
        if selected.status == manifest.STATUS_CREATING:
            _sub(
                _abort,
                f"{selected.name} was interrupted while being created. "
                "Run 'new' with the same name to clean it up, or remove it (ctrl-x).",
            )
            continue
        if common.thread_progress(selected).done:
            ui.heading(f"{selected.name} is done: every pull request is merged")
            if ui.confirm("Remove its worktrees and folder now?", default=True):
                _sub(remove.run, config, selected, delete_branches_default=True)
                continue
            if not ui.confirm("Open it anyway?", default=False):
                continue
        opened = common.open_workspaces(config, selected, focus=True, workspace_id=live.get(selected.name))
        if opened.created:
            common.report_opened(selected, opened)
            if opened.failures:
                ui.pause()
        return


def _abort(message: str) -> None:
    raise ui.Abort(message)
