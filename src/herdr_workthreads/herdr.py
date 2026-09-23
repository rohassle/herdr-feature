"""Talk to the Herdr session through the `herdr` CLI, and map threads to workspaces."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .manifest import Thread, Worktree
from .ui import Abort


class HerdrError(Abort):
    def __init__(self, code: str, message: str, command: tuple[str, ...]):
        super().__init__(f"herdr {' '.join(command)}: {code}: {message}")
        self.code = code
        self.message = message


def binary() -> str:
    return os.environ.get("HERDR_BIN_PATH", "herdr")


def call(*args: str) -> dict:
    result = subprocess.run([binary(), *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        code, message = "error", result.stderr.strip() or result.stdout.strip()
        try:
            payload = json.loads(result.stderr)
            code = payload.get("error", {}).get("code", code)
            message = payload.get("error", {}).get("message", message)
        except (json.JSONDecodeError, AttributeError):
            pass
        raise HerdrError(code, message, args)
    try:
        return json.loads(result.stdout)["result"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise HerdrError("unreadable_response", str(error), args) from error


def try_call(*args: str) -> dict | None:
    try:
        return call(*args)
    except HerdrError:
        return None


# --- invocation context -------------------------------------------------------


@dataclass
class Context:
    workspace_id: str | None
    workspace_cwd: str | None
    focused_pane_cwd: str | None
    workspace_label: str | None


def context() -> Context:
    raw = os.environ.get("WORKTHREADS_INVOKER_CONTEXT") or os.environ.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    return Context(
        workspace_id=os.environ.get("WORKTHREADS_WORKSPACE_ID")
        or data.get("workspace_id")
        or os.environ.get("HERDR_WORKSPACE_ID"),
        workspace_cwd=data.get("workspace_cwd"),
        focused_pane_cwd=data.get("focused_pane_cwd"),
        workspace_label=data.get("workspace_label"),
    )


# --- queries ------------------------------------------------------------------


def workspaces() -> list[dict]:
    return call("workspace", "list").get("workspaces", [])


def panes(workspace_id: str | None = None) -> list[dict]:
    args = ["pane", "list"]
    if workspace_id:
        args += ["--workspace", workspace_id]
    return call(*args).get("panes", [])


def _resolve(path: str | None) -> Path | None:
    if not path:
        return None
    try:
        return Path(path).resolve()
    except OSError:
        return None


def _plugin_root() -> Path | None:
    return _resolve(os.environ.get("HERDR_PLUGIN_ROOT"))


def panes_inside(root: Path, pane_list: list[dict] | None = None) -> list[dict]:
    """Panes whose working directory lies inside `root`, excluding this plugin's own popup."""
    target = root.resolve()
    plugin_root = _plugin_root()
    inside = []
    for pane in pane_list if pane_list is not None else panes():
        cwd = _resolve(pane.get("foreground_cwd") or pane.get("cwd"))
        if cwd is None:
            continue
        if plugin_root and cwd.is_relative_to(plugin_root):
            continue
        if cwd.is_relative_to(target):
            inside.append(pane)
    return inside


def _checkout_path(workspace: dict) -> Path | None:
    """The checkout a Herdr worktree workspace is bound to, or None for plain workspaces."""
    provenance = workspace.get("worktree") or {}
    if not provenance.get("is_linked_worktree"):
        return None
    return _resolve(provenance.get("checkout_path"))


def nested_workspace_ids(root: Path, all_workspaces: dict[str, dict]) -> set[str]:
    """Herdr worktree workspaces whose checkout lies inside `root`: the per-repository
    workspaces of a thread. They are never the thread's own workspace."""
    target = root.resolve()
    found = set()
    for workspace in all_workspaces.values():
        checkout = _checkout_path(workspace)
        if checkout is not None and checkout.is_relative_to(target):
            found.add(workspace["workspace_id"])
    return found


def map_live(threads: list[Thread], *, heal: bool = True) -> dict[str, str]:
    """thread name -> workspace id for every thread with a live thread workspace.

    Pane working directories are the primary signal (workspace ids change on server
    restart). The manifest hint and the workspace label are fallbacks. Matches found by
    the primary signal are written back to the manifest as the new hint. Per-repository
    worktree workspaces (see `repo_workspaces`) are excluded: Herdr marks them with
    worktree provenance.
    """
    live: dict[str, str] = {}
    all_panes = panes()
    all_workspaces = {ws["workspace_id"]: ws for ws in workspaces()}

    for thread in threads:
        if not thread.readable:
            continue
        nested = nested_workspace_ids(thread.root, all_workspaces)
        hits = panes_inside(thread.root, all_panes)
        ids = sorted({pane["workspace_id"] for pane in hits} - nested)
        if ids:
            hinted = (thread.workspace or {}).get("id")
            chosen = hinted if hinted in ids else ids[0]
            live[thread.name] = chosen
            if heal and (thread.workspace or {}).get("id") != chosen and thread.mutable:
                thread.remember_workspace(chosen, all_workspaces.get(chosen, {}).get("label"))
                try:
                    thread.save()
                except Exception:
                    pass
            continue

        hint = thread.workspace or {}
        hinted_id = hint.get("id")
        if hinted_id and hinted_id in all_workspaces and hinted_id not in nested:
            label = all_workspaces[hinted_id].get("label")
            if label == hint.get("label") or label == thread.name:
                live[thread.name] = hinted_id
                continue

        by_label = [
            ws
            for ws in all_workspaces.values()
            if ws.get("label") == thread.name and _checkout_path(ws) is None
        ]
        if len(by_label) == 1:
            live[thread.name] = by_label[0]["workspace_id"]
    return live


def repo_workspaces(thread: Thread, all_workspaces: list[dict] | None = None) -> dict[str, str]:
    """worktree folder -> workspace id for the thread's per-repository worktree workspaces
    that are currently open. Identity comes from Herdr's worktree provenance (the checkout
    path), which survives server restarts."""
    if not thread.readable:
        return {}
    by_checkout: dict[Path, str] = {}
    for workspace in all_workspaces if all_workspaces is not None else workspaces():
        checkout = _checkout_path(workspace)
        if checkout is not None:
            by_checkout.setdefault(checkout, workspace["workspace_id"])
    found: dict[str, str] = {}
    for worktree in thread.worktrees:
        path = _resolve(str(thread.path_of(worktree)))
        if path is not None and path in by_checkout:
            found[worktree.folder] = by_checkout[path]
    return found


def map_repo_live(threads: list[Thread]) -> dict[str, dict[str, str]]:
    """thread name -> {worktree folder -> workspace id} for open per-repository workspaces."""
    all_workspaces = workspaces()
    return {
        thread.name: found
        for thread in threads
        if thread.readable and (found := repo_workspaces(thread, all_workspaces))
    }


def current_thread(threads: list[Thread], ctx: Context) -> Thread | None:
    """The thread the user invoked the action from, if any."""
    readable = [thread for thread in threads if thread.readable]
    if ctx.workspace_id:
        live = map_live(readable)
        for thread in readable:
            if live.get(thread.name) == ctx.workspace_id:
                return thread
        for thread in readable:
            if ctx.workspace_id in repo_workspaces(thread).values():
                return thread
    for candidate in (ctx.workspace_cwd, ctx.focused_pane_cwd):
        cwd = _resolve(candidate)
        if cwd is None:
            continue
        for thread in readable:
            if cwd.is_relative_to(thread.root.resolve()):
                return thread
    return None


# --- mutations ----------------------------------------------------------------


def create_workspace(thread: Thread, *, focus: bool = True) -> str:
    result = call(
        "workspace",
        "create",
        "--cwd",
        str(thread.root),
        "--label",
        thread.name,
        "--focus" if focus else "--no-focus",
    )
    workspace_id = result["workspace"]["workspace_id"]
    thread.remember_workspace(workspace_id, result["workspace"].get("label"))
    return workspace_id


def repo_workspace_label(thread: Thread, worktree: Worktree) -> str:
    return thread.name if worktree.suffix is None else f"{thread.name}@{worktree.suffix}"


def open_repo_workspace(thread: Thread, worktree: Worktree, *, focus: bool = False) -> str:
    """Open one worktree entry as a Herdr worktree workspace. Herdr binds it to its
    repository and indents it under the repository's workspace in the sidebar (opening
    that parent workspace first when none is open). Idempotent: an already-open checkout
    returns its existing workspace id."""
    result = call(
        "worktree",
        "open",
        "--cwd",
        worktree.repo_path,
        "--path",
        str(thread.path_of(worktree)),
        "--label",
        repo_workspace_label(thread, worktree),
        "--focus" if focus else "--no-focus",
    )
    return result["workspace"]["workspace_id"]


@dataclass
class Opened:
    workspace_id: str | None = None  # the thread workspace, when the mode has one
    repo_workspaces: dict[str, str] = field(default_factory=dict)  # folder -> workspace id
    created: bool = False  # something new was opened
    failures: list[str] = field(default_factory=list)

    @property
    def any(self) -> str | None:
        """Some workspace of the thread: the thread workspace, else the first nested one."""
        return self.workspace_id or next(iter(self.repo_workspaces.values()), None)


def open_thread(
    config,
    thread: Thread,
    *,
    focus: bool,
    workspace_id: str | None = None,
    only: list[Worktree] | None = None,
) -> Opened:
    """Open whatever workspaces the configured mode calls for and are not open yet.

    `workspace_id` is the live thread workspace when the caller already knows it. `only`
    restricts the per-repository workspaces to the given entries (after `add`). Failures
    to open a nested workspace are collected, not raised: the thread itself is fine.
    """
    opened = Opened(workspace_id=workspace_id)
    if config.thread_workspace and opened.workspace_id is None:
        opened.workspace_id = create_workspace(thread, focus=False)
        opened.created = True
    if config.repo_workspaces:
        existing = repo_workspaces(thread)
        wanted = only if only is not None else thread.worktrees
        for worktree in wanted:
            if worktree.folder in existing:
                opened.repo_workspaces[worktree.folder] = existing[worktree.folder]
                continue
            try:
                opened.repo_workspaces[worktree.folder] = open_repo_workspace(thread, worktree)
                opened.created = True
            except HerdrError as error:
                opened.failures.append(f"{worktree.folder}: {error.code}: {error.message}")
        if only is not None:
            for folder, existing_id in existing.items():
                opened.repo_workspaces.setdefault(folder, existing_id)
    if focus and opened.any:
        focus_workspace(opened.any)
    return opened


def close_repo_workspaces(thread: Thread, worktrees: list[Worktree]) -> list[str]:
    """Close the per-repository workspaces of the given entries. Returns the closed ids."""
    open_ids = repo_workspaces(thread)
    closed = []
    for worktree in worktrees:
        workspace_id = open_ids.get(worktree.folder)
        if workspace_id and close_workspace(workspace_id):
            closed.append(workspace_id)
    return closed


def focus_workspace(workspace_id: str) -> None:
    """Focus now, and again shortly after this popup has closed (belt and braces:
    a focus request issued while a modal is open may be ignored)."""
    try_call("workspace", "focus", workspace_id)
    try:
        subprocess.Popen(
            ["sh", "-c", 'sleep 0.4; exec "$0" workspace focus "$1"', binary(), workspace_id],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def close_workspace(workspace_id: str) -> bool:
    """True when closed (or already gone)."""
    try:
        call("workspace", "close", workspace_id)
        return True
    except HerdrError as error:
        if "not_found" in error.code or "unknown" in error.code:
            return True
        raise


def busy_panes(*workspace_ids: str) -> list[dict]:
    wanted = set(workspace_ids)
    return [
        pane
        for pane in panes()
        if pane.get("workspace_id") in wanted and pane.get("agent_status") in ("working", "blocked")
    ]


def notify(title: str, body: str) -> None:
    try_call("notification", "show", title, "--body", body)
