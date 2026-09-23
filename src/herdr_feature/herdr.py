"""Talk to the Herdr session through the `herdr` CLI, and map features to workspaces."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .manifest import Feature
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
    raw = os.environ.get("FEATURE_INVOKER_CONTEXT") or os.environ.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    return Context(
        workspace_id=os.environ.get("FEATURE_WORKSPACE_ID")
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


def map_live(features: list[Feature], *, heal: bool = True) -> dict[str, str]:
    """feature name -> workspace id for every feature with a live workspace.

    Pane working directories are the primary signal (workspace ids change on server
    restart). The manifest hint and the workspace label are fallbacks. Matches found by
    the primary signal are written back to the manifest as the new hint.
    """
    live: dict[str, str] = {}
    all_panes = panes()
    all_workspaces = {ws["workspace_id"]: ws for ws in workspaces()}

    for feature in features:
        if not feature.readable:
            continue
        hits = panes_inside(feature.root, all_panes)
        if hits:
            ids = sorted({pane["workspace_id"] for pane in hits})
            hinted = (feature.workspace or {}).get("id")
            chosen = hinted if hinted in ids else ids[0]
            live[feature.name] = chosen
            if heal and (feature.workspace or {}).get("id") != chosen and feature.mutable:
                feature.remember_workspace(chosen, all_workspaces.get(chosen, {}).get("label"))
                try:
                    feature.save()
                except Exception:
                    pass
            continue

        hint = feature.workspace or {}
        hinted_id = hint.get("id")
        if hinted_id and hinted_id in all_workspaces:
            label = all_workspaces[hinted_id].get("label")
            if label == hint.get("label") or label == feature.name:
                live[feature.name] = hinted_id
                continue

        by_label = [ws for ws in all_workspaces.values() if ws.get("label") == feature.name]
        if len(by_label) == 1:
            live[feature.name] = by_label[0]["workspace_id"]
    return live


def current_feature(features: list[Feature], ctx: Context) -> Feature | None:
    """The feature the user invoked the action from, if any."""
    readable = [feature for feature in features if feature.readable]
    if ctx.workspace_id:
        live = map_live(readable)
        for feature in readable:
            if live.get(feature.name) == ctx.workspace_id:
                return feature
    for candidate in (ctx.workspace_cwd, ctx.focused_pane_cwd):
        cwd = _resolve(candidate)
        if cwd is None:
            continue
        for feature in readable:
            if cwd.is_relative_to(feature.root.resolve()):
                return feature
    return None


# --- mutations ----------------------------------------------------------------


def create_workspace(feature: Feature, *, focus: bool = True) -> str:
    result = call(
        "workspace",
        "create",
        "--cwd",
        str(feature.root),
        "--label",
        feature.name,
        "--focus" if focus else "--no-focus",
    )
    workspace_id = result["workspace"]["workspace_id"]
    feature.remember_workspace(workspace_id, result["workspace"].get("label"))
    return workspace_id


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


def busy_panes(workspace_id: str) -> list[dict]:
    return [pane for pane in panes(workspace_id) if pane.get("agent_status") in ("working", "blocked")]


def notify(title: str, body: str) -> None:
    try_call("notification", "show", title, "--body", body)
