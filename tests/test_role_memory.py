from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import role_memory  # noqa: E402


def git(directory: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(directory), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout.strip()


def request(
    summary: str = "Reuse the verified request boundary",
    *,
    detail: str = "The boundary was verified by the focused contract test.",
    importance: str = "normal",
    tags: list[str] | None = None,
    supersedes: list[str] | None = None,
) -> dict:
    return {
        "category": "verified-pattern",
        "summary": summary,
        "detail": detail,
        "tags": tags or ["contract", "request"],
        "importance": importance,
        "evidenceRefs": ["tests/contracts/test_request.py"],
        "futureUse": "Recall when changing the same request boundary.",
        "supersedes": supersedes or [],
    }


class RoleMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        git(self.project, "init", "-q")
        git(self.project, "config", "user.name", "Test User")
        git(self.project, "config", "user.email", "test@example.invalid")
        (self.project / "fixture.txt").write_text("fixture\n", encoding="utf-8")
        git(self.project, "add", "fixture.txt")
        git(self.project, "commit", "-qm", "Initial commit")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def record(
        self,
        role: str = "frontend-developer",
        value: dict | None = None,
        status: str = "success",
    ) -> dict:
        return role_memory.record_memory(
            self.project,
            role,
            value or request(),
            "sf-20260724T010203Z-1234abcd",
            "task-1",
            status,
        )

    def test_git_common_memory_is_shared_by_linked_worktrees(self) -> None:
        worktree = self.root / "linked"
        git(self.project, "worktree", "add", "-q", "-b", "linked", str(worktree))
        self.assertEqual(
            role_memory.memory_root(self.project),
            role_memory.memory_root(worktree),
        )
        self.record()
        capability = role_memory.issue_capability(
            worktree,
            "frontend-developer",
            "sf-20260724T010203Z-1234abcd",
            "task-2",
        )["capability"]
        recalled = role_memory.recall_with_capability(
            worktree, capability, "request contract"
        )
        self.assertEqual(recalled["selected"], 1)

    def test_role_can_recall_its_own_memory_with_bound_capability(self) -> None:
        self.record()
        capability = role_memory.issue_capability(
            self.project,
            "frontend-developer",
            "sf-20260724T010203Z-1234abcd",
            "task-2",
        )["capability"]
        recalled = role_memory.recall_with_capability(
            self.project, capability, "contract request"
        )
        self.assertEqual(recalled["role"], "frontend-developer")
        self.assertEqual(recalled["task_id"], "task-2")
        self.assertEqual(recalled["memories"][0]["summary"], request()["summary"])

    def test_recall_is_read_only_and_does_not_create_a_lock(self) -> None:
        self.record("architect", request("Keep the verified boundary"))
        capability = role_memory.issue_capability(
            self.project,
            "architect",
            "sf-20260724T010203Z-1234abcd",
            "task-2",
        )["capability"]
        root = role_memory.memory_root(self.project)
        lock = root / ".lock"
        lock.unlink()
        original_mode = root.stat().st_mode
        root.chmod(0o500)
        try:
            recalled = role_memory.recall_with_capability(
                self.project,
                capability,
                "verified boundary",
            )
        finally:
            root.chmod(original_mode)

        self.assertEqual(recalled["selected"], 1)
        self.assertFalse(lock.exists())

    def test_capability_does_not_expose_another_role(self) -> None:
        self.record("frontend-developer")
        self.record("backend-developer", request("Preserve backend transaction order"))
        capability = role_memory.issue_capability(
            self.project,
            "frontend-developer",
            "sf-20260724T010203Z-1234abcd",
            "task-2",
        )["capability"]
        recalled = role_memory.recall_with_capability(
            self.project, capability, "backend transaction"
        )
        self.assertEqual(recalled["role"], "frontend-developer")
        self.assertNotIn(
            "Preserve backend transaction order",
            {item["summary"] for item in recalled["memories"]},
        )

    def test_revoked_and_unknown_capabilities_fail_closed(self) -> None:
        capability = role_memory.issue_capability(
            self.project,
            "tester",
            "sf-20260724T010203Z-1234abcd",
            "task-2",
        )["capability"]
        role_memory.revoke_capability(self.project, capability)
        with self.assertRaisesRegex(role_memory.MemoryError, "Unknown"):
            role_memory.recall_with_capability(self.project, capability, "tests")
        with self.assertRaises(role_memory.MemoryError):
            role_memory.recall_with_capability(self.project, "invalid", "tests")

    def test_expired_capability_fails_closed(self) -> None:
        capability = role_memory.issue_capability(
            self.project,
            "tester",
            "sf-20260724T010203Z-1234abcd",
            "task-2",
        )["capability"]
        path = role_memory._capability_path(self.project)
        value = json.loads(path.read_text(encoding="utf-8"))
        value["capabilities"][role_memory._capability_hash(capability)][
            "expires_at"
        ] = "2000-01-01T00:00:00Z"
        role_memory._atomic_json(path, value)
        with self.assertRaisesRegex(role_memory.MemoryError, "expired"):
            role_memory.recall_with_capability(self.project, capability, "tests")

    def test_failed_and_blocked_results_can_write_memory(self) -> None:
        for status in ("failed", "blocked"):
            with self.subTest(status=status):
                result = self.record(
                    "tester",
                    request(f"Investigate the {status} fixture"),
                    status,
                )
                self.assertEqual(result["action"], "recorded")
                self.assertEqual(result["record"]["source"]["result_status"], status)

    def test_sensitive_or_large_content_is_rejected_without_writing(self) -> None:
        unsafe = request(detail="api_key=super-secret-value")
        with self.assertRaisesRegex(role_memory.MemoryError, "credential"):
            self.record(value=unsafe)
        active, _, _ = role_memory._paths(self.project, "frontend-developer")
        self.assertFalse(active.exists())
        with self.assertRaisesRegex(role_memory.MemoryError, "code fences"):
            self.record(value=request(detail="```python\nprint('x')\n```"))

    def test_exact_duplicate_is_not_appended(self) -> None:
        first = self.record()
        second = self.record()
        self.assertEqual(first["action"], "recorded")
        self.assertEqual(second["action"], "duplicate")
        listed = role_memory.list_memories(self.project, "frontend-developer")
        self.assertEqual(len(listed["active"]), 1)

    def test_superseding_memory_moves_old_record_to_archive(self) -> None:
        old = self.record()["record"]
        new = self.record(
            value=request(
                "Use the revised request boundary",
                supersedes=[old["id"]],
            )
        )["record"]
        listed = role_memory.list_memories(
            self.project, "frontend-developer", include_archive=True
        )
        self.assertEqual([item["id"] for item in listed["active"]], [new["id"]])
        self.assertIn(old["id"], {item["id"] for item in listed["archive"]})

    def test_supersedes_cannot_reference_other_role(self) -> None:
        other = self.record("backend-developer")["record"]
        with self.assertRaisesRegex(role_memory.MemoryError, "unknown or inactive"):
            self.record(value=request("Invalid cross-role revision", supersedes=[other["id"]]))

    def test_capacity_archives_oldest_active_records(self) -> None:
        with mock.patch.object(role_memory, "MAX_ACTIVE", 2):
            for index in range(3):
                self.record(value=request(f"Pattern {index}"))
        listed = role_memory.list_memories(
            self.project, "frontend-developer", include_archive=True
        )
        self.assertEqual(len(listed["active"]), 2)
        self.assertEqual(len(listed["archive"]), 1)

    def test_recall_prioritizes_importance_and_relevance_with_budget(self) -> None:
        self.record(value=request("Recent unrelated item", tags=["misc"]))
        self.record(
            value=request(
                "Critical authorization constraint",
                importance="high",
                tags=["authorization"],
            )
        )
        capability = role_memory.issue_capability(
            self.project,
            "frontend-developer",
            "sf-20260724T010203Z-1234abcd",
            "task-2",
        )["capability"]
        recalled = role_memory.recall_with_capability(
            self.project,
            capability,
            "authorization",
            limit=1,
            max_bytes=1024,
        )
        self.assertEqual(
            recalled["memories"][0]["summary"],
            "Critical authorization constraint",
        )
        self.assertTrue(recalled["truncated"])

    def test_corrupt_journal_fails_closed(self) -> None:
        self.record()
        active, _, _ = role_memory._paths(self.project, "frontend-developer")
        active.write_text("{not-json\n", encoding="utf-8")
        with self.assertRaisesRegex(role_memory.MemoryError, "Corrupt"):
            role_memory.list_memories(self.project, "frontend-developer")

    def test_symlinked_memory_directory_is_rejected(self) -> None:
        common = Path(git(self.project, "rev-parse", "--git-common-dir"))
        if not common.is_absolute():
            common = self.project / common
        outside = self.root / "outside"
        outside.mkdir()
        (common / "superflow").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(role_memory.MemoryError, "Symlinked"):
            role_memory.memory_root(self.project)

    def test_concurrent_cli_writes_remain_valid(self) -> None:
        processes = []
        for index in range(6):
            value = request(f"Concurrent pattern {index}")
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        str(SCRIPTS / "role_memory.py"),
                        "--project",
                        str(self.project),
                        "record",
                        "--role",
                        "backend-developer",
                        "--run-id",
                        "sf-20260724T010203Z-1234abcd",
                        "--task-id",
                        f"task-{index}",
                        "--result-status",
                        "success",
                        "--request",
                        json.dumps(value),
                        "--orchestrator-authorized",
                    ],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            )
        results = [process.communicate(timeout=20) for process in processes]
        self.assertTrue(
            all(process.returncode == 0 for process in processes),
            results,
        )
        listed = role_memory.list_memories(self.project, "backend-developer")
        self.assertEqual(len(listed["active"]), 6)
        self.assertEqual(
            os.stat(role_memory._paths(self.project, "backend-developer")[0]).st_mode
            & 0o777,
            0o600,
        )

    def test_ingest_result_checks_role_identity(self) -> None:
        result = {
            "role": "frontend-developer",
            "taskId": "task-1",
            "status": "partial",
            "memoryWriteRequests": [request()],
        }
        ingested = role_memory.ingest_result(
            self.project,
            "frontend-developer",
            "sf-20260724T010203Z-1234abcd",
            result,
        )
        self.assertEqual(ingested["accepted"], 1)
        with self.assertRaisesRegex(role_memory.MemoryError, "does not match"):
            role_memory.ingest_result(
                self.project,
                "backend-developer",
                "sf-20260724T010203Z-1234abcd",
                result,
            )

    def test_legacy_result_without_requests_ingests_nothing(self) -> None:
        result = {
            "role": "architect",
            "taskId": "task-1",
            "status": "success",
        }
        ingested = role_memory.ingest_result(
            self.project,
            "architect",
            "sf-20260724T010203Z-1234abcd",
            result,
        )
        self.assertEqual(ingested["accepted"], 0)

    def test_user_management_requires_explicit_cli_flag(self) -> None:
        process = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "role_memory.py"),
                "--project",
                str(self.project),
                "list",
                "--role",
                "tester",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(process.returncode, 2)
        self.assertIn("explicit user authorization", process.stdout)

    def test_export_import_and_delete_are_role_bound(self) -> None:
        record = self.record("architect")["record"]
        destination = self.root / "architect-memory.json"
        role_memory.export_memory(self.project, "architect", destination)
        role_memory.clear_memory(self.project, "architect")
        imported = role_memory.import_memory(
            self.project, "architect", destination
        )
        self.assertEqual(imported["imported"], 1)
        viewed = role_memory.view_memory(self.project, "architect", record["id"])
        self.assertEqual(viewed["record"]["role"], "architect")
        role_memory.delete_memory(self.project, "architect", record["id"])
        self.assertEqual(
            role_memory.list_memories(self.project, "architect")["active"], []
        )
        with self.assertRaisesRegex(role_memory.MemoryError, "identity"):
            role_memory.import_memory(
                self.project, "backend-developer", destination
            )

    def test_orchestrator_commands_require_explicit_cli_flag(self) -> None:
        process = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "role_memory.py"),
                "--project",
                str(self.project),
                "issue-capability",
                "--role",
                "tester",
                "--run-id",
                "sf-20260724T010203Z-1234abcd",
                "--task-id",
                "task-1",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(process.returncode, 2)
        self.assertIn("reserved for the Superflow orchestrator", process.stdout)


if __name__ == "__main__":
    unittest.main()
