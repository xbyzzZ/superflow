#!/usr/bin/env python3
"""Validate Superflow agent results, path authority, Git snapshots, and tool evidence."""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import shlex
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from schema_validation import SchemaValidationError, validate
from project_config import ProjectConfigError, read_config
from role_memory import MemoryError as RoleMemoryError, validate_request


ROLES = {
    "architect",
    "ui-designer",
    "frontend-developer",
    "backend-developer",
    "tester",
    "code-reviewer",
}
STATUSES = {"success", "partial", "failed", "blocked"}
FORBIDDEN_PREFIXES = (".codex/agents", ".codex/workflows", ".git")
REQUIRED_FIELDS: dict[str, type] = {
    "role": str,
    "taskId": str,
    "status": str,
    "summary": str,
    "filesChanged": list,
    "commandsRun": list,
    "verification": dict,
    "findings": list,
    "evidence": list,
    "workflowUpdateRequest": dict,
    "concerns": list,
}
GIT_TOKEN = re.compile(
    r"(?:^|[\s;&|\"',=])(?:[^\s;&|\"',=]*/)?git(?:\s|$|[\"',;])", re.IGNORECASE
)
READ_ONLY_GIT = {"cat-file", "diff", "grep", "log", "ls-files", "rev-parse", "show", "status"}
NEGATIVE_EVIDENCE = re.compile(
    r"(?:failed|failure|unavailable|denied|permission|error|not created|not saved)",
    re.IGNORECASE,
)
RESULT_SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "assets" / "schemas" / "agent-result.schema.json").read_text(
        encoding="utf-8"
    )
)


def _safe_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        return None
    result = path.as_posix()
    while result.startswith("./"):
        result = result[2:]
    return result


def _matches(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def _is_test_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    name = parts[-1] if parts else ""
    return (
        any(part in {"test", "tests", "__tests__", "fixtures", "snapshots"} for part in parts)
        or name.startswith("test_")
        or name.endswith(
            (
                "_test.py",
                ".test.js",
                ".spec.js",
                ".test.ts",
                ".spec.ts",
                ".test.tsx",
                ".spec.tsx",
                ".snap",
            )
        )
    )


def _has_tool_evidence(
    result: dict[str, Any],
    tools: str | set[str],
    require_success: bool,
    expected_provider: str | None = None,
) -> bool:
    accepted = {tools} if isinstance(tools, str) else tools
    return any(
        isinstance(item, dict)
        and item.get("type") in accepted
        and item.get("status") in ({"success"} if require_success else {"success", "failure"})
        and (
            expected_provider is None
            or item.get("provider") == expected_provider
        )
        for item in result.get("evidence", [])
    )


def _git_write_or_indirect(command: str) -> bool:
    if GIT_TOKEN.search(command) is None:
        return False
    if re.search(r"(?:^|\s)(?:ba|z|fi|da)?sh\s+-[^\s]*c\b|\$\(", command):
        return True
    try:
        tokens = shlex.split(command)
    except ValueError:
        return True
    git_index = next(
        (index for index, token in enumerate(tokens) if Path(token).name.lower() == "git"),
        None,
    )
    if git_index is None:
        return True
    index = git_index + 1
    options_with_value = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
    while index < len(tokens):
        token = tokens[index]
        if token in options_with_value:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return token not in READ_ONLY_GIT
    return True


def check_policy(
    result: Any,
    allowed_paths: list[str],
    before: Any,
    after: Any,
    expected_role: str | None = None,
    expected_task: str | None = None,
    expected_candidate: str | None = None,
    ui: bool = False,
    browser: bool = False,
    code: bool = False,
    expected_ui_provider: str | None = None,
    expected_browser_provider: str | None = None,
) -> dict[str, Any]:
    """Return a fail-closed policy-check result."""
    violations: list[dict[str, str]] = []

    def add(code_value: str, message: str) -> None:
        violations.append({"code": code_value, "message": message})

    if not isinstance(result, dict):
        return {
            "ok": False,
            "violations": [{"code": "structure", "message": "The result must be a JSON object"}],
        }
    try:
        validate(result, RESULT_SCHEMA)
    except SchemaValidationError as exc:
        add("schema", f"The result does not conform to the agent-result schema: {exc}")
    for field, expected_type in REQUIRED_FIELDS.items():
        if not isinstance(result.get(field), expected_type):
            add("structure", f"Field {field} is missing or has the wrong type")

    role = result.get("role")
    if isinstance(role, str) and role not in ROLES:
        add("role", "Unknown agent role")
    if expected_role is not None and role != expected_role:
        add("identity", "The agent role does not match the assignment")
    if expected_role is None:
        add("identity_context", "The policy check requires the role assigned by the primary agent")
    if expected_task is not None and result.get("taskId") != expected_task:
        add("identity", "The agent taskId does not match the assignment")
    if expected_task is None:
        add("identity_context", "The policy check requires the taskId assigned by the primary agent")
    if result.get("status") not in STATUSES:
        add("status", "Invalid agent status")

    for index, request in enumerate(result.get("memoryWriteRequests", [])):
        try:
            validate_request(request)
        except RoleMemoryError as exc:
            add("memory_request", f"Invalid memoryWriteRequests[{index}]: {exc}")

    paths: list[str] = []
    if isinstance(result.get("filesChanged"), list):
        for raw in result["filesChanged"]:
            path = _safe_path(raw)
            if path is None:
                add("path", f"Unsafe modified path: {raw}")
                continue
            paths.append(path)
            if any(path == prefix or path.startswith(prefix + "/") for prefix in FORBIDDEN_PREFIXES):
                add("forbidden_path", f"Forbidden modified path: {path}")
            elif not _matches(path, allowed_paths):
                add("path_scope", f"Modified path exceeds the authorized scope: {path}")
            if role == "tester" and not _is_test_path(path):
                add("tester_scope", f"The tester may modify only test paths: {path}")
    if role in {"architect", "ui-designer", "code-reviewer"} and paths:
        add("read_only_role", f"{role} must not report local file modifications")

    for command in result.get("commandsRun", []):
        if isinstance(command, dict) and isinstance(command.get("command"), str):
            if _git_write_or_indirect(command["command"].strip()):
                add("git_authority", f"An agent is not authorized to perform Git writes: {command['command']}")
            status = command.get("status")
            exit_code = command.get("exitCode")
            if status == "passed" and exit_code != 0:
                add("command_consistency", "A command marked passed must have exitCode 0")
            if status == "failed" and (exit_code is None or exit_code == 0):
                add("command_consistency", "A command marked failed must have a nonzero exitCode")

    if not isinstance(before, dict) or not isinstance(after, dict):
        add("git_snapshot", "The before and after Git snapshots must be objects")
    else:
        for label, snapshot in (("before", before), ("after", after)):
            if not isinstance(snapshot.get("head"), str) or not snapshot["head"]:
                add("git_snapshot", f"The {label} snapshot lacks a valid head")
            if not isinstance(snapshot.get("refs"), dict):
                add("git_snapshot", f"The {label} snapshot lacks valid refs")
            if not isinstance(snapshot.get("index_entries"), str) or not snapshot["index_entries"]:
                add("git_snapshot", f"The {label} snapshot lacks index_entries")
            if not isinstance(snapshot.get("index_flags"), str) or not snapshot["index_flags"]:
                add("git_snapshot", f"The {label} snapshot lacks index_flags")
            if not isinstance(snapshot.get("status"), list):
                add("git_snapshot", f"The {label} snapshot lacks status")
        if before.get("head") != after.get("head"):
            add("head_changed", "HEAD changed while the agent was running")
        if before.get("refs") != after.get("refs"):
            add("refs_changed", "Git refs changed while the agent was running")
        for key in ("index_entries", "index_flags"):
            if before.get(key) != after.get(key):
                add("git_snapshot_changed", f"{key} changed while the agent was running")

    requirements: list[tuple[str | set[str], bool, str | None, str]] = []
    if ui:
        if expected_ui_provider is None:
            add("tool_context", "The UI task lacks a project-level UI prototype provider selection")
        else:
            requirements.append(
                (
                    {"ui-prototype", "penpot"},
                    True,
                    expected_ui_provider,
                    f"The UI task lacks successful evidence from the selected {expected_ui_provider}",
                )
            )
    if browser:
        if expected_browser_provider is None:
            add("tool_context", "The browser task lacks a project-level browser provider selection")
        else:
            requirements.append(
                (
                    "browser",
                    True,
                    expected_browser_provider,
                    f"The browser task lacks successful evidence from the selected {expected_browser_provider}",
                )
            )
    if code:
        requirements.append(
            (
                "codegraph",
                False,
                None,
                "The code task lacks evidence of a CodeGraph-first query or a fallback reason",
            )
        )
    for tools, require_success, expected_provider, message in requirements:
        if not _has_tool_evidence(
            result, tools, require_success, expected_provider
        ):
            add("tool_evidence", message)
    for item in result.get("evidence", []):
        if (
            isinstance(item, dict)
            and item.get("status") == "success"
            and NEGATIVE_EVIDENCE.search(f"{item.get('reference', '')} {item.get('detail', '')}")
        ):
            add("evidence_consistency", "Successful evidence contains an explicit failure signal")

    if result.get("status") == "success":
        verification = result.get("verification", {})
        if verification.get("status") != "passed":
            add("result_consistency", "A success result requires passed verification")
        verdicts = verification.get("verdicts", {})
        if any(value not in {"pass", "not-applicable"} for value in verdicts.values()):
            add("result_consistency", "A success result must not contain a fail or unknown verdict")
        if result.get("workflowUpdateRequest", {}).get("action") in {"request-repair", "block-run"}:
            add("result_consistency", "A success result must not request rework or blocking")
        if any(
            isinstance(item, dict) and item.get("severity") in {"blocker", "critical", "high"}
            for item in result.get("findings", [])
        ):
            add("result_consistency", "A success result must not contain a high-severity finding")
        if role in {"tester", "code-reviewer"}:
            if not verification.get("checks"):
                add("gate_evidence", "A gate PASS requires at least one successful verification check")
            if not any(item.get("status") == "success" for item in result.get("evidence", [])):
                add("gate_evidence", "A gate PASS requires at least one successful locatable evidence item")
        if role == "tester" and not any(
            item.get("status") == "passed" and item.get("exitCode") == 0
            for item in result.get("commandsRun", [])
        ):
            add("gate_evidence", "A tester PASS requires at least one successful test command")

    if role in {"tester", "code-reviewer"}:
        if expected_candidate is None:
            add("candidate_context", "The primary agent must provide the current candidate SHA to the gate policy check")
        elif result.get("candidateSha") != expected_candidate:
            add("candidate_sha", "The gate-role result is not bound to the current candidate SHA")

    return {"ok": not violations, "violations": violations, "checked_paths": paths}


def _load_json(value: str) -> Any:
    path = Path(value)
    text = path.read_text(encoding="utf-8") if path.is_file() else value
    return json.loads(text)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", required=True, type=_load_json)
    parser.add_argument("--allowed-path", action="append", default=[])
    parser.add_argument("--before", required=True, type=_load_json)
    parser.add_argument("--after", required=True, type=_load_json)
    parser.add_argument("--role", required=True, choices=sorted(ROLES))
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--candidate-sha")
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--ui", action="store_true")
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--code", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = read_config(args.project) if args.ui or args.browser else None
        checked = check_policy(
            args.result,
            args.allowed_path,
            args.before,
            args.after,
            args.role,
            args.task_id,
            args.candidate_sha,
            args.ui,
            args.browser,
            args.code,
            config["ui_prototype"]["provider"] if args.ui and config else None,
            config["browser"]["provider"] if args.browser and config else None,
        )
    except (json.JSONDecodeError, OSError, ProjectConfigError) as exc:
        checked = {"ok": False, "violations": [{"code": "input", "message": str(exc)}]}
    print(json.dumps(checked, ensure_ascii=False, sort_keys=True))
    return 0 if checked["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
