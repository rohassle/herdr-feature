"""Non-interactive command line, for agents and scripts.

    herdr-workthreads new --name X --repo a --repo b [--suffix a=api] [--yes] [--json]
    herdr-workthreads add --thread X --repo a [--suffix a=api] [--yes] [--open]
    herdr-workthreads list [--json]
    herdr-workthreads open --thread X [--focus]
    herdr-workthreads close --thread X [--force] --yes
    herdr-workthreads refresh [--thread X] [--json]
    herdr-workthreads drop --thread X --worktree a@api [--force] --yes
    herdr-workthreads remove --thread X [--force] [--delete-branches] --yes

Without --yes, new/add/drop/remove only print what they would do (a dry run). Progress
lines go to stderr; with --json the result object goes to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import gitops, herdr, manifest, names, prs, ui
from .commands import common, install_cli
from .config import Config, load_config
from .discovery import Repo, scan
from .lock import mutation_lock


class Result:
    def __init__(self, **fields):
        self.fields = fields


# --- helpers ------------------------------------------------------------------


def _thread_dict(
    thread: manifest.Thread,
    live: dict[str, str],
    *,
    states: bool,
    repo_live: dict[str, dict[str, str]] | None = None,
) -> dict:
    nested = (repo_live or {}).get(thread.name, {})
    progress = common.thread_progress(thread)
    data = {
        "thread": thread.name,
        "root": str(thread.root),
        "status": common.status_word(thread, live, repo_live),
        "done": progress.done,
        "progress": {"merged": progress.merged, "total": progress.total, "unknown": progress.unknown},
        "workspace_id": live.get(thread.name),
        "branch_prefix": thread.branch_prefix,
        "worktrees": [],
    }
    if not thread.readable:
        data["error"] = thread.error
        return data
    for wt in thread.worktrees:
        wp = common.worktree_progress(wt)
        entry = {
            "repo_name": wt.repo_name,
            "repo_path": wt.repo_path,
            "folder": wt.folder,
            "path": str(thread.path_of(wt)),
            "branch": wt.branch,
            "branch_created": wt.branch_created,
            "branch_source": wt.branch_source,
            "workspace_id": nested.get(wt.folder),
            "progress": wp.kind,
            "progress_detail": wp.detail,
            "pr": wt.pr,
        }
        if states:
            state = gitops.worktree_state(thread.path_of(wt))
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


def _find_thread(threads: list[manifest.Thread], name: str | None) -> manifest.Thread:
    if name:
        thread = manifest.find(threads, name)
        if thread is None:
            raise ui.Abort(f"no thread named {name!r} under the threads directory.")
        return thread
    current = herdr.current_thread(threads, herdr.context())
    if current is None:
        raise ui.Abort("not inside a thread workspace; pass --thread NAME.")
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
    threads = common.load_threads(config)
    live = common.live_map(threads)
    repo_live = common.repo_live_map(threads)
    payload = [_thread_dict(f, live, states=not args.no_states, repo_live=repo_live) for f in threads]
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    if not threads:
        print(f"no threads under {config.threads_directory}")
        return 0
    for item in payload:
        ws = item["workspace_id"] or "-"
        progress = item.get("progress") or {}
        bar = common.ThreadProgress(
            progress.get("merged", 0), progress.get("total", 0), progress.get("unknown", 0)
        ).bar()
        status = "done" if item.get("done") else item["status"]
        print(f"{item['thread']:<32} {status:<14} {ws:<6} {bar}")
        for wt in item["worktrees"]:
            state = wt.get("state_detail", "")
            nested = wt.get("workspace_id") or "-"
            print(
                f"    {wt['folder']:<32} {wt['branch']:<32} {nested:<6} {wt.get('progress_detail', ''):<22} {state}"
            )
    return 0


def _create(
    args, config: Config, thread: manifest.Thread, threads_other: list, requests, *, is_new: bool
) -> list[common.Planned]:
    planned = common.preflight(config, thread, requests, threads_other)
    common.show_plan(planned)
    return planned


def cmd_new(args, config: Config) -> int:
    suffixes = _parse_suffixes(args.suffix)
    ui.set_noninteractive(_answers(args, suffixes))
    threads = common.load_threads(config)
    problem = names.validate_name(args.name)
    if problem:
        raise ui.Abort(problem)
    existing = manifest.find(threads, args.name)
    root = config.threads_directory / args.name
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
            threads = [f for f in threads if f is not existing]
        elif existing is not None:
            raise ui.Abort(f"a thread named {existing.name!r} already exists; use add or open.")
        elif root.exists():
            raise ui.Abort(f"{root} already exists but is not a known thread.")

        thread = manifest.Thread(name=args.name, root=root, branch_prefix=config.branch_prefix)
        requests = [common.Request(repo, suffixes.get(repo.name.casefold())) for repo in repos]
        planned = _create(args, config, thread, threads, requests, is_new=True)
        if not args.yes:
            payload = {
                "dry_run": True,
                "thread": args.name,
                "root": str(root),
                "plan": [
                    {"repo": p.repo.name, "folder": p.folder, "branch": p.branch, "how": p.plan.source}
                    for p in planned
                ],
            }
            _emit(args, payload, "dry run only; rerun with --yes to create.")
            return 0
        config.threads_directory.mkdir(parents=True, exist_ok=True)
        root.mkdir()
        common.execute(thread, planned, is_new=True)

    opened = herdr.Opened()
    if not args.no_workspace:
        try:
            opened = common.open_workspaces(config, thread, focus=args.focus)
        except herdr.HerdrError as error:
            ui.warn(f"thread created but its workspace could not be opened: {error}")
        for failure in opened.failures:
            ui.warn(f"could not open a worktree workspace for {failure}")
    live, repo_live = _opened_maps(thread, opened)
    payload = _thread_dict(thread, live, states=False, repo_live=repo_live)
    _emit(
        args,
        payload,
        f"created {thread.name} with {len(planned)} worktree(s) at {root}" + _opened_summary(opened),
    )
    return 0


def _opened_maps(thread: manifest.Thread, opened: herdr.Opened) -> tuple[dict, dict]:
    live = {thread.name: opened.workspace_id} if opened.workspace_id else {}
    repo_live = {thread.name: opened.repo_workspaces} if opened.repo_workspaces else {}
    return live, repo_live


def _opened_summary(opened: herdr.Opened) -> str:
    parts = []
    if opened.workspace_id:
        parts.append(f"workspace {opened.workspace_id}")
    if opened.repo_workspaces:
        parts.append(f"{len(opened.repo_workspaces)} nested worktree workspace(s)")
    return f"; {', '.join(parts)}" if parts else ""


def cmd_add(args, config: Config) -> int:
    suffixes = _parse_suffixes(args.suffix)
    ui.set_noninteractive(_answers(args, suffixes))
    threads = common.load_threads(config)
    thread = _find_thread(threads, args.thread)
    if not thread.mutable:
        raise ui.Abort(f"{thread.name} cannot be changed right now ({common.status_word(thread, {})}).")
    repos = _resolve_repos(config, args.repo)
    if not repos:
        raise ui.Abort("pass at least one --repo.")
    requests = []
    for repo in repos:
        suffix = suffixes.get(repo.name.casefold())
        already = thread.worktrees_for(repo.path)
        if already and not suffix:
            raise ui.Abort(
                f"{repo.name} is already in {thread.name} as {', '.join(wt.folder for wt in already)}; "
                f"pass --suffix {repo.name}=NAME to add a second worktree."
            )
        if suffix and suffix in {wt.suffix for wt in already}:
            raise ui.Abort(f"{repo.name}@{suffix} already exists in {thread.name}.")
        requests.append(common.Request(repo, suffix))
    others = [f for f in threads if f is not thread]
    with mutation_lock():
        planned = _create(args, config, thread, others, requests, is_new=False)
        if not args.yes:
            payload = {
                "dry_run": True,
                "thread": thread.name,
                "plan": [
                    {"repo": p.repo.name, "folder": p.folder, "branch": p.branch, "how": p.plan.source}
                    for p in planned
                ],
            }
            _emit(args, payload, "dry run only; rerun with --yes to add.")
            return 0
        added = common.execute(thread, planned, is_new=False)
    live = common.live_map([thread])
    repo_live = common.repo_live_map([thread])
    is_open = thread.name in live or bool(repo_live.get(thread.name))
    if args.open or is_open:
        # An open thread gets nested workspaces for the new entries (when configured);
        # --open also creates whatever the mode calls for when nothing is open yet.
        opened = common.open_workspaces(
            config,
            thread,
            focus=args.focus,
            workspace_id=live.get(thread.name),
            only=added if is_open and not args.open else None,
        )
        for failure in opened.failures:
            ui.warn(f"could not open a worktree workspace for {failure}")
        live, repo_live = _opened_maps(thread, opened)
    _emit(
        args,
        _thread_dict(thread, live, states=False, repo_live=repo_live),
        f"added {len(planned)} worktree(s) to {thread.name}",
    )
    return 0


def cmd_open(args, config: Config) -> int:
    ui.set_noninteractive({})
    threads = common.load_threads(config)
    thread = _find_thread(threads, args.thread)
    if not thread.readable:
        raise ui.Abort(f"{thread.name} is unreadable: {thread.error}")
    live = common.live_map(threads)
    opened = common.open_workspaces(config, thread, focus=args.focus, workspace_id=live.get(thread.name))
    for failure in opened.failures:
        ui.warn(f"could not open a worktree workspace for {failure}")
    payload = {
        "thread": thread.name,
        "root": str(thread.root),
        "workspace_id": opened.workspace_id,
        "repo_workspaces": opened.repo_workspaces,
        "created": opened.created,
    }
    _emit(
        args,
        payload,
        f"{thread.name}: workspace {opened.any or '-'}" + (" (created)" if opened.created else ""),
    )
    return 0


def cmd_close(args, config: Config) -> int:
    ui.set_noninteractive(_answers(args))
    threads = common.load_threads(config)
    thread = _find_thread(threads, args.thread)
    live = common.live_map(threads)
    repo_live = common.repo_live_map(threads)
    ids = common.thread_workspace_ids(thread, live, repo_live)
    payload = {
        "thread": thread.name,
        "workspace_id": live.get(thread.name),
        "repo_workspaces": repo_live.get(thread.name, {}),
    }
    if not ids:
        _emit(args, {**payload, "closed": []}, f"{thread.name} has no open workspace")
        return 0
    if not args.yes:
        _emit(
            args,
            {"dry_run": True, **payload},
            f"dry run only; rerun with --yes to close {len(ids)} workspace(s).",
        )
        return 0
    busy = herdr.busy_panes(*ids)
    if busy and not args.force:
        raise ui.Abort(
            f"the thread's workspaces have {len(busy)} agent(s) working or waiting; pass --force to close them anyway."
        )
    closed = common.close_thread_workspaces(thread, live, repo_live)
    for workspace_id in closed:
        ui.ok(f"closed workspace {workspace_id}")
    _emit(
        args,
        {**payload, "closed": closed},
        f"closed {len(closed)} workspace(s) of {thread.name}; files kept",
    )
    return 0


def cmd_refresh(args, config: Config) -> int:
    ui.set_noninteractive({})
    threads = common.load_threads(config)
    if args.thread:
        threads = [_find_thread(threads, args.thread)]
    prs.gh_binary()
    common.refresh_progress(threads)
    live = common.live_map(threads)
    repo_live = common.repo_live_map(threads)
    payload = [_thread_dict(f, live, states=False, repo_live=repo_live) for f in threads]
    done = sum(1 for item in payload if item.get("done"))
    _emit(args, payload, f"refreshed {len(payload)} thread(s); {done} done")
    return 0


def cmd_drop(args, config: Config) -> int:
    ui.set_noninteractive(_answers(args))
    threads = common.load_threads(config)
    thread = _find_thread(threads, args.thread)
    if not thread.mutable:
        raise ui.Abort(f"{thread.name} cannot be changed right now.")
    wanted = set(args.worktree)
    chosen = [wt for wt in thread.worktrees if wt.folder in wanted or wt.repo_name in wanted]
    missing = wanted - {wt.folder for wt in chosen} - {wt.repo_name for wt in chosen}
    if missing:
        raise ui.Abort(
            f"not in {thread.name}: {', '.join(sorted(missing))}. Folders: {', '.join(sorted(thread.folders()))}"
        )
    states = {wt.folder: gitops.worktree_state(thread.path_of(wt)) for wt in chosen}
    careful = [wt for wt in chosen if states[wt.folder].needs_care]
    if careful and not args.force:
        detail = "; ".join(f"{wt.folder}: {states[wt.folder].detail}" for wt in careful)
        raise ui.Abort(
            f"refusing to drop worktrees with unsaved work ({detail}); pass --force to drop anyway."
        )
    if not args.yes:
        payload = {
            "dry_run": True,
            "thread": thread.name,
            "drop": [{"folder": wt.folder, "state": states[wt.folder].detail} for wt in chosen],
        }
        _emit(args, payload, "dry run only; rerun with --yes to drop.")
        return 0
    with mutation_lock():
        for workspace_id in herdr.close_repo_workspaces(thread, chosen):
            ui.ok(f"closed worktree workspace {workspace_id}")
        for wt in chosen:
            gitops.worktree_remove(Path(wt.repo_path), thread.path_of(wt))
            thread.worktrees.remove(wt)
            thread.save()
            ui.ok(f"dropped {wt.folder}; branch {wt.branch} kept")
    _emit(
        args,
        _thread_dict(thread, {}, states=False),
        f"dropped {len(chosen)} worktree(s) from {thread.name}",
    )
    return 0


def cmd_remove(args, config: Config) -> int:
    ui.set_noninteractive(_answers(args))
    threads = common.load_threads(config)
    thread = _find_thread(threads, args.thread)
    if not thread.readable:
        raise ui.Abort(f"{thread.name} is unreadable ({thread.error}); remove {thread.root} by hand.")
    states = {wt.folder: gitops.worktree_state(thread.path_of(wt)) for wt in thread.worktrees}
    careful = [wt for wt in thread.worktrees if states[wt.folder].needs_care]
    if careful and not args.force:
        detail = "; ".join(f"{wt.folder}: {states[wt.folder].detail}" for wt in careful)
        raise ui.Abort(
            f"refusing to remove a thread with unsaved work ({detail}); pass --force to remove anyway."
        )
    live = common.live_map(threads)
    workspace_id = live.get(thread.name)
    nested = common.repo_live_map([thread]).get(thread.name, {})
    if not args.yes:
        payload = {
            "dry_run": True,
            "thread": thread.name,
            "workspace_id": workspace_id,
            "repo_workspaces": nested,
            "worktrees": [
                {
                    "folder": wt.folder,
                    "branch": wt.branch,
                    "state": states[wt.folder].detail,
                    "branch_created": wt.branch_created,
                }
                for wt in thread.worktrees
            ],
        }
        _emit(args, payload, "dry run only; rerun with --yes to remove.")
        return 0
    entries = list(thread.worktrees)
    deleted_branches = []
    with mutation_lock():
        closing = [*nested.values(), *([workspace_id] if workspace_id else [])]
        if closing:
            busy = herdr.busy_panes(*closing)
            if busy and not args.force:
                raise ui.Abort(
                    f"the thread's workspaces have {len(busy)} agent(s) working or waiting; pass --force to close them anyway."
                )
            for closing_id in closing:
                herdr.close_workspace(closing_id)
                ui.ok(f"closed workspace {closing_id}")
        for wt in entries:
            gitops.worktree_remove(Path(wt.repo_path), thread.path_of(wt))
            thread.worktrees.remove(wt)
            ui.ok(f"removed {wt.folder}")
        import shutil

        shutil.rmtree(thread.root, ignore_errors=True)
        if args.delete_branches:
            claims = manifest.branch_claims([f for f in threads if f is not thread])
            for wt in entries:
                if wt.branch_created and (wt.repo_path, wt.branch) not in claims:
                    if gitops.branch_delete(Path(wt.repo_path), wt.branch) is None:
                        deleted_branches.append({"repo": wt.repo_name, "branch": wt.branch})
                        ui.ok(f"deleted branch {wt.branch} in {wt.repo_name}")
    payload = {
        "thread": thread.name,
        "removed": True,
        "closed_workspace": workspace_id,
        "closed_repo_workspaces": nested,
        "deleted_branches": deleted_branches,
        "kept_branches": [
            {"repo": wt.repo_name, "branch": wt.branch}
            for wt in entries
            if not any(d["repo"] == wt.repo_name and d["branch"] == wt.branch for d in deleted_branches)
        ],
    }
    _emit(args, payload, f"removed {thread.name}")
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
        prog="herdr-workthreads",
        description="Manage cross-repository thread workspaces in Herdr (non-interactive).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common_flags(p, *, mutating: bool):
        p.add_argument("--json", action="store_true", help="print the result as JSON on stdout")
        if mutating:
            p.add_argument("--yes", action="store_true", help="actually do it (otherwise dry run)")

    p = sub.add_parser("list", help="list threads, their status and worktrees")
    common_flags(p, mutating=False)
    p.add_argument("--no-states", action="store_true", help="skip git status per worktree (faster)")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("new", help="create a thread")
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

    p = sub.add_parser("add", help="add worktrees to a thread")
    common_flags(p, mutating=True)
    p.add_argument("--thread", help="thread name (default: the one this pane's workspace belongs to)")
    p.add_argument("--repo", action="append", default=[])
    p.add_argument("--suffix", action="append", default=[], metavar="REPO=SUFFIX")
    p.add_argument("--on-fetch-failure", choices=("abort", "continue"), default="abort")
    p.add_argument("--open", action="store_true", help="open a workspace if the thread has none")
    p.add_argument("--focus", action="store_true")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("open", help="open or find a thread's workspace")
    common_flags(p, mutating=False)
    p.add_argument("--thread", required=True)
    p.add_argument("--focus", action="store_true")
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("close", help="close a thread's Herdr workspaces (files and branches kept)")
    common_flags(p, mutating=True)
    p.add_argument("--thread", help="thread name (default: the one this pane's workspace belongs to)")
    p.add_argument("--force", action="store_true", help="close even with agents working or waiting")
    p.set_defaults(func=cmd_close)

    p = sub.add_parser("refresh", help="look up every worktree's pull request with gh and fetch remotes")
    common_flags(p, mutating=False)
    p.add_argument("--thread", help="only this thread (default: all)")
    p.set_defaults(func=cmd_refresh)

    p = sub.add_parser("drop", help="remove worktrees from a thread (branches kept)")
    common_flags(p, mutating=True)
    p.add_argument("--thread")
    p.add_argument(
        "--worktree",
        action="append",
        default=[],
        required=True,
        help="folder name, e.g. repo or repo@suffix (repeatable)",
    )
    p.add_argument("--force", action="store_true", help="drop even with uncommitted or unpushed work")
    p.set_defaults(func=cmd_drop)

    p = sub.add_parser("remove", help="remove a thread entirely")
    common_flags(p, mutating=True)
    p.add_argument("--thread")
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
    """Console-script entry (`uv run herdr-workthreads`, or a pip install)."""
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    entrypoint()
