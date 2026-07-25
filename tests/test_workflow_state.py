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
import role_memory  # noqa: E402


RUN_ID = "sf-20260724T010203Z-1234abcd"


def contracted_task(task_id: str = "t1", dependencies: list[str] | None = None) -> dict:
    return {
        "id": task_id,
        "title": "Implement feature",
        "role": "frontend-developer",
        "dependencies": dependencies or [],
        "authorizedPaths": ["src/**", "tests/**"],
        "acceptanceCriteria": ["The requested behavior is observable"],
        "verificationCommands": ["python3 -m unittest"],
        "observableResults": ["The focused regression test passes"],
    }


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
            "memoryRecall": {
                "status": "success",
                "available": 0,
                "selected": 0,
            },
            "workflowUpdateRequest": {
                "action": "none" if passed else "request-repair",
                "targetId": task_id,
                "reason": "Record the gate conclusion",
            },
            "concerns": [],
            "candidateSha": sha,
        }

    def developer_result(self, dispatch_id: str, task_id: str = "t1") -> dict:
        return {
            "role": "frontend-developer",
            "taskId": task_id,
            "dispatchId": dispatch_id,
            "status": "success",
            "summary": "Implemented and verified the assigned task",
            "filesChanged": [],
            "commandsRun": [
                {
                    "command": "python3 -m unittest",
                    "status": "passed",
                    "exitCode": 0,
                    "summary": "Focused tests passed",
                }
            ],
            "verification": {
                "status": "passed",
                "checks": [
                    {
                        "name": "acceptance",
                        "status": "passed",
                        "details": "The assigned behavior was verified",
                    }
                ],
                "verdicts": {
                    "spec": "pass",
                    "correctness": "pass",
                    "consistency": "pass",
                },
            },
            "findings": [],
            "evidence": [
                {
                    "type": "file",
                    "status": "success",
                    "reference": "tests/result.txt",
                    "detail": "The focused result was inspected",
                },
                {
                    "type": "codegraph",
                    "status": "failure",
                    "reference": "CodeGraph unavailable",
                    "detail": "The fallback reason was recorded before precise file reading",
                }
            ],
            "memoryWriteRequests": [],
            "memoryRecall": {
                "status": "success",
                "available": 0,
                "selected": 0,
            },
            "workflowUpdateRequest": {
                "action": "complete-task",
                "targetId": task_id,
                "reason": "The assigned work passed verification",
            },
            "concerns": [],
        }

    def valid_brief(
        self,
        store: workflow_state.WorkflowState,
        task_id: str = "t1",
        role: str = "frontend-developer",
    ) -> dict:
        state = store.load()
        task = next(item for item in state["plan"] if item["id"] == task_id)
        browser_provider = state["tool_config"]["browser"]["provider"]
        brief = {
            "runId": store.run_id,
            "taskId": task_id,
            "role": role,
            "workDirectory": str(self.project),
            "objective": "Implement the frozen behavior",
            "dependencies": task["dependencies"],
            "authorizedPaths": task["authorizedPaths"],
            "exclusions": [".git", ".codex"],
            "acceptanceCriteria": task["acceptanceCriteria"],
            "verificationCommands": task["verificationCommands"],
            "observableResults": task["observableResults"],
            "browserProvider": browser_provider,
            "browserRequired": False,
            "browserAccessMode": (
                "main-relay"
                if browser_provider == "codex-browser"
                else "specialist-direct"
            ),
            "uiPrototypeProvider": "penpot-mcp",
            "beforeSnapshot": store._git_snapshot(),
            "resultSchema": "assets/schemas/agent-result.schema.json",
            "roleMemoryScript": str((SCRIPTS / "role_memory.py").resolve()),
        }
        guide_by_role = {
            "architect": "architecture-design-rules.md",
            "ui-designer": "ui-ux-design-rules.md",
            "frontend-developer": "frontend-engineering-rules.md",
            "backend-developer": "backend-engineering-rules.md",
        }
        if role in guide_by_role:
            brief["builtinGuide"] = str(
                (SCRIPTS.parent / "references" / guide_by_role[role]).resolve()
            )
        return brief

    def make_tester_result(
        self,
        dispatch_id: str,
        candidate_sha: str,
        task_id: str = "t1",
    ) -> dict:
        result = self.gate_result("tester", "PASS", candidate_sha, task_id)
        result["dispatchId"] = dispatch_id
        return result

    def memory_capability(
        self,
        store: workflow_state.WorkflowState,
        role: str = "frontend-developer",
        task_id: str = "t1",
    ) -> str:
        return role_memory.issue_capability(
            self.project,
            role,
            store.run_id,
            task_id,
        )["capability"]

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
        gate_id = self.store.load()["gates"]["review"]["id"]
        self.assertTrue((self.store.directory / "gates" / f"{gate_id}.json").is_file())

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
                "collectorRole": "tester",
                "collectorTaskId": "t1",
                "collectorSession": "browser-session-1",
                "artifactSha256": "d" * 64,
                "adjudicatedBy": "tester",
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

    def test_failed_tester_command_cannot_derive_pass(self) -> None:
        result = self.gate_result("tester", "PASS", self.sha_one)
        result["commandsRun"].append(
            {
                "command": "npm run typecheck",
                "status": "failed",
                "exitCode": 2,
                "summary": "Type checking failed",
            }
        )
        self.assertEqual(
            workflow_state.WorkflowState._derive_gate_result(result),
            "FAIL",
        )

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
                self.store.set_task("t1", "in_progress")
        self.assertEqual(self.store.load()["status"], "initialized")
        plan = __import__("json").loads(
            (self.store.directory / "plan.json").read_text(encoding="utf-8")
        )
        self.assertEqual(plan["tasks"][0]["status"], "pending")

    def test_symlinked_workflow_directory_is_rejected(self) -> None:
        project = Path(self.temporary.name) / "symlink-repo"
        project.mkdir()
        subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        project_config.write_config(
            project,
            project_config.build_config("codex-browser", "penpot-mcp"),
        )
        common = workflow_state._git_common_directory(project)
        (common / "superflow").mkdir()
        (common / "superflow" / "workflows").symlink_to(
            outside,
            target_is_directory=True,
        )
        with self.assertRaises(workflow_state.StateError):
            workflow_state.WorkflowState.create(
                project,
                [{"id": "t1", "title": "Symlink"}],
                "sf-20260724T010206Z-1234abd0",
            )
        self.assertEqual(list(outside.iterdir()), [])

    def test_workflow_ledger_is_shared_by_linked_worktrees(self) -> None:
        linked = self.project.parent / f"{self.project.name}-linked-worktree"
        branch = f"{self.project.name}-linked-test"
        self.git("worktree", "add", "-q", "-b", branch, str(linked), "HEAD")
        try:
            linked_store = workflow_state.WorkflowState(linked, RUN_ID)

            self.assertEqual(linked_store.directory, self.store.directory)
            self.assertEqual(
                linked_store.load()["revision"], self.store.load()["revision"]
            )

            linked_store.set_task("t1", "in_progress")
            self.assertEqual(self.store.load()["plan"][0]["status"], "in_progress")
            plan_snapshot = __import__("json").loads(
                (self.store.directory / "plan.json").read_text(encoding="utf-8")
            )
            self.assertEqual(plan_snapshot["tasks"][0]["status"], "in_progress")
        finally:
            self.git("worktree", "remove", "--force", str(linked))

    def test_new_cli_plan_requires_a_complete_acyclic_contract(self) -> None:
        with self.assertRaisesRegex(workflow_state.StateError, "complete task contract"):
            workflow_state.WorkflowState.create(
                self.project,
                ["Underspecified task"],
                "sf-20260724T010207Z-1234abd1",
                require_contract=True,
            )

    def test_task_ids_and_authorized_paths_cannot_escape_audit_storage(self) -> None:
        for task_id in ("../escape", "nested/task"):
            with self.subTest(task_id=task_id), self.assertRaises(
                workflow_state.StateError
            ):
                workflow_state.WorkflowState.create(
                    self.project,
                    [{**contracted_task(), "id": task_id}],
                    "sf-20260724T010214Z-1234abd8",
                    require_contract=True,
                )
        for path in (".", ".git/config", ":(top)**"):
            with self.subTest(path=path), self.assertRaises(
                workflow_state.StateError
            ):
                workflow_state.WorkflowState.create(
                    self.project,
                    [{**contracted_task(), "authorizedPaths": [path]}],
                    "sf-20260724T010215Z-1234abd9",
                    require_contract=True,
                )
        with self.assertRaisesRegex(workflow_state.StateError, "cycle"):
            workflow_state.WorkflowState.create(
                self.project,
                [
                    contracted_task("t1", ["t2"]),
                    contracted_task("t2", ["t1"]),
                ],
                "sf-20260724T010208Z-1234abd2",
                require_contract=True,
            )

    def test_contracted_task_requires_brief_and_accepted_attempt(self) -> None:
        store = workflow_state.WorkflowState.create(
            self.project,
            [contracted_task()],
            "sf-20260724T010209Z-1234abd3",
            require_contract=True,
        )
        store.register_worktree(self.project, "main", "HEAD")
        for status in ("preflight", "discovery", "requirements_ready", "planned"):
            store.transition(status)
        store.transition("implementing", "t1")
        with self.assertRaisesRegex(workflow_state.StateError, "accepted audited attempt"):
            store.set_task("t1", "done")
        snapshot = store._git_snapshot()
        brief = self.valid_brief(store)
        store.record_brief("t1", brief)
        first_capability = self.memory_capability(store)
        dispatch = store.record_dispatch(
            "t1",
            "frontend-developer",
            "agent-session-1",
            snapshot,
            first_capability,
        )
        store.record_attempt(
            "t1",
            "frontend-developer",
            "initial",
            "rejected",
            {"status": "failed"},
            snapshot,
            snapshot,
            "The result omitted required evidence",
            dispatch["dispatch_id"],
        )
        self.assertEqual(
            len(list((store.directory / "attempts" / "t1").glob("*.json"))),
            1,
        )
        with self.assertRaisesRegex(workflow_state.StateError, "accepted audited attempt"):
            store.set_task("t1", "done")
        with self.assertRaisesRegex(workflow_state.StateError, "already used"):
            store.record_dispatch(
                "t1",
                "frontend-developer",
                "agent-session-reused-memory",
                snapshot,
                first_capability,
            )
        retry_dispatch = store.record_dispatch(
            "t1",
            "frontend-developer",
            "agent-session-2",
            snapshot,
            self.memory_capability(store),
        )
        store.record_attempt(
            "t1",
            "frontend-developer",
            "retry",
            "accepted",
            self.developer_result(retry_dispatch["dispatch_id"]),
            snapshot,
            snapshot,
            "The corrected result passed validation",
            retry_dispatch["dispatch_id"],
        )
        store.set_task("t1", "done")
        routing = __import__("json").loads(
            (store.directory / "routing.json").read_text(encoding="utf-8")
        )
        self.assertEqual(routing["assignments"][0]["task_id"], "t1")

    def test_waiting_dispatch_blocks_progress_until_bound_result_returns(self) -> None:
        store = workflow_state.WorkflowState.create(
            self.project,
            [contracted_task()],
            "sf-20260724T010217Z-1234abdb",
            require_contract=True,
        )
        store.register_worktree(self.project, "main", "HEAD")
        for status in ("preflight", "discovery", "requirements_ready", "planned"):
            store.transition(status)
        store.transition("implementing", "t1")
        snapshot = store._git_snapshot()
        brief = self.valid_brief(store)
        store.record_brief("t1", brief)
        dispatch = store.record_dispatch(
            "t1",
            "frontend-developer",
            "agent-session-waiting",
            snapshot,
            self.memory_capability(store),
        )

        with self.assertRaisesRegex(workflow_state.StateError, "waiting"):
            store.transition("verifying")
        with self.assertRaisesRegex(workflow_state.StateError, "waiting"):
            store.transition("cancelled")
        with self.assertRaisesRegex(workflow_state.StateError, "waiting"):
            store.set_task("t1", "done")
        with self.assertRaisesRegex(workflow_state.StateError, "dispatch"):
            store.record_attempt(
                "t1",
                "frontend-developer",
                "initial",
                "accepted",
                self.developer_result("wrong-dispatch"),
                snapshot,
                snapshot,
                "A fabricated result must not release the wait",
                "wrong-dispatch",
            )

        store.record_attempt(
            "t1",
            "frontend-developer",
            "initial",
            "accepted",
            self.developer_result(dispatch["dispatch_id"]),
            snapshot,
            snapshot,
            "The returned result passed validation",
            dispatch["dispatch_id"],
        )
        self.assertEqual(
            store.load()["dispatches"][dispatch["dispatch_id"]]["status"],
            "accepted",
        )
        store.set_task("t1", "done")

    def test_codex_browser_uses_closed_dispatch_relay_before_tester_adjudication(
        self,
    ) -> None:
        project_config.write_config(
            self.project,
            project_config.build_config("codex-browser", "penpot-mcp"),
        )
        task = {
            **contracted_task(),
            "role": "tester",
            "authorizedPaths": ["tests/**"],
        }
        store = workflow_state.WorkflowState.create(
            self.project,
            [task],
            "sf-20260724T010220Z-1234abde",
            require_contract=True,
        )
        store.register_worktree(self.project, "main", "HEAD")
        for status in ("preflight", "discovery", "requirements_ready", "planned"):
            store.transition(status)
        store.transition("implementing")
        store.transition("verifying", "t1")
        store.set_candidate(self.sha_one)
        brief = self.valid_brief(store, role="tester")
        brief["browserRequired"] = True
        store.record_brief("t1", brief)
        snapshot = store._git_snapshot()

        with self.assertRaisesRegex(workflow_state.StateError, "main-relay"):
            store.record_dispatch(
                "t1",
                "tester",
                "tester-session-without-relay",
                snapshot,
                self.memory_capability(store, role="tester"),
            )

        source = store.directory / "browser-relay-source.json"
        screenshot = store.directory / "browser-page.png"
        screenshot.write_bytes(b"browser screenshot fixture")
        source.write_text(
            __import__("json").dumps(
                {
                    "provider": "codex-browser",
                    "collectorRole": "product-manager",
                    "collectorTaskId": "t1",
                    "collectorSession": "main-browser-session-1",
                    "page": "http://localhost:8080/example",
                    "actions": ["Open the page", "Exercise the target interaction"],
                    "result": "The requested states were captured",
                    "artifacts": [
                        {
                            "kind": "screenshot",
                            "path": str(screenshot),
                        }
                    ],
                    "capturedAt": "2026-07-24T01:02:03Z",
                }
            ),
            encoding="utf-8",
        )
        dispatch = store.record_dispatch(
            "t1",
            "tester",
            "tester-session-with-relay",
            snapshot,
            self.memory_capability(store, role="tester"),
            str(source),
        )
        relay = dispatch["browser_evidence"]
        result = self.make_tester_result(dispatch["dispatch_id"], self.sha_one)
        result["evidence"].append(
            {
                "type": "browser",
                "status": "success",
                "provider": "codex-browser",
                "reference": relay["artifact_path"],
                "detail": "The tester independently adjudicated the relayed page evidence",
                "collectorRole": "product-manager",
                "collectorTaskId": "t1",
                "collectorSession": "main-browser-session-1",
                "artifactSha256": relay["artifact_sha256"],
                "adjudicatedBy": "tester",
            }
        )
        relay_path = Path(relay["artifact_path"])
        original_relay = relay_path.read_bytes()
        relay_path.write_text('{"tampered": true}\n', encoding="utf-8")
        with self.assertRaisesRegex(workflow_state.StateError, "dispatch artifact"):
            store.record_attempt(
                "t1",
                "tester",
                "retry",
                "accepted",
                result,
                snapshot,
                snapshot,
                "Tampered relay evidence must fail closed",
                dispatch["dispatch_id"],
            )
        relay_path.write_bytes(original_relay)
        manifest = __import__("json").loads(original_relay)
        copied_screenshot = Path(manifest["artifacts"][0]["path"])
        original_screenshot = copied_screenshot.read_bytes()
        copied_screenshot.write_bytes(b"tampered screenshot")
        with self.assertRaisesRegex(workflow_state.StateError, "dispatch artifact"):
            store.record_attempt(
                "t1",
                "tester",
                "retry",
                "accepted",
                result,
                snapshot,
                snapshot,
                "Tampered screenshot evidence must fail closed",
                dispatch["dispatch_id"],
            )
        copied_screenshot.write_bytes(original_screenshot)
        attempt = store.record_attempt(
            "t1",
            "tester",
            "retry",
            "accepted",
            result,
            snapshot,
            snapshot,
            "The tester adjudicated immutable relayed browser evidence",
            dispatch["dispatch_id"],
        )
        self.assertEqual(attempt["outcome"], "accepted")
        self.assertNotEqual(relay["artifact_path"], str(source))

    def test_brief_and_dispatch_require_role_memory_and_builtin_guide(self) -> None:
        store = workflow_state.WorkflowState.create(
            self.project,
            [contracted_task()],
            "sf-20260724T010219Z-1234abdd",
            require_contract=True,
        )
        valid = self.valid_brief(store)

        for field in ("roleMemoryScript", "builtinGuide"):
            with self.subTest(missing=field):
                incomplete = {key: value for key, value in valid.items() if key != field}
                with self.assertRaisesRegex(workflow_state.StateError, "incomplete"):
                    store.record_brief("t1", incomplete)

        wrong_script = dict(valid)
        wrong_script["roleMemoryScript"] = str(
            (SCRIPTS / "workflow_state.py").resolve()
        )
        with self.assertRaisesRegex(workflow_state.StateError, "memory script"):
            store.record_brief("t1", wrong_script)

        wrong_guide = dict(valid)
        wrong_guide["builtinGuide"] = str(
            (SCRIPTS.parent / "references" / "backend-engineering-rules.md").resolve()
        )
        with self.assertRaisesRegex(workflow_state.StateError, "built-in guide"):
            store.record_brief("t1", wrong_guide)

        recorded = store.record_brief("t1", valid)
        self.assertEqual(recorded["digest"], store._digest(valid))
        store.register_worktree(self.project, "main", "HEAD")
        for status in ("preflight", "discovery", "requirements_ready", "planned"):
            store.transition(status)
        store.transition("implementing", "t1")
        wrong_role_capability = self.memory_capability(
            store,
            role="backend-developer",
        )
        with self.assertRaisesRegex(workflow_state.StateError, "capability scope"):
            store.record_dispatch(
                "t1",
                "frontend-developer",
                "agent-session-wrong-memory-role",
                store._git_snapshot(),
                wrong_role_capability,
            )
        revoked_capability = self.memory_capability(store)
        role_memory.revoke_capability(
            self.project,
            revoked_capability,
        )
        with self.assertRaisesRegex(workflow_state.StateError, "capability is invalid"):
            store.record_dispatch(
                "t1",
                "frontend-developer",
                "agent-session-revoked-memory",
                store._git_snapshot(),
                revoked_capability,
            )
        live_capability = self.memory_capability(store)
        dispatch = store.record_dispatch(
            "t1",
            "frontend-developer",
            "agent-session-live-memory",
            store._git_snapshot(),
            live_capability,
        )
        self.assertNotIn(live_capability, dispatch.values())
        self.assertRegex(dispatch["memory_capability_digest"], r"^[a-f0-9]{64}$")

    def test_orphan_attempt_file_cannot_complete_a_task(self) -> None:
        store = workflow_state.WorkflowState.create(
            self.project,
            [contracted_task()],
            "sf-20260724T010212Z-1234abd6",
            require_contract=True,
        )
        orphan_directory = store.directory / "attempts" / "t1"
        orphan_directory.mkdir(parents=True)
        (orphan_directory / "t1-001-deadbeef.json").write_text(
            __import__("json").dumps(
                {
                    "attempt_id": "t1-001-deadbeef",
                    "outcome": "accepted",
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(workflow_state.StateError, "accepted audited attempt"):
            store.set_task("t1", "done")

    def test_task_cannot_start_before_dependencies_finish(self) -> None:
        store = workflow_state.WorkflowState.create(
            self.project,
            [
                {"id": "t1", "title": "First", "status": "pending"},
                {
                    **contracted_task("t2", ["t1"]),
                    "title": "Second",
                },
            ],
            "sf-20260724T010213Z-1234abd7",
        )

        with self.assertRaisesRegex(workflow_state.StateError, "dependencies"):
            store.set_task("t2", "in_progress")

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
