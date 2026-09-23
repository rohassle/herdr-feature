"""The `.workthread.json` manifest: one per thread root, the source of truth."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

MANIFEST_NAME = ".workthread.json"
LEGACY_MANIFEST_NAME = ".feature.json"  # written by herdr-feature (versions 1)
VERSION = 2
STATUS_CREATING = "creating"
STATUS_READY = "ready"


class ManifestError(Exception):
    pass


def now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class Worktree:
    repo_name: str
    repo_path: str
    folder: str
    suffix: str | None
    branch: str
    branch_created: bool
    branch_source: str  # new | origin | local
    base_ref: str | None
    base_commit: str | None
    remote: str | None
    added_at: str = field(default_factory=now)
    pr: dict | None = None  # last GitHub pull request lookup, see prs.py

    @property
    def label(self) -> str:
        return self.folder


@dataclass
class Thread:
    name: str
    root: Path
    status: str = STATUS_READY
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)
    branch_prefix: str = ""
    workspace: dict | None = None
    worktrees: list[Worktree] = field(default_factory=list)
    version: int = VERSION
    error: str | None = None  # set for manifests that could not be read

    # --- derived -------------------------------------------------------------

    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST_NAME

    @property
    def readable(self) -> bool:
        return self.error is None

    @property
    def mutable(self) -> bool:
        return self.readable and self.version <= VERSION and self.status == STATUS_READY

    def path_of(self, worktree: Worktree) -> Path:
        return self.root / worktree.folder

    def worktrees_for(self, repo_path: Path) -> list[Worktree]:
        wanted = str(repo_path)
        return [wt for wt in self.worktrees if wt.repo_path == wanted]

    def folders(self) -> set[str]:
        return {wt.folder for wt in self.worktrees}

    def remember_workspace(self, workspace_id: str, label: str | None = None) -> None:
        self.workspace = {"id": workspace_id, "label": label or self.name, "seen_at": now()}

    # --- persistence ---------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "thread": self.name,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "branch_prefix": self.branch_prefix,
            "workspace": self.workspace,
            "worktrees": [asdict(wt) for wt in self.worktrees],
        }

    def save(self) -> None:
        if not self.readable:
            raise ManifestError(f"refusing to overwrite unreadable manifest {self.manifest_path}")
        if self.version > VERSION:
            raise ManifestError(
                f"{self.manifest_path} was written by a newer herdr-workthreads (version {self.version}); "
                "upgrade the plugin before changing this thread."
            )
        self.updated_at = now()
        self.root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), indent=2) + "\n"
        fd, tmp = tempfile.mkstemp(prefix=".thread.", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(payload)
            os.replace(tmp, self.manifest_path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def _migrate_1_to_2(data: dict) -> dict:
    """herdr-feature called the unit a feature; herdr-workthreads calls it a thread."""
    data = dict(data)
    if "thread" not in data and "feature" in data:
        data["thread"] = data.pop("feature")
    data["version"] = 2
    return data


MIGRATIONS: dict[int, callable] = {1: _migrate_1_to_2}


def _migrate(data: dict) -> dict:
    version = int(data.get("version", 0))
    while version < VERSION and version in MIGRATIONS:
        data = MIGRATIONS[version](data)
        version = int(data.get("version", version + 1))
    return data


REQUIRED_WORKTREE_KEYS = {"repo_name", "repo_path", "folder", "branch", "branch_created", "branch_source"}


def from_dict(data: dict, root: Path) -> Thread:
    raw_version = data.get("version")
    if not isinstance(raw_version, int):
        raise ManifestError("missing integer 'version'")
    data = _migrate(dict(data))
    version = data.get("version")
    name = data.get("thread")
    if not isinstance(name, str) or not name:
        raise ManifestError("missing 'thread' name")
    status = data.get("status", STATUS_READY)
    if status not in (STATUS_CREATING, STATUS_READY):
        raise ManifestError(f"unknown status {status!r}")
    worktrees = []
    for raw in data.get("worktrees", []):
        if not isinstance(raw, dict) or not REQUIRED_WORKTREE_KEYS <= raw.keys():
            raise ManifestError("malformed worktree entry")
        worktrees.append(
            Worktree(
                repo_name=raw["repo_name"],
                repo_path=raw["repo_path"],
                folder=raw["folder"],
                suffix=raw.get("suffix"),
                branch=raw["branch"],
                branch_created=bool(raw["branch_created"]),
                branch_source=raw["branch_source"],
                base_ref=raw.get("base_ref"),
                base_commit=raw.get("base_commit"),
                remote=raw.get("remote"),
                added_at=raw.get("added_at", ""),
                pr=raw["pr"] if isinstance(raw.get("pr"), dict) else None,
            )
        )
    workspace = data.get("workspace")
    if workspace is not None and not isinstance(workspace, dict):
        workspace = None
    return Thread(
        name=name,
        root=root,
        status=status,
        created_at=data.get("created_at", ""),
        updated_at=data.get("updated_at", ""),
        branch_prefix=data.get("branch_prefix", ""),
        workspace=workspace,
        worktrees=worktrees,
        version=version,
    )


def manifest_file(root: Path) -> Path | None:
    """The manifest in `root`: the current name, else the legacy `.feature.json`."""
    for name in (MANIFEST_NAME, LEGACY_MANIFEST_NAME):
        if (root / name).exists():
            return root / name
    return None


def load(root: Path) -> Thread:
    path = manifest_file(root) or root / MANIFEST_NAME
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        raise ManifestError(f"{path} does not exist") from None
    except (OSError, json.JSONDecodeError) as error:
        raise ManifestError(f"{path}: {error}") from error
    if not isinstance(data, dict):
        raise ManifestError(f"{path}: not a JSON object")
    thread = from_dict(data, root)
    if path.name == LEGACY_MANIFEST_NAME and thread.mutable:
        # Rewrite under the new name once; the legacy file goes away.
        thread.save()
        try:
            path.unlink()
        except OSError:
            pass
    return thread


def discover(threads_directory: Path) -> list[Thread]:
    """Every thread folder, readable or not (unreadable ones carry `error`)."""
    found: list[Thread] = []
    if not threads_directory.is_dir():
        return found
    for child in sorted(threads_directory.iterdir()):
        if not child.is_dir() or manifest_file(child) is None:
            continue
        try:
            found.append(load(child))
        except ManifestError as error:
            found.append(Thread(name=child.name, root=child, error=str(error)))
    return found


def branch_claims(threads: list[Thread]) -> dict[tuple[str, str], str]:
    """(repo_path, branch) -> thread name, across every readable manifest."""
    claims: dict[tuple[str, str], str] = {}
    for thread in threads:
        if not thread.readable:
            continue
        for wt in thread.worktrees:
            claims.setdefault((wt.repo_path, wt.branch), thread.name)
    return claims


def find(threads: list[Thread], name: str) -> Thread | None:
    for thread in threads:
        if thread.name.casefold() == name.casefold():
            return thread
    return None
