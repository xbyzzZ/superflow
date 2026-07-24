#!/usr/bin/env python3
"""Manage Git project-level provider selections for Superflow."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any


BROWSER_PROVIDERS = ("codex-browser", "chrome-mcp", "custom")
UI_PROVIDERS = ("penpot-mcp", "codex-figma", "custom")
CONFIG_GIT_PATH = "info/superflow.json"


class ProjectConfigError(RuntimeError):
    """The project provider configuration is missing, corrupt, or invalid."""


def _git(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(project), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def config_path(project: Path) -> Path:
    """Return the project configuration path shared by all worktrees."""
    result = _git(project, "rev-parse", "--git-path", CONFIG_GIT_PATH)
    if result.returncode != 0 or not result.stdout.strip():
        raise ProjectConfigError("Unable to resolve the Superflow project configuration path")
    path = Path(result.stdout.strip())
    if not path.is_absolute():
        path = project / path
    return path.absolute()


def _selection(provider: str, details: str | None, allowed: tuple[str, ...], label: str) -> dict[str, Any]:
    provider = provider.strip()
    normalized_details = details.strip() if isinstance(details, str) and details.strip() else None
    if provider not in allowed:
        raise ProjectConfigError(f"Invalid {label} provider: {provider}")
    if provider == "custom" and normalized_details is None:
        raise ProjectConfigError(f"A custom {label} provider requires tool and invocation details")
    if provider != "custom" and normalized_details is not None:
        raise ProjectConfigError(f"A built-in {label} provider must not include custom details")
    return {"provider": provider, "details": normalized_details}


def build_config(
    browser_provider: str,
    ui_provider: str,
    browser_custom: str | None = None,
    ui_custom: str | None = None,
) -> dict[str, Any]:
    """Build and validate a project provider configuration."""
    return {
        "schema_version": 1,
        "browser": _selection(
            browser_provider, browser_custom, BROWSER_PROVIDERS, "browser"
        ),
        "ui_prototype": _selection(
            ui_provider, ui_custom, UI_PROVIDERS, "UI prototype"
        ),
    }


def validate_config(value: Any) -> dict[str, Any]:
    """Strictly validate the configuration and return a normalized value."""
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "browser",
        "ui_prototype",
    }:
        raise ProjectConfigError("Invalid Superflow project configuration structure")
    if value.get("schema_version") != 1:
        raise ProjectConfigError("Unsupported Superflow project configuration version")
    browser = value.get("browser")
    ui = value.get("ui_prototype")
    if not isinstance(browser, dict) or set(browser) != {"provider", "details"}:
        raise ProjectConfigError("Invalid browser configuration structure")
    if not isinstance(ui, dict) or set(ui) != {"provider", "details"}:
        raise ProjectConfigError("Invalid UI prototype configuration structure")
    return build_config(
        browser.get("provider", ""),
        ui.get("provider", ""),
        browser.get("details"),
        ui.get("details"),
    )


def read_config(project: Path, required: bool = True) -> dict[str, Any] | None:
    """Read the shared configuration for the current Git project."""
    path = config_path(project)
    if not path.exists():
        if required:
            raise ProjectConfigError("The Superflow project has not selected browser and UI prototype providers")
        return None
    if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
        raise ProjectConfigError("Reading an unsafe Superflow project configuration path is refused")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectConfigError("The Superflow project configuration is corrupt") from exc
    return validate_config(value)


def write_config(project: Path, value: dict[str, Any]) -> Path:
    """Atomically write a validated project provider configuration."""
    normalized = validate_config(value)
    path = config_path(project)
    if path.is_symlink() or path.parent.is_symlink():
        raise ProjectConfigError("Writing to an unsafe Superflow project configuration path is refused")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(normalized, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path
