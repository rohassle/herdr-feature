"""add: add worktrees from more repositories to an existing feature."""

from __future__ import annotations

from .. import herdr, names, ui
from ..config import Config
from ..lock import mutation_lock
from ..manifest import Feature
from . import common


def resolve_target(config: Config, features: list[Feature], *, verb: str) -> tuple[Feature, dict[str, str]]:
    live = common.live_map(features)
    current = herdr.current_feature(features, herdr.context())
    if current is not None and current.mutable:
        return current, live
    feature = common.choose_feature(
        features,
        live,
        prompt_text="feature> ",
        header=f"Choose the feature to {verb}",
        only_mutable=True,
        repo_live=common.repo_live_map(features),
    )
    return feature, live


def run(config: Config) -> None:
    features = common.load_features(config)
    feature, live = resolve_target(config, features, verb="add worktrees to")
    ui.heading(f"Add worktrees to {feature.name}")
    others = [f for f in features if f is not feature]

    with mutation_lock():
        repos = common.pick_repos(config, feature)
        requests = []
        for repo in repos:
            suffix = None
            if feature.worktrees_for(repo.path):
                used = {wt.suffix for wt in feature.worktrees_for(repo.path)}
                ui.warn(
                    f"{repo.name} is already in this feature as "
                    + ", ".join(wt.folder for wt in feature.worktrees_for(repo.path))
                )

                def validate(value: str, used=used, repo=repo) -> str | None:
                    problem = names.validate_name(value, "suffix")
                    if problem:
                        return problem
                    if value in used:
                        return f"{repo.name}@{value} already exists in this feature."
                    return None

                suffix = ui.prompt(f"suffix for the extra {repo.name} worktree", validator=validate)
            requests.append(common.Request(repo, suffix))

        planned = common.preflight(config, feature, requests, others)
        common.show_plan(planned)
        if not ui.confirm(f"Add {len(planned)} worktree(s) to {feature.name!r}?", default=True):
            raise ui.Cancelled()
        added = common.execute(feature, planned, is_new=False)

    print(f"\n{feature.name}: added {len(planned)} worktree(s).")
    is_open = feature.name in live or bool(herdr.repo_workspaces(feature))
    if is_open:
        if config.repo_workspaces:
            ui.heading("Opening worktree workspaces")
            opened = common.open_workspaces(
                config, feature, focus=False, workspace_id=live.get(feature.name), only=added
            )
            common.report_opened(feature, opened)
            ui.pause()
        return
    if ui.confirm("The feature has no open workspace. Open it now?", default=True):
        opened = common.open_workspaces(config, feature, focus=True)
        common.report_opened(feature, opened)
        if opened.failures:
            ui.pause()
        return
    ui.pause()
