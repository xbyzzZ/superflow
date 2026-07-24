from __future__ import annotations

import sys
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import workflow_state  # noqa: E402
import project_config  # noqa: E402


RUN_ID = "sf-20260724T010203Z-1234abcd"
class WorkflowStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name)
        subprocess.run(["git", "-C", str(self.project), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(self.project), "config", "user.name", "Test User"], check=True)
        subprocess.run(
            ["git", "-C", str(self.project), "config", "user.email", "test@example.invalid"],
            check=True,
        )
        (self.project / "fixture.txt").write_text("one\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.project), "add", "fixture.txt"], check=True)
        subprocess.run(["git", "-C", str(self.project), "commit", "-qm", "Initial commit"], check=True)
        self.sha_one = self.git("rev-parse", "HEAD")
        project_config.write_config(
            self.project,
            project_config.build_config("chrome-mcp", "penpot-mcp"),
        )
        self.store = workflow_state.WorkflowState.create(
            self.project, [{"id": "t1", "title": "Implement feature"}], RUN_ID
        )

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.project), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        ).stdout.strip()

    def create_second_commit(self) -> str:
        (self.project / "fixture.txt").write_text("two\n", encoding="utf-8")
        self.git("add", "fixture.txt")
        self.git("commit", "-m", "Second commit")
        return self.git("rev-parse", "HEAD")

    def gate_result(self, role: str, verdict: str, sha: str, task_id: str = "t1") -> dict:
        passed = verdict == "PASS"
        return {
            "role": role,
            "taskId": task_id,
            "status": "success" if passed else "failed",
            "summary": "Gate passed" if passed else "Gate failed",
            "filesChanged": [],
            "commandsRun": (
                [
                    {
                        "command": "python3 -m unittest",
                        "status": "passed" if passed else "failed",
                        "exitCode": 0 if passed else 1,
                        "summary": "Tests complete",
                    }
                ]
                if role == "tester"
                else []
            ),
            "verification": {
                "status": "passed" if passed else "failed",
                "checks": [
                    {
                        "name": "gate",
                        "status": "passed" if passed else "failed",
                        "details": "Verified",
                    }
                ],
                "verdicts": {
                    "spec": "pass" if passed else "fail",
                    "correctness": "pass" if passed else "fail",
                    "consistency": "pass" if passed else "unknown",
                },
            },
            "findings": [],
            "evidence": [
                {
                    "type": "file",
                    "status": "success",
                    "reference": "tests/result.txt",
                    "detail": "Locatable results were inspected",
                },
                {
                    "type": "codegraph",
                    "status": "failure",
                    "reference": "Not configured",
                    "detail": "Fallback reason recorded",
                }
            ],
            "memoryWriteRequests": [],
            "workflowUpdateRequest": {
                "action": "none" if passed else "request-repair",
                "targetId": task_id,
                "reason": "Record the gate conclusion",
            },
            "concerns": [],
            "candidateSha": sha,
        }

    def record_gate(
        self, gate: str, verdict: str, sha: str | None = None, task_id: str = "t1"
    ):
        candidate = sha or self.sha_one
        snapshot = self.store._git_snapshot()
        role = "tester" if gate == "test" else "code-reviewer"
        return self.store.record_gate(
            gate,
            candidate,
            task_id,
            self.gate_result(role, verdict, candidate, task_id),
            snapshot,
            snapshot,
            ["tests/**"],
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def advance_to_verifying(self) -> None:
        self.store.transition("preflight")
        self.store.transition("discovery")
        self.store.transition("requirements_ready")
        self.store.transition("planned")
        self.store.transition("implementing", "t1")
        self.store.transition("verifying")
        self.store.set_task("t1", "done")

    def test_legal_and_illegal_transitions(self) -> None:
        self.assertEqual(self.store.transition("preflight")["status"], "preflight")
        with self.assertRaises(workflow_state.StateError):
            self.store.transition("finished")

    def test_dual_gate_requires_same_candidate_sha(self) -> None:
        self.advance_to_verifying()
        self.store.set_candidate(self.sha_one)
        self.record_gate("test", "PASS")
        with self.assertRaises(workflow_state.StateError):
            self.store.transition("ready")
        self.record_gate("review", "PASS")
        self.assertEqual(self.store.transition("ready")["status"], "ready")

    def test_browser_gate_uses_shared_project_provider(self) -> None:
        self.advance_to_verifying()
        self.store.set_candidate(self.sha_one)
        snapshot = self.store._git_snapshot()
        result = self.gate_result("tester", "PASS", self.sha_one)
        result["evidence"].append(
            {
                "type": "browser",
                "status": "success",
                "provider": "codex-browser",
                "reference": "Wrong browser",
                "detail": "The project selection was not used",
            }
        )
        with self.assertRaisesRegex(workflow_state.StateError, "chrome-mcp"):
            self.store.record_gate(
                "test",
                self.sha_one,
                "t1",
                result,
                snapshot,
                snapshot,
                ["tests/**"],
                browser=True,
            )
        result["evidence"][-1]["provider"] = "chrome-mcp"
        result["evidence"][-1]["reference"] = "Chrome MCP page session"
        state = self.store.record_gate(
            "test",
            self.sha_one,
            "t1",
            result,
            snapshot,
            snapshot,
            ["tests/**"],
            browser=True,
        )
        self.assertEqual(state["gates"]["test"]["result"], "PASS")

    def test_active_run_rejects_project_tool_reconfiguration(self) -> None:
        project_config.write_config(
            self.project,
            project_config.build_config("codex-browser", "codex-figma"),
        )
        with self.assertRaisesRegex(workflow_state.StateError, "old run cannot continue"):
            self.store.transition("preflight")
        self.assertEqual(self.store.transition("blocked")["status"], "blocked")

    def test_candidate_change_invalidates_existing_gates(self) -> None:
        self.advance_to_verifying()
        self.store.set_candidate(self.sha_one)
        self.record_gate("test", "PASS")
        sha_two = self.create_second_commit()
        state = self.store.set_candidate(sha_two)
        self.assertFalse(state["gates"]["test"]["valid"])
        with self.assertRaises(workflow_state.StateError):
            self.record_gate("review", "PASS", self.sha_one)

    def test_three_fix_round_limit(self) -> None:
        self.advance_to_verifying()
        self.store.set_candidate(self.sha_one)
        self.record_gate("test", "FAIL")
        for _ in range(3):
            self.store.transition("fixing", "t1")
            self.store.transition("verifying")
        with self.assertRaises(workflow_state.StateError):
            self.store.transition("fixing", "t1")
        self.assertEqual(self.store.load()["repair_rounds"]["t1"], 3)

    def test_third_round_failure_blocks_run(self) -> None:
        self.advance_to_verifying()
        self.store.set_candidate(self.sha_one)
        self.record_gate("test", "FAIL")
        for _ in range(3):
            self.store.transition("fixing", "t1")
            self.store.transition("verifying")
        state = self.record_gate("test", "FAIL")
        self.assertEqual(state["status"], "blocked")
        self.assertEqual(state["plan"][0]["status"], "blocked")

    def test_architecture_and_design_are_conditional(self) -> None:
        self.store.transition("preflight")
        self.store.transition("discovery")
        self.store.transition("requirements_ready")
        self.store.transition("architecting")
        self.assertEqual(self.store.transition("designing")["status"], "designing")
        self.assertEqual(self.store.transition("planned")["status"], "planned")

    def test_any_nonterminal_state_can_block_or_cancel(self) -> None:
        self.assertEqual(self.store.transition("blocked")["status"], "blocked")
        another = workflow_state.WorkflowState.create(
            self.project, [{"id": "t2", "title": "Cancel task"}], "sf-20260724T010204Z-1234abce"
        )
        self.assertEqual(another.transition("cancelled")["status"], "cancelled")

    def test_risk_acceptance_preserves_failed_gate(self) -> None:
        self.advance_to_verifying()
        self.store.set_candidate(self.sha_one)
        self.record_gate("test", "FAIL")
        self.store.record_risk("test", "user", "Accept known test failure")
        with self.assertRaises(workflow_state.StateError):
            self.store.transition("risk_accepted")
        self.record_gate("review", "PASS")
        state = self.store.transition("risk_accepted")
        self.assertEqual(state["gates"]["test"]["result"], "FAIL")
        self.assertTrue(state["gates"]["test"]["valid"])
        finished = self.store.finish()
        self.assertEqual(finished["status"], "finished")

    def test_finish_requires_plan_consistency(self) -> None:
        self.advance_to_verifying()
        self.store.set_task("t1", "in_progress")
        self.store.set_candidate(self.sha_one)
        self.record_gate("test", "PASS")
        self.record_gate("review", "PASS")
        with self.assertRaises(workflow_state.StateError):
            self.store.transition("ready")

    def test_ready_rejects_late_gate_mutation(self) -> None:
        self.advance_to_verifying()
        self.store.set_candidate(self.sha_one)
        self.record_gate("test", "PASS")
        self.record_gate("review", "PASS")
        self.store.transition("ready")
        with self.assertRaises(workflow_state.StateError):
            self.record_gate("review", "FAIL")

    def test_risk_is_bound_to_gate_instance(self) -> None:
        self.advance_to_verifying()
        self.store.set_candidate(self.sha_one)
        self.record_gate("test", "FAIL")
        self.store.record_risk("test", "user", "Accept the first failure")
        self.record_gate("test", "FAIL")
        self.record_gate("review", "PASS")
        with self.assertRaises(workflow_state.StateError):
            self.store.transition("risk_accepted")

    def test_candidate_must_be_current_git_head(self) -> None:
        self.advance_to_verifying()
        old_sha = self.sha_one
        self.create_second_commit()
        with self.assertRaises(workflow_state.StateError):
            self.store.set_candidate(old_sha)
        with self.assertRaises(workflow_state.StateError):
            self.store.set_candidate("deadbee")

    def test_corrupted_state_is_rejected_by_schema(self) -> None:
        state = self.store.load()
        state["unexpected"] = True
        self.store.state_path.write_text(__import__("json").dumps(state), encoding="utf-8")
        with self.assertRaises(workflow_state.StateError):
            self.store.load()

    def test_transition_cannot_bypass_finish_validation(self) -> None:
        self.advance_to_verifying()
        self.store.set_candidate(self.sha_one)
        self.record_gate("test", "PASS")
        self.record_gate("review", "PASS")
        self.store.transition("ready")
        with self.assertRaises(workflow_state.StateError):
            self.store.transition("finished")

    def test_gate_rechecks_head_and_clean_worktree(self) -> None:
        self.advance_to_verifying()
        self.store.set_candidate(self.sha_one)
        (self.project / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaises(workflow_state.StateError):
            self.record_gate("test", "PASS")
        (self.project / "dirty.txt").unlink()
        self.create_second_commit()
        with self.assertRaises(workflow_state.StateError):
            self.record_gate("test", "PASS", self.sha_one)

    def test_gate_failure_uses_bound_task_not_active_task(self) -> None:
        second = workflow_state.WorkflowState.create(
            self.project,
            [{"id": "t1", "title": "Task one"}, {"id": "t2", "title": "Task two"}],
            "sf-20260724T010205Z-1234abcf",
        )
        for status in ("preflight", "discovery", "requirements_ready", "planned"):
            second.transition(status)
        second.transition("implementing", "t1")
        second.transition("verifying")
        second.set_candidate(self.sha_one)
        snapshot = second._git_snapshot()
        result = self.gate_result("tester", "FAIL", self.sha_one, "t1")
        second.record_gate(
            "test", self.sha_one, "t1", result, snapshot, snapshot, ["tests/**"]
        )
        for _ in range(3):
            second.transition("fixing", "t1")
            second.transition("verifying")
        second.transition("reviewing")
        with self.assertRaises(workflow_state.StateError):
            second.transition("fixing", "t2")
        second.transition("verifying")
        snapshot = second._git_snapshot()
        result = self.gate_result("tester", "FAIL", self.sha_one, "t1")
        state = second.record_gate(
            "test", self.sha_one, "t1", result, snapshot, snapshot, ["tests/**"]
        )
        self.assertEqual(state["status"], "blocked")
        self.assertEqual(next(item for item in state["plan"] if item["id"] == "t1")["status"], "blocked")

    def test_terminal_state_is_immutable(self) -> None:
        self.advance_to_verifying()
        self.store.set_candidate(self.sha_one)
        self.record_gate("test", "PASS")
        self.record_gate("review", "PASS")
        self.store.transition("ready")
        self.store.finish()
        with self.assertRaises(workflow_state.StateError):
            self.store.set_task("t1", "pending")
        with self.assertRaises(workflow_state.StateError):
            self.store.set_candidate(self.sha_one)

    def test_events_corruption_is_rejected(self) -> None:
        with self.store.events_path.open("a", encoding="utf-8") as handle:
            handle.write("{broken\n")
        with self.assertRaises(workflow_state.StateError):
            self.store.load()

    def test_event_write_failure_rolls_back_state(self) -> None:
        original_open = Path.open

        def controlled_open(path, *args, **kwargs):
            mode = args[0] if args else kwargs.get("mode", "r")
            if path == self.store.events_path and "a" in mode:
                raise OSError("Simulated event write failure")
            return original_open(path, *args, **kwargs)

        with mock.patch.object(Path, "open", controlled_open):
            with self.assertRaises(OSError):
                self.store.transition("preflight")
        self.assertEqual(self.store.load()["status"], "initialized")

    def test_symlinked_workflow_directory_is_rejected(self) -> None:
        project = Path(self.temporary.name) / "symlink-repo"
        project.mkdir()
        subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        (project / ".codex").mkdir()
        (project / ".codex" / "workflows").symlink_to(outside, target_is_directory=True)
        project_config.write_config(
            project,
            project_config.build_config("codex-browser", "penpot-mcp"),
        )
        with self.assertRaises(workflow_state.StateError):
            workflow_state.WorkflowState.create(
                project,
                [{"id": "t1", "title": "Symlink"}],
                "sf-20260724T010206Z-1234abd0",
            )
        self.assertEqual(list(outside.iterdir()), [])

    def test_candidate_and_gate_require_correct_lifecycle(self) -> None:
        with self.assertRaises(workflow_state.StateError):
            self.store.set_candidate(self.sha_one)
        self.store.transition("preflight")
        self.store.transition("discovery")
        self.store.transition("requirements_ready")
        self.store.transition("planned")
        self.store.transition("implementing", "t1")
        self.store.set_candidate(self.sha_one)
        with self.assertRaises(workflow_state.StateError):
            self.record_gate("test", "PASS")

    def test_project_must_be_git_worktree_root(self) -> None:
        child = self.project / "child"
        child.mkdir()
        with self.assertRaises(workflow_state.StateError):
            workflow_state.WorkflowState(child, RUN_ID)

    def test_historical_event_tampering_breaks_hash_chain(self) -> None:
        self.store.transition("preflight")
        lines = self.store.events_path.read_text(encoding="utf-8").splitlines()
        first = __import__("json").loads(lines[0])
        first["detail"] = {"tampered": True}
        lines[0] = __import__("json").dumps(first, ensure_ascii=False, sort_keys=True)
        self.store.events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaises(workflow_state.StateError):
            self.store.load()

    def test_stale_revision_cannot_overwrite_newer_state(self) -> None:
        stale = self.store.load()
        self.store.transition("preflight")
        stale["status"] = "preflight"
        with self.assertRaises(workflow_state.StateError):
            self.store._save(stale, "stale", {})


if __name__ == "__main__":
    unittest.main()
