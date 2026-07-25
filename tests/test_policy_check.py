from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import policy_check  # noqa: E402


SHA = "a" * 40
SNAPSHOT = {
    "head": SHA,
    "refs": {"refs/heads/main": SHA},
    "index_entries": "b" * 64,
    "index_flags": "c" * 64,
    "status": [],
}


def base_result(**overrides):
    value = {
        "role": "frontend-developer",
        "taskId": "task-1",
        "status": "success",
        "summary": "Implementation complete",
        "filesChanged": ["src/example.ts"],
        "commandsRun": [
            {"command": "npm test", "status": "passed", "exitCode": 0, "summary": "Passed"}
        ],
        "verification": {
            "status": "passed",
            "checks": [{"name": "tests", "status": "passed", "details": "Passed"}],
            "verdicts": {"spec": "pass", "correctness": "pass", "consistency": "pass"},
        },
        "findings": [],
        "evidence": [
            {
                "type": "codegraph",
                "status": "success",
                "reference": "AuthService",
                "detail": "The call path was inspected",
            }
        ],
        "memoryWriteRequests": [],
        "workflowUpdateRequest": {"action": "none", "targetId": None, "reason": "No update required"},
        "concerns": [],
    }
    value.update(overrides)
    return value


class PolicyCheckTests(unittest.TestCase):
    def check(self, result, **flags):
        return policy_check.check_policy(
            result,
            ["src/**", "tests/**"],
            flags.pop("before", SNAPSHOT),
            flags.pop("after", SNAPSHOT),
            **flags,
        )

    def test_valid_result(self) -> None:
        checked = self.check(
            base_result(),
            expected_role="frontend-developer",
            expected_task="task-1",
            code=True,
        )
        self.assertTrue(checked["ok"], checked["violations"])

    def test_result_must_match_recorded_dispatch(self) -> None:
        dispatch_id = "1" * 16
        checked = self.check(
            base_result(
                dispatchId=dispatch_id,
                memoryRecall={"status": "success", "available": 0, "selected": 0},
            ),
            expected_role="frontend-developer",
            expected_task="task-1",
            expected_dispatch=dispatch_id,
            code=True,
        )
        self.assertTrue(checked["ok"], checked["violations"])

        mismatched = self.check(
            base_result(
                dispatchId="2" * 16,
                memoryRecall={"status": "success", "available": 0, "selected": 0},
            ),
            expected_dispatch=dispatch_id,
        )
        self.assertIn("identity", {item["code"] for item in mismatched["violations"]})

        missing_recall = self.check(
            base_result(dispatchId=dispatch_id),
            expected_dispatch=dispatch_id,
        )
        self.assertIn(
            "memory_recall",
            {item["code"] for item in missing_recall["violations"]},
        )

    def test_legacy_result_without_memory_requests_remains_valid(self) -> None:
        result = base_result()
        result.pop("memoryWriteRequests")
        checked = self.check(
            result,
            expected_role="frontend-developer",
            expected_task="task-1",
            code=True,
        )
        self.assertTrue(checked["ok"], checked["violations"])

    def test_tester_out_of_bounds_is_rejected(self) -> None:
        result = base_result(role="tester", filesChanged=["src/product.ts"])
        checked = self.check(result, expected_role="tester")
        self.assertIn("tester_scope", {item["code"] for item in checked["violations"]})

    def test_forbidden_paths_and_git_snapshot_changes_are_rejected(self) -> None:
        result = base_result(filesChanged=[".codex/local.json"])
        after = {"head": "b" * 40, "refs": {"refs/heads/main": "b" * 40}}
        checked = self.check(result, after=after)
        codes = {item["code"] for item in checked["violations"]}
        self.assertIn("forbidden_path", codes)
        self.assertIn("head_changed", codes)
        self.assertIn("refs_changed", codes)

    def test_mutating_git_command_is_rejected(self) -> None:
        result = base_result(
            commandsRun=[{"command": "git commit -m bad", "status": "passed", "exitCode": 0, "summary": "Commit"}]
        )
        checked = self.check(result)
        self.assertIn("git_authority", {item["code"] for item in checked["violations"]})

    def test_allowlisted_read_only_git_commands_are_allowed(self) -> None:
        for command in (
            "git cat-file -t HEAD",
            "git diff --stat",
            "git grep pattern",
            "git log -1 --oneline",
            "git ls-files",
            "git rev-parse HEAD",
            "git show --stat HEAD",
            "git status --short",
            "git -C . rev-parse HEAD",
        ):
            result = base_result(
                commandsRun=[
                    {
                        "command": command,
                        "status": "passed",
                        "exitCode": 0,
                        "summary": "Read-only Git evidence",
                    }
                ]
            )
            checked = self.check(result)
            self.assertNotIn(
                "git_authority",
                {item["code"] for item in checked["violations"]},
                command,
            )

    def test_wrapped_mutating_git_commands_are_rejected(self) -> None:
        for command in (
            "sh -c 'git reset --hard'",
            "env git push",
            "/usr/bin/git commit -m bad",
            "git -C . commit -m bad",
            "git update-ref refs/heads/main HEAD~1",
            "git update-index --add file",
            "python -c \"import subprocess; subprocess.run(['git','commit'])\"",
            "G=git; $G update-index --assume-unchanged file",
        ):
            result = base_result(
                commandsRun=[
                    {"command": command, "status": "passed", "exitCode": 0, "summary": "Executed"}
                ]
            )
            checked = self.check(result)
            self.assertIn(
                "git_authority",
                {item["code"] for item in checked["violations"]},
                command,
            )

    def test_required_tool_evidence(self) -> None:
        result = base_result(evidence=[])
        checked = self.check(
            result,
            ui=True,
            browser=True,
            code=True,
            expected_ui_provider="codex-figma",
            expected_browser_provider="chrome-mcp",
        )
        messages = [item for item in checked["violations"] if item["code"] == "tool_evidence"]
        self.assertEqual(len(messages), 3)

    def test_failure_text_cannot_impersonate_successful_tool_evidence(self) -> None:
        result = base_result(
            evidence=[
                {
                    "type": "other",
                    "status": "success",
                    "reference": "Penpot failed; Browser unavailable",
                    "detail": "All providers were unavailable",
                }
            ]
        )
        checked = self.check(
            result,
            ui=True,
            browser=True,
            expected_ui_provider="penpot-mcp",
            expected_browser_provider="codex-browser",
        )
        self.assertEqual(
            2,
            sum(item["code"] == "tool_evidence" for item in checked["violations"]),
        )

    def test_typed_success_evidence_cannot_contradict_its_text(self) -> None:
        result = base_result(
            evidence=[
                {
                    "type": "penpot",
                    "status": "success",
                    "provider": "penpot-mcp",
                    "reference": "Penpot connection failed",
                    "detail": "Permission denied; design was not saved",
                }
            ]
        )
        checked = self.check(
            result,
            expected_role="frontend-developer",
            expected_task="task-1",
            ui=True,
            expected_ui_provider="penpot-mcp",
        )
        self.assertIn("evidence_consistency", {item["code"] for item in checked["violations"]})

    def test_tool_evidence_must_match_project_provider(self) -> None:
        result = base_result(
            evidence=[
                {
                    "type": "ui-prototype",
                    "status": "success",
                    "provider": "penpot-mcp",
                    "reference": "Penpot board",
                    "detail": "Prototype completed",
                },
                {
                    "type": "browser",
                    "status": "success",
                    "provider": "codex-browser",
                    "reference": "Built-in browser",
                    "detail": "Page acceptance completed",
                },
            ]
        )
        checked = self.check(
            result,
            expected_role="frontend-developer",
            expected_task="task-1",
            ui=True,
            browser=True,
            expected_ui_provider="codex-figma",
            expected_browser_provider="chrome-mcp",
        )
        self.assertEqual(
            2,
            sum(item["code"] == "tool_evidence" for item in checked["violations"]),
        )

    def test_tool_tasks_require_project_provider_context(self) -> None:
        checked = self.check(base_result(), ui=True, browser=True)
        self.assertEqual(
            2,
            sum(item["code"] == "tool_context" for item in checked["violations"]),
        )

    def test_read_only_roles_cannot_report_local_changes(self) -> None:
        result = base_result(role="code-reviewer", candidateSha=SHA)
        checked = self.check(
            result,
            expected_role="code-reviewer",
            expected_task="task-1",
            expected_candidate=SHA,
        )
        self.assertIn("read_only_role", {item["code"] for item in checked["violations"]})

    def test_success_result_must_be_semantically_consistent(self) -> None:
        result = base_result(
            verification={
                "status": "failed",
                "checks": [{"name": "tests", "status": "failed", "details": "Failed"}],
                "verdicts": {"spec": "fail", "correctness": "unknown", "consistency": "pass"},
            },
            workflowUpdateRequest={"action": "block-run", "targetId": "task-1", "reason": "Blocked"},
        )
        checked = self.check(
            result,
            expected_role="frontend-developer",
            expected_task="task-1",
        )
        self.assertIn("result_consistency", {item["code"] for item in checked["violations"]})

    def test_gate_role_must_bind_candidate(self) -> None:
        result = base_result(role="code-reviewer", filesChanged=[], candidateSha="b" * 40)
        checked = self.check(
            result,
            expected_role="code-reviewer",
            expected_candidate=SHA,
        )
        self.assertIn("candidate_sha", {item["code"] for item in checked["violations"]})

    def test_gate_role_requires_schema_field_and_expected_context(self) -> None:
        result = base_result(role="tester", filesChanged=[])
        checked = self.check(result, expected_role="tester")
        codes = {item["code"] for item in checked["violations"]}
        self.assertIn("schema", codes)
        self.assertIn("candidate_context", codes)

    def test_gate_pass_rejects_empty_verification_and_evidence(self) -> None:
        result = base_result(
            role="tester",
            candidateSha=SHA,
            commandsRun=[],
            verification={
                "status": "passed",
                "checks": [],
                "verdicts": {"spec": "pass", "correctness": "pass", "consistency": "pass"},
            },
            evidence=[],
        )
        checked = self.check(
            result,
            expected_role="tester",
            expected_task="task-1",
            expected_candidate=SHA,
        )
        codes = {item["code"] for item in checked["violations"]}
        self.assertIn("schema", codes)
        self.assertIn("gate_evidence", codes)

    def test_gate_pass_rejects_any_failed_command(self) -> None:
        result = base_result(
            role="tester",
            filesChanged=[],
            candidateSha=SHA,
            commandsRun=[
                {
                    "command": "python3 -m unittest",
                    "status": "passed",
                    "exitCode": 0,
                    "summary": "Unit tests passed",
                },
                {
                    "command": "npm run typecheck",
                    "status": "failed",
                    "exitCode": 2,
                    "summary": "Type checking failed in a dependency",
                },
            ],
        )
        checked = self.check(
            result,
            expected_role="tester",
            expected_task="task-1",
            expected_candidate=SHA,
        )
        self.assertIn(
            "failed_gate_command",
            {item["code"] for item in checked["violations"]},
        )

    def test_browser_evidence_requires_collector_and_adjudicator_provenance(self) -> None:
        result = base_result(
            role="tester",
            filesChanged=[],
            candidateSha=SHA,
            evidence=[
                {
                    "type": "browser",
                    "status": "success",
                    "provider": "codex-browser",
                    "reference": "Captured browser session",
                    "detail": "The page behavior passed",
                }
            ],
        )
        checked = self.check(
            result,
            expected_role="tester",
            expected_task="task-1",
            expected_candidate=SHA,
            browser=True,
            expected_browser_provider="codex-browser",
        )
        self.assertIn(
            "evidence_provenance",
            {item["code"] for item in checked["violations"]},
        )

        result["evidence"][0].update(
            {
                "collectorRole": "product-manager",
                "collectorTaskId": "task-1",
                "collectorSession": "browser-session-1",
                "artifactSha256": "d" * 64,
                "adjudicatedBy": "tester",
            }
        )
        accepted = self.check(
            result,
            expected_role="tester",
            expected_task="task-1",
            expected_candidate=SHA,
            browser=True,
            expected_browser_provider="codex-browser",
        )
        self.assertTrue(accepted["ok"], accepted["violations"])

    def test_browser_evidence_request_cannot_claim_success(self) -> None:
        result = base_result(
            role="tester",
            filesChanged=[],
            candidateSha=SHA,
            browserEvidenceRequest={
                "provider": "codex-browser",
                "page": "http://localhost:8080/example",
                "actions": ["Open the page"],
                "viewports": ["180x800"],
                "requiredArtifacts": ["screenshot", "interaction-log"],
                "reason": "The selected session is not available to this tester",
            },
        )
        checked = self.check(
            result,
            expected_role="tester",
            expected_task="task-1",
            expected_candidate=SHA,
            expected_browser_provider="codex-browser",
        )
        self.assertIn(
            "browser_evidence_request",
            {item["code"] for item in checked["violations"]},
        )

    def test_frontend_may_request_unavailable_direct_browser(self) -> None:
        result = base_result(
            status="blocked",
            filesChanged=[],
            browserEvidenceRequest={
                "provider": "chrome-mcp",
                "page": "http://localhost:8080/example",
                "actions": ["Open the page", "Reproduce the defect"],
                "viewports": ["1280x800"],
                "requiredArtifacts": ["screenshot", "console-log"],
                "reason": "The selected direct provider is unavailable in this session",
            },
        )
        checked = self.check(
            result,
            expected_role="frontend-developer",
            expected_task="task-1",
            expected_browser_provider="chrome-mcp",
        )
        self.assertTrue(checked["ok"], checked["violations"])

    def test_full_schema_is_enforced(self) -> None:
        result = base_result(verification={})
        checked = self.check(result)
        self.assertIn("schema", {item["code"] for item in checked["violations"]})

    def test_empty_git_snapshots_are_rejected(self) -> None:
        checked = self.check(base_result(), before={}, after={})
        self.assertIn("git_snapshot", {item["code"] for item in checked["violations"]})

    def test_incomplete_git_snapshots_are_rejected(self) -> None:
        incomplete = {"head": SHA, "refs": {"refs/heads/main": SHA}}
        checked = self.check(base_result(), before=incomplete, after=incomplete)
        self.assertIn("git_snapshot", {item["code"] for item in checked["violations"]})

    def test_identity_and_structure_are_checked(self) -> None:
        result = base_result(role="code-reviewer", taskId="other", filesChanged=[])
        checked = self.check(
            result,
            expected_role="frontend-developer",
            expected_task="task-1",
        )
        self.assertGreaterEqual(
            sum(item["code"] == "identity" for item in checked["violations"]), 2
        )

    def test_valid_memory_write_request_is_accepted(self) -> None:
        result = base_result(
            memoryWriteRequests=[
                {
                    "category": "verified-pattern",
                    "summary": "Reuse the verified request boundary",
                    "detail": "The focused contract test verified the boundary.",
                    "tags": ["contract"],
                    "importance": "normal",
                    "evidenceRefs": ["tests/contracts/test_request.py"],
                    "futureUse": "Recall when changing the same request boundary.",
                    "supersedes": [],
                }
            ]
        )
        checked = self.check(
            result,
            expected_role="frontend-developer",
            expected_task="task-1",
        )
        self.assertTrue(checked["ok"], checked["violations"])

    def test_sensitive_memory_write_request_rejects_the_result(self) -> None:
        result = base_result(
            memoryWriteRequests=[
                {
                    "category": "tool-fact",
                    "summary": "Use the service credential",
                    "detail": "password=super-secret-value",
                    "tags": ["service"],
                    "importance": "high",
                    "evidenceRefs": ["runtime configuration"],
                    "futureUse": "Recall for future service access.",
                    "supersedes": [],
                }
            ]
        )
        checked = self.check(
            result,
            expected_role="frontend-developer",
            expected_task="task-1",
        )
        self.assertIn("memory_request", {item["code"] for item in checked["violations"]})

    def test_more_than_three_memory_requests_fail_schema(self) -> None:
        item = {
            "category": "constraint",
            "summary": "One durable constraint",
            "detail": "The requirement establishes this project constraint.",
            "tags": ["scope"],
            "importance": "normal",
            "evidenceRefs": ["requirement-1"],
            "futureUse": "Recall during scope analysis.",
            "supersedes": [],
        }
        checked = self.check(
            base_result(memoryWriteRequests=[item, item, item, item]),
            expected_role="frontend-developer",
            expected_task="task-1",
        )
        self.assertIn("schema", {entry["code"] for entry in checked["violations"]})


if __name__ == "__main__":
    unittest.main()
