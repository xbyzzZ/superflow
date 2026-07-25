#!/usr/bin/env python3
"""Maintain Superflow workflow state, events, and dual-gate validation."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from schema_validation import SchemaValidationError, validate
from policy_check import check_policy
from project_config import ProjectConfigError, read_config
from role_memory import (
    MemoryError as RoleMemoryError,
    resolve_capability,
)
from workflow_profile import PROFILE_POLICIES, ProfileError, select_profile
from user_documents import render_process_log, render_requirements


GATES = {"test", "review"}
GATE_RESULTS = {"PASS", "FAIL"}
TRANSITIONS = {
    "initialized": {"preflight"},
    "preflight": {"discovery"},
    "discovery": {"requirements_ready"},
    "requirements_ready": {"architecting", "designing", "planned"},
    "architecting": {"designing", "planned"},
    "designing": {"architecting", "planned"},
    "planned": {"implementing", "blocked"},
    "implementing": {"verifying", "reviewing", "blocked"},
    "verifying": {"reviewing", "fixing", "ready", "risk_accepted", "blocked"},
    "reviewing": {"verifying", "fixing", "ready", "risk_accepted", "blocked"},
    "fixing": {"implementing", "verifying", "reviewing", "blocked"},
    "ready": {"blocked"},
    "risk_accepted": {"blocked"},
    "blocked": set(),
    "cancelled": set(),
    "finished": set(),
}
RUN_ID_RE = re.compile(r"^sf-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}$")
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
STATE_SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "assets" / "schemas" / "state.schema.json").read_text(
        encoding="utf-8"
    )
)
AGENT_RESULT_SCHEMA = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "assets"
        / "schemas"
        / "agent-result.schema.json"
    ).read_text(encoding="utf-8")
)
REQUIREMENTS_SCHEMA = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "assets"
        / "schemas"
        / "requirements.schema.json"
    ).read_text(encoding="utf-8")
)


class StateError(RuntimeError):
    """A workflow state operation is invalid."""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def new_run_id() -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"sf-{stamp}-{secrets.token_hex(4)}"


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".state.", dir=path.parent)
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


def _atomic_write_text(path: Path, value: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".document.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _git_common_directory(project: Path) -> Path:
    process = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "--git-common-dir"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode != 0 or not process.stdout.strip():
        raise StateError(process.stderr.strip() or "Unable to resolve the Git common directory")
    raw = Path(process.stdout.strip())
    common = (raw if raw.is_absolute() else project / raw).resolve()
    if not common.is_dir():
        raise StateError("Git returned an invalid common directory")
    return common


def _worktree_roots(project: Path) -> list[Path]:
    process = subprocess.run(
        ["git", "-C", str(project), "worktree", "list", "--porcelain"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode != 0:
        raise StateError(process.stderr.strip() or "Unable to enumerate Git worktrees")
    return [
        Path(line.removeprefix("worktree ")).resolve()
        for line in process.stdout.splitlines()
        if line.startswith("worktree ")
    ]


def workflow_root(project: Path) -> Path:
    """Return the non-versioned workflow storage shared by every linked worktree."""
    return _git_common_directory(project.resolve()) / "superflow" / "workflows"


def _legacy_run_directory(project: Path, run_id: str) -> Path | None:
    matches = [
        root / ".codex" / "workflows" / run_id
        for root in _worktree_roots(project)
        if (root / ".codex" / "workflows" / run_id).is_dir()
    ]
    if len(matches) > 1:
        raise StateError("Multiple legacy workflow ledgers exist for the same run-id")
    return matches[0] if matches else None


TASK_ROLES = {
    "product-manager",
    "architect",
    "ui-designer",
    "frontend-developer",
    "backend-developer",
    "tester",
    "code-reviewer",
}
DISPATCH_PHASES = {
    "architect": {"architecting", "designing"},
    "ui-designer": {"architecting", "designing"},
    "frontend-developer": {"implementing", "fixing"},
    "backend-developer": {"implementing", "fixing"},
    "tester": {"verifying", "reviewing"},
    "code-reviewer": {"verifying", "reviewing"},
}
TERMINAL_STATES = {"blocked", "cancelled", "finished"}
PROFILES = {"lite", "standard", "strict"}
PROFILE_CONTEXT = {
    profile: {
        "memory_limit": policy["memoryLimit"],
        "memory_max_bytes": policy["memoryMaxBytes"],
        "result_detail": policy["resultDetail"],
    }
    for profile, policy in PROFILE_POLICIES.items()
}
BUILTIN_GUIDES = {
    "architect": "architecture-design-rules.md",
    "ui-designer": "ui-ux-design-rules.md",
    "frontend-developer": "frontend-engineering-rules.md",
    "backend-developer": "backend-engineering-rules.md",
}
TASK_CONTRACT_FIELDS = {
    "role",
    "dependencies",
    "authorizedPaths",
    "acceptanceCriteria",
    "verificationCommands",
    "observableResults",
}


def _safe_contract_path(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    return (
        normalized != "."
        and not normalized.startswith(":")
        and not path.is_absolute()
        and ".." not in path.parts
        and (not path.parts or path.parts[0] != ".git")
    )


def _normalize_string_list(value: Any, field: str, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise StateError(f"{field} must be a {'possibly empty ' if allow_empty else 'non-empty '}array")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise StateError(f"{field} must contain non-empty strings")
        normalized = item.strip()
        if normalized not in result:
            result.append(normalized)
    return result


def _normalize_plan(plan: Any, require_contract: bool = True) -> list[dict[str, Any]]:
    if not isinstance(plan, list) or not plan:
        raise StateError("The plan must be a non-empty array")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(plan, 1):
        if isinstance(item, str):
            if require_contract:
                raise StateError("Every new plan item requires a complete task contract")
            task = {"id": f"task-{index}", "title": item, "status": "pending"}
        elif isinstance(item, dict):
            task = {
                "id": str(item.get("id", f"task-{index}")),
                "title": str(item.get("title", "")),
                "status": str(item.get("status", "pending")),
            }
            missing = TASK_CONTRACT_FIELDS - set(item)
            if require_contract and missing:
                raise StateError(
                    "Task contract is missing fields: " + ", ".join(sorted(missing))
                )
            if not missing:
                role = item.get("role")
                if role not in TASK_ROLES:
                    raise StateError("Invalid task role")
                task.update(
                    {
                        "role": role,
                        "dependencies": _normalize_string_list(
                            item.get("dependencies"),
                            "dependencies",
                            allow_empty=True,
                        ),
                        "authorizedPaths": _normalize_string_list(
                            item.get("authorizedPaths"),
                            "authorizedPaths",
                            allow_empty=role
                            in {"product-manager", "architect", "ui-designer", "code-reviewer"},
                        ),
                        "acceptanceCriteria": _normalize_string_list(
                            item.get("acceptanceCriteria"),
                            "acceptanceCriteria",
                        ),
                        "verificationCommands": _normalize_string_list(
                            item.get("verificationCommands"),
                            "verificationCommands",
                        ),
                        "observableResults": _normalize_string_list(
                            item.get("observableResults"),
                            "observableResults",
                        ),
                    }
                )
                if not all(
                    _safe_contract_path(path) for path in task["authorizedPaths"]
                ):
                    raise StateError("authorizedPaths contains an unsafe path")
        else:
            raise StateError("A plan item must be a string or object")
        if (
            not task["title"]
            or not TASK_ID_RE.fullmatch(task["id"])
            or task["id"] in seen
        ):
            raise StateError("Plan item titles must be non-empty and IDs must be unique")
        if task["status"] not in {"pending", "in_progress", "done", "blocked"}:
            raise StateError("Invalid plan item status")
        seen.add(task["id"])
        normalized.append(task)
    task_ids = {task["id"] for task in normalized}
    for task in normalized:
        for dependency in task.get("dependencies", []):
            if dependency == task["id"] or dependency not in task_ids:
                raise StateError("Task dependencies must reference another task in the plan")
    visiting: set[str] = set()
    visited: set[str] = set()
    by_id = {task["id"]: task for task in normalized}

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise StateError("Task dependencies contain a cycle")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in by_id[task_id].get("dependencies", []):
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in by_id:
        visit(task_id)
    return normalized


class WorkflowState:
    def __init__(self, project: Path, run_id: str):
        if not RUN_ID_RE.fullmatch(run_id):
            raise StateError("Invalid run-id format")
        self.run_id = run_id
        self.project = project.resolve()
        process = subprocess.run(
            ["git", "-C", str(self.project), "rev-parse", "--show-toplevel"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if process.returncode != 0:
            raise StateError(process.stderr.strip() or "The target project is not a Git worktree")
        if Path(process.stdout.strip()).resolve() != self.project:
            raise StateError("--project must point to the target Git worktree root")
        self.shared_root = workflow_root(self.project)
        shared_directory = self.shared_root / run_id
        legacy_directory = None if shared_directory.exists() else _legacy_run_directory(
            self.project, run_id
        )
        self.directory = legacy_directory or shared_directory
        self.storage_boundary = (
            self.directory.parent if legacy_directory is not None else self.shared_root
        )
        self.state_path = self.directory / "state.json"
        self.events_path = self.directory / "events.jsonl"
        self.lock_path = self.directory / ".state.lock"
        self.requirements_lock_path = self.directory / ".requirements.lock"

    def _assert_safe_storage(self) -> None:
        for path in (
            self.storage_boundary,
            self.directory,
            self.state_path,
            self.events_path,
            self.lock_path,
            self.requirements_lock_path,
        ):
            if path.is_symlink():
                raise StateError(f"Symlinked state path is refused: {path}")
            if path.exists():
                try:
                    path.resolve().relative_to(self.storage_boundary.resolve())
                except ValueError as exc:
                    raise StateError(f"The state path escapes its workflow storage boundary: {path}") from exc

    @classmethod
    def create(
        cls,
        project: Path,
        plan: Any,
        run_id: str | None = None,
        require_contract: bool = False,
        profile: str = "strict",
        profile_selection: dict[str, Any] | None = None,
        document_language: str | None = None,
    ) -> "WorkflowState":
        if profile not in PROFILES:
            raise StateError("Invalid execution profile")
        if document_language not in {None, "en", "zh-CN"}:
            raise StateError("Invalid user document language")
        store = cls(project, run_id or new_run_id())
        store._assert_safe_storage()
        if store.directory.exists():
            raise StateError("The run-id already exists")
        try:
            tool_config = read_config(store.project)
        except ProjectConfigError as exc:
            raise StateError(str(exc)) from exc
        normalized_plan = _normalize_plan(plan, require_contract=require_contract)
        store.directory.mkdir(parents=True, exist_ok=False)
        for name in ("briefs", "artifacts", "attempts", "gates"):
            (store.directory / name).mkdir()
        now = _now()
        state = {
            "schema_version": 2,
            "revision": 0,
            "run_id": store.run_id,
            "status": "initialized",
            "tool_config": tool_config,
            "profile": profile,
            **(
                {"profile_selection": profile_selection}
                if profile_selection is not None
                else {}
            ),
            **(
                {
                    "user_documents": {
                        "language": document_language,
                        "requirements_path": "requirements.md",
                        "process_log_path": "process-log.md",
                    },
                    "requirements": None,
                }
                if document_language is not None
                else {}
            ),
            "active_task_id": None,
            "pending_repair": None,
            "plan": normalized_plan,
            "candidate_sha": None,
            "gates": {},
            "repair_rounds": {},
            "risks": [],
            "dispatches": {},
            "created_at": now,
            "updated_at": now,
        }
        _atomic_write(store.directory / "plan.json", {"tasks": normalized_plan})
        _atomic_write(store.directory / "routing.json", {"assignments": []})
        _atomic_write(store.directory / "worktrees.json", {"worktrees": []})
        store._save(state, "init", {})
        return store

    def load(self) -> dict[str, Any]:
        self._assert_safe_storage()
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise StateError("The workflow does not exist") from exc
        except json.JSONDecodeError as exc:
            raise StateError("state.json is corrupt") from exc
        if state.get("run_id") != self.run_id:
            raise StateError("state.json does not match the run-id")
        try:
            validate(state, STATE_SCHEMA)
        except SchemaValidationError as exc:
            raise StateError(f"state.json does not conform to the schema: {exc}") from exc
        self._validate_invariants(state)
        self._validate_events(state)
        return state

    @staticmethod
    def _state_hash(state: dict[str, Any]) -> str:
        payload = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _validate_invariants(self, state: dict[str, Any]) -> None:
        profile = state.get("profile", "strict")
        selection = state.get("profile_selection")
        if selection is not None and (
            selection.get("profile") != profile
            or selection.get("policy") != PROFILE_POLICIES[profile]
        ):
            raise StateError("The frozen profile selection does not match its policy")
        documents = state.get("user_documents")
        requirements = state.get("requirements")
        if documents is not None:
            if requirements is not None:
                requirements_path = self.directory / "requirements.json"
                markdown_path = self.directory / documents["requirements_path"]
                try:
                    payload = json.loads(requirements_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise StateError("The frozen requirements artifact is unavailable") from exc
                try:
                    validate(payload, REQUIREMENTS_SCHEMA)
                except SchemaValidationError as exc:
                    raise StateError(
                        f"The frozen requirements artifact is invalid: {exc}"
                    ) from exc
                if (
                    self._digest(payload) != requirements["digest"]
                    or not markdown_path.is_file()
                    or markdown_path.read_text(encoding="utf-8")
                    != render_requirements(
                        payload,
                        self.run_id,
                        state.get("profile", "strict"),
                        requirements["recorded_at"],
                        documents["language"],
                    )
                ):
                    raise StateError("The frozen requirements document was modified")
        elif requirements is not None:
            raise StateError("Requirements metadata requires user document configuration")
        task_ids = {item["id"] for item in state["plan"]}
        if len(task_ids) != len(state["plan"]):
            raise StateError("state.json contains duplicate task IDs")
        active = state.get("active_task_id")
        if active is not None and active not in task_ids:
            raise StateError("active_task_id is not part of the current plan")
        if not set(state["repair_rounds"]).issubset(task_ids):
            raise StateError("repair_rounds contains an unknown task")
        waiting_tasks: set[str] = set()
        waiting_sessions: set[str] = set()
        for dispatch_id, dispatch in state.get("dispatches", {}).items():
            if dispatch.get("dispatch_id") != dispatch_id:
                raise StateError("A dispatch key does not match its immutable ID")
            task = next(
                (item for item in state["plan"] if item["id"] == dispatch.get("task_id")),
                None,
            )
            if task is None or task.get("role") != dispatch.get("role"):
                raise StateError("A dispatch does not match its frozen task contract")
            waiting = dispatch.get("status") == "waiting"
            if waiting and (
                dispatch.get("completed_at") is not None
                or dispatch.get("attempt_id") is not None
            ):
                raise StateError("A waiting dispatch cannot have a completion record")
            if not waiting and (
                dispatch.get("completed_at") is None
                or dispatch.get("attempt_id") is None
            ):
                raise StateError("A completed dispatch lacks its attempt binding")
            if waiting and dispatch["task_id"] in waiting_tasks:
                raise StateError("A task has more than one waiting dispatch")
            if waiting and dispatch["session_id"] in waiting_sessions:
                raise StateError("A session is bound to more than one waiting dispatch")
            if waiting:
                waiting_tasks.add(dispatch["task_id"])
                waiting_sessions.add(dispatch["session_id"])
        pending = state.get("pending_repair")
        if pending is not None:
            if pending.get("task_id") not in task_ids:
                raise StateError("pending_repair references an unknown task")
            current_gate = state["gates"].get(pending.get("gate"), {})
            if current_gate.get("id") != pending.get("gate_id"):
                raise StateError("pending_repair is not bound to the current failed gate")
            if current_gate.get("result") != "FAIL" or current_gate.get("sha") != pending.get("sha"):
                raise StateError("pending_repair does not match the failed gate")
        for gate in state["gates"].values():
            if gate.get("task_id") not in task_ids:
                raise StateError("A gate references an unknown task")
            if gate.get("valid") and gate.get("sha") != state.get("candidate_sha"):
                raise StateError("A valid gate is not bound to the current candidate")
        status = state["status"]
        if status == "ready" and not self._both_gates_pass(state):
            raise StateError("The ready state requires valid PASS results from both gates")
        if status == "risk_accepted" and not self._risks_cover_failures(state):
            raise StateError("The risk_accepted state requires valid risk acceptance")
        if status in TERMINAL_STATES and waiting_tasks:
            raise StateError("A terminal state cannot retain waiting dispatches")
        if status == "finished":
            if active is not None or any(item["status"] != "done" for item in state["plan"]):
                raise StateError("The finished state does not match plan completion")
            if not (self._both_gates_pass(state) or self._risks_cover_failures(state)):
                raise StateError("The finished state lacks valid approval evidence")

    def _validate_events(self, state: dict[str, Any]) -> None:
        try:
            lines = self.events_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError as exc:
            raise StateError("events.jsonl is missing") from exc
        if not lines:
            raise StateError("events.jsonl is empty")
        records: list[dict[str, Any]] = []
        try:
            records = [json.loads(line) for line in lines]
        except json.JSONDecodeError as exc:
            raise StateError("events.jsonl is corrupt") from exc
        previous_hash = "0" * 64
        for expected_revision, record in enumerate(records, 1):
            required = {
                "run_id",
                "actor",
                "at",
                "event",
                "detail",
                "revision",
                "state_hash",
                "previous_event_hash",
                "event_hash",
            }
            if set(record) != required or record.get("run_id") != self.run_id:
                raise StateError("events.jsonl contains an invalid event structure or run-id")
            if record.get("revision") != expected_revision:
                raise StateError("events.jsonl revisions are not contiguous")
            if record.get("previous_event_hash") != previous_hash:
                raise StateError("The events.jsonl hash chain is broken")
            payload = {key: value for key, value in record.items() if key != "event_hash"}
            event_hash = hashlib.sha256(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                    "utf-8"
                )
            ).hexdigest()
            if record.get("event_hash") != event_hash:
                raise StateError("events.jsonl contains an invalid event hash")
            previous_hash = event_hash
        last = records[-1]
        if last.get("revision") != state["revision"] or last.get("state_hash") != self._state_hash(state):
            raise StateError("state.json does not match events.jsonl")

    def _save(self, state: dict[str, Any], event: str, detail: dict[str, Any]) -> None:
        self._assert_safe_storage()
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            previous_state = None
            if self.state_path.exists():
                previous_state = json.loads(self.state_path.read_text(encoding="utf-8"))
            plan_path = self.directory / "plan.json"
            previous_plan = (
                json.loads(plan_path.read_text(encoding="utf-8"))
                if plan_path.exists()
                else None
            )
            expected_revision = state.get("revision", 0)
            actual_revision = (previous_state or {}).get("revision", 0)
            if expected_revision != actual_revision:
                raise StateError("The state revision changed; overwriting a concurrent update is refused")
            previous_event_size = self.events_path.stat().st_size if self.events_path.exists() else None
            previous_event_hash = "0" * 64
            if previous_event_size:
                last_line = self.events_path.read_text(encoding="utf-8").splitlines()[-1]
                previous_event_hash = json.loads(last_line)["event_hash"]
            state["revision"] = actual_revision + 1
            state["updated_at"] = _now()
            try:
                validate(state, STATE_SCHEMA)
            except SchemaValidationError as exc:
                raise StateError(f"Saving state that violates the schema is refused: {exc}") from exc
            self._validate_invariants(state)
            record = {
                "run_id": self.run_id,
                "actor": "product-manager",
                "at": state["updated_at"],
                "event": event,
                "detail": detail,
                "revision": state["revision"],
                "state_hash": self._state_hash(state),
                "previous_event_hash": previous_event_hash,
            }
            record["event_hash"] = hashlib.sha256(
                json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                    "utf-8"
                )
            ).hexdigest()
            try:
                _atomic_write(self.state_path, state)
                _atomic_write(plan_path, {"tasks": state["plan"]})
                with self.events_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                documents = state.get("user_documents")
                if documents is not None:
                    events = [
                        json.loads(line)
                        for line in self.events_path.read_text(encoding="utf-8").splitlines()
                    ]
                    _atomic_write_text(
                        self.directory / documents["process_log_path"],
                        render_process_log(state, events, documents["language"]),
                    )
            except OSError:
                if previous_state is None:
                    self.state_path.unlink(missing_ok=True)
                else:
                    _atomic_write(self.state_path, previous_state)
                if previous_plan is None:
                    plan_path.unlink(missing_ok=True)
                else:
                    _atomic_write(plan_path, previous_plan)
                if previous_event_size is None:
                    self.events_path.unlink(missing_ok=True)
                elif self.events_path.exists():
                    with self.events_path.open("r+b") as handle:
                        handle.truncate(previous_event_size)
                raise

    def record_requirements(self, requirements: Any) -> dict[str, Any]:
        self._assert_safe_storage()
        with self.requirements_lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            return self._record_requirements_locked(requirements)

    def _record_requirements_locked(self, requirements: Any) -> dict[str, Any]:
        state = self.load()
        self._require_mutable(state)
        self._ensure_tool_config_current(state)
        self._ensure_no_waiting_dispatches(state, "Requirements recording")
        if state["status"] != "discovery":
            raise StateError("Requirements can be frozen only during discovery")
        documents = state.get("user_documents")
        if documents is None:
            raise StateError("This legacy run has no user document configuration")
        if state.get("requirements") is not None:
            raise StateError("The user requirements baseline is immutable")
        try:
            validate(requirements, REQUIREMENTS_SCHEMA)
        except SchemaValidationError as exc:
            raise StateError(f"The requirements baseline is invalid: {exc}") from exc
        recorded_at = _now()
        metadata = {
            "digest": self._digest(requirements),
            "recorded_at": recorded_at,
            "title": requirements["title"],
        }
        json_path = self.directory / "requirements.json"
        markdown_path = self.directory / documents["requirements_path"]
        _atomic_write(json_path, requirements)
        _atomic_write_text(
            markdown_path,
            render_requirements(
                requirements,
                self.run_id,
                state.get("profile", "strict"),
                recorded_at,
                documents["language"],
            ),
        )
        state["requirements"] = metadata
        try:
            self._save(
                state,
                "record_requirements",
                {"title": requirements["title"], "digest": metadata["digest"]},
            )
        except (OSError, StateError):
            json_path.unlink(missing_ok=True)
            markdown_path.unlink(missing_ok=True)
            raise
        return state

    def summary(self) -> dict[str, Any]:
        state = self.load()
        documents = state.get("user_documents")
        document_summary = (
            {
                "language": documents["language"],
                "requirements_path": str(
                    self.directory / documents["requirements_path"]
                ),
                "process_log_path": str(
                    self.directory / documents["process_log_path"]
                ),
            }
            if documents is not None
            else None
        )
        waiting = [
            {
                "dispatch_id": item["dispatch_id"],
                "task_id": item["task_id"],
                "role": item["role"],
                "started_at": item["started_at"],
            }
            for item in self._waiting_dispatches(state)
        ]
        gates = {
            name: {
                key: value
                for key, value in record.items()
                if key in {"id", "task_id", "result", "sha", "valid", "recorded_at"}
            }
            for name, record in state["gates"].items()
        }
        return {
            "run_id": state["run_id"],
            "revision": state["revision"],
            "status": state["status"],
            "profile": state.get("profile", "strict"),
            "active_task_id": state["active_task_id"],
            "requirements": state.get("requirements"),
            "user_documents": document_summary,
            "plan": [
                {
                    key: value
                    for key, value in task.items()
                    if key in {"id", "title", "role", "status", "dependencies"}
                }
                for task in state["plan"]
            ],
            "candidate_sha": state["candidate_sha"],
            "gates": gates,
            "waiting_dispatches": waiting,
            "repair_rounds": state["repair_rounds"],
            "risks": state["risks"],
            "updated_at": state["updated_at"],
        }

    def register_worktree(
        self,
        worktree: Path,
        branch: str,
        base_ref: str,
    ) -> dict[str, Any]:
        state = self.load()
        self._require_mutable(state)
        target = worktree.resolve()
        process = subprocess.run(
            ["git", "-C", str(target), "rev-parse", "--show-toplevel"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if process.returncode != 0 or Path(process.stdout.strip()).resolve() != target:
            raise StateError("The registered path is not a Git worktree root")
        if _git_common_directory(target) != _git_common_directory(self.project):
            raise StateError("The registered worktree belongs to another Git repository")
        path = self.directory / "worktrees.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        worktrees = value.get("worktrees")
        if not isinstance(worktrees, list):
            raise StateError("worktrees.json is corrupt")
        original = copy.deepcopy(value)
        entry = {
            "path": str(target),
            "branch": branch,
            "base_ref": base_ref,
            "registered_at": _now(),
        }
        changed = not any(item.get("path") == entry["path"] for item in worktrees)
        if changed:
            worktrees.append(entry)
            _atomic_write(path, {"worktrees": worktrees})
        try:
            self._save(
                state,
                "register_worktree",
                {"path": entry["path"], "branch": branch, "base_ref": base_ref},
            )
        except (OSError, StateError):
            if changed:
                _atomic_write(path, original)
            raise
        return entry

    @staticmethod
    def _digest(value: Any) -> str:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _validate_brief_static_context(
        self,
        role: str,
        brief: dict[str, Any],
    ) -> None:
        expected_script = Path(__file__).resolve().with_name("role_memory.py")
        script_value = brief.get("roleMemoryScript")
        if (
            not isinstance(script_value, str)
            or not Path(script_value).is_absolute()
            or Path(script_value).resolve() != expected_script
            or not expected_script.is_file()
        ):
            raise StateError(
                "The task brief role memory script does not match this Superflow installation"
            )
        guide_name = BUILTIN_GUIDES.get(role)
        if guide_name is None:
            return
        expected_guide = Path(__file__).resolve().parents[1] / "references" / guide_name
        guide_value = brief.get("builtinGuide")
        if (
            not isinstance(guide_value, str)
            or not Path(guide_value).is_absolute()
            or Path(guide_value).resolve() != expected_guide
            or not expected_guide.is_file()
        ):
            raise StateError(
                "The task brief built-in guide does not match the assigned specialist role"
            )

    def _validate_dispatch_memory_capability(
        self,
        task_id: str,
        role: str,
        capability: Any,
    ) -> str:
        try:
            scope = resolve_capability(self.project, capability)
        except RoleMemoryError as exc:
            raise StateError(
                f"The task dispatch role memory capability is invalid: {exc}"
            ) from exc
        if (
            scope.get("role") != role
            or scope.get("run_id") != self.run_id
            or scope.get("task_id") != task_id
        ):
            raise StateError(
                "The task dispatch role memory capability scope does not match its task"
            )
        return hashlib.sha256(capability.encode("utf-8")).hexdigest()

    def record_brief(self, task_id: str, brief: Any) -> dict[str, Any]:
        state = self.load()
        self._require_mutable(state)
        task = next((item for item in state["plan"] if item["id"] == task_id), None)
        if task is None or "role" not in task:
            raise StateError("A brief requires a contracted task")
        required = {
            "runId",
            "taskId",
            "role",
            "workDirectory",
            "objective",
            "dependencies",
            "authorizedPaths",
            "exclusions",
            "acceptanceCriteria",
            "verificationCommands",
            "observableResults",
            "browserProvider",
            "browserRequired",
            "browserAccessMode",
            "executionProfile",
            "contextMode",
            "memoryLimit",
            "memoryMaxBytes",
            "resultDetail",
            "codeGraphRequired",
            "uiPrototypeProvider",
            "beforeSnapshot",
            "resultSchema",
            "roleMemoryScript",
        }
        if task["role"] in BUILTIN_GUIDES:
            required.add("builtinGuide")
        if not isinstance(brief, dict) or set(brief) != required:
            raise StateError("The task brief is incomplete or contains unknown fields")
        if (
            brief["runId"] != self.run_id
            or brief["taskId"] != task_id
            or brief["role"] != task["role"]
            or brief["dependencies"] != task["dependencies"]
            or brief["authorizedPaths"] != task["authorizedPaths"]
            or brief["acceptanceCriteria"] != task["acceptanceCriteria"]
            or brief["verificationCommands"] != task["verificationCommands"]
            or brief["observableResults"] != task["observableResults"]
        ):
            raise StateError("The task brief does not match the frozen task contract")
        expected_browser = state["tool_config"]["browser"]["provider"]
        profile = state.get("profile", "strict")
        context = PROFILE_CONTEXT[profile]
        expected_access_mode = (
            "main-only"
            if expected_browser == "codex-browser"
            else "specialist-direct"
        )
        if (
            brief["browserProvider"] != expected_browser
            or not isinstance(brief["browserRequired"], bool)
            or brief["browserAccessMode"] != expected_access_mode
            or brief["executionProfile"] != profile
            or brief["contextMode"] != "minimal"
            or brief["memoryLimit"] != context["memory_limit"]
            or brief["memoryMaxBytes"] != context["memory_max_bytes"]
            or brief["resultDetail"] != context["result_detail"]
            or not isinstance(brief["codeGraphRequired"], bool)
        ):
            raise StateError(
                "The task brief execution routing does not match the frozen run profile"
            )
        if brief["browserRequired"] and expected_access_mode == "main-only":
            raise StateError(
                "A browser-required task cannot use the main-only codex-browser provider; "
                "reconfigure a specialist-direct browser provider for a new run"
            )
        self._validate_brief_static_context(task["role"], brief)
        path = self.directory / "briefs" / f"{task_id}.json"
        if path.exists():
            raise StateError("A task brief is immutable once recorded")
        _atomic_write(path, brief)
        routing_path = self.directory / "routing.json"
        routing = json.loads(routing_path.read_text(encoding="utf-8"))
        assignments = routing.get("assignments")
        if not isinstance(assignments, list):
            raise StateError("routing.json is corrupt")
        original_routing = copy.deepcopy(routing)
        digest = self._digest(brief)
        assignments.append(
            {
                "task_id": task_id,
                "role": task["role"],
                "decision": "dispatched",
                "brief_digest": digest,
                "recorded_at": _now(),
            }
        )
        _atomic_write(routing_path, {"assignments": assignments})
        try:
            self._save(
                state,
                "record_brief",
                {"task_id": task_id, "role": task["role"], "digest": digest},
            )
        except (OSError, StateError):
            _atomic_write(routing_path, original_routing)
            path.unlink(missing_ok=True)
            raise
        return {"path": str(path), "digest": digest}

    @staticmethod
    def _waiting_dispatches(state: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            dispatch
            for dispatch in state.get("dispatches", {}).values()
            if dispatch.get("status") == "waiting"
        ]

    def _ensure_no_waiting_dispatches(
        self,
        state: dict[str, Any],
        operation: str,
    ) -> None:
        waiting = self._waiting_dispatches(state)
        if waiting:
            identifiers = ", ".join(
                sorted(item["dispatch_id"] for item in waiting)
            )
            raise StateError(
                f"{operation} is blocked while subagent dispatches are waiting: "
                f"{identifiers}"
            )

    def record_dispatch(
        self,
        task_id: str,
        role: str,
        session_id: str,
        before: Any,
        memory_capability: Any,
    ) -> dict[str, Any]:
        state = self.load()
        self._require_mutable(state)
        self._ensure_tool_config_current(state)
        task = next((item for item in state["plan"] if item["id"] == task_id), None)
        if task is None or task.get("role") != role or role not in DISPATCH_PHASES:
            raise StateError("The dispatch role does not match a specialist task")
        if state["status"] not in DISPATCH_PHASES[role]:
            raise StateError("The workflow phase does not permit this role dispatch")
        if task["status"] != "in_progress":
            raise StateError("The dispatched task must already be in progress")
        if not isinstance(session_id, str) or not session_id.strip():
            raise StateError("A dispatch requires the actual subagent session identifier")
        if not isinstance(before, dict) or before != self._git_snapshot():
            raise StateError("The dispatch snapshot must match the current worktree")
        by_id = {item["id"]: item for item in state["plan"]}
        incomplete = [
            dependency
            for dependency in task.get("dependencies", [])
            if by_id[dependency]["status"] != "done"
        ]
        if incomplete:
            raise StateError(
                "Task dependencies are incomplete: " + ", ".join(incomplete)
            )
        if any(
            item.get("task_id") == task_id or item.get("session_id") == session_id
            for item in self._waiting_dispatches(state)
        ):
            raise StateError("The task or subagent session already has a waiting dispatch")
        brief_path = self.directory / "briefs" / f"{task_id}.json"
        if not brief_path.is_file():
            raise StateError("Record the immutable task brief before dispatch")
        brief = json.loads(brief_path.read_text(encoding="utf-8"))
        self._validate_brief_static_context(role, brief)
        memory_capability_digest = self._validate_dispatch_memory_capability(
            task_id,
            role,
            memory_capability,
        )
        if any(
            item.get("memory_capability_digest") == memory_capability_digest
            for item in state.get("dispatches", {}).values()
        ):
            raise StateError(
                "The task dispatch role memory capability was already used"
            )
        if Path(brief.get("workDirectory", "")).resolve() != self.project:
            raise StateError("The task brief workDirectory does not match this worktree")
        worktrees = json.loads(
            (self.directory / "worktrees.json").read_text(encoding="utf-8")
        ).get("worktrees", [])
        if not any(Path(item.get("path", "")).resolve() == self.project for item in worktrees):
            raise StateError("The dispatch worktree is not registered in the run ledger")
        dispatch_id = secrets.token_hex(8)
        dispatch = {
            "dispatch_id": dispatch_id,
            "task_id": task_id,
            "role": role,
            "session_id": session_id.strip(),
            "status": "waiting",
            "worktree": str(self.project),
            "before_digest": self._digest(before),
            "brief_digest": self._digest(brief),
            "memory_capability_digest": memory_capability_digest,
            "execution_profile": brief["executionProfile"],
            "context_mode": brief["contextMode"],
            "started_at": _now(),
            "completed_at": None,
            "attempt_id": None,
        }
        state.setdefault("dispatches", {})[dispatch_id] = dispatch
        self._save(
            state,
            "record_dispatch",
            {
                "dispatch_id": dispatch_id,
                "task_id": task_id,
                "role": role,
                "session_id": session_id.strip(),
            },
        )
        return dispatch

    def record_attempt(
        self,
        task_id: str,
        role: str,
        kind: str,
        outcome: str,
        result: Any,
        before: Any,
        after: Any,
        reason: str,
        dispatch_id: str | None = None,
    ) -> dict[str, Any]:
        state = self.load()
        self._require_mutable(state)
        task = next((item for item in state["plan"] if item["id"] == task_id), None)
        if task is None or task.get("role") != role:
            raise StateError("The attempt role does not match the frozen task contract")
        if kind not in {"initial", "retry", "repair"}:
            raise StateError("Invalid attempt kind")
        if outcome not in {"accepted", "rejected", "blocked"}:
            raise StateError("Invalid attempt outcome")
        if (
            not isinstance(result, dict)
            or not isinstance(before, dict)
            or not isinstance(after, dict)
        ):
            raise StateError("Attempt result and Git snapshots must be JSON objects")
        if not isinstance(reason, str) or not reason.strip():
            raise StateError("An attempt requires a reason")
        dispatches = state.get("dispatches", {})
        dispatch = dispatches.get(dispatch_id) if dispatch_id is not None else None
        if not isinstance(dispatch, dict):
            raise StateError("The attempt requires a recorded dispatch")
        if (
            dispatch.get("status") != "waiting"
            or dispatch.get("task_id") != task_id
            or dispatch.get("role") != role
            or Path(dispatch.get("worktree", "")).resolve() != self.project
        ):
            raise StateError("The attempt does not match a waiting dispatch")
        if self._digest(before) != dispatch.get("before_digest"):
            raise StateError("The attempt before snapshot does not match its dispatch")
        if after != self._git_snapshot():
            raise StateError("The attempt after snapshot does not match the current worktree")
        if result.get("dispatchId") not in {None, dispatch_id}:
            raise StateError("The result dispatchId does not match the recorded dispatch")
        evidence_request = result.get("browserEvidenceRequest")
        if evidence_request is not None:
            try:
                validate(result, AGENT_RESULT_SCHEMA)
            except SchemaValidationError as exc:
                raise StateError(
                    f"A browser evidence request must conform to the Agent result schema: {exc}"
                ) from exc
            if (
                role not in {"frontend-developer", "tester"}
                or result.get("status") not in {"blocked", "partial"}
                or not isinstance(evidence_request, dict)
                or evidence_request.get("provider")
                != state["tool_config"]["browser"]["provider"]
                or any(
                    isinstance(item, dict)
                    and item.get("type") == "browser"
                    and item.get("status") == "success"
                    for item in result.get("evidence", [])
                )
            ):
                raise StateError(
                    "A browser evidence request must be a blocked or partial frontend or tester result "
                    "for the frozen provider without successful browser evidence"
                )
        checked: dict[str, Any] | None = None
        if outcome == "accepted":
            if result.get("dispatchId") != dispatch_id or result.get("status") != "success":
                raise StateError(
                    "An accepted attempt requires a successful result bound to its dispatch"
                )
            ui = role == "ui-designer"
            browser = any(
                isinstance(item, dict) and item.get("type") == "browser"
                for item in result.get("evidence", [])
            )
            brief_path = self.directory / "briefs" / f"{task_id}.json"
            brief = json.loads(brief_path.read_text(encoding="utf-8"))
            if brief.get("browserRequired") is True and not browser:
                raise StateError(
                    "A browser-required task cannot be accepted without browser evidence"
                )
            checked = check_policy(
                result,
                task["authorizedPaths"],
                before,
                after,
                expected_role=role,
                expected_task=task_id,
                expected_candidate=(
                    state.get("candidate_sha")
                    if role in {"tester", "code-reviewer"}
                    else None
                ),
                ui=ui,
                browser=browser,
                code=brief.get("codeGraphRequired") is True,
                expected_ui_provider=(
                    state["tool_config"]["ui_prototype"]["provider"] if ui else None
                ),
                expected_browser_provider=(
                    state["tool_config"]["browser"]["provider"] if browser else None
                ),
                expected_dispatch=dispatch_id,
            )
            if not checked["ok"]:
                raise StateError(
                    "An accepted attempt must pass result policy validation: "
                    + json.dumps(
                        checked["violations"],
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
        brief_path = self.directory / "briefs" / f"{task_id}.json"
        if not brief_path.is_file():
            raise StateError("Record the immutable task brief before an attempt")
        attempt_directory = self.directory / "attempts" / task_id
        attempt_directory.mkdir(parents=True, exist_ok=True)
        sequence = len(list(attempt_directory.glob("*.json"))) + 1
        attempt_id = f"{task_id}-{sequence:03d}-{secrets.token_hex(4)}"
        attempt = {
            "attempt_id": attempt_id,
            "sequence": sequence,
            "task_id": task_id,
            "role": role,
            "kind": kind,
            "outcome": outcome,
            "reason": reason.strip(),
            "brief_digest": self._digest(
                json.loads(brief_path.read_text(encoding="utf-8"))
            ),
            "result_digest": self._digest(result),
            "before": before,
            "after": after,
            "result": result,
            "policy": checked,
            "recorded_at": _now(),
        }
        path = attempt_directory / f"{attempt_id}.json"
        _atomic_write(path, attempt)
        dispatch["status"] = outcome
        dispatch["completed_at"] = attempt["recorded_at"]
        dispatch["attempt_id"] = attempt_id
        try:
            self._save(
                state,
                "record_attempt",
                {
                    "attempt_id": attempt["attempt_id"],
                    "task_id": task_id,
                    "role": role,
                    "kind": kind,
                    "outcome": outcome,
                    "dispatch_id": dispatch_id,
                },
            )
        except (OSError, StateError):
            path.unlink(missing_ok=True)
            raise
        return attempt

    def _recorded_attempt_ids(self) -> set[str]:
        if not self.events_path.is_file():
            return set()
        result: set[str] = set()
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record.get("event") == "record_attempt":
                attempt_id = record.get("detail", {}).get("attempt_id")
                if isinstance(attempt_id, str):
                    result.add(attempt_id)
        return result

    def _accepted_attempt_exists(self, task_id: str) -> bool:
        directory = self.directory / "attempts" / task_id
        if not directory.is_dir():
            return False
        recorded = self._recorded_attempt_ids()
        return any(
            (
                value := json.loads(path.read_text(encoding="utf-8"))
            ).get("attempt_id")
            in recorded
            and value.get("outcome") == "accepted"
            for path in directory.glob("*.json")
        )

    def _ensure_audit_complete(self, state: dict[str, Any]) -> None:
        contracted = [task for task in state["plan"] if "role" in task]
        if not contracted:
            return
        worktrees = json.loads(
            (self.directory / "worktrees.json").read_text(encoding="utf-8")
        ).get("worktrees")
        if not isinstance(worktrees, list) or not worktrees:
            raise StateError("A contracted run requires a registered integration worktree")
        for task in contracted:
            if not (self.directory / "briefs" / f"{task['id']}.json").is_file():
                raise StateError(f"Task {task['id']} lacks an immutable brief")
            if not self._accepted_attempt_exists(task["id"]):
                raise StateError(f"Task {task['id']} lacks an accepted audited attempt")
        for gate in GATES:
            current = state["gates"].get(gate)
            if current and not (
                self.directory / "gates" / f"{current['id']}.json"
            ).is_file():
                raise StateError(f"The current {gate} gate lacks an immutable artifact")

    @staticmethod
    def _require_mutable(state: dict[str, Any]) -> None:
        if state["status"] in {"blocked", "cancelled", "finished"}:
            raise StateError("A terminal workflow cannot be modified")

    def _ensure_tool_config_current(self, state: dict[str, Any]) -> None:
        try:
            current = read_config(self.project)
        except ProjectConfigError as exc:
            raise StateError(str(exc)) from exc
        if current != state["tool_config"]:
            raise StateError(
                "The project browser or UI prototype provider changed after this run started; "
                "the old run cannot continue. Move it to blocked/cancelled and create a new run"
            )

    def transition(self, target: str, task_id: str | None = None) -> dict[str, Any]:
        state = self.load()
        source = state["status"]
        emergency = target in {"blocked", "cancelled"} and source not in TERMINAL_STATES
        self._ensure_no_waiting_dispatches(state, "Workflow transition")
        if not emergency:
            self._ensure_tool_config_current(state)
        if not emergency and target not in TRANSITIONS.get(source, set()):
            raise StateError(f"Invalid state transition: {source} -> {target}")
        if (
            target == "requirements_ready"
            and state.get("user_documents") is not None
            and state.get("requirements") is None
        ):
            raise StateError(
                "Freeze the user-facing requirements baseline before requirements_ready"
            )
        if target == "fixing":
            if task_id is None:
                raise StateError("The fixing state requires a task-id")
            pending = state.get("pending_repair")
            if pending is None or pending.get("task_id") != task_id:
                raise StateError("fixing must inherit the current failed gate's repair lineage")
            rounds = state["repair_rounds"].get(task_id, 0)
            if rounds >= 3:
                raise StateError("The three-round repair limit has been reached")
            state["repair_rounds"][task_id] = rounds + 1
        if target == "implementing" and state.get("pending_repair") is not None:
            if task_id != state["pending_repair"]["task_id"]:
                raise StateError("implementing cannot switch away from an unfinished repair lineage")
        if target == "ready" and not self._both_gates_pass(state):
            raise StateError("The test and review gates have not passed for the same candidate SHA")
        if target == "risk_accepted" and not self._risks_cover_failures(state):
            raise StateError("Not all current failed gates have user risk acceptance")
        if target in {"ready", "risk_accepted"}:
            if any(item["status"] != "done" for item in state["plan"]):
                raise StateError("All plan items must be complete before entering an approval terminal state")
            self._ensure_audit_complete(state)
            self._ensure_candidate_current(state)
        if target in {"implementing", "fixing"}:
            for gate in state["gates"].values():
                gate["valid"] = False
        self._apply_plan_transition(state, target, task_id)
        if task_id is not None and target in {"implementing", "fixing"}:
            state["active_task_id"] = task_id
        if target in {"finished", "cancelled"}:
            state["active_task_id"] = None
        state["status"] = target
        self._save(state, "transition", {"from": source, "to": target, "task_id": task_id})
        return state

    @staticmethod
    def _apply_plan_transition(state: dict[str, Any], target: str, task_id: str | None) -> None:
        tasks = state["plan"]
        if task_id is not None:
            matching = [task for task in tasks if task["id"] == task_id]
            if not matching:
                raise StateError("The specified plan item does not exist")
            if target in {"implementing", "verifying", "reviewing", "fixing"}:
                matching[0]["status"] = "in_progress"
        if target == "finished":
            if any(task["status"] not in {"done"} for task in tasks):
                raise StateError("The plan still has incomplete items and cannot finish")

    def set_task(self, task_id: str, status: str) -> dict[str, Any]:
        if status not in {"pending", "in_progress", "done", "blocked"}:
            raise StateError("Invalid plan item status")
        state = self.load()
        self._require_mutable(state)
        self._ensure_tool_config_current(state)
        self._ensure_no_waiting_dispatches(state, "Task status update")
        task = next((item for item in state["plan"] if item["id"] == task_id), None)
        if task is None:
            raise StateError("The plan item does not exist")
        if state.get("pending_repair") is not None:
            raise StateError("Plan item status cannot change while a repair lineage is unfinished")
        if status in {"in_progress", "done"}:
            by_id = {item["id"]: item for item in state["plan"]}
            incomplete = [
                dependency
                for dependency in task.get("dependencies", [])
                if by_id[dependency]["status"] != "done"
            ]
            if incomplete:
                raise StateError(
                    "Task dependencies are incomplete: " + ", ".join(incomplete)
                )
        if status == "done" and "role" in task and not self._accepted_attempt_exists(task_id):
            raise StateError("A contracted task requires an accepted audited attempt before completion")
        if task["status"] != status:
            for gate in state["gates"].values():
                gate["valid"] = False
        task["status"] = status
        self._save(state, "set_task", {"task_id": task_id, "status": status})
        return state

    def set_candidate(self, sha: str) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-fA-F]{7,64}", sha):
            raise StateError("Invalid candidate SHA format")
        state = self.load()
        self._require_mutable(state)
        self._ensure_tool_config_current(state)
        self._ensure_no_waiting_dispatches(state, "Candidate update")
        if state["status"] not in {"implementing", "verifying", "reviewing", "fixing"}:
            raise StateError("A candidate can be frozen only during implementation or verification")
        sha = self._resolve_candidate(sha)
        previous = state.get("candidate_sha")
        if previous != sha:
            for gate in state["gates"].values():
                gate["valid"] = False
            state["candidate_sha"] = sha
            if state["status"] in {"ready", "risk_accepted"}:
                state["status"] = "implementing"
        self._save(state, "set_candidate", {"from": previous, "to": sha})
        return state

    def _resolve_candidate(self, sha: str) -> str:
        resolved = self._git("rev-parse", "--verify", f"{sha}^{{commit}}").lower()
        head = self._git("rev-parse", "--verify", "HEAD^{commit}").lower()
        if resolved != head:
            raise StateError("The candidate SHA must equal the integration worktree HEAD")
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", resolved):
            raise StateError("Git returned an invalid commit SHA")
        return resolved

    def _git(self, *arguments: str) -> str:
        process = subprocess.run(
            ["git", "-C", str(self.project), *arguments],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if process.returncode != 0:
            raise StateError(process.stderr.strip() or "Unable to read the target Git repository")
        return process.stdout.strip()

    def _git_snapshot(self) -> dict[str, Any]:
        refs: dict[str, str] = {}
        output = self._git("for-each-ref", "--format=%(refname) %(objectname)")
        for line in output.splitlines():
            name, sha = line.split(" ", 1)
            refs[name] = sha
        digest = lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest()
        return {
            "head": self._git("rev-parse", "HEAD^{commit}"),
            "refs": refs,
            "index_entries": digest(self._git("ls-files", "--stage")),
            "index_flags": digest(self._git("ls-files", "-v")),
            "status": self._git("status", "--porcelain=v1", "--untracked-files=all").splitlines(),
        }

    def _ensure_candidate_current(self, state: dict[str, Any]) -> None:
        candidate = state.get("candidate_sha")
        if not candidate or self._git("rev-parse", "HEAD^{commit}").lower() != candidate:
            raise StateError("The integration worktree HEAD diverged from the candidate SHA")
        if self._git(
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            ".",
        ):
            raise StateError("The integration worktree contains uncommitted or untracked changes")
        git_dir = Path(self._git("rev-parse", "--git-dir"))
        if not git_dir.is_absolute():
            git_dir = self.project / git_dir
        for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REBASE_HEAD", "rebase-merge", "rebase-apply"):
            if (git_dir / marker).exists():
                raise StateError(f"A Git operation is still in progress: {marker}")

    def record_gate(
        self,
        gate: str,
        sha: str,
        task_id: str,
        agent_result: dict[str, Any],
        before: dict[str, Any],
        after: dict[str, Any],
        allowed_paths: list[str],
        browser: bool = False,
        code: bool = True,
    ) -> dict[str, Any]:
        gate = gate.lower()
        state = self.load()
        self._require_mutable(state)
        self._ensure_tool_config_current(state)
        self._ensure_no_waiting_dispatches(state, "Gate recording")
        if state["status"] not in {"verifying", "reviewing"}:
            raise StateError("A gate can be recorded only during verifying or reviewing")
        if gate not in GATES:
            raise StateError("Invalid gate")
        if sha.lower() != state.get("candidate_sha"):
            raise StateError("The gate SHA does not match the current candidate SHA")
        if task_id not in {item["id"] for item in state["plan"]}:
            raise StateError("The gate task-id is not part of the current plan")
        self._ensure_candidate_current(state)
        fresh = self._git_snapshot()
        if after != fresh:
            raise StateError("The after Git snapshot does not match the current repository state")
        profile = state.get("profile", "strict")
        expected_role = (
            "code-reviewer"
            if profile == "lite"
            else "tester" if gate == "test" else "code-reviewer"
        )
        task = next(item for item in state["plan"] if item["id"] == task_id)
        brief: dict[str, Any] | None = None
        attempt: dict[str, Any] | None = None
        if "role" in task:
            dispatch_id = agent_result.get("dispatchId")
            dispatch = state.get("dispatches", {}).get(dispatch_id)
            if (
                not isinstance(dispatch, dict)
                or dispatch.get("status") != "accepted"
                or dispatch.get("task_id") != task_id
                or dispatch.get("role") != expected_role
            ):
                raise StateError(
                    "A contracted gate requires the accepted result of its recorded dispatch"
                )
            attempt_path = (
                self.directory
                / "attempts"
                / task_id
                / f"{dispatch['attempt_id']}.json"
            )
            try:
                attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise StateError("The gate dispatch attempt artifact is unavailable") from exc
            if attempt.get("result_digest") != self._digest(agent_result):
                raise StateError(
                    "The gate result differs from the accepted dispatch attempt"
                )
            brief_path = self.directory / "briefs" / f"{task_id}.json"
            try:
                brief = json.loads(brief_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise StateError("The gate task brief is unavailable") from exc
            code = code and brief.get("codeGraphRequired") is True
        checked = check_policy(
            agent_result,
            allowed_paths,
            before,
            after,
            expected_role=expected_role,
            expected_task=task_id,
            expected_candidate=state["candidate_sha"],
            browser=browser,
            code=code,
            expected_browser_provider=(
                state["tool_config"]["browser"]["provider"] if browser else None
            ),
            expected_dispatch=agent_result.get("dispatchId"),
        )
        if not checked["ok"]:
            raise StateError(
                "The gate agent result failed the policy check: "
                + json.dumps(checked["violations"], ensure_ascii=False, sort_keys=True)
            )
        result = self._derive_gate_result(
            agent_result,
            require_test_commands=gate == "test",
        )
        pending = state.get("pending_repair")
        if result == "FAIL" and pending is not None and pending.get("task_id") != task_id:
            raise StateError("A new failed gate cannot switch away from an unfinished repair lineage")
        if state["status"] in {"ready", "risk_accepted"}:
            state["status"] = "verifying"
        gate_record = {
            "id": secrets.token_hex(8),
            "task_id": task_id,
            "result": result,
            "sha": sha.lower(),
            "evidence": (
                {
                    "source": "attempt",
                    "attempt_id": dispatch["attempt_id"],
                    "dispatch_id": agent_result.get("dispatchId"),
                    "result_digest": attempt["result_digest"],
                    "policy_digest": self._digest(checked),
                }
                if attempt is not None
                else {"agent_result": agent_result, "policy": checked}
            ),
            "valid": True,
            "recorded_at": _now(),
        }
        state["gates"][gate] = gate_record
        gate_path = self.directory / "gates" / f"{gate_record['id']}.json"
        _atomic_write(gate_path, gate_record)
        if result == "FAIL":
            state["pending_repair"] = {
                "task_id": task_id,
                "gate": gate,
                "gate_id": state["gates"][gate]["id"],
                "sha": sha.lower(),
            }
        elif pending is not None and pending.get("gate") == gate:
            state["pending_repair"] = None
        exhausted = (
            result == "FAIL"
            and state["repair_rounds"].get(task_id, 0) >= 3
        )
        if exhausted:
            state["status"] = "blocked"
            task = next((item for item in state["plan"] if item["id"] == task_id), None)
            if task is not None:
                task["status"] = "blocked"
        try:
            self._save(
                state,
                "record_gate",
                {
                    "gate": gate,
                    "result": result,
                    "sha": sha.lower(),
                    "repair_exhausted": exhausted,
                },
            )
        except (OSError, StateError):
            gate_path.unlink(missing_ok=True)
            raise
        return state

    @staticmethod
    def _derive_gate_result(
        agent_result: dict[str, Any],
        require_test_commands: bool = False,
    ) -> str:
        verification = agent_result.get("verification", {})
        checks = verification.get("checks", [])
        verdicts = verification.get("verdicts", {})
        findings = agent_result.get("findings", [])
        passed = (
            agent_result.get("status") == "success"
            and verification.get("status") == "passed"
            and bool(checks)
            and all(item.get("status") == "passed" for item in checks)
            and all(value in {"pass", "not-applicable"} for value in verdicts.values())
            and not any(item.get("severity") != "info" for item in findings)
            and any(item.get("status") == "success" for item in agent_result.get("evidence", []))
        )
        if agent_result.get("role") == "tester" or require_test_commands:
            commands = agent_result.get("commandsRun", [])
            passed = (
                passed
                and bool(commands)
                and all(
                    item.get("status") == "passed" and item.get("exitCode") == 0
                    for item in commands
                )
            )
        return "PASS" if passed else "FAIL"

    def record_risk(self, gate: str, accepted_by: str, reason: str) -> dict[str, Any]:
        state = self.load()
        self._require_mutable(state)
        self._ensure_tool_config_current(state)
        self._ensure_no_waiting_dispatches(state, "Risk recording")
        current = state["gates"].get(gate)
        if not current or not current.get("valid") or current.get("result") != "FAIL":
            raise StateError("Risk can be accepted only for a failed gate on the current candidate")
        if not accepted_by.strip() or not reason.strip():
            raise StateError("Risk acceptance must record the user and reason")
        state["risks"].append(
            {
                "gate": gate,
                "gate_id": current["id"],
                "sha": state["candidate_sha"],
                "accepted_by": accepted_by,
                "reason": reason,
                "accepted_at": _now(),
            }
        )
        self._save(state, "record_risk", {"gate": gate, "accepted_by": accepted_by})
        return state

    @staticmethod
    def _both_gates_pass(state: dict[str, Any]) -> bool:
        sha = state.get("candidate_sha")
        return bool(sha) and all(
            state["gates"].get(name, {}).get("valid")
            and state["gates"][name].get("sha") == sha
            and state["gates"][name].get("result") == "PASS"
            for name in GATES
        )

    @staticmethod
    def _risks_cover_failures(state: dict[str, Any]) -> bool:
        sha = state.get("candidate_sha")
        if not sha or any(
            not state["gates"].get(name, {}).get("valid")
            or state["gates"][name].get("sha") != sha
            or state["gates"][name].get("result") not in GATE_RESULTS
            for name in GATES
        ):
            return False
        failures = {
            name
            for name in GATES
            if state["gates"][name].get("result") == "FAIL"
        }
        accepted = {
            (risk.get("gate"), risk.get("gate_id"))
            for risk in state["risks"]
            if risk.get("sha") == sha
        }
        return bool(failures) and all(
            (name, state["gates"][name].get("id")) in accepted for name in failures
        )

    def finish(self) -> dict[str, Any]:
        state = self.load()
        self._ensure_tool_config_current(state)
        self._ensure_no_waiting_dispatches(state, "Workflow finish")
        if state["status"] not in {"ready", "risk_accepted"}:
            raise StateError("The workflow has not met its finish conditions")
        if any(task["status"] != "done" for task in state["plan"]):
            raise StateError("The plan still has incomplete items")
        if state["status"] == "ready" and not self._both_gates_pass(state):
            raise StateError("The dual-gate state became invalid before finishing")
        if state["status"] == "risk_accepted" and not self._risks_cover_failures(state):
            raise StateError("Risk acceptance became invalid before finishing")
        self._ensure_audit_complete(state)
        self._ensure_candidate_current(state)
        state["status"] = "finished"
        state["active_task_id"] = None
        self._save(state, "finish", {})
        return state


def _json_argument(value: str) -> Any:
    path = Path(value)
    try:
        is_file = path.is_file()
    except OSError:
        is_file = False
    text = path.read_text(encoding="utf-8") if is_file else value
    return json.loads(text)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--plan", required=True, type=_json_argument)
    init.add_argument("--run-id")
    init.add_argument(
        "--profile",
        choices=("auto", *sorted(PROFILES)),
        default="auto",
    )
    init.add_argument("--risk-signals", type=_json_argument, default={})
    init.add_argument(
        "--document-language",
        choices=("en", "zh-CN"),
        default="en",
    )
    for name in ("show", "summary", "finish"):
        command = sub.add_parser(name)
        command.add_argument("run_id")
    transition = sub.add_parser("transition")
    transition.add_argument("run_id")
    transition.add_argument("status")
    transition.add_argument("--task-id")
    candidate = sub.add_parser("set-candidate")
    candidate.add_argument("run_id")
    candidate.add_argument("sha")
    gate = sub.add_parser("record-gate")
    gate.add_argument("run_id")
    gate.add_argument("gate", choices=sorted(GATES))
    gate.add_argument("sha")
    gate.add_argument("task_id")
    gate.add_argument("--agent-result", required=True, type=_json_argument)
    gate.add_argument("--before", required=True, type=_json_argument)
    gate.add_argument("--after", required=True, type=_json_argument)
    gate.add_argument("--allowed-path", action="append", default=[])
    gate.add_argument("--browser", action="store_true")
    gate.add_argument("--no-code", action="store_false", dest="code")
    risk = sub.add_parser("record-risk")
    risk.add_argument("run_id")
    risk.add_argument("gate", choices=sorted(GATES))
    risk.add_argument("--accepted-by", required=True)
    risk.add_argument("--reason", required=True)
    requirements = sub.add_parser("record-requirements")
    requirements.add_argument("run_id")
    requirements.add_argument(
        "--requirements",
        required=True,
        type=_json_argument,
    )
    task = sub.add_parser("set-task")
    task.add_argument("run_id")
    task.add_argument("task_id")
    task.add_argument("status")
    brief = sub.add_parser("record-brief")
    brief.add_argument("run_id")
    brief.add_argument("task_id")
    brief.add_argument("--brief", required=True, type=_json_argument)
    dispatch = sub.add_parser("record-dispatch")
    dispatch.add_argument("run_id")
    dispatch.add_argument("task_id")
    dispatch.add_argument("role", choices=sorted(DISPATCH_PHASES))
    dispatch.add_argument("--session-id", required=True)
    dispatch.add_argument("--before", required=True, type=_json_argument)
    dispatch.add_argument("--memory-capability", required=True)
    attempt = sub.add_parser("record-attempt")
    attempt.add_argument("run_id")
    attempt.add_argument("task_id")
    attempt.add_argument("role", choices=sorted(TASK_ROLES))
    attempt.add_argument("kind", choices=("initial", "retry", "repair"))
    attempt.add_argument("outcome", choices=("accepted", "rejected", "blocked"))
    attempt.add_argument("--agent-result", required=True, type=_json_argument)
    attempt.add_argument("--before", required=True, type=_json_argument)
    attempt.add_argument("--after", required=True, type=_json_argument)
    attempt.add_argument("--reason", required=True)
    attempt.add_argument("--dispatch-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "init":
            selection = select_profile(args.risk_signals, args.profile)
            store = WorkflowState.create(
                args.project,
                args.plan,
                args.run_id,
                require_contract=True,
                profile=selection["profile"],
                profile_selection=selection,
                document_language=args.document_language,
            )
            result = store.load()
        else:
            store = WorkflowState(args.project, args.run_id)
            if args.command == "show":
                result = store.load()
            elif args.command == "summary":
                result = store.summary()
            elif args.command == "transition":
                result = store.transition(args.status, args.task_id)
            elif args.command == "set-candidate":
                result = store.set_candidate(args.sha)
            elif args.command == "record-gate":
                result = store.record_gate(
                    args.gate,
                    args.sha,
                    args.task_id,
                    args.agent_result,
                    args.before,
                    args.after,
                    args.allowed_path,
                    args.browser,
                    args.code,
                )
            elif args.command == "record-risk":
                result = store.record_risk(args.gate, args.accepted_by, args.reason)
            elif args.command == "record-requirements":
                result = store.record_requirements(args.requirements)
            elif args.command == "set-task":
                result = store.set_task(args.task_id, args.status)
            elif args.command == "record-brief":
                result = store.record_brief(args.task_id, args.brief)
            elif args.command == "record-dispatch":
                result = store.record_dispatch(
                    args.task_id,
                    args.role,
                    args.session_id,
                    args.before,
                    args.memory_capability,
                )
            elif args.command == "record-attempt":
                result = store.record_attempt(
                    args.task_id,
                    args.role,
                    args.kind,
                    args.outcome,
                    args.agent_result,
                    args.before,
                    args.after,
                    args.reason,
                    args.dispatch_id,
                )
            else:
                result = store.finish()
        print(json.dumps({"ok": True, "state": result}, ensure_ascii=False, sort_keys=True))
        return 0
    except (StateError, ProfileError, json.JSONDecodeError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
