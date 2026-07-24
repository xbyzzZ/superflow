#!/usr/bin/env python3
"""Manage project-scoped, role-isolated Superflow memory."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


ROLES = (
    "product-manager",
    "architect",
    "ui-designer",
    "frontend-developer",
    "backend-developer",
    "tester",
    "code-reviewer",
)
CATEGORIES = ("constraint", "decision", "verified-pattern", "pitfall", "tool-fact")
IMPORTANCE = ("normal", "high")
RESULT_STATUSES = ("success", "partial", "failed", "blocked")
MAX_ACTIVE = 500
DEFAULT_LIMIT = 10
DEFAULT_MAX_BYTES = 8192
MAX_REQUESTS = 3
MAX_SUMMARY = 240
MAX_DETAIL = 1200
MAX_FUTURE_USE = 400
MAX_TAGS = 8
MAX_EVIDENCE_REFS = 8
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MEMORY_ID_RE = re.compile(r"^[a-f0-9]{16}$")
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._/-]*|[\u3400-\u4dbf\u4e00-\u9fff]", re.IGNORECASE)
SENSITIVE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(
        r"\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token)"
        r"\s*[:=]\s*[\"']?[^\s,\"']{6,}",
        re.IGNORECASE,
    ),
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
)


class MemoryError(RuntimeError):
    """A role-memory operation failed validation or a safety check."""


def _git(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(project), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def memory_root(project: Path) -> Path:
    """Resolve the memory directory shared by every worktree of one Git project."""
    result = _git(project, "rev-parse", "--git-common-dir")
    if result.returncode != 0 or not result.stdout.strip():
        raise MemoryError("The target project is not a Git repository")
    raw = Path(result.stdout.strip())
    common = (raw if raw.is_absolute() else project / raw).resolve()
    if not common.is_dir():
        raise MemoryError("Invalid Git common directory")
    root = common / "superflow" / "memory"
    current = common
    for part in root.relative_to(common).parts:
        current = current / part
        if current.is_symlink():
            raise MemoryError(f"Symlinked memory path is refused: {current}")
    return root


def _role(value: str) -> str:
    if value not in ROLES:
        raise MemoryError(f"Unknown role: {value}")
    return value


def _paths(project: Path, role: str) -> tuple[Path, Path, Path]:
    root = memory_root(project)
    return root / f"{role}.jsonl", root / "archive" / f"{role}.jsonl", root / ".lock"


def _capability_path(project: Path) -> Path:
    return memory_root(project) / ".capabilities.json"


@contextmanager
def _locked(project: Path, role: str) -> Iterator[tuple[Path, Path]]:
    active, archive, lock = _paths(project, _role(role))
    root = active.parent
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise MemoryError("Symlinked memory root is refused")
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        with os.fdopen(descriptor, "r+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield active, archive
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        pass


def _read_records(path: Path, role: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if path.is_symlink() or not path.is_file():
        raise MemoryError(f"Unsafe role-memory file: {path}")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise MemoryError(f"Unable to read role memory: {exc}") from exc
    for number, line in enumerate(lines, 1):
        if not line.strip():
            raise MemoryError(f"Blank line in role-memory journal at line {number}")
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MemoryError(f"Corrupt role-memory journal at line {number}") from exc
        _validate_record(record, role)
        if record["id"] in seen:
            raise MemoryError(f"Duplicate memory ID: {record['id']}")
        seen.add(record["id"])
        records.append(record)
    return records


def _atomic_records(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.parent.is_symlink():
        raise MemoryError(f"Unsafe role-memory path: {path}")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.parent.is_symlink():
        raise MemoryError(f"Unsafe role-memory path: {path}")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read_capabilities(project: Path) -> dict[str, Any]:
    path = _capability_path(project)
    if not path.exists():
        return {"schema_version": 1, "capabilities": {}}
    if path.is_symlink() or not path.is_file():
        raise MemoryError("Unsafe role-memory capability file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MemoryError("Corrupt role-memory capability file") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or not isinstance(value.get("capabilities"), dict)
    ):
        raise MemoryError("Invalid role-memory capability structure")
    for key, item in value["capabilities"].items():
        if not isinstance(key, str) or re.fullmatch(r"^[a-f0-9]{64}$", key) is None:
            raise MemoryError("Invalid role-memory capability hash")
        _validate_capability_record(item)
    return value


def _capability_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _validate_capability_record(item: Any) -> datetime:
    if (
        not isinstance(item, dict)
        or set(item) != {"role", "run_id", "task_id", "expires_at"}
        or item.get("role") not in ROLES
        or not all(
            isinstance(item.get(field), str) and RUN_ID_RE.fullmatch(item[field])
            for field in ("run_id", "task_id")
        )
    ):
        raise MemoryError("Invalid role-memory capability record")
    try:
        expiry = datetime.fromisoformat(item["expires_at"].replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise MemoryError("Invalid role-memory capability expiry") from exc
    if expiry.tzinfo is None:
        raise MemoryError("Role-memory capability expiry must include a timezone")
    return expiry


def issue_capability(
    project: Path,
    role: str,
    run_id: str,
    task_id: str,
    ttl_minutes: int = 1440,
) -> dict[str, Any]:
    """Issue a temporary role-bound read capability for one dispatched task."""
    role = _role(role)
    if (
        RUN_ID_RE.fullmatch(run_id) is None
        or RUN_ID_RE.fullmatch(task_id) is None
        or not 1 <= ttl_minutes <= 1440
    ):
        raise MemoryError("Invalid capability scope or lifetime")
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    with _locked(project, role):
        value = _read_capabilities(project)
        now = datetime.now(timezone.utc)
        retained: dict[str, Any] = {}
        for key, item in value["capabilities"].items():
            expiry = _validate_capability_record(item)
            if expiry > now:
                retained[key] = item
        retained[_capability_hash(token)] = {
            "role": role,
            "run_id": run_id,
            "task_id": task_id,
            "expires_at": expires.isoformat().replace("+00:00", "Z"),
        }
        value["capabilities"] = retained
        _atomic_json(_capability_path(project), value)
    return {
        "capability": token,
        "role": role,
        "run_id": run_id,
        "task_id": task_id,
        "expires_at": expires.isoformat().replace("+00:00", "Z"),
    }


def resolve_capability(project: Path, token: str) -> dict[str, str]:
    """Resolve one unexpired capability without exposing other role mappings."""
    if not isinstance(token, str) or len(token) < 32:
        raise MemoryError("Invalid role-memory capability")
    value = _read_capabilities(project)
    item = value["capabilities"].get(_capability_hash(token))
    if not isinstance(item, dict):
        raise MemoryError("Unknown role-memory capability")
    expires = _validate_capability_record(item)
    if expires <= datetime.now(timezone.utc):
        raise MemoryError("The role-memory capability has expired")
    return item


def revoke_capability(project: Path, token: str) -> dict[str, Any]:
    """Revoke one role-bound read capability after task execution."""
    key = _capability_hash(token)
    root = memory_root(project)
    lock = root / ".lock"
    root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "r+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        value = _read_capabilities(project)
        removed = value["capabilities"].pop(key, None)
        if removed is None:
            raise MemoryError("Unknown role-memory capability")
        _atomic_json(_capability_path(project), value)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return {"revoked": True, "role": removed["role"]}


def _text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MemoryError(f"{field} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise MemoryError(f"{field} exceeds {maximum} characters")
    return normalized


def _string_list(value: Any, field: str, maximum: int) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise MemoryError(f"{field} must be an array with at most {maximum} items")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise MemoryError(f"{field} must contain only non-empty strings")
        normalized = item.strip()
        if len(normalized) > 160:
            raise MemoryError(f"{field} contains an item longer than 160 characters")
        if normalized not in result:
            result.append(normalized)
    return result


def _assert_safe_content(request: dict[str, Any]) -> None:
    text = "\n".join(
        [
            request["summary"],
            request["detail"],
            request["futureUse"],
            *request["tags"],
            *request["evidenceRefs"],
        ]
    )
    if "```" in text or request["detail"].count("\n") > 7:
        raise MemoryError("Memory must not contain code fences, full logs, or large code excerpts")
    for pattern in SENSITIVE_PATTERNS:
        if pattern.search(text):
            raise MemoryError("Memory contains a credential or personal-data pattern")


def validate_request(value: Any) -> dict[str, Any]:
    """Validate and normalize one agent-proposed memory request."""
    if not isinstance(value, dict) or set(value) != {
        "category",
        "summary",
        "detail",
        "tags",
        "importance",
        "evidenceRefs",
        "futureUse",
        "supersedes",
    }:
        raise MemoryError("Invalid memory request structure")
    category = value.get("category")
    importance = value.get("importance")
    if category not in CATEGORIES:
        raise MemoryError(f"Invalid memory category: {category}")
    if importance not in IMPORTANCE:
        raise MemoryError(f"Invalid memory importance: {importance}")
    supersedes = _string_list(value.get("supersedes"), "supersedes", 8)
    if any(MEMORY_ID_RE.fullmatch(item) is None for item in supersedes):
        raise MemoryError("supersedes contains an invalid memory ID")
    request = {
        "category": category,
        "summary": _text(value.get("summary"), "summary", MAX_SUMMARY),
        "detail": _text(value.get("detail"), "detail", MAX_DETAIL),
        "tags": _string_list(value.get("tags"), "tags", MAX_TAGS),
        "importance": importance,
        "evidenceRefs": _string_list(
            value.get("evidenceRefs"), "evidenceRefs", MAX_EVIDENCE_REFS
        ),
        "futureUse": _text(value.get("futureUse"), "futureUse", MAX_FUTURE_USE),
        "supersedes": supersedes,
    }
    if not request["evidenceRefs"]:
        raise MemoryError("A memory request requires at least one evidence reference")
    _assert_safe_content(request)
    return request


def _validate_record(value: Any, expected_role: str) -> None:
    required = {
        "schema_version",
        "id",
        "role",
        "category",
        "summary",
        "detail",
        "tags",
        "importance",
        "evidence_refs",
        "future_use",
        "supersedes",
        "source",
        "created_at",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise MemoryError("Invalid role-memory record structure")
    if value.get("schema_version") != 1 or value.get("role") != expected_role:
        raise MemoryError("Role-memory record identity mismatch")
    if not isinstance(value.get("id"), str) or MEMORY_ID_RE.fullmatch(value["id"]) is None:
        raise MemoryError("Invalid role-memory record ID")
    request = {
        "category": value.get("category"),
        "summary": value.get("summary"),
        "detail": value.get("detail"),
        "tags": value.get("tags"),
        "importance": value.get("importance"),
        "evidenceRefs": value.get("evidence_refs"),
        "futureUse": value.get("future_use"),
        "supersedes": value.get("supersedes"),
    }
    validate_request(request)
    source = value.get("source")
    if (
        not isinstance(source, dict)
        or set(source) != {"run_id", "task_id", "result_status"}
        or source.get("result_status") not in RESULT_STATUSES
        or not all(
            isinstance(source.get(field), str) and RUN_ID_RE.fullmatch(source[field])
            for field in ("run_id", "task_id")
        )
    ):
        raise MemoryError("Invalid role-memory source")
    try:
        datetime.fromisoformat(value["created_at"].replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise MemoryError("Invalid role-memory timestamp") from exc


def _active(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    superseded = {
        memory_id
        for record in records
        for memory_id in record.get("supersedes", [])
    }
    return [record for record in records if record["id"] not in superseded]


def _deduplicate(
    records: list[dict[str, Any]], request: dict[str, Any]
) -> dict[str, Any] | None:
    for record in _active(records):
        if (
            record["category"] == request["category"]
            and record["summary"].casefold() == request["summary"].casefold()
            and record["detail"].casefold() == request["detail"].casefold()
        ):
            return record
    return None


def record_memory(
    project: Path,
    role: str,
    request: Any,
    run_id: str,
    task_id: str,
    result_status: str,
) -> dict[str, Any]:
    """Validate and append one role-bound memory record."""
    role = _role(role)
    normalized = validate_request(request)
    if RUN_ID_RE.fullmatch(run_id) is None or RUN_ID_RE.fullmatch(task_id) is None:
        raise MemoryError("Invalid run-id or task-id")
    if result_status not in RESULT_STATUSES:
        raise MemoryError("Invalid result status")
    with _locked(project, role) as (active_path, archive_path):
        records = _read_records(active_path, role)
        archived = _read_records(archive_path, role)
        duplicate = _deduplicate(records, normalized)
        if duplicate is not None:
            return {"action": "duplicate", "record": duplicate, "archived": 0}
        active_ids = {item["id"] for item in _active(records)}
        missing = set(normalized["supersedes"]) - active_ids
        if missing:
            raise MemoryError(
                "supersedes references unknown or inactive memory IDs: "
                + ", ".join(sorted(missing))
            )
        record = {
            "schema_version": 1,
            "id": "",
            "role": role,
            "category": normalized["category"],
            "summary": normalized["summary"],
            "detail": normalized["detail"],
            "tags": normalized["tags"],
            "importance": normalized["importance"],
            "evidence_refs": normalized["evidenceRefs"],
            "future_use": normalized["futureUse"],
            "supersedes": normalized["supersedes"],
            "source": {
                "run_id": run_id,
                "task_id": task_id,
                "result_status": result_status,
            },
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        known_ids = {item["id"] for item in records + archived}
        while not record["id"] or record["id"] in known_ids:
            record["id"] = secrets.token_hex(8)
        records.append(record)
        active_ids = {item["id"] for item in _active(records)}
        inactive = [item for item in records if item["id"] not in active_ids]
        active_records = [item for item in records if item["id"] in active_ids]
        overflow = max(0, len(active_records) - MAX_ACTIVE)
        if overflow:
            oldest = sorted(active_records, key=lambda item: (item["created_at"], item["id"]))[
                :overflow
            ]
            overflow_ids = {item["id"] for item in oldest}
            inactive.extend(oldest)
            active_records = [item for item in active_records if item["id"] not in overflow_ids]
        archived_by_id = {item["id"]: item for item in archived}
        for item in inactive:
            archived_by_id[item["id"]] = item
        _atomic_records(
            archive_path,
            sorted(archived_by_id.values(), key=lambda item: (item["created_at"], item["id"])),
        )
        _atomic_records(active_path, active_records)
        return {"action": "recorded", "record": record, "archived": len(inactive)}


def ingest_result(
    project: Path,
    role: str,
    run_id: str,
    result: Any,
) -> dict[str, Any]:
    """Ingest memory requests from an already accepted agent result."""
    role = _role(role)
    if not isinstance(result, dict) or result.get("role") != role:
        raise MemoryError("The result role does not match the authorized memory role")
    task_id = result.get("taskId")
    status = result.get("status")
    requests = result.get("memoryWriteRequests", [])
    if (
        not isinstance(task_id, str)
        or not isinstance(requests, list)
        or len(requests) > MAX_REQUESTS
        or status not in RESULT_STATUSES
    ):
        raise MemoryError("The agent result has an invalid memory request envelope")
    outcomes = [
        record_memory(project, role, item, run_id, task_id, status)
        for item in requests
    ]
    return {"role": role, "accepted": len(outcomes), "outcomes": outcomes}


def _tokens(value: str) -> set[str]:
    return {token.casefold() for token in TOKEN_RE.findall(value)}


def recall(
    project: Path,
    role: str,
    query: str,
    limit: int = DEFAULT_LIMIT,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    """Return only the authorized role's bounded, deterministic memory selection."""
    role = _role(role)
    if not 1 <= limit <= 50 or not 512 <= max_bytes <= 65536:
        raise MemoryError("Invalid recall limit or byte budget")
    with _locked(project, role) as (active_path, _):
        records = _active(_read_records(active_path, role))
    query_tokens = _tokens(query)
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for record in records:
        tags = _tokens(" ".join(record["tags"]))
        content = _tokens(
            " ".join(
                (
                    record["summary"],
                    record["detail"],
                    record["future_use"],
                    record["category"],
                )
            )
        )
        score = (
            (100 if record["importance"] == "high" else 0)
            + 12 * len(query_tokens & tags)
            + 4 * len(query_tokens & content)
        )
        ranked.append((score, record["created_at"], record))
    ranked.sort(key=lambda item: (item[0], item[1], item[2]["id"]), reverse=True)
    selected: list[dict[str, Any]] = []
    for _, _, record in ranked:
        if len(selected) >= limit:
            break
        candidate = {
            "id": record["id"],
            "category": record["category"],
            "summary": record["summary"],
            "detail": record["detail"],
            "tags": record["tags"],
            "importance": record["importance"],
            "futureUse": record["future_use"],
            "evidenceRefs": record["evidence_refs"],
        }
        payload = {"role": role, "memories": [*selected, candidate]}
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > max_bytes:
            continue
        selected.append(candidate)
    return {
        "role": role,
        "memories": selected,
        "available": len(records),
        "selected": len(selected),
        "truncated": len(selected) < len(records),
        "max_bytes": max_bytes,
    }


def recall_with_capability(
    project: Path,
    capability: str,
    query: str,
    limit: int = DEFAULT_LIMIT,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    """Recall memory for exactly the role bound to a temporary capability."""
    scope = resolve_capability(project, capability)
    result = recall(project, scope["role"], query, limit, max_bytes)
    result["run_id"] = scope["run_id"]
    result["task_id"] = scope["task_id"]
    return result


def list_memories(
    project: Path, role: str, include_archive: bool = False
) -> dict[str, Any]:
    role = _role(role)
    with _locked(project, role) as (active_path, archive_path):
        active = _active(_read_records(active_path, role))
        archived = _read_records(archive_path, role) if include_archive else []
    return {"role": role, "active": active, "archive": archived}


def view_memory(project: Path, role: str, memory_id: str) -> dict[str, Any]:
    role = _role(role)
    if MEMORY_ID_RE.fullmatch(memory_id) is None:
        raise MemoryError("Invalid memory ID")
    listed = list_memories(project, role, include_archive=True)
    for location in ("active", "archive"):
        for record in listed[location]:
            if record["id"] == memory_id:
                return {"role": role, "location": location, "record": record}
    raise MemoryError("Memory ID does not exist for the selected role")


def delete_memory(project: Path, role: str, memory_id: str) -> dict[str, Any]:
    role = _role(role)
    if MEMORY_ID_RE.fullmatch(memory_id) is None:
        raise MemoryError("Invalid memory ID")
    with _locked(project, role) as (active_path, archive_path):
        active = _read_records(active_path, role)
        archive = _read_records(archive_path, role)
        before = len(active) + len(archive)
        active = [item for item in active if item["id"] != memory_id]
        archive = [item for item in archive if item["id"] != memory_id]
        if len(active) + len(archive) == before:
            raise MemoryError("Memory ID does not exist for the selected role")
        _atomic_records(active_path, active)
        _atomic_records(archive_path, archive)
    return {"role": role, "deleted": memory_id}


def clear_memory(project: Path, role: str) -> dict[str, Any]:
    role = _role(role)
    with _locked(project, role) as (active_path, archive_path):
        count = len(_read_records(active_path, role)) + len(
            _read_records(archive_path, role)
        )
        _atomic_records(active_path, [])
        _atomic_records(archive_path, [])
    return {"role": role, "deleted": count}


def export_memory(project: Path, role: str, output: Path) -> dict[str, Any]:
    role = _role(role)
    value = {
        "schema_version": 1,
        "role": role,
        **list_memories(project, role, include_archive=True),
    }
    output = output.absolute()
    if output.exists() and (output.is_symlink() or not output.is_file()):
        raise MemoryError("Unsafe export destination")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {"role": role, "output": str(output), "exported": len(value["active"]) + len(value["archive"])}


def import_memory(project: Path, role: str, source: Path) -> dict[str, Any]:
    role = _role(role)
    if source.is_symlink() or not source.is_file():
        raise MemoryError("Unsafe import source")
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MemoryError("Invalid memory import file") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or value.get("role") != role
        or not isinstance(value.get("active"), list)
        or not isinstance(value.get("archive"), list)
    ):
        raise MemoryError("Memory import identity or structure mismatch")
    imported_active = value["active"]
    imported_archive = value["archive"]
    imported = imported_active + imported_archive
    imported_ids = [record.get("id") for record in imported if isinstance(record, dict)]
    if len(imported_ids) != len(set(imported_ids)):
        raise MemoryError("Memory import contains duplicate IDs")
    for record in imported:
        _validate_record(record, role)
    with _locked(project, role) as (active_path, archive_path):
        existing_active = _read_records(active_path, role)
        existing_archive = _read_records(archive_path, role)
        known = {item["id"] for item in existing_active + existing_archive}
        available_ids = known | set(imported_ids)
        missing_supersedes = {
            memory_id
            for item in imported
            for memory_id in item["supersedes"]
            if memory_id not in available_ids
        }
        if missing_supersedes:
            raise MemoryError(
                "Memory import supersedes unknown IDs: "
                + ", ".join(sorted(missing_supersedes))
            )
        new_active = [item for item in imported_active if item["id"] not in known]
        new_archive = [item for item in imported_archive if item["id"] not in known]
        combined = existing_active + new_active
        active_ids = {item["id"] for item in _active(combined)}
        active = [item for item in combined if item["id"] in active_ids]
        overflow = max(0, len(active) - MAX_ACTIVE)
        to_archive = [item for item in combined if item["id"] not in active_ids]
        if overflow:
            oldest = sorted(active, key=lambda item: (item["created_at"], item["id"]))[
                :overflow
            ]
            oldest_ids = {item["id"] for item in oldest}
            to_archive.extend(oldest)
            active = [item for item in active if item["id"] not in oldest_ids]
        archive_by_id = {item["id"]: item for item in existing_archive + new_archive}
        for item in to_archive:
            archive_by_id[item["id"]] = item
        _atomic_records(active_path, active)
        _atomic_records(
            archive_path,
            sorted(archive_by_id.values(), key=lambda item: (item["created_at"], item["id"])),
        )
    return {"role": role, "imported": len(new_active) + len(new_archive)}


def _json_value(value: str) -> Any:
    path = Path(value)
    text = path.read_text(encoding="utf-8") if path.is_file() else value
    return json.loads(text)


def _require_user_authorized(args: argparse.Namespace) -> None:
    if not args.user_authorized:
        raise MemoryError("This management operation requires explicit user authorization")


def _require_orchestrator_authorized(args: argparse.Namespace) -> None:
    if not args.orchestrator_authorized:
        raise MemoryError("This operation is reserved for the Superflow orchestrator")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)

    issue = sub.add_parser("issue-capability")
    issue.add_argument("--role", required=True, choices=ROLES)
    issue.add_argument("--run-id", required=True)
    issue.add_argument("--task-id", required=True)
    issue.add_argument("--ttl-minutes", type=int, default=1440)
    issue.add_argument("--orchestrator-authorized", action="store_true")

    recall_parser = sub.add_parser("recall")
    recall_parser.add_argument("--capability", required=True)
    recall_parser.add_argument("--query", required=True)
    recall_parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    recall_parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)

    revoke = sub.add_parser("revoke-capability")
    revoke.add_argument("--capability", required=True)
    revoke.add_argument("--orchestrator-authorized", action="store_true")

    record_parser = sub.add_parser("record")
    record_parser.add_argument("--role", required=True, choices=ROLES)
    record_parser.add_argument("--run-id", required=True)
    record_parser.add_argument("--task-id", required=True)
    record_parser.add_argument("--result-status", required=True, choices=RESULT_STATUSES)
    record_parser.add_argument("--request", required=True, type=_json_value)
    record_parser.add_argument("--orchestrator-authorized", action="store_true")

    ingest = sub.add_parser("ingest-result")
    ingest.add_argument("--role", required=True, choices=ROLES)
    ingest.add_argument("--run-id", required=True)
    ingest.add_argument("--result", required=True, type=_json_value)
    ingest.add_argument("--orchestrator-authorized", action="store_true")

    for name in ("list", "clear"):
        item = sub.add_parser(name)
        item.add_argument("--role", required=True, choices=ROLES)
        item.add_argument("--user-authorized", action="store_true")
        if name == "list":
            item.add_argument("--include-archive", action="store_true")
    delete = sub.add_parser("delete")
    delete.add_argument("--role", required=True, choices=ROLES)
    delete.add_argument("memory_id")
    delete.add_argument("--user-authorized", action="store_true")
    view = sub.add_parser("view")
    view.add_argument("--role", required=True, choices=ROLES)
    view.add_argument("memory_id")
    view.add_argument("--user-authorized", action="store_true")
    export = sub.add_parser("export")
    export.add_argument("--role", required=True, choices=ROLES)
    export.add_argument("--output", required=True, type=Path)
    export.add_argument("--user-authorized", action="store_true")
    import_parser = sub.add_parser("import")
    import_parser.add_argument("--role", required=True, choices=ROLES)
    import_parser.add_argument("--input", required=True, type=Path)
    import_parser.add_argument("--user-authorized", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "issue-capability":
            _require_orchestrator_authorized(args)
            result = issue_capability(
                args.project, args.role, args.run_id, args.task_id, args.ttl_minutes
            )
        elif args.command == "recall":
            result = recall_with_capability(
                args.project,
                args.capability,
                args.query,
                args.limit,
                args.max_bytes,
            )
        elif args.command == "revoke-capability":
            _require_orchestrator_authorized(args)
            result = revoke_capability(args.project, args.capability)
        elif args.command == "record":
            _require_orchestrator_authorized(args)
            result = record_memory(
                args.project,
                args.role,
                args.request,
                args.run_id,
                args.task_id,
                args.result_status,
            )
        elif args.command == "ingest-result":
            _require_orchestrator_authorized(args)
            result = ingest_result(args.project, args.role, args.run_id, args.result)
        else:
            _require_user_authorized(args)
            if args.command == "list":
                result = list_memories(args.project, args.role, args.include_archive)
            elif args.command == "view":
                result = view_memory(args.project, args.role, args.memory_id)
            elif args.command == "delete":
                result = delete_memory(args.project, args.role, args.memory_id)
            elif args.command == "clear":
                result = clear_memory(args.project, args.role)
            elif args.command == "export":
                result = export_memory(args.project, args.role, args.output)
            elif args.command == "import":
                result = import_memory(args.project, args.role, args.input)
            else:
                raise MemoryError("Unknown command")
        code = 0
        payload = {"ok": True, **result}
    except (MemoryError, OSError, json.JSONDecodeError) as exc:
        code = 2
        payload = {"ok": False, "error": str(exc)}
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
