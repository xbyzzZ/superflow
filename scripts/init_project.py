#!/usr/bin/env python3
"""Safely initialize project-local Superflow directories."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from project_config import (
    BROWSER_PROVIDERS,
    UI_PROVIDERS,
    ProjectConfigError,
    build_config,
    config_path,
    read_config,
    write_config,
)

EXCLUDE_LINES = (".codex/", ".worktrees/superflow/")
MANIFEST_NAME = ".superflow-managed.json"


class InitBlocked(RuntimeError):
    """Initialization is blocked by an unmet safety condition."""


def _git(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(project), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def find_git_root(project: Path) -> Path:
    result = _git(project, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        raise InitBlocked("The target directory is not in a Git repository")
    root = Path(result.stdout.strip()).resolve()
    if not root.is_dir():
        raise InitBlocked("Invalid Git root")
    return root


def _ensure_no_symlink(root: Path, target: Path) -> None:
    root = root.resolve()
    try:
        relative = target.absolute().relative_to(root)
    except ValueError as exc:
        raise InitBlocked(f"The target path escapes the Git root: {target}") from exc
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise InitBlocked(f"Symlink path is refused: {current}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _template_files(skill_root: Path) -> list[Path]:
    directory = skill_root / "assets" / "agent-templates"
    if directory.is_symlink() or not directory.is_dir():
        raise InitBlocked("The agent-templates directory is missing or unsafe")
    files = sorted(path for path in directory.iterdir() if path.is_file() and not path.is_symlink())
    if len(files) != 6 or any(path.name.startswith(".") for path in files):
        raise InitBlocked("agent-templates must contain exactly six regular template files")
    return files


def _read_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "templates": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InitBlocked("The managed-template manifest is corrupt") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or not isinstance(value.get("templates"), dict)
    ):
        raise InitBlocked("The managed-template manifest has an invalid structure")
    return value


def _tracked_codex_files(root: Path) -> list[str]:
    result = _git(root, "ls-files", "--", ".codex")
    if result.returncode != 0:
        raise InitBlocked("Unable to inspect tracked .codex files")
    return [line for line in result.stdout.splitlines() if line.strip()]


def _git_info_exclude(root: Path) -> Path:
    result = _git(root, "rev-parse", "--git-path", "info/exclude")
    if result.returncode != 0 or not result.stdout.strip():
        raise InitBlocked("Unable to resolve Git info/exclude")
    path = Path(result.stdout.strip())
    if not path.is_absolute():
        path = root / path
    return path.absolute()


def _resolve_tool_config(
    root: Path,
    browser_provider: str | None,
    ui_provider: str | None,
    browser_custom: str | None,
    ui_custom: str | None,
    reconfigure: bool,
) -> tuple[dict[str, Any], str]:
    existing = read_config(root, required=False)
    supplied = any(
        value is not None
        for value in (browser_provider, ui_provider, browser_custom, ui_custom)
    )
    if not supplied:
        if existing is None:
            raise InitBlocked(
                "Ask the user to select browser and UI prototype providers before first initialization"
            )
        if reconfigure:
            raise InitBlocked("--reconfigure requires both provider selections")
        return existing, "unchanged"
    if browser_provider is None or ui_provider is None:
        raise InitBlocked("Both browser and UI prototype provider selections are required")
    try:
        desired = build_config(
            browser_provider,
            ui_provider,
            browser_custom,
            ui_custom,
        )
    except ProjectConfigError as exc:
        raise InitBlocked(str(exc)) from exc
    if existing is None:
        return desired, "installed"
    if desired == existing:
        return existing, "unchanged"
    if not reconfigure:
        raise InitBlocked("Project provider selections already exist; --reconfigure requires explicit user approval")
    return desired, "reconfigured"


def initialize(
    project: Path,
    skill_root: Path,
    browser_provider: str | None = None,
    ui_provider: str | None = None,
    browser_custom: str | None = None,
    ui_custom: str | None = None,
    reconfigure: bool = False,
) -> dict[str, Any]:
    """Initialize a project and return a directly serializable result."""
    root = find_git_root(project)
    try:
        tool_config, config_action = _resolve_tool_config(
            root,
            browser_provider,
            ui_provider,
            browser_custom,
            ui_custom,
            reconfigure,
        )
        tool_config_path = config_path(root)
    except ProjectConfigError as exc:
        raise InitBlocked(str(exc)) from exc
    codex = root / ".codex"
    agents = codex / "agents"
    workflows = codex / "workflows"
    manifest_path = agents / MANIFEST_NAME
    exclude_path = _git_info_exclude(root)

    for path in (codex, agents, workflows, manifest_path, exclude_path):
        _ensure_no_symlink(root, path) if path.is_relative_to(root) else None
    if exclude_path.is_symlink():
        raise InitBlocked("Writing through a symlinked Git info/exclude is refused")
    tracked = _tracked_codex_files(root)
    if tracked:
        raise InitBlocked(".codex is tracked by Git; initialization is refused")

    templates = _template_files(skill_root.resolve())
    manifest = _read_manifest(manifest_path)
    managed = manifest["templates"]
    decisions: list[tuple[str, Path, Path, str]] = []
    conflicts: list[str] = []
    for source in templates:
        destination = agents / source.name
        _ensure_no_symlink(root, destination)
        source_hash = _sha256(source)
        record = managed.get(source.name)
        if destination.exists():
            if not destination.is_file():
                raise InitBlocked(f"The template target is not a regular file: {destination}")
            current_hash = _sha256(destination)
            if not isinstance(record, dict) or current_hash != record.get("installed_sha256"):
                conflicts.append(source.name)
                decisions.append(("preserved", source, destination, source_hash))
            elif current_hash == source_hash:
                decisions.append(("unchanged", source, destination, source_hash))
            else:
                decisions.append(("upgraded", source, destination, source_hash))
        else:
            decisions.append(("installed", source, destination, source_hash))

    agents.mkdir(parents=True, exist_ok=True)
    workflows.mkdir(parents=True, exist_ok=True)
    actions: list[dict[str, str]] = []
    for action, source, destination, source_hash in decisions:
        if action in {"installed", "upgraded"}:
            data = source.read_bytes()
            descriptor, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=agents)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, destination)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            managed[source.name] = {"installed_sha256": source_hash}
        elif action == "unchanged":
            managed[source.name] = {"installed_sha256": source_hash}
        actions.append({"template": source.name, "action": action})
    _atomic_json(manifest_path, manifest)
    if config_action in {"installed", "reconfigured"}:
        try:
            write_config(root, tool_config)
        except ProjectConfigError as exc:
            raise InitBlocked(str(exc)) from exc

    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
    additions = [line for line in EXCLUDE_LINES if line not in existing.splitlines()]
    if additions:
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        with exclude_path.open("a", encoding="utf-8") as handle:
            handle.write(prefix + "\n".join(additions) + "\n")

    return {
        "ok": True,
        "status": "initialized",
        "git_root": str(root),
        "actions": actions,
        "conflicts": conflicts,
        "exclude_added": additions,
        "project_config": {
            "path": str(tool_config_path),
            "action": config_action,
            **tool_config,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument(
        "--skill-root", type=Path, default=Path(__file__).resolve().parent.parent
    )
    parser.add_argument("--browser-provider", choices=BROWSER_PROVIDERS)
    parser.add_argument("--browser-custom")
    parser.add_argument("--ui-provider", choices=UI_PROVIDERS)
    parser.add_argument("--ui-custom")
    parser.add_argument("--reconfigure", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = initialize(
            args.project,
            args.skill_root,
            args.browser_provider,
            args.ui_provider,
            args.browser_custom,
            args.ui_custom,
            args.reconfigure,
        )
        code = 0
    except InitBlocked as exc:
        result = {"ok": False, "status": "blocked", "error": str(exc)}
        code = 2
    except OSError as exc:
        result = {"ok": False, "status": "error", "error": str(exc)}
        code = 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
