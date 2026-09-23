"""open: focus a feature's workspace, or create one for a feature that has none."""

from __future__ import annotations

from .. import herdr, manifest, ui
from ..config import Config
from . import common


def run(config: Config) -> None:
    features = common.load_features(config)
    if not features:
        raise ui.Abort(f"No features under {config.features_directory}. Create one with 'new'.")
    live = common.live_map(features)
    feature = common.choose_feature(
        features,
        live,
        prompt_text="open> ",
        header="Enter: focus or reopen   Esc: cancel",
    )
    if not feature.readable:
        raise ui.Abort(f"{feature.name} cannot be opened: {feature.error}")
    if feature.status == manifest.STATUS_CREATING:
        raise ui.Abort(
            f"{feature.name} was interrupted while being created. Run 'new' with the same name to clean it up, or 'remove'."
        )

    workspace_id = live.get(feature.name)
    if workspace_id:
        herdr.focus_workspace(workspace_id)
        return
    workspace_id = herdr.create_workspace(feature, focus=True)
    if feature.mutable:
        feature.save()
    ui.ok(f"opened {feature.name} as workspace {workspace_id}")
