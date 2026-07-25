from __future__ import annotations

import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

import sys

sys.path.insert(0, str(ROOT / "scripts"))

import build_release  # noqa: E402


class ReleasePackageTests(unittest.TestCase):
    def test_version_is_semantic(self) -> None:
        self.assertEqual(build_release.read_version(), "0.2.3")

    def test_project_metadata_identifies_the_author(self) -> None:
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        readme_cn = (ROOT / "README_CN.md").read_text(encoding="utf-8")

        self.assertIn("Copyright (c) 2026 beautiful boy", license_text)
        for content in (readme, readme_cn):
            self.assertIn("beautiful boy", content)
            self.assertIn("xbyzzz0917@163.com", content)
        redundant_notice = (
            "\u82f1\u6587\u7248 README.md \u662f\u4e3b\u8981\u6587\u6863"
            "\uff1bREADME_CN.md \u662f\u5bf9\u5e94\u7684\u7b80\u4f53"
            "\u4e2d\u6587\u8bf4\u660e\u3002"
        )
        self.assertNotIn(redundant_notice, readme_cn)

    def test_release_archive_contains_only_installable_skill_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = build_release.build_release(Path(temporary))
            archive = Path(str(result["archive"]))
            with zipfile.ZipFile(archive) as bundle:
                names = set(bundle.namelist())
                self.assertIsNone(bundle.testzip())
                self.assertIn("superflow/SKILL.md", names)
                self.assertIn("superflow/LICENSE", names)
                self.assertIn("superflow/VERSION", names)
                self.assertIn("superflow/scripts/role_memory.py", names)
                self.assertIn("superflow/references/role-memory.md", names)
                self.assertFalse(any(name.startswith("superflow/tests/") for name in names))
                self.assertFalse(any(name.startswith("superflow/.github/") for name in names))
                self.assertFalse(any("__pycache__" in name for name in names))
                self.assertEqual(
                    bundle.read("superflow/VERSION").decode("utf-8").strip(),
                    result["version"],
                )

    def test_release_build_is_reproducible(self) -> None:
        with (
            tempfile.TemporaryDirectory() as first,
            tempfile.TemporaryDirectory() as second,
        ):
            one = build_release.build_release(Path(first))
            two = build_release.build_release(Path(second))
            self.assertEqual(one["sha256"], two["sha256"])
            self.assertEqual(
                hashlib.sha256(Path(str(one["archive"])).read_bytes()).hexdigest(),
                one["sha256"],
            )
            sidecar = Path(str(one["checksum"])).read_text(encoding="ascii")
            self.assertEqual(
                sidecar,
                f"{one['sha256']}  superflow-v{one['version']}.zip\n",
            )

    def test_github_workflows_test_and_publish_the_same_versioned_package(self) -> None:
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("python -m unittest discover -s tests -v", ci)
        self.assertIn("python scripts/build_release.py", ci)
        self.assertIn("python -m zipfile -t", ci)
        self.assertIn('tags:\n      - "v*"', release)
        self.assertIn('test "v$(cat VERSION)" = "$GITHUB_REF_NAME"', release)
        self.assertIn("gh release create", release)
        self.assertIn("--verify-tag", release)


if __name__ == "__main__":
    unittest.main()
