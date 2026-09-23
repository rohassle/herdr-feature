"""close: shut a feature's Herdr workspaces (feature workspace and nested worktree
workspaces). Files, worktrees and branches stay exactly as they are (ADR 0002)."""

from __future__ import annotations

from .. import herdr, ui
from ..config import Config
from ..manifest import Feature
from . import common


def run(config: Config, feature: Feature | None = None) -> None:
    features = common.load_features(config)
    live = common.live_map(features)
    repo_live = common.repo_live_map(features)
    if feature is None:
        open_features = [f for f in features if f.name in live or repo_live.get(f.name)]
        if not open_features:
            raise ui.Abort("No feature has an open workspace.")
        feature = common.choose_feature(
            open_features,
            live,
            prompt_text="close> ",
            header="Enter: close this feature's workspaces (files are kept)   Esc: cancel",
            repo_live=repo_live,
        )
    ids = common.feature_workspace_ids(feature, live, repo_live)
    if not ids:
        ui.warn(f"{feature.name} has no open workspace.")
        ui.pause()
        return

    ui.heading(f"Close {feature.name}")
    for folder, workspace_id in repo_live.get(feature.name, {}).items():
        ui.step(f"{workspace_id:<8} {folder}")
    if live.get(feature.name):
        ui.step(f"{live[feature.name]:<8} (feature root)")
    busy = herdr.busy_panes(*ids)
    if busy:
        ui.warn(f"{len(busy)} agent(s) still working or waiting:")
        for pane in busy:
            ui.step(f"{pane['pane_id']:<8} {pane.get('agent') or ''} {pane.get('agent_status')}")
        if not ui.confirm("Close anyway? Running processes will be killed.", default=False):
            raise ui.Cancelled()
    elif not ui.confirm(
        f"Close {len(ids)} workspace(s) of {feature.name!r}? Worktrees and branches are kept.", default=True
    ):
        raise ui.Cancelled()

    closed = common.close_feature_workspaces(feature, live, repo_live)
    for workspace_id in closed:
        ui.ok(f"closed {workspace_id}")
    print(f"\n{feature.name}: {len(closed)} workspace(s) closed; files and branches kept. Reopen with Enter.")
    ui.pause()
