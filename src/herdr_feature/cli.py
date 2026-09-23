"""Non-interactive command line, for agents and scripts.

    herdr-feature new --name X --repo a --repo b [--suffix a=api] [--yes] [--json]
    herdr-feature add --feature X --repo a [--suffix a=api] [--yes] [--open]
    herdr-feature list [--json]
    herdr-feature open --feature X [--focus]
    herdr-feature drop --feature X --worktree a@api [--force] --yes
    herdr-feature remove --feature X [--force] [--delete-branches] --yes

Without --yes, new/add/drop/remove only print what they would do (a dry run). Progress
lines go to stderr; with --json the result object goes to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import gitops, herdr, manifest, names, ui
from .commands import common, install_cli
from .config import Config, load_config
from .discovery import Repo, scan
from .lock import mutation_lock


class Result:
    def __init__(self, **fields):
        self.fields = fields


# --- helpers ------------------------------------------------------------------


def _feature_dict(feature: manifest.Feature, live: dict[str, str], *, states: bool) -> dict:
    data = {
        "feature": feature.name,
        "root": str(feature.root),
        "status": common.status_word(feature, live),
        "workspace_id": live.get(feature.name),
        "branch_prefix": feature.branch_prefix,
        "worktrees": [],
    }
    if not feature.readable:
        data["error"] = feature.error
        return data
    for wt in feature.worktrees:
        entry = {
            "repo_name": wt.repo_name,
            "repo_path": wt.repo_path,
            "folder": wt.folder,
            "path": str(feature.path_of(wt)),
            "branch": wt.branch,
            "branch_created": wt.branch_created,
            "branch_source": wt.branch_source,
        }
        if states:
            state = gitops.worktree_state(feature.path_of(wt))
            entry["state"] = state.kind
            entry["state_detail"] = state.detail
        data["worktrees"].append(entry)
    return data


def _resolve_repos(config: Config, wanted: list[str]) -> list[Repo]:
    repos = scan(config)
    by_name: dict[str, list[Repo]] = {}
    by_path = {}
    for repo in repos:
        by_name.setdefault(repo.name.casefold(), []).append(repo)
        by_path[repo.path.resolve()] = repo
    chosen: list[Repo] = []
    for item in wanted:
        candidate = Path(item).expanduser()
        if candidate.is_dir() and candidate.resolve() in by_path:
            chosen.append(by_path[candidate.resolve()])
            continue
        if candidate.is_dir() and (candidate / ".git").is_dir():
            chosen.append(Repo(name=candidate.name, path=candidate.resolve()))
            continue
        matches = by_name.get(item.casefold(), [])
        if len(matches) == 1:
            chosen.append(matches[0])
        elif not matches:
            known = ", ".join(sorted(repo.name for repo in repos))
            raise ui.Abort(f"unknown repository {item!r}. Known: {known}")
        else:
            paths = ", ".join(str(repo.path) for repo in matches)
            raise ui.Abort(f"repository name {item!r} is ambiguous; use a path: {paths}")
    return chosen


def _parse_suffixes(values: list[str]) -> dict[str, str]:
    result = {}
    for value in values:
        if "=" not in value:
            raise ui.Abort(f"--suffix expects repo=suffix, got {value!r}")
        repo, suffix = value.split("=", 1)
        problem = names.validate_name(suffix, "suffix")
        if problem:
            raise ui.Abort(problem)
        result[repo.casefold()] = suffix
    return result


def _find_feature(features: list[manifest.Feature], name: str | None) -> manifest.Feature:
    if name:
        feature = manifest.find(features, name)
        if feature is None:
            raise ui.Abort(f"no feature named {name!r} under the features directory.")
        return feature
    current = herdr.current_feature(features, herdr.context())
    if current is None:
        raise ui.Abort("not inside a feature workspace; pass --feature NAME.")
    return current


def _answers(args: argparse.Namespace, suffixes: dict[str, str] | None = None) -> dict[str, str]:
    answers: dict[str, str] = {}
    yes = getattr(args, "yes", False)
    if yes:
        answers["confirm"] = "y"
        answers["cleanup-interrupted"] = "y"
    fetch_policy = getattr(args, "on_fetch_failure", "abort")
    answers["fetch-failure"] = "c" if fetch_policy == "continue" else "a"
    for repo, suffix in (suffixes or {}).items():
        answers[f"suffix:{repo}"] = suffix
    return answers


def _emit(args: argparse.Namespace, payload: dict, human: str) -> None:
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(human)


# --- commands -----------------------------------------------------------------


def cmd_list(args, config: Config) -> int:
    features = common.load_features(config)
    live = common.live_map(features)
    payload = [_feature_dict(f, live, states=not args.no_states) for f in features]
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    if not features:
        print(f"no features under {config.features_directory}")
        return 0
    for item in payload:
        ws = item["workspace_id"] or "-"
        print(f"{item['feature']:<32} {item['status']:<14} {ws:<6} {len(item['worktrees'])} worktree(s)")
        for wt in item["worktrees"]:
            state = wt.get("state_detail", "")
            print(f"    {wt['folder']:<36} {wt['branch']:<36} {state}")
    return 0


def _create(
    args, config: Config, feature: manifest.Feature, features_other: list, requests, *, is_new: bool
) -> list[common.Planned]:
    planned = common.preflight(config, feature, requests, features_other)
    common.show_plan(planned)
    return planned


def cmd_new(args, config: Config) -> int:
    suffixes = _parse_suffixes(args.suffix)
    ui.set_noninteractive(_answers(args, suffixes))
    features = common.load_features(config)
    problem = names.validate_name(args.name)
    if problem:
        raise ui.Abort(problem)
    existing = manifest.find(features, args.name)
    root = config.features_directory / args.name
    repos = _resolve_repos(config, args.repo)
    if not repos:
        raise ui.Abort("pass at least one --repo.")
    unknown = [key for key in suffixes if key not in {r.name.casefold() for r in repos}]
    if unknown:
        raise ui.Abort(f"--suffix names repositories that are not in --repo: {', '.join(unknown)}")

    with mutation_lock():
        if existing is not None and existing.readable and existing.status == manifest.STATUS_CREATING:
            if not args.yes:
                raise ui.Abort(
                    f"{existing.name} was interrupted while being created; rerun with --yes to clean it up first."
                )
            common.cleanup_interrupted(existing)
            features = [f for f in features if f is not existing]
        elif existing is not None:
            raise ui.Abort(f"a feature named {existing.name!r} already exists; use add or open.")
        elif root.exists():
            raise ui.Abort(f"{root} already exists but is not a known feature.")

        feature = manifest.Feature(name=args.name, root=root, branch_prefix=config.branch_prefix)
        requests = [common.Request(repo, suffixes.get(repo.name.casefold())) for repo in repos]
        planned = _create(args, config, feature, features, requests, is_new=True)
        if not args.yes:
            payload = {
                "dry_run": True,
                "feature": args.name,
                "root": str(root),
                "plan": [
                    {"repo": p.repo.name, "folder": p.folder, "branch": p.branch, "how": p.plan.source}
                    for p in planned
                ],
            }
            _emit(args, payload, "dry run only; rerun with --yes to create.")
            return 0
        config.features_directory.mkdir(parents=True, exist_ok=True)
        root.mkdir()
        common.execute(feature, planned, is_new=True)

    workspace_id = None
    if not args.no_workspace:
        try:
            workspace_id = herdr.create_workspace(feature, focus=args.focus)
            feature.save()
        except herdr.HerdrError as error:
            ui.warn(f"feature created but its workspace could not be opened: {error}")
    payload = _feature_dict(feature, {feature.name: workspace_id} if workspace_id else {}, states=False)
    _emit(
        args,
        payload,
        f"created {feature.name} with {len(planned)} worktree(s) at {root}"
        + (f"; workspace {workspace_id}" if workspace_id else ""),
    )
    return 0


def cmd_add(args, config: Config) -> int:
    suffixes = _parse_suffixes(args.suffix)
    ui.set_noninteractive(_answers(args, suffixes))
    features = common.load_features(config)
    feature = _find_feature(features, args.feature)
    if not feature.mutable:
        raise ui.Abort(f"{feature.name} cannot be changed right now ({common.status_word(feature, {})}).")
    repos = _resolve_repos(config, args.repo)
    if not repos:
        raise ui.Abort("pass at least one --repo.")
    requests = []
    for repo in repos:
        suffix = suffixes.get(repo.name.casefold())
        already = feature.worktrees_for(repo.path)
        if already and not suffix:
            raise ui.Abort(
                f"{repo.name} is already in {feature.name} as {', '.join(wt.folder for wt in already)}; "
                f"pass --suffix {repo.name}=NAME to add a second worktree."
            )
        if suffix and suffix in {wt.suffix for wt in already}:
            raise ui.Abort(f"{repo.name}@{suffix} already exists in {feature.name}.")
        requests.append(common.Request(repo, suffix))
    others = [f for f in features if f is not feature]
    with mutation_lock():
        planned = _create(args, config, feature, others, requests, is_new=False)
        if not args.yes:
            payload = {
                "dry_run": True,
                "feature": feature.name,
                "plan": [
                    {"repo": p.repo.name, "folder": p.folder, "branch": p.branch, "how": p.plan.source}
                    for p in planned
                ],
            }
            _emit(args, payload, "dry run only; rerun with --yes to add.")
            return 0
        common.execute(feature, planned, is_new=False)
    live = common.live_map([feature])
    if args.open and feature.name not in live:
        workspace_id = herdr.create_workspace(feature, focus=args.focus)
        feature.save()
        live[feature.name] = workspace_id
    _emit(
        args,
        _feature_dict(feature, live, states=False),
        f"added {len(planned)} worktree(s) to {feature.name}",
    )
    return 0


def cmd_open(args, config: Config) -> int:
    ui.set_noninteractive({})
    features = common.load_features(config)
    feature = _find_feature(features, args.feature)
    if not feature.readable:
        raise ui.Abort(f"{feature.name} is unreadable: {feature.error}")
    live = common.live_map(features)
    workspace_id = live.get(feature.name)
    created = False
    if workspace_id:
        if args.focus:
            herdr.focus_workspace(workspace_id)
    else:
        workspace_id = herdr.create_workspace(feature, focus=args.focus)
        if feature.mutable:
            feature.save()
        created = True
    payload = {
        "feature": feature.name,
        "root": str(feature.root),
        "workspace_id": workspace_id,
        "created": created,
    }
    _emit(args, payload, f"{feature.name}: workspace {workspace_id}" + (" (created)" if created else ""))
    return 0


def cmd_drop(args, config: Config) -> int:
    ui.set_noninteractive(_answers(args))
    features = common.load_features(config)
    feature = _find_feature(features, args.feature)
    if not feature.mutable:
        raise ui.Abort(f"{feature.name} cannot be changed right now.")
    wanted = set(args.worktree)
    chosen = [wt for wt in feature.worktrees if wt.folder in wanted or wt.repo_name in wanted]
    missing = wanted - {wt.folder for wt in chosen} - {wt.repo_name for wt in chosen}
    if missing:
        raise ui.Abort(
            f"not in {feature.name}: {', '.join(sorted(missing))}. Folders: {', '.join(sorted(feature.folders()))}"
        )
    states = {wt.folder: gitops.worktree_state(feature.path_of(wt)) for wt in chosen}
    careful = [wt for wt in chosen if states[wt.folder].needs_care]
    if careful and not args.force:
        detail = "; ".join(f"{wt.folder}: {states[wt.folder].detail}" for wt in careful)
        raise ui.Abort(
            f"refusing to drop worktrees with unsaved work ({detail}); pass --force to drop anyway."
        )
    if not args.yes:
        payload = {
            "dry_run": True,
            "feature": feature.name,
            "drop": [{"folder": wt.folder, "state": states[wt.folder].detail} for wt in chosen],
        }
        _emit(args, payload, "dry run only; rerun with --yes to drop.")
        return 0
    with mutation_lock():
        for wt in chosen:
            gitops.worktree_remove(Path(wt.repo_path), feature.path_of(wt))
            feature.worktrees.remove(wt)
            feature.save()
            ui.ok(f"dropped {wt.folder}; branch {wt.branch} kept")
    _emit(
        args,
        _feature_dict(feature, {}, states=False),
        f"dropped {len(chosen)} worktree(s) from {feature.name}",
    )
    return 0


def cmd_remove(args, config: Config) -> int:
    ui.set_noninteractive(_answers(args))
    features = common.load_features(config)
    feature = _find_feature(features, args.feature)
    if not feature.readable:
        raise ui.Abort(f"{feature.name} is unreadable ({feature.error}); remove {feature.root} by hand.")
    states = {wt.folder: gitops.worktree_state(feature.path_of(wt)) for wt in feature.worktrees}
    careful = [wt for wt in feature.worktrees if states[wt.folder].needs_care]
    if careful and not args.force:
        detail = "; ".join(f"{wt.folder}: {states[wt.folder].detail}" for wt in careful)
        raise ui.Abort(
            f"refusing to remove a feature with unsaved work ({detail}); pass --force to remove anyway."
        )
    live = common.live_map(features)
    workspace_id = live.get(feature.name)
    if not args.yes:
        payload = {
            "dry_run": True,
            "feature": feature.name,
            "workspace_id": workspace_id,
            "worktrees": [
                {
                    "folder": wt.folder,
                    "branch": wt.branch,
                    "state": states[wt.folder].detail,
                    "branch_created": wt.branch_created,
                }
                for wt in feature.worktrees
            ],
        }
        _emit(args, payload, "dry run only; rerun with --yes to remove.")
        return 0
    entries = list(feature.worktrees)
    deleted_branches = []
    with mutation_lock():
        if workspace_id:
            busy = herdr.busy_panes(workspace_id)
            if busy and not args.force:
                raise ui.Abort(
                    f"workspace {workspace_id} has {len(busy)} agent(s) working or waiting; pass --force to close it anyway."
                )
            herdr.close_workspace(workspace_id)
            ui.ok(f"closed workspace {workspace_id}")
        for wt in entries:
            gitops.worktree_remove(Path(wt.repo_path), feature.path_of(wt))
            feature.worktrees.remove(wt)
            ui.ok(f"removed {wt.folder}")
        import shutil

        shutil.rmtree(feature.root, ignore_errors=True)
        if args.delete_branches:
            claims = manifest.branch_claims([f for f in features if f is not feature])
            for wt in entries:
                if wt.branch_created and (wt.repo_path, wt.branch) not in claims:
                    if gitops.branch_delete(Path(wt.repo_path), wt.branch) is None:
                        deleted_branches.append({"repo": wt.repo_name, "branch": wt.branch})
                        ui.ok(f"deleted branch {wt.branch} in {wt.repo_name}")
    payload = {
        "feature": feature.name,
        "removed": True,
        "closed_workspace": workspace_id,
        "deleted_branches": deleted_branches,
        "kept_branches": [
            {"repo": wt.repo_name, "branch": wt.branch}
            for wt in entries
            if not any(d["repo"] == wt.repo_name and d["branch"] == wt.branch for d in deleted_branches)
        ],
    }
    _emit(args, payload, f"removed {feature.name}")
    return 0


def cmd_install_cli(args, config=None) -> int:
    ui.set_noninteractive({})
    result = install_cli.install(with_skill=not args.no_skill, replace_foreign=args.replace)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        install_cli.report(result)
    return 0


def cmd_uninstall_cli(args, config=None) -> int:
    ui.set_noninteractive({})
    result = install_cli.uninstall(with_skill=True)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for link in result["removed"]:
            ui.ok(f"removed {link}")
        if not result["removed"]:
            ui.warn("nothing to remove")
    return 0


# --- parser -------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="herdr-feature",
        description="Manage cross-repository feature workspaces in Herdr (non-interactive).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common_flags(p, *, mutating: bool):
        p.add_argument("--json", action="store_true", help="print the result as JSON on stdout")
        if mutating:
            p.add_argument("--yes", action="store_true", help="actually do it (otherwise dry run)")

    p = sub.add_parser("list", help="list features, their status and worktrees")
    common_flags(p, mutating=False)
    p.add_argument("--no-states", action="store_true", help="skip git status per worktree (faster)")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("new", help="create a feature")
    common_flags(p, mutating=True)
    p.add_argument("--name", required=True)
    p.add_argument("--repo", action="append", default=[], help="repository name or path (repeatable)")
    p.add_argument(
        "--suffix",
        action="append",
        default=[],
        metavar="REPO=SUFFIX",
        help="use <branch>-SUFFIX and folder REPO@SUFFIX for that repo",
    )
    p.add_argument("--on-fetch-failure", choices=("abort", "continue"), default="abort")
    p.add_argument("--no-workspace", action="store_true", help="create files only, no Herdr workspace")
    p.add_argument(
        "--focus", action="store_true", help="focus the new workspace (default: leave focus alone)"
    )
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("add", help="add worktrees to a feature")
    common_flags(p, mutating=True)
    p.add_argument("--feature", help="feature name (default: the one this pane's workspace belongs to)")
    p.add_argument("--repo", action="append", default=[])
    p.add_argument("--suffix", action="append", default=[], metavar="REPO=SUFFIX")
    p.add_argument("--on-fetch-failure", choices=("abort", "continue"), default="abort")
    p.add_argument("--open", action="store_true", help="open a workspace if the feature has none")
    p.add_argument("--focus", action="store_true")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("open", help="open or find a feature's workspace")
    common_flags(p, mutating=False)
    p.add_argument("--feature", required=True)
    p.add_argument("--focus", action="store_true")
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("drop", help="remove worktrees from a feature (branches kept)")
    common_flags(p, mutating=True)
    p.add_argument("--feature")
    p.add_argument(
        "--worktree",
        action="append",
        default=[],
        required=True,
        help="folder name, e.g. repo or repo@suffix (repeatable)",
    )
    p.add_argument("--force", action="store_true", help="drop even with uncommitted or unpushed work")
    p.set_defaults(func=cmd_drop)

    p = sub.add_parser("remove", help="remove a feature entirely")
    common_flags(p, mutating=True)
    p.add_argument("--feature")
    p.add_argument("--force", action="store_true", help="remove even with unsaved work or busy agents")
    p.add_argument(
        "--delete-branches", action="store_true", help="also delete local branches this plugin created"
    )
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser(
        "install-cli", help="symlink this command into ~/.local/bin and install the agent skill"
    )
    common_flags(p, mutating=False)
    p.add_argument(
        "--no-skill", action="store_true", help="do not link the Claude Code skill into ~/.claude/skills"
    )
    p.add_argument("--replace", action="store_true", help="replace an existing file at the link location")
    p.set_defaults(func=cmd_install_cli, needs_config=False)

    p = sub.add_parser("uninstall-cli", help="remove the symlinks created by install-cli")
    common_flags(p, mutating=False)
    p.set_defaults(func=cmd_uninstall_cli, needs_config=False)
    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if not getattr(args, "needs_config", True):
            return args.func(args)
        config = load_config(interactive=False)
        return args.func(args, config)
    except ui.Cancelled:
        return 1
    except ui.Abort as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def entrypoint() -> None:
    """Console-script entry (`uv run herdr-feature`, or a pip install)."""
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    entrypoint()
