"""End-to-end run against throwaway repositories and the LIVE Herdr session.

Creates workspaces labelled zz-test-* and closes only the ids it created. Everything on
disk lives under a temp dir. Run from the repo root:

    python3 tests/e2e/run.py

Requires HERDR_ENV=1 (an attached Herdr session) and git.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
FAKE_FZF = ROOT / "tests" / "e2e" / "fake_fzf.sh"
HERDR = os.environ.get("HERDR_BIN_PATH", "herdr")

passed = 0
failed = 0


def check(condition: bool, message: str) -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  ok   {message}")
    else:
        failed += 1
        print(f"  FAIL {message}")


def sh(*args: str, cwd: Path | None = None, check_rc: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True)
    if check_rc and result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)}\n{result.stdout}\n{result.stderr}")
    return result


def git(repo: Path, *args: str, check_rc: bool = True) -> str:
    return sh("git", "-C", str(repo), *args, check_rc=check_rc).stdout.strip()


def herdr(*args: str) -> dict:
    result = sh(HERDR, *args, check_rc=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout)["result"]


def workspace_is_linked_worktree(workspace_id: str) -> bool:
    for ws in herdr("workspace", "list")["workspaces"]:
        if ws["workspace_id"] == workspace_id:
            return bool((ws.get("worktree") or {}).get("is_linked_worktree"))
    return False


def nested_workspaces(feature_root: Path) -> dict[str, dict]:
    """checkout folder name -> workspace, for Herdr worktree workspaces inside a feature root."""
    found = {}
    for ws in herdr("workspace", "list")["workspaces"]:
        provenance = ws.get("worktree") or {}
        checkout = provenance.get("checkout_path")
        if provenance.get("is_linked_worktree") and checkout:
            path = Path(checkout).resolve()
            if path.is_relative_to(feature_root.resolve()):
                found[path.name] = ws
    return found


def workspace_by_label(label: str) -> dict | None:
    matches = [ws for ws in herdr("workspace", "list")["workspaces"] if ws["label"] == label]
    return matches[0] if matches else None


class Fixture:
    def __init__(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="herdr-feature-e2e-"))
        self.repos = self.base / "repos"
        self.origins = self.base / "origins"
        self.features = self.base / "features"
        self.queue = self.base / "fzf-queue"
        self.config = self.base / "config.toml"
        self.config.write_text(
            f'repo_directories = ["{self.repos}"]\n'
            f'features_directory = "{self.features}"\n'
            'branch_prefix = "feat/"\n'
        )
        self.config_both = self.base / "config-both.toml"
        self.config_both.write_text(self.config.read_text() + 'workspaces = "both"\n')
        self.make("alpha", "main", remote=True)
        self.make("beta", "master", remote=True)
        self.make("gamma-nohead", "main", remote=True)
        self.make("delta-noremote", "main", remote=False)
        git(self.repos / "gamma-nohead", "remote", "set-head", "origin", "-d")
        self.created_workspaces: list[str] = []

    def make(self, name: str, branch: str, *, remote: bool) -> Path:
        repo = self.repos / name
        repo.mkdir(parents=True)
        sh("git", "init", "-q", "-b", branch, str(repo))
        git(
            repo,
            "-c",
            "user.email=e2e@test",
            "-c",
            "user.name=e2e",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "init",
        )
        if remote:
            origin = self.origins / f"{name}.git"
            sh("git", "init", "-q", "--bare", "-b", branch, str(origin))
            git(repo, "remote", "add", "origin", str(origin))
            git(repo, "push", "-q", "-u", "origin", branch)
            git(repo, "remote", "set-head", "origin", "--auto")
        return repo

    def run(
        self, command: str, *, fzf: list[str], inputs: list[str], expect_rc: int = 0
    ) -> subprocess.CompletedProcess:
        self.queue.write_text("".join(line + "\n" for line in fzf))
        env = {
            **os.environ,
            "PYTHONPATH": str(SRC),
            "HERDR_FEATURE_CONFIG": str(self.config),
            "HERDR_FEATURE_FZF": str(FAKE_FZF),
            "FAKE_FZF_QUEUE": str(self.queue),
            "HERDR_FEATURE_INPUTS": "\n".join(inputs),
            "HERDR_PLUGIN_ROOT": str(ROOT),
            "HERDR_PLUGIN_STATE_DIR": str(self.base / "state"),
            "HERDR_BIN_PATH": HERDR,
        }
        env.pop("FEATURE_WORKSPACE_ID", None)
        env.pop("FEATURE_INVOKER_CONTEXT", None)
        env.pop("HERDR_PLUGIN_CONTEXT_JSON", None)
        result = subprocess.run(
            [sys.executable, "-m", "herdr_feature", command],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        print(f"\n$ feature {command}  (rc={result.returncode})")
        for line in (result.stdout + result.stderr).splitlines():
            print(f"    | {line}")
        check(result.returncode == expect_rc, f"{command} exit code {result.returncode} == {expect_rc}")
        return result

    def cli(self, *args: str, expect_rc: int = 0) -> tuple[subprocess.CompletedProcess, dict | list | None]:
        env = {
            **os.environ,
            "PYTHONPATH": str(SRC),
            "HERDR_FEATURE_CONFIG": str(self.config),
            "HERDR_PLUGIN_ROOT": str(ROOT),
            "HERDR_PLUGIN_STATE_DIR": str(self.base / "state"),
            "HERDR_BIN_PATH": HERDR,
        }
        for key in (
            "FEATURE_WORKSPACE_ID",
            "FEATURE_INVOKER_CONTEXT",
            "HERDR_PLUGIN_CONTEXT_JSON",
            "HERDR_WORKSPACE_ID",
            "HERDR_FEATURE_INPUTS",
            "HERDR_FEATURE_FZF",
        ):
            env.pop(key, None)
        result = subprocess.run(
            [sys.executable, "-m", "herdr_feature", "cli", *args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        print(f"\n$ herdr-feature {' '.join(args)}  (rc={result.returncode})")
        for line in (result.stdout + result.stderr).splitlines():
            print(f"    | {line}")
        check(result.returncode == expect_rc, f"cli {args[0]} exit code {result.returncode} == {expect_rc}")
        payload = None
        if "--json" in args and result.stdout.strip():
            try:
                payload = json.loads(result.stdout)
            except json.JSONDecodeError:
                check(False, "stdout is valid JSON")
        return result, payload

    def manifest(self, name: str) -> dict | None:
        path = self.features / name / ".feature.json"
        return json.loads(path.read_text()) if path.exists() else None

    def track(self, label: str) -> str | None:
        ws = workspace_by_label(label)
        if ws and ws["workspace_id"] not in self.created_workspaces:
            self.created_workspaces.append(ws["workspace_id"])
        return ws["workspace_id"] if ws else None

    def track_id(self, workspace_id: str | None) -> None:
        if workspace_id and workspace_id not in self.created_workspaces:
            self.created_workspaces.append(workspace_id)

    def track_fixture_repo_workspaces(self) -> None:
        """Herdr opens a repository's own workspace as the parent of a nested worktree
        workspace; the fixture repositories are ours, so close those parents too."""
        for ws in herdr("workspace", "list")["workspaces"]:
            root = (ws.get("worktree") or {}).get("repo_root")
            if root and Path(root).resolve().is_relative_to(self.repos.resolve()):
                self.track_id(ws["workspace_id"])

    def cleanup(self) -> None:
        self.track_fixture_repo_workspaces()
        # Nested worktree workspaces first: closing a parent with open children needs --group.
        ordered = sorted(
            self.created_workspaces,
            key=lambda ws_id: 0 if workspace_is_linked_worktree(ws_id) else 1,
        )
        for workspace_id in ordered:
            sh(HERDR, "workspace", "close", workspace_id, check_rc=False)
        shutil.rmtree(self.base, ignore_errors=True)


def scenario_new(f: Fixture) -> None:
    print("\n=== A. new feature with three repos (one without a remote)")
    f.run("new", fzf=["alpha,beta,delta-noremote"], inputs=["zz-test-one", "y"])
    m = f.manifest("zz-test-one")
    check(m is not None and m["status"] == "ready", "manifest written and ready")
    folders = sorted(wt["folder"] for wt in m["worktrees"]) if m else []
    check(folders == ["alpha", "beta", "delta-noremote"], f"worktrees recorded: {folders}")
    alpha = f.features / "zz-test-one" / "alpha"
    check(git(alpha, "symbolic-ref", "--short", "HEAD") == "feat/zz-test-one", "alpha on feat/zz-test-one")
    check(
        git(alpha, "rev-parse", "--abbrev-ref", "@{u}", check_rc=False) == "",
        "new branch has no upstream (--no-track)",
    )
    check(
        [wt for wt in m["worktrees"] if wt["folder"] == "beta"][0]["base_ref"]
        == "refs/remotes/origin/master",
        "beta based on origin/master",
    )
    check(
        [wt for wt in m["worktrees"] if wt["folder"] == "delta-noremote"][0]["remote"] is None,
        "no-remote repo recorded with remote=null",
    )
    ws = f.track("zz-test-one")
    check(ws is not None, f"workspace created: {ws}")
    check(m["workspace"]["id"] == ws, "manifest hint equals live workspace id")
    panes = herdr("pane", "list", "--workspace", ws)["panes"] if ws else []
    check(
        bool(panes) and Path(panes[0]["cwd"]).resolve() == (f.features / "zz-test-one").resolve(),
        "root pane cwd is the feature root",
    )
    tabs = herdr("tab", "list", "--workspace", ws)["tabs"] if ws else []
    check(len(tabs) == 1, f"exactly one tab ({len(tabs)})")


def scenario_add(f: Fixture) -> None:
    print("\n=== B. add: second alpha worktree with suffix + gamma (origin/HEAD unset)")
    f.run("add", fzf=["zz-test-one", "alpha,gamma-nohead"], inputs=["api", "y"])
    m = f.manifest("zz-test-one")
    folders = sorted(wt["folder"] for wt in m["worktrees"])
    check(
        folders == ["alpha", "alpha@api", "beta", "delta-noremote", "gamma-nohead"], f"folders now {folders}"
    )
    api = f.features / "zz-test-one" / "alpha@api"
    check(git(api, "symbolic-ref", "--short", "HEAD") == "feat/zz-test-one-api", "suffix branch name")
    check(
        git(f.repos / "gamma-nohead", "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check_rc=False)
        == "origin/main",
        "origin/HEAD was repaired on gamma",
    )


def scenario_remote_only(f: Fixture) -> None:
    print("\n=== C. remote-only branch is reused as a tracking branch")
    beta = f.repos / "beta"
    git(beta, "branch", "feat/zz-test-two")
    git(beta, "push", "-q", "origin", "feat/zz-test-two")
    git(beta, "branch", "-D", "feat/zz-test-two")
    f.run("new", fzf=["beta"], inputs=["zz-test-two", "y"])
    m = f.manifest("zz-test-two")
    wt = m["worktrees"][0]
    check(
        wt["branch_source"] == "origin" and wt["branch_created"] is True, "branch_source=origin, created=true"
    )
    path = f.features / "zz-test-two" / "beta"
    check(
        git(path, "rev-parse", "--abbrev-ref", "@{u}", check_rc=False) == "origin/feat/zz-test-two",
        "tracking upstream set",
    )
    f.track("zz-test-two")


def scenario_checked_out_elsewhere(f: Fixture) -> None:
    print("\n=== D. branch checked out in the main checkout -> suffix prompt")
    alpha = f.repos / "alpha"
    git(alpha, "checkout", "-q", "-b", "feat/zz-test-three")
    f.run("new", fzf=["alpha"], inputs=["zz-test-three", "alt", "y"])
    m = f.manifest("zz-test-three")
    check(m is not None and m["worktrees"][0]["folder"] == "alpha@alt", "worktree created as alpha@alt")
    check(m is not None and m["worktrees"][0]["branch"] == "feat/zz-test-three-alt", "branch got the suffix")
    git(alpha, "checkout", "-q", "main")
    f.track("zz-test-three")


def scenario_fetch_failure(f: Fixture) -> None:
    print("\n=== E. fetch failure: abort, then continue")
    beta = f.repos / "beta"
    good_url = git(beta, "remote", "get-url", "origin")
    git(beta, "remote", "set-url", "origin", str(f.base / "missing.git"))
    f.run("new", fzf=["beta"], inputs=["zz-test-four", "a"], expect_rc=1)
    check(not (f.features / "zz-test-four").exists(), "abort left no feature folder")
    check(workspace_by_label("zz-test-four") is None, "abort created no workspace")
    check(
        git(beta, "rev-parse", "--verify", "--quiet", "feat/zz-test-four", check_rc=False) == "",
        "abort created no branch",
    )
    f.run("new", fzf=["beta"], inputs=["zz-test-four", "c", "y"])
    check(f.manifest("zz-test-four") is not None, "continue created the feature from local refs")
    git(beta, "remote", "set-url", "origin", good_url)
    f.track("zz-test-four")


def scenario_collision(f: Fixture) -> None:
    print("\n=== F. collision guard: feature 'zz-test-one-api' would reuse alpha's feat/zz-test-one-api")
    f.run("new", fzf=["alpha"], inputs=["zz-test-one-api", "y"], expect_rc=1)
    check(not (f.features / "zz-test-one-api").exists(), "collision refused before creating anything")


def scenario_drop(f: Fixture) -> None:
    print("\n=== G. drop a dirty worktree (typed confirmation)")
    api = f.features / "zz-test-one" / "alpha@api"
    (api / "scratch.txt").write_text("uncommitted")
    f.run("drop", fzf=["zz-test-one", "alpha@api"], inputs=["zz-test-one"])
    m = f.manifest("zz-test-one")
    check("alpha@api" not in [wt["folder"] for wt in m["worktrees"]], "manifest no longer lists alpha@api")
    check(not api.exists(), "folder removed")
    check(
        git(f.repos / "alpha", "rev-parse", "--verify", "--quiet", "feat/zz-test-one-api", check_rc=False)
        != "",
        "branch kept after drop",
    )


def scenario_close_and_open(f: Fixture) -> None:
    print("\n=== H. closing the workspace keeps files; open reattaches")
    old = workspace_by_label("zz-test-one")["workspace_id"]
    herdr("workspace", "close", old)
    check(workspace_by_label("zz-test-one") is None, "workspace closed")
    check((f.features / "zz-test-one" / "alpha").is_dir(), "files still on disk after close")
    f.run("open", fzf=["zz-test-one"], inputs=[])
    new = f.track("zz-test-one")
    check(new is not None and new != old, f"open created a new workspace {new}")
    m = f.manifest("zz-test-one")
    check(m["workspace"]["id"] == new, "manifest hint updated to the new id")
    print("\n=== H2. open on a live feature only focuses (no duplicate workspace)")
    f.run("open", fzf=["zz-test-one"], inputs=[])
    count = len([ws for ws in herdr("workspace", "list")["workspaces"] if ws["label"] == "zz-test-one"])
    check(count == 1, "still exactly one zz-test-one workspace")


def scenario_remove(f: Fixture) -> None:
    print("\n=== I. remove with dirty worktree, then delete branches")
    (f.features / "zz-test-one" / "beta" / "dirty.txt").write_text("x")
    f.run("remove", fzf=["zz-test-one"], inputs=["zz-test-one", "y"])
    check(not (f.features / "zz-test-one").exists(), "feature folder gone")
    check(workspace_by_label("zz-test-one") is None, "workspace closed by remove")
    check(
        git(f.repos / "alpha", "rev-parse", "--verify", "--quiet", "feat/zz-test-one", check_rc=False) == "",
        "created branch deleted",
    )
    check(
        "zz-test-one" not in git(f.repos / "alpha", "worktree", "list", "--porcelain"),
        "alpha no longer registers a zz-test-one worktree",
    )


def scenario_rollback(f: Fixture) -> None:
    print("\n=== J. rollback when the second worktree fails to create")
    beta = f.repos / "beta"
    hooks = f.base / "hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "post-checkout").write_text("#!/bin/sh\nexit 1\n")
    (hooks / "post-checkout").chmod(0o755)
    git(beta, "config", "core.hooksPath", str(hooks))
    f.run("new", fzf=["alpha,beta"], inputs=["zz-test-five", "y"], expect_rc=1)
    git(beta, "config", "--unset", "core.hooksPath")
    check(not (f.features / "zz-test-five").exists(), "feature folder rolled back")
    check(
        git(f.repos / "alpha", "rev-parse", "--verify", "--quiet", "feat/zz-test-five", check_rc=False) == "",
        "alpha branch rolled back",
    )
    check(
        "zz-test-five" not in git(f.repos / "alpha", "worktree", "list", "--porcelain"),
        "alpha worktree rolled back",
    )
    check(workspace_by_label("zz-test-five") is None, "no workspace created")


def scenario_interrupted(f: Fixture) -> None:
    print("\n=== K. a manifest left in 'creating' is offered for cleanup on retry")
    root = f.features / "zz-test-six"
    root.mkdir(parents=True)
    (root / ".feature.json").write_text(
        json.dumps(
            {
                "version": 1,
                "feature": "zz-test-six",
                "status": "creating",
                "created_at": "",
                "updated_at": "",
                "branch_prefix": "feat/",
                "workspace": None,
                "worktrees": [],
            }
        )
    )
    f.run("new", fzf=["alpha"], inputs=["zz-test-six", "y", "y"])
    m = f.manifest("zz-test-six")
    check(
        m is not None and m["status"] == "ready" and len(m["worktrees"]) == 1,
        "retry cleaned up and created the feature",
    )
    f.track("zz-test-six")


def scenario_cli(f: Fixture) -> None:
    print("\n=== M. CLI: dry run, then create with --yes --json")
    f.cli("new", "--name", "zz-test-cli", "--repo", "alpha", "--repo", "delta-noremote", "--json")
    check(not (f.features / "zz-test-cli").exists(), "dry run created nothing")
    _, payload = f.cli(
        "new", "--name", "zz-test-cli", "--repo", "alpha", "--repo", "delta-noremote", "--yes", "--json"
    )
    check(payload is not None and payload.get("workspace_id"), "json result carries workspace_id")
    check(
        payload is not None
        and sorted(wt["folder"] for wt in payload["worktrees"]) == ["alpha", "delta-noremote"],
        "json lists both worktrees",
    )
    ws = f.track("zz-test-cli")
    check(ws is not None and payload["workspace_id"] == ws, "workspace really exists")
    focused = [w for w in herdr("workspace", "list")["workspaces"] if w["focused"]]
    check(not focused or focused[0]["label"] != "zz-test-cli", "new did not steal focus")
    _, listing = f.cli("list", "--json")
    mine = [item for item in (listing or []) if item["feature"] == "zz-test-cli"]
    check(
        bool(mine) and mine[0]["status"] == "open" and mine[0]["workspace_id"] == ws,
        "list reports it open with the right workspace",
    )

    print("\n=== N. CLI: add needs --suffix for a repeated repo; unknown repo is an error")
    f.cli("add", "--feature", "zz-test-cli", "--repo", "alpha", "--yes", expect_rc=1)
    f.cli("add", "--feature", "zz-test-cli", "--repo", "nope", "--yes", expect_rc=1)
    f.cli(
        "add",
        "--feature",
        "zz-test-cli",
        "--repo",
        "alpha",
        "--suffix",
        "alpha=api",
        "--repo",
        "beta",
        "--yes",
    )
    m = f.manifest("zz-test-cli")
    check(
        sorted(wt["folder"] for wt in m["worktrees"]) == ["alpha", "alpha@api", "beta", "delta-noremote"],
        "add created alpha@api and beta",
    )

    print("\n=== O. CLI: drop refuses dirty worktree without --force")
    (f.features / "zz-test-cli" / "beta" / "wip.txt").write_text("x")
    f.cli("drop", "--feature", "zz-test-cli", "--worktree", "beta", "--yes", expect_rc=1)
    check((f.features / "zz-test-cli" / "beta").exists(), "beta still there")
    f.cli("drop", "--feature", "zz-test-cli", "--worktree", "beta", "--yes", "--force")
    check(not (f.features / "zz-test-cli" / "beta").exists(), "beta dropped with --force")

    print("\n=== P. CLI: open recreates a workspace after a manual close")
    herdr("workspace", "close", ws)
    _, payload = f.cli("open", "--feature", "zz-test-cli", "--json")
    check(payload is not None and payload.get("created") is True, "open created a workspace")
    f.track("zz-test-cli")
    _, payload = f.cli("open", "--feature", "zz-test-cli", "--json")
    check(payload is not None and payload.get("created") is False, "second open reused it")

    print("\n=== Q. CLI: fetch failure aborts by default, continues on request")
    beta = f.repos / "beta"
    good_url = git(beta, "remote", "get-url", "origin")
    git(beta, "remote", "set-url", "origin", str(f.base / "missing.git"))
    f.cli("new", "--name", "zz-test-cli2", "--repo", "beta", "--yes", expect_rc=1)
    check(not (f.features / "zz-test-cli2").exists(), "abort left nothing")
    f.cli(
        "new",
        "--name",
        "zz-test-cli2",
        "--repo",
        "beta",
        "--yes",
        "--on-fetch-failure",
        "continue",
        "--no-workspace",
    )
    check(f.manifest("zz-test-cli2") is not None, "continue created the feature")
    check(workspace_by_label("zz-test-cli2") is None, "--no-workspace made no workspace")
    git(beta, "remote", "set-url", "origin", good_url)

    print("\n=== R. CLI: remove with --force --delete-branches")
    f.cli("remove", "--feature", "zz-test-cli", "--yes", expect_rc=1)  # alpha@api etc. never pushed
    _, payload = f.cli(
        "remove", "--feature", "zz-test-cli", "--yes", "--force", "--delete-branches", "--json"
    )
    check(not (f.features / "zz-test-cli").exists(), "feature folder gone")
    check(workspace_by_label("zz-test-cli") is None, "workspace closed")
    check(
        git(f.repos / "alpha", "rev-parse", "--verify", "--quiet", "feat/zz-test-cli", check_rc=False) == "",
        "alpha branch deleted",
    )
    check(
        payload is not None and len(payload.get("deleted_branches", [])) == 3,
        "three branches reported deleted",
    )
    f.cli("remove", "--feature", "zz-test-cli2", "--yes", "--force")
    check(not (f.features / "zz-test-cli2").exists(), "cli2 removed")


def scenario_nested(f: Fixture) -> None:
    print("\n=== S. workspaces = both: feature workspace plus nested per-repository workspaces")
    plain_config = f.config
    f.config = f.config_both
    try:
        root = f.features / "zz-test-nested"
        _, payload = f.cli(
            "new", "--name", "zz-test-nested", "--repo", "alpha", "--repo", "beta", "--yes", "--json"
        )
        ws = f.track("zz-test-nested")
        check(ws is not None and payload["workspace_id"] == ws, "feature workspace created")
        nested = nested_workspaces(root)
        for entry in nested.values():
            f.track_id(entry["workspace_id"])
        f.track_fixture_repo_workspaces()
        check(
            sorted(nested) == ["alpha", "beta"], f"one nested worktree workspace per repo: {sorted(nested)}"
        )
        check(
            all(entry["label"] == "zz-test-nested" for entry in nested.values()),
            "nested workspaces are labelled with the feature name",
        )
        by_folder = {wt["folder"]: wt.get("workspace_id") for wt in payload["worktrees"]}
        check(
            by_folder == {name: entry["workspace_id"] for name, entry in nested.items()},
            f"json reports the nested workspace per worktree: {by_folder}",
        )
        parents = [
            w
            for w in herdr("workspace", "list")["workspaces"]
            if (w.get("worktree") or {}).get("is_linked_worktree") is False
            and Path(w["worktree"]["repo_root"]).resolve()
            in {(f.repos / "alpha").resolve(), (f.repos / "beta").resolve()}
        ]
        check(len(parents) == 2, f"Herdr opened the two repository parents: {[p['label'] for p in parents]}")
        m = f.manifest("zz-test-nested")
        check(m["workspace"]["id"] == ws, "manifest hint is the feature workspace, not a nested one")
        _, listing = f.cli("list", "--json", "--no-states")
        mine = [item for item in listing if item["feature"] == "zz-test-nested"][0]
        check(mine["workspace_id"] == ws and mine["status"] == "open", "list maps the feature workspace")
        check(
            {wt["folder"]: wt["workspace_id"] for wt in mine["worktrees"]} == by_folder,
            "list maps each nested workspace",
        )

        print("\n=== S2. add on an open feature opens a nested workspace for the new entry only")
        _, payload = f.cli(
            "add", "--feature", "zz-test-nested", "--repo", "delta-noremote", "--yes", "--json"
        )
        nested = nested_workspaces(root)
        for entry in nested.values():
            f.track_id(entry["workspace_id"])
        f.track_fixture_repo_workspaces()
        check(sorted(nested) == ["alpha", "beta", "delta-noremote"], f"nested now {sorted(nested)}")
        check(payload["workspace_id"] == ws, "feature workspace unchanged after add")

        print("\n=== S3. open re-creates only what is missing")
        herdr("workspace", "close", nested["beta"]["workspace_id"])
        _, payload = f.cli("open", "--feature", "zz-test-nested", "--json")
        check(payload["created"] is True and payload["workspace_id"] == ws, "open kept the feature workspace")
        nested = nested_workspaces(root)
        for entry in nested.values():
            f.track_id(entry["workspace_id"])
        check("beta" in nested, "open reopened the closed nested workspace")
        _, payload = f.cli("open", "--feature", "zz-test-nested", "--json")
        check(payload["created"] is False, "second open created nothing")
        count = len([w for w in herdr("workspace", "list")["workspaces"] if w["label"] == "zz-test-nested"])
        check(count == 4, f"exactly one feature workspace plus three nested ({count})")

        print("\n=== S4. drop closes the nested workspace; remove closes everything")
        f.cli("drop", "--feature", "zz-test-nested", "--worktree", "delta-noremote", "--yes", "--force")
        check("delta-noremote" not in nested_workspaces(root), "dropped entry's workspace closed")
        _, payload = f.cli("remove", "--feature", "zz-test-nested", "--yes", "--force", "--json")
        check(payload["closed_workspace"] == ws, "remove closed the feature workspace")
        check(sorted(payload["closed_repo_workspaces"]) == ["alpha", "beta"], "remove reported nested closes")
        check(nested_workspaces(root) == {}, "no nested workspaces remain")
        check(workspace_by_label("zz-test-nested") is None, "no zz-test-nested workspace remains")

        print("\n=== S5. popup: new, open and remove with workspaces = both")
        root2 = f.features / "zz-test-nested2"
        f.run("new", fzf=["alpha"], inputs=["zz-test-nested2", "y"])
        ws2 = f.track("zz-test-nested2")
        nested = nested_workspaces(root2)
        for entry in nested.values():
            f.track_id(entry["workspace_id"])
        check(
            ws2 is not None and sorted(nested) == ["alpha"], "popup new opened feature and nested workspace"
        )
        focused = [w for w in herdr("workspace", "list")["workspaces"] if w["focused"]]
        check(bool(focused) and focused[0]["workspace_id"] == ws2, "popup new focused the feature workspace")
        herdr("workspace", "close", nested["alpha"]["workspace_id"])
        f.run("open", fzf=["zz-test-nested2"], inputs=[])
        nested = nested_workspaces(root2)
        for entry in nested.values():
            f.track_id(entry["workspace_id"])
        check("alpha" in nested, "popup open reopened the nested workspace")
        f.run("remove", fzf=["zz-test-nested2"], inputs=["zz-test-nested2", "n"])
        check(not root2.exists(), "popup remove deleted the feature")
        check(
            nested_workspaces(root2) == {} and workspace_by_label("zz-test-nested2") is None,
            "popup remove closed all",
        )
    finally:
        f.config = plain_config


def scenario_cleanup_rest(f: Fixture) -> None:
    print("\n=== L. remove the remaining test features")
    for name in ("zz-test-two", "zz-test-three", "zz-test-four", "zz-test-six"):
        if f.manifest(name) is None:
            continue
        # zz-test-two is clean (tracking branch, nothing unpushed): plain y/N confirm.
        answers = ["y", "n"] if name == "zz-test-two" else [name, "n"]
        f.run("remove", fzf=[name], inputs=answers)
        check(not (f.features / name).exists(), f"{name} removed")
        check(workspace_by_label(name) is None, f"{name} workspace closed")
    check(
        git(f.repos / "beta", "rev-parse", "--verify", "--quiet", "feat/zz-test-two", check_rc=False) != "",
        "reused/tracking branch kept when declining deletion",
    )


def main() -> int:
    if os.environ.get("HERDR_ENV") != "1":
        print("run inside a Herdr session (HERDR_ENV=1)")
        return 2
    f = Fixture()
    print(f"fixture at {f.base}")
    try:
        for scenario in (
            scenario_new,
            scenario_add,
            scenario_remote_only,
            scenario_checked_out_elsewhere,
            scenario_fetch_failure,
            scenario_collision,
            scenario_drop,
            scenario_close_and_open,
            scenario_remove,
            scenario_rollback,
            scenario_interrupted,
            scenario_cli,
            scenario_nested,
            scenario_cleanup_rest,
        ):
            try:
                scenario(f)
            except Exception as error:  # keep going so cleanup still happens
                global failed
                failed += 1
                print(f"  FAIL {scenario.__name__} raised: {error!r}")
    finally:
        leftovers = [
            ws for ws in herdr("workspace", "list")["workspaces"] if ws["label"].startswith("zz-test-")
        ]
        for ws in leftovers:
            if ws["workspace_id"] in f.created_workspaces:
                sh(HERDR, "workspace", "close", ws["workspace_id"], check_rc=False)
        f.cleanup()
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
