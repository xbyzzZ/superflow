from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import workflow_profile  # noqa: E402


class WorkflowProfileTests(unittest.TestCase):
    def test_localized_work_defaults_to_lite(self) -> None:
        result = workflow_profile.select_profile({})
        self.assertEqual(result["profile"], "lite")
        self.assertEqual(result["reasons"], ["localized-low-risk"])
        self.assertEqual(result["policy"]["qualityAgents"], 1)
        self.assertEqual(result["policy"]["memoryMaxBytes"], 2048)
        self.assertFalse(result["policy"]["parentConversation"])

    def test_product_and_cross_module_signals_upgrade_to_standard(self) -> None:
        result = workflow_profile.select_profile(
            {"userVisible": True, "crossModule": True},
            "lite",
        )
        self.assertEqual(result["profile"], "standard")
        self.assertTrue(result["upgraded"])
        self.assertEqual(result["policy"]["qualityAgents"], 2)

    def test_high_risk_signals_cannot_be_downgraded(self) -> None:
        result = workflow_profile.select_profile(
            {"production": True, "authorization": True},
            "lite",
        )
        self.assertEqual(result["profile"], "strict")
        self.assertEqual(result["reasons"], ["authorization", "production"])

    def test_user_can_raise_but_not_lower_the_profile(self) -> None:
        self.assertEqual(
            workflow_profile.select_profile({}, "strict")["profile"],
            "strict",
        )
        self.assertEqual(
            workflow_profile.select_profile({"browser": True}, "lite")["profile"],
            "standard",
        )

    def test_cli_returns_small_structured_output(self) -> None:
        process = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "workflow_profile.py"),
                "--signals",
                json.dumps({"release": True}),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertLess(len(process.stdout.encode("utf-8")), 1000)
        self.assertEqual(json.loads(process.stdout)["profile"], "strict")
