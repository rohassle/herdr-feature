"""The `.feature.json` manifest: one per feature root, the source of truth."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

MANIFEST_NAME = ".feature.json"
VERSION = 1
STATUS_CREATING = "creating"
STATUS_READY = "ready"


class ManifestError(Exception):
    pass


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class Worktree:
    repo_name: str
    repo_path: str
    folder: str
    suffix: str | None
    branch: str
    branch_created: bool
    branch_source: str          # new | origin | local
    base_ref: str | None
    base_commit: str | None
    remote: str | None
    added_at: str = field(default_factory=now)

    @property
    def label(self) -> str:
        return self.folder


@dataclass
class Feature:
    name: str
    root: Path
    status: str = STATUS_READY
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)
    branch_prefix: str = ""
    workspace: dict | None = None
    worktrees: list[Worktree] = field(default_factory=list)
    version: int = VERSION
    error: str | None = None        # set for manifests that could not be read

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
            "feature": self.name,
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
                f"{self.manifest_path} was written by a newer herdr-feature (version {self.version}); "
                "upgrade the plugin before changing this feature."
            )
        self.updated_at = now()
        self.root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), indent=2) + "\n"
        fd, tmp = tempfile.mkstemp(prefix=".feature.", suffix=".tmp", dir=self.root)
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


MIGRATIONS: dict[int, callable] = {}


def _migrate(data: dict) -> dict:
    version = int(data.get("version", 0))
    while version < VERSION and version in MIGRATIONS:
        data = MIGRATIONS[version](data)
        version = int(data.get("version", version + 1))
    return data


REQUIRED_WORKTREE_KEYS = {"repo_name", "repo_path", "folder", "branch", "branch_created", "branch_source"}


def from_dict(data: dict, root: Path) -> Feature:
    data = _migrate(dict(data))
    version = data.get("version")
    if not isinstance(version, int):
        raise ManifestError("missing integer 'version'")
    name = data.get("feature")
    if not isinstance(name, str) or not name:
        raise ManifestError("missing 'feature' name")
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
            )
        )
    workspace = data.get("workspace")
    if workspace is not None and not isinstance(workspace, dict):
        workspace = None
    return Feature(
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


def load(root: Path) -> Feature:
    path = root / MANIFEST_NAME
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        raise ManifestError(f"{path} does not exist")
    except (OSError, json.JSONDecodeError) as error:
        raise ManifestError(f"{path}: {error}")
    if not isinstance(data, dict):
        raise ManifestError(f"{path}: not a JSON object")
    return from_dict(data, root)


def discover(features_directory: Path) -> list[Feature]:
    """Every feature folder, readable or not (unreadable ones carry `error`)."""
    found: list[Feature] = []
    if not features_directory.is_dir():
        return found
    for child in sorted(features_directory.iterdir()):
        if not child.is_dir() or not (child / MANIFEST_NAME).exists():
            continue
        try:
            found.append(load(child))
        except ManifestError as error:
            found.append(Feature(name=child.name, root=child, error=str(error)))
    return found


def branch_claims(features: list[Feature]) -> dict[tuple[str, str], str]:
    """(repo_path, branch) -> feature name, across every readable manifest."""
    claims: dict[tuple[str, str], str] = {}
    for feature in features:
        if not feature.readable:
            continue
        for wt in feature.worktrees:
            claims.setdefault((wt.repo_path, wt.branch), feature.name)
    return claims


def find(features: list[Feature], name: str) -> Feature | None:
    for feature in features:
        if feature.name.casefold() == name.casefold():
            return feature
    return None
