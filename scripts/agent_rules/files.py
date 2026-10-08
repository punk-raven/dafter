from __future__ import annotations

import subprocess
from pathlib import Path

PACKAGE_DIRECTORY = Path(__file__).resolve().parent


def git_lines(root: Path, *arguments: str) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments], capture_output=True, text=True, check=True
    )
    return [line for line in completed.stdout.splitlines() if line]


def repository_root(start: Path = PACKAGE_DIRECTORY) -> Path:
    return Path(git_lines(start, "rev-parse", "--show-toplevel")[0])


def is_regular_file(root: Path, relative: str) -> bool:
    path = root / relative
    return path.is_file() and not path.is_symlink()


def repository_files(root: Path) -> list[str]:
    tracked = git_lines(root, "ls-files")
    untracked = git_lines(root, "ls-files", "--others", "--exclude-standard")
    return sorted(
        relative for relative in set(tracked + untracked) if is_regular_file(root, relative)
    )


def changed_files(root: Path) -> list[str]:
    modified = git_lines(root, "diff", "--name-only", "HEAD")
    untracked = git_lines(root, "ls-files", "--others", "--exclude-standard")
    return sorted(
        relative for relative in set(modified + untracked) if is_regular_file(root, relative)
    )


def is_ignored(root: Path, relative: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "-q", "--", relative], capture_output=True
    )
    return completed.returncode == 0


def relative_to_root(root: Path, file_path: str) -> str | None:
    path = Path(file_path)
    absolute = path if path.is_absolute() else root / path
    try:
        return absolute.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def read_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None
