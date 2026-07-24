from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BuiltinGuidesTests(unittest.TestCase):
    def test_skill_requires_explicit_current_request_invocation(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        agent_metadata = (ROOT / "agents" / "openai.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "Use only when the user explicitly invokes Superflow in the current request",
            skill,
        )
        self.assertIn(
            "Without an explicit current-request invocation, do not initialize Superflow",
            skill,
        )
        self.assertIn("allow_implicit_invocation: false", agent_metadata)
        self.assertNotIn("allow_implicit_invocation: true", agent_metadata)

    def test_dispatch_wait_prevents_main_agent_overlap(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        role_contracts = (ROOT / "references" / "role-contracts.md").read_text(
            encoding="utf-8"
        )
        state_machine = (
            ROOT / "references" / "workflow-state-machine.md"
        ).read_text(encoding="utf-8")
        for content in (skill, role_contracts, state_machine):
            self.assertIn("coordination-only", content)
            self.assertIn("waiting", content)
        self.assertIn("record-dispatch", skill)
        self.assertIn("--dispatch-id", skill)
        self.assertIn("commits, and cherry-picks fail closed", state_machine)
        for path in sorted((ROOT / "assets" / "agent-templates").glob("*.toml")):
            with self.subTest(template=path.name):
                self.assertIn(
                    "immutable `dispatchId` supplied with the task dispatch",
                    path.read_text(encoding="utf-8"),
                )

    """Verify that built-in expertise never degrades into external Skill dependencies."""

    def test_skill_directly_links_all_builtin_guides(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        for name in (
            "product-management-rules.md",
            "architecture-design-rules.md",
            "ui-ux-design-rules.md",
            "frontend-engineering-rules.md",
            "backend-engineering-rules.md",
            "testing-strategy.md",
            "code-review-criteria.md",
        ):
            with self.subTest(name=name):
                self.assertIn(f"(references/{name})", skill)

    def test_external_skill_policy_does_not_require_replaced_skills(self) -> None:
        policy = (ROOT / "references" / "tool-and-skill-policy.md").read_text(
            encoding="utf-8"
        )
        optional_section = policy.split("## Optional enhancement Skills", 1)[1]
        for name in ("product-manager-toolkit", "testing-expert", "`code-review`"):
            with self.subTest(name=name):
                self.assertNotIn(name, optional_section)

    def test_testing_rules_reject_masking_flaky_tests(self) -> None:
        strategy = (ROOT / "references" / "testing-strategy.md").read_text(
            encoding="utf-8"
        )
        tester = (
            ROOT / "assets" / "agent-templates" / "tester.toml"
        ).read_text(encoding="utf-8")
        for content in (strategy, tester):
            self.assertIn("flaky", content)
            self.assertIn("larger timeouts", content)
            self.assertIn("retries", content)
            self.assertIn("mask", content)

    def test_reviewer_contract_is_candidate_bound_and_defect_first(self) -> None:
        criteria = (ROOT / "references" / "code-review-criteria.md").read_text(
            encoding="utf-8"
        )
        reviewer = (
            ROOT / "assets" / "agent-templates" / "code-reviewer.toml"
        ).read_text(encoding="utf-8")
        for content in (criteria, reviewer):
            self.assertIn("candidate", content)
            self.assertIn("spec", content)
            self.assertIn("correctness", content)
            self.assertIn("consistency", content)
            self.assertIn("trigger", content)

    def test_product_rules_require_scope_and_observable_acceptance(self) -> None:
        product = (
            ROOT / "references" / "product-management-rules.md"
        ).read_text(encoding="utf-8")
        for phrase in (
            "in-scope",
            "out-of-scope",
            "success criteria",
            "externally observable behavior",
            "task DAG",
        ):
            self.assertIn(phrase, product)

    def test_professional_role_guides_are_wired_into_templates(self) -> None:
        cases = {
            "architect": (
                "architecture-design-rules.md",
                ("quality attributes", "alternatives", "migration", "rollback"),
            ),
            "ui-designer": (
                "ui-ux-design-rules.md",
                ("WCAG 2.2 AA", "partial", "responsive", "prototype"),
            ),
            "frontend-developer": (
                "frontend-engineering-rules.md",
                ("nearest common consumer", "race", "semantic", "dependency"),
            ),
            "backend-developer": (
                "backend-engineering-rules.md",
                ("RFC 9457", "OWASP API Security", "idempotency", "transaction"),
            ),
        }
        for role, (guide_name, phrases) in cases.items():
            with self.subTest(role=role):
                guide = (ROOT / "references" / guide_name).read_text(encoding="utf-8")
                template = (
                    ROOT / "assets" / "agent-templates" / f"{role}.toml"
                ).read_text(encoding="utf-8")
                self.assertIn(guide_name, template)
                self.assertIn("builtinGuide", template)
                for phrase in phrases:
                    self.assertIn(phrase, guide)

    def test_every_specialist_has_role_bound_memory_contract(self) -> None:
        for path in sorted((ROOT / "assets" / "agent-templates").glob("*.toml")):
            with self.subTest(template=path.name):
                content = path.read_text(encoding="utf-8")
                self.assertIn("roleMemoryCapability", content)
                self.assertIn("never request, infer, or access another role's memory", content)
                self.assertIn("memoryWriteRequests", content)
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("(references/role-memory.md)", skill)
        self.assertIn("--capability <capability>", skill)

    def test_repository_is_english_except_localized_readme_content(self) -> None:
        han = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
        text_suffixes = {".json", ".md", ".py", ".toml", ".txt", ".yaml", ".yml"}
        chinese_label = "\u7b80\u4f53\u4e2d\u6587"
        tracked = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z"],
            check=True,
            stdout=subprocess.PIPE,
        ).stdout.decode("utf-8").split("\0")
        for relative in tracked:
            path = ROOT / relative
            if (
                not relative
                or not path.is_file()
                or path.name == "README_CN.md"
                or path.suffix not in text_suffixes
            ):
                continue
            with self.subTest(path=str(path.relative_to(ROOT))):
                content = path.read_text(encoding="utf-8")
                if path.name == "README.md":
                    self.assertEqual(content.count(f"[{chinese_label}](README_CN.md)"), 1)
                    content = content.replace(chinese_label, "")
                self.assertIsNone(han.search(content))


if __name__ == "__main__":
    unittest.main()
