"""new: create a feature from a set of repositories and open its workspace."""

from __future__ import annotations

from .. import herdr, manifest, names, ui
from ..config import Config
from ..lock import mutation_lock
from . import common


def run(config: Config) -> None:
    features = common.load_features(config)

    def validate(value: str) -> str | None:
        problem = names.validate_name(value)
        if problem:
            return problem
        existing = manifest.find(features, value)
        if existing and existing.status == manifest.STATUS_READY:
            return f"a feature named {existing.name!r} already exists; use 'open' or 'add'."
        return None

    ui.heading("New feature")
    name = ui.prompt("feature name", validator=validate)
    root = config.features_directory / name

    with mutation_lock():
        existing = manifest.find(features, name)
        if existing is not None and existing.readable and existing.status == manifest.STATUS_CREATING:
            common.cleanup_interrupted(existing)
            features = [f for f in features if f is not existing]
        elif root.exists():
            raise ui.Abort(f"{root} already exists but is not a known feature. Move it away first.")

        repos = common.pick_repos(config)
        feature = manifest.Feature(name=name, root=root, branch_prefix=config.branch_prefix)
        requests = [common.Request(repo) for repo in repos]
        planned = common.preflight(config, feature, requests, features)
        common.show_plan(planned)
        if not ui.confirm(f"Create feature {name!r} with {len(planned)} worktree(s)?", default=True):
            raise ui.Cancelled()

        config.features_directory.mkdir(parents=True, exist_ok=True)
        try:
            root.mkdir()
        except FileExistsError:
            raise ui.Abort(f"{root} appeared while planning; try again.")
        common.execute(feature, planned, is_new=True)

    ui.heading("Opening workspace")
    try:
        workspace_id = herdr.create_workspace(feature, focus=True)
        feature.save()
        ui.ok(f"workspace {workspace_id} ({feature.name}) at {root}")
    except herdr.HerdrError as error:
        ui.warn(f"the feature is on disk but its workspace could not be created:\n  {error}")
        ui.warn("use 'open' to try again.")
        ui.pause()
        return
    print(f"\n{feature.name}: {len(planned)} worktree(s) ready.")
