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

    def _assert_safe_storage(self) -> None:
        for path in (
            self.storage_boundary,
            self.directory,
            self.state_path,
            self.events_path,
            self.lock_path,
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
    ) -> "WorkflowState":
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
            "active_task_id": None,
            "pending_repair": None,
            "plan": normalized_plan,
            "candidate_sha": None,
            "gates": {},
            "repair_rounds": {},
            "risks": [],
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
        task_ids = {item["id"] for item in state["plan"]}
        if len(task_ids) != len(state["plan"]):
            raise StateError("state.json contains duplicate task IDs")
        active = state.get("active_task_id")
        if active is not None and active not in task_ids:
            raise StateError("active_task_id is not part of the current plan")
        if not set(state["repair_rounds"]).issubset(task_ids):
            raise StateError("repair_rounds contains an unknown task")
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
            "uiPrototypeProvider",
            "beforeSnapshot",
            "resultSchema",
        }
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
        if not reason.strip():
            raise StateError("An attempt requires a reason")
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
            "recorded_at": _now(),
        }
        path = attempt_directory / f"{attempt_id}.json"
        _atomic_write(path, attempt)
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
        terminal = {"blocked", "cancelled", "finished"}
        emergency = target in {"blocked", "cancelled"} and source not in terminal
        if not emergency:
            self._ensure_tool_config_current(state)
        if not emergency and target not in TRANSITIONS.get(source, set()):
            raise StateError(f"Invalid state transition: {source} -> {target}")
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
        expected_role = "tester" if gate == "test" else "code-reviewer"
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
        )
        if not checked["ok"]:
            raise StateError(
                "The gate agent result failed the policy check: "
                + json.dumps(checked["violations"], ensure_ascii=False, sort_keys=True)
            )
        result = self._derive_gate_result(agent_result)
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
            "evidence": {"agent_result": agent_result, "policy": checked},
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
    def _derive_gate_result(agent_result: dict[str, Any]) -> str:
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
        if agent_result.get("role") == "tester":
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
    text = path.read_text(encoding="utf-8") if path.is_file() else value
    return json.loads(text)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--plan", required=True, type=_json_argument)
    init.add_argument("--run-id")
    for name in ("show", "finish"):
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
    task = sub.add_parser("set-task")
    task.add_argument("run_id")
    task.add_argument("task_id")
    task.add_argument("status")
    brief = sub.add_parser("record-brief")
    brief.add_argument("run_id")
    brief.add_argument("task_id")
    brief.add_argument("--brief", required=True, type=_json_argument)
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "init":
            store = WorkflowState.create(
                args.project,
                args.plan,
                args.run_id,
                require_contract=True,
            )
            result = store.load()
        else:
            store = WorkflowState(args.project, args.run_id)
            if args.command == "show":
                result = store.load()
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
            elif args.command == "set-task":
                result = store.set_task(args.task_id, args.status)
            elif args.command == "record-brief":
                result = store.record_brief(args.task_id, args.brief)
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
                )
            else:
                result = store.finish()
        print(json.dumps({"ok": True, "state": result}, ensure_ascii=False, sort_keys=True))
        return 0
    except (StateError, json.JSONDecodeError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
