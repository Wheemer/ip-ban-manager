"""Keep documentation shortcuts narrow and fail closed for runtime changes."""

import os
import subprocess
from unittest import TestCase
from unittest.mock import patch

from scripts.ci_runtime_changes import documentation_only, runtime_required


class TestRuntimeScope(TestCase):
    """Exercise classification using the actual git command and commit paths."""

    def test_recognized_documentation(self):
        """Allow README, reference Markdown, and documentation images only."""
        self.assertTrue(
            documentation_only(
                ["README.md", "docs/configuration.md", "docs/images/setup-flow.png"]
            )
        )

    def test_code_and_unknown_paths_require_runtime(self):
        """Never skip application, dependency, test, or workflow changes."""
        for path in [
            "custom_components/ip_ban_manager/panel.js",
            "requirements.test",
            ".github/workflows/unittest.yaml",
            "scripts/ci_runtime_changes.py",
            "tests/test_setup.py",
            "docs/tool.py",
            "docs/../source.py",
        ]:
            with self.subTest(path=path):
                self.assertFalse(documentation_only(["README.md", path]))
        self.assertFalse(documentation_only([]))

    def test_schedule_and_missing_history_run_full(self):
        """Schedules, zero SHAs, unknown events, and unavailable git history run tests."""
        sha = "a" * 40
        self.assertTrue(runtime_required("schedule", sha, sha))
        self.assertTrue(runtime_required("workflow_dispatch", sha, sha))
        self.assertTrue(runtime_required("push", "0" * 40, sha))
        self.assertTrue(runtime_required("pull_request", "", sha))
        with patch(
            "scripts.ci_runtime_changes.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "git"),
        ):
            self.assertTrue(runtime_required("pull_request", sha, "b" * 40))

    def test_renames_include_deleted_source_path(self):
        """Moving source into documentation cannot disguise the source deletion."""
        result = subprocess.CompletedProcess(
            [], 0, b"custom_components/ip_ban_manager/old.py\0docs/old.md\0"
        )
        with patch(
            "scripts.ci_runtime_changes.subprocess.run", return_value=result
        ) as git:
            self.assertTrue(runtime_required("pull_request", "a" * 40, "b" * 40))
            self.assertIn("--no-renames", git.call_args.args[0])

    def test_spaces_and_newlines_use_nul_separators(self):
        """Do not mistake a filename for separate documentation paths."""
        result = subprocess.CompletedProcess(
            [], 0, os.fsencode("docs/images/example image.png\0docs/example\nname.md\0")
        )
        with patch("scripts.ci_runtime_changes.subprocess.run", return_value=result):
            self.assertFalse(runtime_required("pull_request", "a" * 40, "b" * 40))
