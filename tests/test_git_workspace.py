from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import git_workspace  # noqa: E402
import project_config  # noqa: E402
import role_memory  # noqa: E402
import workflow_state  # noqa: E402


def git(directory: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(directory), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout.strip()


class GitWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        git(self.root, "init", "-q")
        git(self.root, "config", "user.name", "Superflow Test")
        git(self.root, "config", "user.email", "superflow@example.invalid")
        (self.root / "README.md").write_text("initial\n", encoding="utf-8")
        git(self.root, "add", "README.md")
        git(self.root, "commit", "-q", "-m", "Initial commit")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_preflight_and_dirty_base_rejection(self) -> None:
        result = git_workspace.preflight(self.root)
        self.assertTrue(result["clean"])
        (self.root / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaises(git_workspace.GitSafetyError):
            git_workspace.preflight(self.root)

    def test_snapshot_contains_head_and_refs(self) -> None:
        result = git_workspace.snapshot(self.root)
        self.assertEqual(result["head"], git(self.root, "rev-parse", "HEAD"))
        self.assertIn("refs/heads", " ".join(result["refs"]))

    def test_create_worktree_and_local_commit(self) -> None:
        created = git_workspace.create_worktree(self.root, "run-1")
        worktree = Path(created["worktree"])
        self.assertEqual(
            worktree.resolve(),
            (self.root / ".worktrees" / "superflow" / "run-1").resolve(),
        )
        (worktree / "feature.txt").write_text("feature\n", encoding="utf-8")
        committed = git_workspace.commit(worktree, "Test: local commit", ["feature.txt"])
        self.assertEqual(len(committed["sha"]), 40)
        self.assertEqual(committed["paths"], ["feature.txt"])

    def test_superflow_worktree_registers_in_shared_ledger(self) -> None:
        project_config.write_config(
            self.root,
            project_config.build_config("codex-browser", "penpot-mcp"),
        )
        run_id = "sf-20260724T010210Z-1234abd4"
        store = workflow_state.WorkflowState.create(
            self.root,
            [{"id": "t1", "title": "Legacy-compatible task"}],
            run_id,
        )

        created = git_workspace.create_worktree(self.root, run_id)

        worktrees = __import__("json").loads(
            (store.directory / "worktrees.json").read_text(encoding="utf-8")
        )["worktrees"]
        self.assertEqual(len(worktrees), 1)
        self.assertEqual(
            Path(worktrees[0]["path"]).resolve(),
            Path(created["worktree"]).resolve(),
        )

    def test_missing_superflow_run_is_rejected_before_git_mutation(self) -> None:
        run_id = "sf-20260724T010211Z-1234abd5"
        with self.assertRaises(git_workspace.GitSafetyError):
            git_workspace.create_worktree(self.root, run_id)
        self.assertFalse((self.root / ".worktrees" / "superflow" / run_id).exists())
        self.assertNotIn(
            f"superflow/{run_id}",
            git(self.root, "branch", "--format=%(refname:short)").splitlines(),
        )

    def test_failed_ledger_registration_rolls_back_new_worktree(self) -> None:
        project_config.write_config(
            self.root,
            project_config.build_config("codex-browser", "penpot-mcp"),
        )
        run_id = "sf-20260724T010216Z-1234abda"
        workflow_state.WorkflowState.create(
            self.root,
            [{"id": "t1", "title": "Legacy-compatible task"}],
            run_id,
        )
        target = self.root / ".worktrees" / "superflow" / run_id

        with mock.patch.object(
            workflow_state.WorkflowState,
            "register_worktree",
            side_effect=workflow_state.StateError("Simulated ledger failure"),
        ):
            with self.assertRaises(git_workspace.GitSafetyError):
                git_workspace.create_worktree(self.root, run_id)

        self.assertFalse(target.exists())
        self.assertNotIn(
            f"superflow/{run_id}",
            git(self.root, "branch", "--format=%(refname:short)").splitlines(),
        )

    def test_commit_is_blocked_while_a_subagent_dispatch_is_waiting(self) -> None:
        project_config.write_config(
            self.root,
            project_config.build_config("codex-browser", "penpot-mcp"),
        )
        run_id = "sf-20260724T010218Z-1234abdc"
        task = {
            "id": "t1",
            "title": "Implement feature",
            "role": "frontend-developer",
            "dependencies": [],
            "authorizedPaths": ["feature.txt"],
            "acceptanceCriteria": ["The feature is implemented"],
            "verificationCommands": ["python3 -m unittest"],
            "observableResults": ["The focused test passes"],
        }
        store = workflow_state.WorkflowState.create(
            self.root,
            [task],
            run_id,
            require_contract=True,
        )
        store.register_worktree(self.root, "main", "HEAD")
        for status in ("preflight", "discovery", "requirements_ready", "planned"):
            store.transition(status)
        store.transition("implementing", "t1")
        snapshot = store._git_snapshot()
        store.record_brief(
            "t1",
            {
                "runId": run_id,
                "taskId": "t1",
                "role": "frontend-developer",
                "workDirectory": str(self.root),
                "objective": "Implement the frozen behavior",
                "dependencies": [],
                "authorizedPaths": ["feature.txt"],
                "exclusions": [".git", ".codex"],
                "acceptanceCriteria": ["The feature is implemented"],
                "verificationCommands": ["python3 -m unittest"],
                "observableResults": ["The focused test passes"],
                "browserProvider": "codex-browser",
                "browserRequired": False,
                "browserAccessMode": "main-only",
                "executionProfile": "strict",
                "contextMode": "minimal",
                "memoryLimit": 10,
                "memoryMaxBytes": 8192,
                "resultDetail": "full",
                "codeGraphRequired": True,
                "uiPrototypeProvider": "penpot-mcp",
                "beforeSnapshot": snapshot,
                "resultSchema": "assets/schemas/agent-result.schema.json",
                "roleMemoryScript": str((SCRIPTS / "role_memory.py").resolve()),
                "builtinGuide": str(
                    (
                        SCRIPTS.parent
                        / "references"
                        / "frontend-engineering-rules.md"
                    ).resolve()
                ),
            },
        )
        store.record_dispatch(
            "t1",
            "frontend-developer",
            "agent-session-commit-block",
            snapshot,
            role_memory.issue_capability(
                self.root,
                "frontend-developer",
                run_id,
                "t1",
            )["capability"],
        )
        (self.root / "feature.txt").write_text("feature\n", encoding="utf-8")

        with self.assertRaisesRegex(git_workspace.GitSafetyError, "waiting"):
            git_workspace.commit(
                self.root,
                "This commit must be blocked",
                ["feature.txt"],
            )

    def test_local_cherry_pick(self) -> None:
        created = git_workspace.create_worktree(self.root, "run-pick")
        worktree = Path(created["worktree"])
        (worktree / "picked.txt").write_text("picked\n", encoding="utf-8")
        commit = git_workspace.commit(worktree, "Test: commit to cherry-pick", ["picked.txt"])
        result = git_workspace.cherry_pick(self.root, commit["sha"])
        self.assertEqual(len(result["head"]), 40)
        self.assertTrue((self.root / "picked.txt").exists())
        self.assertEqual((self.root / "picked.txt").read_text(encoding="utf-8"), "picked\n")

    def test_commit_rejects_preexisting_staged_content(self) -> None:
        created = git_workspace.create_worktree(self.root, "run-staged")
        worktree = Path(created["worktree"])
        (worktree / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
        git(worktree, "add", "unrelated.txt")
        (worktree / "wanted.txt").write_text("wanted\n", encoding="utf-8")
        with self.assertRaises(git_workspace.GitSafetyError):
            git_workspace.commit(worktree, "Test: reject mixed changes", ["wanted.txt"])
        self.assertEqual(git(worktree, "diff", "--cached", "--name-only"), "unrelated.txt")

    def test_failed_commit_restores_index(self) -> None:
        created = git_workspace.create_worktree(self.root, "run-hook-failure")
        worktree = Path(created["worktree"])
        hook = Path(git(worktree, "rev-parse", "--git-path", "hooks/pre-commit"))
        if not hook.is_absolute():
            hook = worktree / hook
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        (worktree / "wanted.txt").write_text("wanted\n", encoding="utf-8")
        with self.assertRaises(git_workspace.GitSafetyError):
            git_workspace.commit(worktree, "Test: hook failure", ["wanted.txt"])
        self.assertEqual(git(worktree, "diff", "--cached", "--name-only"), "")

    def test_non_git_and_unsafe_worktree_path_are_rejected(self) -> None:
        plain = Path(self.temporary.name) / "plain"
        plain.mkdir()
        with self.assertRaises(git_workspace.GitSafetyError):
            git_workspace.preflight(plain)
        with self.assertRaises(git_workspace.GitSafetyError):
            git_workspace.create_worktree(self.root, "run-2", path=self.root / "outside")

    def test_commit_rejects_broad_or_magic_pathspecs(self) -> None:
        created = git_workspace.create_worktree(self.root, "run-pathspec")
        worktree = Path(created["worktree"])
        (worktree / "wanted.txt").write_text("wanted\n", encoding="utf-8")
        for path in (".", ":(top)**"):
            with self.subTest(path=path), self.assertRaises(git_workspace.GitSafetyError):
                git_workspace.commit(worktree, "Test: reject broad path", [path])

    def test_commit_allows_normal_dot_git_prefixed_files(self) -> None:
        created = git_workspace.create_worktree(self.root, "run-dot-git-files")
        worktree = Path(created["worktree"])
        (worktree / ".github").mkdir()
        (worktree / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
        (worktree / ".github" / "ci.yml").write_text("name: ci\n", encoding="utf-8")
        result = git_workspace.commit(
            worktree,
            "Test: allow a regular Git configuration file",
            [".gitignore", ".github/ci.yml"],
        )
        self.assertEqual(set(result["paths"]), {".gitignore", ".github/ci.yml"})

    def test_any_executable_hook_blocks_automatic_git_write(self) -> None:
        hook = Path(git(self.root, "rev-parse", "--git-path", "hooks/post-checkout"))
        if not hook.is_absolute():
            hook = self.root / hook
        hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        hook.chmod(0o755)
        with self.assertRaises(git_workspace.GitSafetyError):
            git_workspace.create_worktree(self.root, "run-hook-block")

    def test_cli_has_no_remote_or_destructive_subcommands(self) -> None:
        parser = git_workspace._parser()
        for command in ("push", "merge", "delete-worktree"):
            with self.subTest(command=command), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit):
                    parser.parse_args([command])


if __name__ == "__main__":
    unittest.main()
