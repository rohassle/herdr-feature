import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def make_repo(path: Path, *, branch: str = "main", origin: Path | None = None) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
    git(path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init")
    if origin is not None:
        subprocess.run(["git", "init", "-q", "--bare", "-b", branch, str(origin)], check=True)
        git(path, "remote", "add", "origin", str(origin))
        git(path, "push", "-q", "-u", "origin", branch)
        git(path, "remote", "set-head", "origin", "--auto")
    return path
