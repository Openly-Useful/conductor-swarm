#!/usr/bin/env python3
"""Regression tests for protected local provider-session lineage capture."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "skills" / "cross-tool-continuity-swarm" / "scripts" / "lineage.py"


class LineageCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name).resolve()
        self.workspace = self.directory / "workspace"
        self.workspace.mkdir()
        self.store = self.directory / "private" / "lineage.json"
        self.thread_id = "00000000-0000-4000-8000-000000000001"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_cli(self, *arguments: str, thread_id: str | None = None, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        if thread_id is None:
            environment.pop("CODEX_THREAD_ID", None)
        else:
            environment["CODEX_THREAD_ID"] = thread_id
        return subprocess.run(
            [sys.executable, str(CLI), *arguments, "--store", str(self.store)],
            cwd=ROOT,
            env=environment,
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
        )

    def common(self, command: str, provider: str = "codex") -> tuple[str, ...]:
        return (
            command,
            "--provider",
            provider,
            "--project-id",
            "demo-project",
            "--workspace",
            str(self.workspace),
        )

    def test_codex_capture_and_verify_are_private_idempotent_and_opaque(self) -> None:
        first = self.run_cli(*self.common("capture"), thread_id=self.thread_id)
        self.assertEqual(first.returncode, 0, first.stderr)
        receipt = json.loads(first.stdout)
        self.assertTrue(receipt["captured"])
        self.assertTrue(receipt["verified"])
        self.assertNotIn(self.thread_id, first.stdout)
        self.assertEqual(stat.S_IMODE(self.store.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.store.parent.stat().st_mode), 0o700)
        stored = self.store.read_bytes()
        self.assertIn(self.thread_id.encode("utf-8"), stored)

        retry = self.run_cli(*self.common("capture"), thread_id=self.thread_id)
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertEqual(self.store.read_bytes(), stored)
        verify = self.run_cli(*self.common("verify"), thread_id=self.thread_id)
        self.assertEqual(verify.returncode, 0, verify.stderr)
        self.assertEqual(json.loads(verify.stdout), receipt)
        self.assertEqual(len(json.loads(stored)["links"]), 1)

        status_result = self.run_cli(
            "status",
            "--project-id",
            "demo-project",
            "--workspace",
            str(self.workspace),
        )
        self.assertEqual(status_result.returncode, 0, status_result.stderr)
        status_value = json.loads(status_result.stdout)
        self.assertEqual(status_value["captured_count"], 1)
        self.assertEqual(status_value["links"][0]["reference_id"], receipt["reference_id"])
        self.assertNotIn(self.thread_id, status_result.stdout)

    def test_missing_or_changed_current_thread_never_verifies(self) -> None:
        missing = self.run_cli(*self.common("capture"))
        self.assertEqual(missing.returncode, 2)
        self.assertIn("host did not expose CODEX_THREAD_ID", missing.stderr)
        self.assertFalse(self.store.exists())

        captured = self.run_cli(*self.common("capture"), thread_id=self.thread_id)
        self.assertEqual(captured.returncode, 0, captured.stderr)
        changed = self.run_cli(
            *self.common("verify"),
            thread_id="00000000-0000-4000-8000-000000000002",
        )
        self.assertEqual(changed.returncode, 2)
        self.assertIn("is not captured", changed.stderr)

    def test_claude_identifier_uses_stdin_and_store_symlinks_are_rejected(self) -> None:
        capture = self.run_cli(
            *self.common("capture", provider="claude-code"),
            "--session-id-stdin",
            stdin="00000000-0000-4000-8000-000000000003\n",
        )
        self.assertEqual(capture.returncode, 0, capture.stderr)
        self.assertNotIn("00000000-0000-4000-8000-000000000003", capture.stdout)

        victim = self.directory / "victim.json"
        victim.write_text('{"safe":true}\n', encoding="utf-8")
        self.store.unlink()
        self.store.symlink_to(victim)
        before = victim.read_bytes()
        rejected = self.run_cli(*self.common("capture"), thread_id=self.thread_id)
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("regular non-symlink", rejected.stderr)
        self.assertEqual(victim.read_bytes(), before)

    def test_store_inside_workspace_is_rejected_before_any_write(self) -> None:
        self.store = self.workspace / ".continuity" / "session-lineage.json"
        rejected = self.run_cli(*self.common("capture"), thread_id=self.thread_id)
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("must remain outside the approved workspace", rejected.stderr)
        self.assertFalse(self.store.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
