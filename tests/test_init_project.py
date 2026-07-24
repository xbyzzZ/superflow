from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import init_project  # noqa: E402
import project_config  # noqa: E402


def git(directory: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(directory), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout.strip()


class InitProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        git(self.project, "init", "-q")
        self.skill = self.root / "skill"
        self.templates = self.skill / "assets" / "agent-templates"
        self.templates.mkdir(parents=True)
        for index in range(6):
            (self.templates / f"agent-{index}.md").write_text(
                f"template-v1-{index}\n", encoding="utf-8"
            )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def initialize(self, **overrides):
        options = {
            "browser_provider": "codex-browser",
            "ui_provider": "penpot-mcp",
        }
        options.update(overrides)
        return init_project.initialize(self.project, self.skill, **options)

    def test_first_install_and_exclude_are_idempotent(self) -> None:
        result = self.initialize()
        self.assertTrue(result["ok"])
        self.assertEqual(result["project_config"]["action"], "installed")
        self.assertEqual(result["project_config"]["browser"]["provider"], "codex-browser")
        self.assertEqual(result["project_config"]["ui_prototype"]["provider"], "penpot-mcp")
        self.assertEqual(
            {item["action"] for item in result["actions"]}, {"installed"}
        )
        for index in range(6):
            self.assertEqual(
                (self.project / ".codex" / "agents" / f"agent-{index}.md").read_text(),
                f"template-v1-{index}\n",
            )
        second = init_project.initialize(self.project, self.skill)
        self.assertEqual(second["project_config"]["action"], "unchanged")
        self.assertEqual(
            {item["action"] for item in second["actions"]}, {"unchanged"}
        )
        exclude = Path(git(self.project, "rev-parse", "--git-path", "info/exclude"))
        if not exclude.is_absolute():
            exclude = self.project / exclude
        lines = exclude.read_text(encoding="utf-8").splitlines()
        for line in init_project.EXCLUDE_LINES:
            self.assertEqual(lines.count(line), 1)

    def test_managed_template_is_upgraded(self) -> None:
        self.initialize()
        source = self.templates / "agent-2.md"
        source.write_text("template-v2\n", encoding="utf-8")
        result = init_project.initialize(self.project, self.skill)
        action = next(item for item in result["actions"] if item["template"] == "agent-2.md")
        self.assertEqual(action["action"], "upgraded")
        self.assertEqual(
            (self.project / ".codex" / "agents" / "agent-2.md").read_text(),
            "template-v2\n",
        )

    def test_user_conflict_is_preserved(self) -> None:
        self.initialize()
        destination = self.project / ".codex" / "agents" / "agent-3.md"
        destination.write_text("user content\n", encoding="utf-8")
        (self.templates / "agent-3.md").write_text("new template\n", encoding="utf-8")
        result = init_project.initialize(self.project, self.skill)
        self.assertIn("agent-3.md", result["conflicts"])
        self.assertEqual(destination.read_text(encoding="utf-8"), "user content\n")

    def test_invalid_manifest_type_is_structured_block(self) -> None:
        self.initialize()
        manifest = self.project / ".codex" / "agents" / init_project.MANIFEST_NAME
        manifest.write_text("[]\n", encoding="utf-8")
        process = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "init_project.py"),
                "--project",
                str(self.project),
                "--skill-root",
                str(self.skill),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(process.returncode, 2)
        self.assertEqual(json.loads(process.stdout)["status"], "blocked")
        self.assertEqual(process.stderr, "")

    def test_malformed_manifest_is_structured_block(self) -> None:
        self.initialize()
        manifest = self.project / ".codex" / "agents" / init_project.MANIFEST_NAME
        manifest.write_text("{not-json\n", encoding="utf-8")
        process = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "init_project.py"),
                "--project",
                str(self.project),
                "--skill-root",
                str(self.skill),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(process.returncode, 2)
        self.assertEqual(json.loads(process.stdout)["status"], "blocked")
        self.assertEqual(process.stderr, "")

    def test_non_git_is_structured_block_without_writes(self) -> None:
        plain = self.root / "plain"
        plain.mkdir()
        process = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "init_project.py"),
                "--project",
                str(plain),
                "--skill-root",
                str(self.skill),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        payload = json.loads(process.stdout)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(payload["status"], "blocked")
        self.assertFalse((plain / ".codex").exists())

    def test_symlink_and_tracked_workflows_are_rejected(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        (self.project / ".codex").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(init_project.InitBlocked):
            self.initialize()
        (self.project / ".codex").unlink()
        workflow = self.project / ".codex" / "workflows" / "tracked.txt"
        workflow.parent.mkdir(parents=True)
        workflow.write_text("tracked\n", encoding="utf-8")
        git(self.project, "add", ".codex/workflows/tracked.txt", "-f")
        with self.assertRaises(init_project.InitBlocked):
            self.initialize()

    def test_first_initialization_requires_both_user_choices(self) -> None:
        with self.assertRaisesRegex(init_project.InitBlocked, "Ask the user"):
            init_project.initialize(self.project, self.skill)
        self.assertFalse((self.project / ".codex").exists())

    def test_custom_provider_requires_details(self) -> None:
        with self.assertRaisesRegex(init_project.InitBlocked, "requires tool and invocation details"):
            self.initialize(browser_provider="custom")
        result = self.initialize(
            browser_provider="custom",
            browser_custom="Team browser MCP; evidence uses the custom provider",
            ui_provider="custom",
            ui_custom="Internal design platform MCP; evidence uses the custom provider",
        )
        self.assertEqual(result["project_config"]["browser"]["provider"], "custom")
        self.assertIn("Team browser", result["project_config"]["browser"]["details"])

    def test_existing_choices_require_explicit_reconfigure(self) -> None:
        self.initialize()
        with self.assertRaisesRegex(init_project.InitBlocked, "--reconfigure"):
            self.initialize(
                browser_provider="chrome-mcp",
                ui_provider="codex-figma",
            )
        changed = self.initialize(
            browser_provider="chrome-mcp",
            ui_provider="codex-figma",
            reconfigure=True,
        )
        self.assertEqual(changed["project_config"]["action"], "reconfigured")
        reused = init_project.initialize(self.project, self.skill)
        self.assertEqual(reused["project_config"]["browser"]["provider"], "chrome-mcp")
        self.assertEqual(reused["project_config"]["ui_prototype"]["provider"], "codex-figma")

    def test_project_choices_are_shared_by_git_worktrees(self) -> None:
        self.initialize(
            browser_provider="chrome-mcp",
            ui_provider="codex-figma",
        )
        worktree = self.root / "linked-worktree"
        git(self.project, "worktree", "add", "-q", "-b", "linked-test", str(worktree))
        config = project_config.read_config(worktree)
        self.assertEqual(config["browser"]["provider"], "chrome-mcp")
        self.assertEqual(config["ui_prototype"]["provider"], "codex-figma")


if __name__ == "__main__":
    unittest.main()
