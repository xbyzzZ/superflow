#!/usr/bin/env python3
"""Perform restricted, local-only Git workspace operations."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


class GitSafetyError(RuntimeError):
    """A Git operation failed a safety check."""


def _run(directory: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(directory), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _checked(directory: Path, *args: str) -> str:
    result = _run(directory, *args)
    if result.returncode != 0:
        raise GitSafetyError(result.stderr.strip() or f"Git command failed: {' '.join(args)}")
    return result.stdout.strip()


def git_root(project: Path) -> Path:
    output = _checked(project, "rev-parse", "--show-toplevel")
    root = Path(output).resolve()
    if not root.is_dir():
        raise GitSafetyError("Invalid Git root")
    return root


def _assert_clean(root: Path) -> None:
    if _checked(
        root,
        "status",
        "--porcelain",
        "--untracked-files=normal",
        "--",
        ".",
        ":(exclude).worktrees/superflow",
    ):
        raise GitSafetyError("The base worktree is dirty; automatic handling is refused")


def preflight(project: Path) -> dict[str, Any]:
    root = git_root(project)
    _assert_clean(root)
    head = _checked(root, "rev-parse", "HEAD")
    branch = _checked(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    return {"ok": True, "git_root": str(root), "head": head, "branch": branch, "clean": True}


def _safe_worktree_path(root: Path, run_id: str, path: Path | None) -> Path:
    if not RUN_ID_RE.fullmatch(run_id) or run_id in {".", ".."}:
        raise GitSafetyError("Invalid run-id format")
    base = root / ".worktrees" / "superflow"
    target = (path if path is not None else base / run_id).absolute()
    try:
        target.relative_to(base.absolute())
    except ValueError as exc:
        raise GitSafetyError("The worktree path must be under .worktrees/superflow") from exc
    current = root
    relative = target.relative_to(root.absolute())
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise GitSafetyError(f"Symlink path is refused: {current}")
    if target.exists():
        raise GitSafetyError("The target worktree path already exists")
    return target


def create_worktree(
    project: Path, run_id: str, base_ref: str = "HEAD", path: Path | None = None
) -> dict[str, Any]:
    root = git_root(project)
    _assert_clean(root)
    _assert_no_active_hooks(root)
    from workflow_state import RUN_ID_RE as SUPERFLOW_RUN_ID_RE, WorkflowState

    store = None
    if SUPERFLOW_RUN_ID_RE.fullmatch(run_id):
        store = WorkflowState(root, run_id)
        if not store.state_path.is_file():
            raise GitSafetyError("A Superflow run must exist before its worktree is created")
    target = _safe_worktree_path(root, run_id, path)
    _checked(root, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
    branch = f"superflow/{run_id}"
    exists = _run(root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}")
    if exists.returncode == 0:
        raise GitSafetyError("The local work branch already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    _checked(root, "worktree", "add", "-b", branch, str(target), base_ref)
    if store is not None:
        try:
            store.register_worktree(target, branch, base_ref)
        except (OSError, RuntimeError) as exc:
            _run(root, "worktree", "remove", "--force", str(target))
            _run(root, "branch", "-D", branch)
            raise GitSafetyError(
                f"The worktree could not be registered and was rolled back: {exc}"
            ) from exc
    return {
        "ok": True,
        "git_root": str(root),
        "worktree": str(target),
        "branch": branch,
        "base_ref": base_ref,
    }


def status(project: Path) -> dict[str, Any]:
    root = git_root(project)
    porcelain = _checked(project, "status", "--porcelain", "--branch")
    head = _checked(project, "rev-parse", "HEAD")
    return {"ok": True, "git_root": str(root), "head": head, "status": porcelain.splitlines()}


def snapshot(project: Path) -> dict[str, Any]:
    root = git_root(project)
    refs: dict[str, str] = {}
    output = _checked(project, "for-each-ref", "--format=%(refname) %(objectname)")
    for line in output.splitlines():
        name, sha = line.split(" ", 1)
        refs[name] = sha
    index_entries = _checked(project, "ls-files", "--stage")
    index_flags = _checked(project, "ls-files", "-v")
    worktree_status = _checked(project, "status", "--porcelain=v1", "--untracked-files=all")
    digest = lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest()
    return {
        "head": _checked(project, "rev-parse", "HEAD^{commit}"),
        "refs": refs,
        "index_entries": digest(index_entries),
        "index_flags": digest(index_flags),
        "status": worktree_status.splitlines(),
    }


def _safe_relative_path(value: str) -> str:
    path = Path(value)
    if (
        not value
        or value == "."
        or value.startswith(":")
        or path.is_absolute()
        or ".." in path.parts
        or (path.parts and path.parts[0] == ".git")
    ):
        raise GitSafetyError(f"Unsafe commit path: {value}")
    return path.as_posix()


def _assert_no_active_hooks(root: Path) -> None:
    raw = _checked(root, "rev-parse", "--git-path", "hooks")
    hooks = Path(raw)
    if not hooks.is_absolute():
        hooks = root / hooks
    if not hooks.exists():
        return
    if hooks.is_symlink() or not hooks.is_dir():
        raise GitSafetyError("The Git hooks path is unsafe")
    active = sorted(
        item.name
        for item in hooks.iterdir()
        if item.is_file() and os.access(item, os.X_OK) and not item.name.endswith(".sample")
    )
    if active:
        raise GitSafetyError("Executable Git hooks detected; automatic Git writes are blocked: " + ", ".join(active))


def _is_authorized_staged_path(path: str, allowed: list[str]) -> bool:
    return any(path == item or path.startswith(item.rstrip("/") + "/") for item in allowed)


def commit(worktree: Path, message: str, paths: list[str]) -> dict[str, Any]:
    root = git_root(worktree)
    if root != worktree.resolve():
        raise GitSafetyError("commit must explicitly target the worktree root")
    if not message.strip() or not paths:
        raise GitSafetyError("commit requires a message and at least one path")
    safe_paths = [_safe_relative_path(path) for path in paths]
    _assert_no_active_hooks(root)
    if _checked(root, "diff", "--cached", "--name-only"):
        raise GitSafetyError("The index already contains staged changes; mixed commits are refused")
    index_raw = _checked(root, "rev-parse", "--git-path", "index")
    index_path = Path(index_raw)
    if not index_path.is_absolute():
        index_path = root / index_path
    index_before = index_path.read_bytes()
    try:
        _checked(root, "--literal-pathspecs", "add", "--", *safe_paths)
        staged = _checked(root, "diff", "--cached", "--name-only")
        if not staged:
            raise GitSafetyError("The selected paths contain no committable changes")
        if any(not _is_authorized_staged_path(path, safe_paths) for path in staged.splitlines()):
            raise GitSafetyError("The actual staged paths exceed the authorized scope")
        _checked(root, "commit", "-m", message)
    except (GitSafetyError, OSError):
        try:
            with index_path.open("wb") as handle:
                handle.write(index_before)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise GitSafetyError(f"The commit failed and the index could not be restored: {exc}") from exc
        raise
    sha = _checked(root, "rev-parse", "HEAD")
    committed = _checked(root, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).splitlines()
    if any(not _is_authorized_staged_path(path, safe_paths) for path in committed):
        raise GitSafetyError("The actual committed paths exceed the authorized scope")
    return {"ok": True, "git_root": str(root), "sha": sha, "paths": committed}


def cherry_pick(project: Path, commit_sha: str) -> dict[str, Any]:
    root = git_root(project)
    _assert_clean(root)
    _assert_no_active_hooks(root)
    if not re.fullmatch(r"[0-9a-fA-F]{7,64}", commit_sha):
        raise GitSafetyError("Invalid commit SHA format")
    object_type = _checked(root, "cat-file", "-t", commit_sha)
    if object_type != "commit":
        raise GitSafetyError("The target object is not a local commit")
    _checked(root, "cherry-pick", commit_sha)
    return {"ok": True, "git_root": str(root), "head": _checked(root, "rev-parse", "HEAD")}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    pre = sub.add_parser("preflight")
    pre.add_argument("--project", type=Path, default=Path.cwd())
    create = sub.add_parser("create-worktree")
    create.add_argument("run_id")
    create.add_argument("--project", type=Path, default=Path.cwd())
    create.add_argument("--base-ref", default="HEAD")
    create.add_argument("--path", type=Path)
    show = sub.add_parser("status")
    show.add_argument("--project", type=Path, default=Path.cwd())
    snap = sub.add_parser("snapshot")
    snap.add_argument("--project", type=Path, default=Path.cwd())
    save = sub.add_parser("commit")
    save.add_argument("--worktree", type=Path, required=True)
    save.add_argument("--message", required=True)
    save.add_argument("paths", nargs="+")
    pick = sub.add_parser("cherry-pick")
    pick.add_argument("commit_sha")
    pick.add_argument("--project", type=Path, default=Path.cwd())
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "preflight":
            result = preflight(args.project)
        elif args.command == "create-worktree":
            result = create_worktree(args.project, args.run_id, args.base_ref, args.path)
        elif args.command == "status":
            result = status(args.project)
        elif args.command == "snapshot":
            result = snapshot(args.project)
        elif args.command == "commit":
            result = commit(args.worktree, args.message, args.paths)
        else:
            result = cherry_pick(args.project, args.commit_sha)
        code = 0
    except (GitSafetyError, OSError) as exc:
        result = {"ok": False, "status": "blocked", "error": str(exc)}
        code = 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
