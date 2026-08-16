#!/usr/bin/env python3
"""Deterministic standard-library regression tests for the continuity CLI."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "skills" / "cross-tool-continuity-swarm" / "scripts" / "continuity.py"
EXAMPLE = ROOT / "skills" / "cross-tool-continuity-swarm" / "examples" / "continuity.json"


def load_continuity_module():
    spec = importlib.util.spec_from_file_location("continuity_under_test", CLI)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load continuity.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


continuity = load_continuity_module()


class ContinuityCliTest(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        # macOS exposes its temporary tree through /var -> /private/var.  The
        # audit command deliberately rejects symlinked parent components, so
        # use the canonical fixture path rather than weakening that guard.
        self.directory = Path(self.tempdir.name).resolve()
        self.state = self.directory / "state.json"
        result = self.run_cli(
            "init",
            "--output",
            str(self.state),
            "--project-id",
            "demo",
            "--objective",
            "Keep the handoff safe",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CLI), *arguments],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    def read_state(self) -> dict[str, object]:
        return json.loads(self.state.read_text(encoding="utf-8"))

    def write_json(self, filename: str, value: object) -> Path:
        path = self.directory / filename
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def descriptor_count(self) -> int | None:
        try:
            return len(os.listdir("/dev/fd"))
        except OSError:
            return None

    def assert_no_audit_temps(self) -> None:
        self.assertEqual(list(self.directory.rglob(".continuity-*.tmp")), [])

    def assert_json_file(self, path: Path) -> object:
        return json.loads(path.read_text(encoding="utf-8"))

    def prepare_verified_state(self) -> None:
        recovery = self.write_json(
            "recovery.json",
            {
                "context": {
                    "summary": "The bounded implementation is ready for continuation.",
                    "decisions": ["Preserve the public interface."],
                    "constraints": ["No external writes."],
                    "next_action": "Run the verification command and inspect the current diff.",
                    "open_questions": [],
                },
                "acceptance": [{"id": "a1", "criterion": "The required verification passes."}],
                "verification": [{"command": "python3 -m unittest", "status": "pending"}],
                "artifacts": [{"path": "src/example.py", "role": "implementation"}],
            },
        )
        result = self.run_cli("recover", "--state", str(self.state), "--input", str(recovery))
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = self.write_json(
            "evidence.json",
            {
                "summary": "The current verification command passed.",
                "confidence": "verified",
                "provenance": "test",
            },
        )
        result = self.run_cli("capture", "--state", str(self.state), "--input", str(evidence))
        self.assertEqual(result.returncode, 0, result.stderr)
        audit = self.run_cli("audit", "--state", str(self.state))
        self.assertEqual(audit.returncode, 0, audit.stderr)
        self.assertTrue(json.loads(audit.stdout)["ok"])
        state = self.read_state()
        self.assertEqual(
            state["audit"],
            {"status": "passed", "findings": [], "evidence_ids": [state["evidence"][0]["id"]]},
        )
        validation = self.run_cli("validate", "--state", str(self.state))
        self.assertEqual(validation.returncode, 0, validation.stderr)
        result = self.run_cli("prepare-switch", "--state", str(self.state), "--target-tool", "receiver")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_prepare_does_not_manufacture_a_passed_audit_or_ready_state(self) -> None:
        before = self.state.read_bytes()
        audit = self.run_cli("audit", "--state", str(self.state))
        self.assertEqual(audit.returncode, 1)
        self.assertFalse(json.loads(audit.stdout)["ok"])
        self.assertEqual(self.state.read_bytes(), before)
        result = self.run_cli("prepare-switch", "--state", str(self.state), "--target-tool", "receiver")
        self.assertEqual(result.returncode, 2)
        self.assertIn("persisted audit.status 'passed'", result.stderr)
        self.assertIn("persisted verified evidence", result.stderr)
        self.assertIn("recovered context summary", result.stderr)
        self.assertIn("at least one acceptance criterion", result.stderr)
        self.assertIn("verification command or check", result.stderr)
        self.assertEqual(self.state.read_bytes(), before)

    def test_recover_requires_actionable_bounded_state_and_is_idempotent(self) -> None:
        incomplete = self.write_json(
            "incomplete-recovery.json",
            {
                "context": {"summary": "", "next_action": ""},
                "acceptance": [],
                "verification": [],
                "artifacts": [],
            },
        )
        before = self.state.read_bytes()
        rejected = self.run_cli("recover", "--state", str(self.state), "--input", str(incomplete))
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("context.summary must be a nonempty string", rejected.stderr)
        self.assertIn("acceptance must not be empty", rejected.stderr)
        self.assertIn("requires at least one command or check", rejected.stderr)
        self.assertEqual(self.state.read_bytes(), before)

        valid = self.write_json(
            "valid-recovery.json",
            {
                "context": {
                    "summary": "Recovered bounded state.",
                    "next_action": "Run the focused verification.",
                    "decisions": [],
                    "constraints": [],
                    "open_questions": [],
                },
                "acceptance": [{"id": "a1", "criterion": "Focused verification passes."}],
                "verification": [{"check": "Inspect the focused verification result."}],
                "artifacts": [],
            },
        )
        first = self.run_cli("recover", "--state", str(self.state), "--input", str(valid))
        self.assertEqual(first.returncode, 0, first.stderr)
        first_bytes = self.state.read_bytes()
        retry = self.run_cli("recover", "--state", str(self.state), "--input", str(valid))
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertEqual(self.state.read_bytes(), first_bytes)

    def test_empty_evidence_fields_are_rejected_and_cannot_unlock_audit_or_prepare(self) -> None:
        for field in ("summary", "kind", "provenance"):
            with self.subTest(field=field):
                evidence = {"summary": "Verified result.", "kind": "observation", "provenance": "test", "confidence": "verified"}
                evidence[field] = ""
                input_path = self.write_json(f"empty-{field}.json", evidence)
                result = self.run_cli("capture", "--state", str(self.state), "--input", str(input_path))
                self.assertEqual(result.returncode, 2)
                self.assertIn(f"evidence.{field} must be a nonempty string", result.stderr)

        state = self.read_state()
        state["evidence"] = [
            {"id": "", "kind": "", "summary": "", "provenance": "", "confidence": "verified"}
        ]
        malformed = self.write_json("empty-stored-evidence.json", state)
        before = malformed.read_bytes()
        validation = self.run_cli("validate", "--state", str(malformed))
        self.assertEqual(validation.returncode, 1)
        errors = json.loads(validation.stdout)["errors"]
        for field in ("id", "kind", "summary", "provenance"):
            self.assertIn(f"evidence[0].{field} must be a nonempty string", errors)

        audit = self.run_cli("audit", "--state", str(malformed))
        self.assertEqual(audit.returncode, 1)
        self.assertFalse(json.loads(audit.stdout)["ok"])
        self.assertEqual(malformed.read_bytes(), before)
        prepare = self.run_cli("prepare-switch", "--state", str(malformed), "--target-tool", "receiver")
        self.assertEqual(prepare.returncode, 2)
        self.assertIn("cannot prepare invalid state", prepare.stderr)

        evidence = self.write_json(
            "claimed-evidence.json",
            {"summary": "A handoff says the tests passed.", "confidence": "claimed"},
        )
        self.assertEqual(self.run_cli("capture", "--state", str(self.state), "--input", str(evidence)).returncode, 0)
        before = self.state.read_bytes()
        result = self.run_cli("prepare-switch", "--state", str(self.state), "--target-tool", "receiver")
        self.assertEqual(result.returncode, 2)
        self.assertIn("persisted audit.status 'passed'", result.stderr)
        self.assertIn("persisted verified evidence", result.stderr)
        self.assertEqual(self.state.read_bytes(), before)

    def test_positive_lifecycle_preserves_persisted_audit_and_reaches_ready_sync(self) -> None:
        self.prepare_verified_state()
        prepared = self.read_state()
        self.assertEqual(prepared["continuity"]["phase"], "prepare")
        self.assertEqual(prepared["continuity"]["status"], "ready")
        self.assertEqual(prepared["audit"]["status"], "passed")
        self.assertEqual(prepared["audit"]["findings"], [])
        self.assertEqual(prepared["audit"]["evidence_ids"], [prepared["evidence"][0]["id"]])

        sync_input = self.write_json(
            "applied.json",
            {
                "source_tool": "source",
                "target_tool": "receiver",
                "status": "applied",
                "summary": "Receiver applied the checkpoint.",
                "idempotency_key": "applied-attempt-1",
                "evidence": [
                    {
                        "summary": "Receiver verification command accepted revision abc123.",
                        "confidence": "verified",
                        "provenance": "receiver-test-log",
                        "destination": "receiver",
                        "verification": "Receiver accepted revision abc123.",
                    }
                ],
            },
        )
        result = self.run_cli("record-sync", "--state", str(self.state), "--input", str(sync_input))
        self.assertEqual(result.returncode, 0, result.stderr)
        synced = self.read_state()
        self.assertEqual(synced["continuity"]["phase"], "sync")
        self.assertEqual(synced["continuity"]["status"], "ready")
        self.assertEqual(len(synced["sync"]), 1)

    def test_audit_rejects_symlink_and_hardlink_report_aliases_without_overwrite(self) -> None:
        self.prepare_verified_state()
        before = self.state.read_bytes()
        symlink = self.directory / "state-symlink.json"
        symlink.symlink_to(self.state)
        result = self.run_cli("audit", "--state", str(self.state), "--output", str(symlink))
        self.assertEqual(result.returncode, 2)
        self.assertIn("regular non-symlink", result.stderr)
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(symlink.read_bytes(), before)

        hardlink = self.directory / "state-hardlink.json"
        os.link(self.state, hardlink)
        result = self.run_cli("audit", "--state", str(self.state), "--output", str(hardlink))
        self.assertEqual(result.returncode, 2)
        self.assertIn("must not have hardlink aliases", result.stderr)
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(hardlink.read_bytes(), before)
        validation = self.run_cli("validate", "--state", str(self.state))
        self.assertEqual(validation.returncode, 0, validation.stderr)
        self.assertTrue(json.loads(validation.stdout)["valid"])

    def test_audit_writes_report_through_a_new_parent_directory(self) -> None:
        self.prepare_verified_state()
        report = self.directory / "new" / "nested" / "audit.json"
        result = self.run_cli("audit", "--state", str(self.state), "--output", str(report))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(report.read_text(encoding="utf-8"))["ok"])
        validation = self.run_cli("validate", "--state", str(self.state))
        self.assertEqual(validation.returncode, 0, validation.stderr)
        self.assertTrue(json.loads(validation.stdout)["valid"])

    def test_audit_report_swap_after_preflight_cannot_overwrite_checkpoint(self) -> None:
        self.prepare_verified_state()
        report = self.directory / "report.json"
        report.write_text('{"old": true}\n', encoding="utf-8")
        before = self.state.read_bytes()
        original_preflight = continuity.audit_output_targets_state

        def preflight_then_swap(state_path: str, output_path: str) -> bool:
            result = original_preflight(state_path, output_path)
            Path(output_path).unlink()
            os.link(state_path, output_path)
            return result

        continuity.audit_output_targets_state = preflight_then_swap
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr):
                exit_code = continuity.main(["audit", "--state", str(self.state), "--output", str(report)])
        finally:
            continuity.audit_output_targets_state = original_preflight

        self.assertEqual(exit_code, 2)
        self.assertIn("audit output must not overwrite the checkpoint state", stderr.getvalue())
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(report.read_bytes(), before)
        validation = self.run_cli("validate", "--state", str(self.state))
        self.assertEqual(validation.returncode, 0, validation.stderr)
        self.assertTrue(json.loads(validation.stdout)["valid"])

    def test_audit_state_swap_after_report_open_preserves_checkpoint_and_victim(self) -> None:
        self.prepare_verified_state()
        checkpoint_bytes = self.state.read_bytes()
        for swap_kind in ("symlink", "hardlink"):
            with self.subTest(swap=swap_kind):
                checkpoint = self.directory / f"{swap_kind}-checkpoint.json"
                checkpoint.write_bytes(checkpoint_bytes)
                preserved = self.directory / f"{swap_kind}-preserved.json"
                preserved.write_bytes(checkpoint_bytes)
                victim = self.directory / f"{swap_kind}-victim.json"
                victim_bytes = b"unrelated victim data\n"
                victim.write_bytes(victim_bytes)
                report = self.directory / f"{swap_kind}-report.json"
                report_bytes = b'{"old": true}\n'
                report.write_bytes(report_bytes)
                original_stage = continuity.stage_json

                def stage_then_swap(target: object, value: object, *, label: str) -> object:
                    stage = original_stage(target, value, label=label)
                    if label != "audit report output":
                        return stage
                    checkpoint.unlink()
                    if swap_kind == "symlink":
                        checkpoint.symlink_to(victim)
                    else:
                        os.link(victim, checkpoint)
                    return stage

                continuity.stage_json = stage_then_swap
                stderr = io.StringIO()
                try:
                    with contextlib.redirect_stderr(stderr):
                        exit_code = continuity.main(["audit", "--state", str(checkpoint), "--output", str(report)])
                finally:
                    continuity.stage_json = original_stage

                self.assertEqual(exit_code, 2)
                self.assertRegex(
                    stderr.getvalue(),
                    r"audit checkpoint state (changed before it could be committed|must be a regular non-symlink file)",
                )
                self.assertEqual(victim.read_bytes(), victim_bytes)
                self.assertEqual(report.read_bytes(), report_bytes)
                self.assertEqual(preserved.read_bytes(), checkpoint_bytes)
                self.assert_no_audit_temps()
                validation = self.run_cli("validate", "--state", str(preserved))
                self.assertEqual(validation.returncode, 0, validation.stderr)
                self.assertTrue(json.loads(validation.stdout)["valid"])

    def test_audit_report_parent_swap_after_mkdir_fails_without_writes(self) -> None:
        self.prepare_verified_state()
        before = self.state.read_bytes()
        victim_parent = self.directory / "victim-parent"
        victim_parent.mkdir()
        report_parent = self.directory / "created-report-parent"
        report = report_parent / "report.json"
        original_mkdir = continuity.os.mkdir

        def mkdir_then_swap(path: str, mode: int = 0o777, *, dir_fd: int | None = None) -> None:
            original_mkdir(path, mode, dir_fd=dir_fd)
            if path == report_parent.name and dir_fd is not None:
                report_parent.rmdir()
                report_parent.symlink_to(victim_parent, target_is_directory=True)

        continuity.os.mkdir = mkdir_then_swap
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr):
                exit_code = continuity.main(["audit", "--state", str(self.state), "--output", str(report)])
        finally:
            continuity.os.mkdir = original_mkdir

        self.assertEqual(exit_code, 2)
        self.assertIn("cannot safely open the audit report output parent directory", stderr.getvalue())
        self.assertEqual(self.state.read_bytes(), before)
        self.assertFalse((victim_parent / "report.json").exists())
        validation = self.run_cli("validate", "--state", str(self.state))
        self.assertEqual(validation.returncode, 0, validation.stderr)
        self.assertTrue(json.loads(validation.stdout)["valid"])

    def test_audit_parent_move_after_staging_never_reports_success_and_can_recover(self) -> None:
        self.prepare_verified_state()
        report = self.directory / "parent-move-report.json"
        state_before = self.state.read_bytes()
        moved_parent = self.directory.parent / f"{self.directory.name}-detached"
        original_stage = continuity.stage_json
        moved = False

        def stage_then_move(target: object, value: object, *, label: str) -> object:
            nonlocal moved
            stage = original_stage(target, value, label=label)
            if label == "audit report output":
                os.rename(self.directory, moved_parent)
                self.directory.mkdir()
                moved = True
            return stage

        continuity.stage_json = stage_then_move
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr):
                exit_code = continuity.main(["audit", "--state", str(self.state), "--output", str(report)])
        finally:
            continuity.stage_json = original_stage

        self.assertTrue(moved)
        self.assertEqual(exit_code, 2)
        self.assertIn("parent directory changed", stderr.getvalue())
        self.assertFalse(self.state.exists())
        self.assertFalse(report.exists())
        self.assertEqual((moved_parent / "state.json").read_bytes(), state_before)
        self.assert_json_file(moved_parent / "state.json")
        self.assertEqual(list(moved_parent.rglob(".continuity-*.tmp")), [])

        # Recovery is explicit: restore the detached directory, then retry.
        self.directory.rmdir()
        os.rename(moved_parent, self.directory)
        retry = self.run_cli("audit", "--state", str(self.state), "--output", str(report))
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertTrue(self.assert_json_file(report)["ok"])
        self.assertEqual(self.read_state()["audit"]["status"], "passed")
        self.assert_no_audit_temps()

    def test_applied_and_complete_sync_require_destination_verification_evidence(self) -> None:
        self.prepare_verified_state()
        original = self.state.read_bytes()
        weak_evidence = {
            "unstructured-string": ["receiver says it worked"],
            "boolean-verification": [
                {
                    "summary": "Receiver says it worked.",
                    "confidence": "verified",
                    "provenance": "receiver",
                    "destination": "receiver",
                    "verification": True,
                }
            ],
            "claimed-narrative": [
                {
                    "summary": "Receiver says it worked.",
                    "confidence": "claimed",
                    "provenance": "receiver",
                    "destination": "receiver",
                    "verification": "Receiver accepted the checkpoint.",
                }
            ],
            "missing-provenance": [
                {
                    "summary": "Receiver verification completed.",
                    "confidence": "verified",
                    "destination": "receiver",
                    "verification": "Receiver accepted the checkpoint.",
                }
            ],
            "empty-destination": [
                {
                    "summary": "Receiver verification completed.",
                    "confidence": "verified",
                    "provenance": "receiver",
                    "destination": "",
                    "verification": "Receiver accepted the checkpoint.",
                }
            ],
            "wrong-destination": [
                {
                    "summary": "Receiver verification completed.",
                    "confidence": "verified",
                    "provenance": "receiver",
                    "destination": "another-target",
                    "verification": "Receiver accepted the checkpoint.",
                }
            ],
        }
        for status in ("applied", "complete"):
            for name, evidence in weak_evidence.items():
                with self.subTest(status=status, evidence=name):
                    event = self.write_json(
                        f"{status}-{name}.json",
                        {
                            "source_tool": "source",
                            "target_tool": "receiver",
                            "status": status,
                            "summary": "Receiver reports success.",
                            "evidence": evidence,
                        },
                    )
                    result = self.run_cli("record-sync", "--state", str(self.state), "--input", str(event))
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("requires destination or verification evidence", result.stderr)
                    self.assertEqual(self.state.read_bytes(), original)

    def test_malformed_checkpoint_containers_and_items_never_trace_back(self) -> None:
        malformed_variants = {
            "continuity-container": {"continuity": []},
            "project-container": {"project": []},
            "acceptance-container": {"acceptance": {}},
            "verification-item": {"verification": ["not-an-object"]},
            "evidence-item": {"evidence": ["not-an-object"]},
            "evidence-fields": {
                "evidence": [
                    {"id": "e1", "kind": "observation", "summary": [], "provenance": "test", "confidence": "verified"},
                    {"id": "e2", "kind": "observation", "summary": 1, "provenance": "test", "confidence": "claimed"},
                ]
            },
            "sync-item": {"sync": [["not-an-object"]]},
            "idempotency-container": {"idempotency": []},
        }
        baseline = self.read_state()
        for name, mutation in malformed_variants.items():
            with self.subTest(name=name):
                state = json.loads(json.dumps(baseline))
                state.update(mutation)
                path = self.write_json(f"{name}.json", state)
                for command in ("validate", "audit", "status"):
                    result = self.run_cli(command, "--state", str(path))
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertNotIn("Traceback", result.stdout)
                    report = json.loads(result.stdout)
                    self.assertFalse(report.get("valid", report.get("ok")))

    def test_validation_enforces_schema_shape_and_status_enums(self) -> None:
        baseline = self.read_state()
        invalid_variants = {
            "unknown-root": {"unpublished": True},
            "unknown-project": {"project": {"id": "demo", "objective": "safe", "surprise": True}},
            "missing-continuity-target": {
                "continuity": {"phase": "audit", "status": "initialized", "source_tool": "source"}
            },
            "bad-evidence-confidence": {
                "evidence": [{"id": "e1", "kind": "observation", "summary": "x", "provenance": "test", "confidence": "maybe"}]
            },
            "bad-sync-status": {
                "sync": [{"id": "s1", "idempotency_key": "k", "source_tool": "source", "target_tool": "target", "status": "success", "summary": "x"}]
            },
            "empty-stored-sync-fields": {
                "sync": [{"id": "", "idempotency_key": "", "source_tool": "", "target_tool": "", "status": "recorded", "summary": ""}]
            },
        }
        for name, mutation in invalid_variants.items():
            with self.subTest(name=name):
                state = json.loads(json.dumps(baseline))
                state.update(mutation)
                path = self.write_json(f"{name}.json", state)
                result = self.run_cli("validate", "--state", str(path))
                self.assertEqual(result.returncode, 1)
                report = json.loads(result.stdout)
                self.assertFalse(report["valid"])
                self.assertTrue(report["errors"])

        empty_sync = json.loads(json.dumps(baseline))
        empty_sync["sync"] = [
            {"id": "", "idempotency_key": "", "source_tool": "", "target_tool": "", "status": "recorded", "summary": ""}
        ]
        path = self.write_json("empty-stored-sync.json", empty_sync)
        result = self.run_cli("validate", "--state", str(path))
        self.assertEqual(result.returncode, 1)
        errors = json.loads(result.stdout)["errors"]
        for field in ("id", "idempotency_key", "source_tool", "target_tool", "summary"):
            self.assertIn(f"sync[0].{field} must be a nonempty string", errors)

        event = self.write_json(
            "bad-event.json",
            {
                "source_tool": "source",
                "target_tool": "target",
                "status": "success",
                "summary": "An unpublished status.",
                "extra": "not allowed by sync-event.schema.json",
            },
        )
        result = self.run_cli("record-sync", "--state", str(self.state), "--input", str(event))
        self.assertEqual(result.returncode, 2)
        self.assertIn("sync input has unexpected field: extra", result.stderr)
        self.assertIn("sync input.status must be one of", result.stderr)

        empty_key = self.write_json(
            "empty-idempotency-key.json",
            {"source_tool": "source", "target_tool": "target", "status": "recorded", "summary": "Recorded.", "idempotency_key": ""},
        )
        result = self.run_cli("record-sync", "--state", str(self.state), "--input", str(empty_key))
        self.assertEqual(result.returncode, 2)
        self.assertIn("sync[0].idempotency_key must be a nonempty string", result.stderr)

    def test_idempotency_deduplicates_retries_and_rejects_same_key_conflicts(self) -> None:
        event = self.write_json(
            "recorded.json",
            {
                "source_tool": "source",
                "target_tool": "target",
                "status": "recorded",
                "summary": "Transferred for review.",
                "idempotency_key": "retry-1",
            },
        )
        first = self.run_cli("record-sync", "--state", str(self.state), "--input", str(event))
        self.assertEqual(first.returncode, 0, first.stderr)
        first_bytes = self.state.read_bytes()
        retry = self.run_cli("record-sync", "--state", str(self.state), "--input", str(event))
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertEqual(self.state.read_bytes(), first_bytes)
        self.assertEqual(len(self.read_state()["sync"]), 1)

        conflict = self.write_json(
            "conflict.json",
            {
                "source_tool": "source",
                "target_tool": "target",
                "status": "recorded",
                "summary": "A different operation reused the key.",
                "idempotency_key": "retry-1",
            },
        )
        result = self.run_cli("record-sync", "--state", str(self.state), "--input", str(conflict))
        self.assertEqual(result.returncode, 2)
        self.assertIn("idempotency_key conflicts", result.stderr)
        self.assertEqual(self.state.read_bytes(), first_bytes)

    def test_audit_partial_state_staging_failure_keeps_old_json_and_cleans_up(self) -> None:
        self.prepare_verified_state()
        report = self.directory / "partial-state-report.json"
        report.write_text('{"old": true}\n', encoding="utf-8")
        state_before = self.state.read_bytes()
        report_before = report.read_bytes()
        descriptors_before = self.descriptor_count()
        original_write = continuity.write_descriptor_payload

        def partial_state(descriptor: int, payload: bytes, *, label: str) -> None:
            if label == "audit checkpoint state":
                os.write(descriptor, payload[: max(1, len(payload) // 2)])
                raise OSError(28, "injected ENOSPC while staging state")
            original_write(descriptor, payload, label=label)

        continuity.write_descriptor_payload = partial_state
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr):
                exit_code = continuity.main(["audit", "--state", str(self.state), "--output", str(report)])
        finally:
            continuity.write_descriptor_payload = original_write

        self.assertEqual(exit_code, 2)
        self.assertIn("ENOSPC", stderr.getvalue())
        self.assertEqual(self.state.read_bytes(), state_before)
        self.assertEqual(report.read_bytes(), report_before)
        self.assert_json_file(self.state)
        self.assert_json_file(report)
        self.assert_no_audit_temps()
        if descriptors_before is not None:
            self.assertEqual(self.descriptor_count(), descriptors_before)

    def test_audit_partial_report_staging_and_fsync_failures_keep_old_json(self) -> None:
        self.prepare_verified_state()
        for failure in ("partial", "fsync"):
            with self.subTest(failure=failure):
                report = self.directory / f"{failure}-report.json"
                report.write_text('{"old": true}\n', encoding="utf-8")
                state_before = self.state.read_bytes()
                report_before = report.read_bytes()
                descriptors_before = self.descriptor_count()
                original_write = continuity.write_descriptor_payload
                original_fsync = continuity.os.fsync

                def partial_report(descriptor: int, payload: bytes, *, label: str) -> None:
                    if label == "audit report output":
                        os.write(descriptor, payload[: max(1, len(payload) // 2)])
                        raise OSError(28, "injected ENOSPC while staging report")
                    original_write(descriptor, payload, label=label)

                calls = 0

                def fail_first_fsync(descriptor: int) -> None:
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        raise OSError(5, "injected fsync failure")
                    original_fsync(descriptor)

                if failure == "partial":
                    continuity.write_descriptor_payload = partial_report
                else:
                    continuity.os.fsync = fail_first_fsync
                stderr = io.StringIO()
                try:
                    with contextlib.redirect_stderr(stderr):
                        exit_code = continuity.main(["audit", "--state", str(self.state), "--output", str(report)])
                finally:
                    continuity.write_descriptor_payload = original_write
                    continuity.os.fsync = original_fsync

                self.assertEqual(exit_code, 2)
                self.assertEqual(self.state.read_bytes(), state_before)
                self.assertEqual(report.read_bytes(), report_before)
                self.assert_json_file(self.state)
                self.assert_json_file(report)
                self.assert_no_audit_temps()
                if descriptors_before is not None:
                    self.assertEqual(self.descriptor_count(), descriptors_before)

    def test_audit_rename_failures_leave_valid_artifacts_and_retry_repairs_report(self) -> None:
        self.prepare_verified_state()
        for failure in ("state", "report"):
            with self.subTest(failure=failure):
                report = self.directory / f"rename-{failure}-report.json"
                report.write_text('{"old": true}\n', encoding="utf-8")
                state_before = self.state.read_bytes()
                report_before = report.read_bytes()
                descriptors_before = self.descriptor_count()
                original_rename = continuity.os.rename
                original_dir_fd_support = continuity.os.supports_dir_fd
                calls = 0

                def fail_selected_rename(source: str, destination: str, *, src_dir_fd: int, dst_dir_fd: int) -> None:
                    nonlocal calls
                    calls += 1
                    if calls == (1 if failure == "state" else 2):
                        raise OSError(5, f"injected {failure} rename failure")
                    original_rename(source, destination, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

                continuity.os.rename = fail_selected_rename
                continuity.os.supports_dir_fd = original_dir_fd_support | {fail_selected_rename}
                stderr = io.StringIO()
                try:
                    with contextlib.redirect_stderr(stderr):
                        exit_code = continuity.main(["audit", "--state", str(self.state), "--output", str(report)])
                finally:
                    continuity.os.rename = original_rename
                    continuity.os.supports_dir_fd = original_dir_fd_support

                self.assertEqual(exit_code, 2)
                self.assertIn("cannot safely commit", stderr.getvalue())
                self.assert_json_file(self.state)
                self.assert_json_file(report)
                self.assert_no_audit_temps()
                if descriptors_before is not None:
                    self.assertEqual(self.descriptor_count(), descriptors_before)
                if failure == "state":
                    self.assertEqual(self.state.read_bytes(), state_before)
                    self.assertEqual(report.read_bytes(), report_before)
                else:
                    self.assertEqual(self.read_state()["audit"]["status"], "passed")
                    self.assertEqual(report.read_bytes(), report_before)
                    retry = self.run_cli("audit", "--state", str(self.state), "--output", str(report))
                    self.assertEqual(retry.returncode, 0, retry.stderr)
                    self.assertTrue(self.assert_json_file(report)["ok"])
                    self.assertEqual(self.read_state()["audit"]["status"], "passed")
                    self.assert_no_audit_temps()

    def test_secret_detection_rejects_real_tokens_without_false_positive_words(self) -> None:
        safe = self.write_json(
            "safe.json",
            {
                "summary": "Keep task-idempotency, skill-installer, and skip-review as ordinary labels.",
                "confidence": "verified",
            },
        )
        result = self.run_cli("capture", "--state", str(self.state), "--input", str(safe))
        self.assertEqual(result.returncode, 0, result.stderr)

        # Assemble synthetic fixtures at runtime so no secret-shaped literal is
        # committed or mistaken for a usable credential by push protection.
        secret_values = (
            "-".join(("sk", "proj", "abcdefghijklmnopqrstuvwxyz0123456789")),
            "_".join(("sk", "live", "51QY4Qm9QS6DZu5Es0testtoken12345")),
            "_".join(("sk", "test", "51QY4Qm9QS6DZu5Estesttoken12345")),
        )
        for index, token in enumerate(secret_values):
            with self.subTest(token=token[:7]):
                secret = self.write_json(
                    f"secret-{index}.json",
                    {"summary": f"Credential {token} must not be transferred.", "confidence": "verified"},
                )
                result = self.run_cli("capture", "--state", str(self.state), "--input", str(secret))
                self.assertEqual(result.returncode, 2)
                self.assertIn("prohibited local-only value", result.stderr)

    def test_launch_cap_counts_the_final_newline(self) -> None:
        state = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        state["context"]["summary"] = ""
        original_cap = continuity.CONTEXT_CAP
        continuity.CONTEXT_CAP = 100_000
        try:
            baseline = continuity.launch_prompt(state, "receiver")
            state["context"]["summary"] = "x" * (continuity.CONTEXT_CAP - len(baseline.encode("utf-8")))
            exact = continuity.launch_prompt(state, "receiver")
            self.assertEqual(len(exact.encode("utf-8")), continuity.CONTEXT_CAP)

            state["context"]["summary"] += "x"
            with self.assertRaisesRegex(continuity.ContinuityError, "launch prompt exceeds"):
                continuity.launch_prompt(state, "receiver")
        finally:
            continuity.CONTEXT_CAP = original_cap

    def test_render_requires_the_prepared_target_and_preserves_checkpoint(self) -> None:
        self.prepare_verified_state()
        before = self.state.read_bytes()
        output = self.directory / "launch.txt"

        mismatch = self.run_cli(
            "render",
            "--state",
            str(self.state),
            "--target-tool",
            "different-target",
            "--output",
            str(output),
        )
        self.assertEqual(mismatch.returncode, 2)
        self.assertIn("render target must match the prepared target tool", mismatch.stderr)
        self.assertFalse(output.exists())
        self.assertEqual(self.state.read_bytes(), before)

        overwrite = self.run_cli(
            "render",
            "--state",
            str(self.state),
            "--target-tool",
            "receiver",
            "--output",
            str(self.state),
        )
        self.assertEqual(overwrite.returncode, 2)
        self.assertIn("must not overwrite the checkpoint state", overwrite.stderr)
        self.assertEqual(self.state.read_bytes(), before)
        self.assertTrue(json.loads(self.state.read_text(encoding="utf-8")))

    def test_render_is_atomic_idempotent_and_rejects_output_aliases(self) -> None:
        self.prepare_verified_state()
        before = self.state.read_bytes()
        output = self.directory / "new" / "nested" / "claude.txt"

        first = self.run_cli(
            "render",
            "--state",
            str(self.state),
            "--target-tool",
            "receiver",
            "--output",
            str(output),
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        first_bytes = output.read_bytes()
        self.assertIn(b"TARGET_TOOL: receiver", first_bytes)
        second = self.run_cli(
            "render",
            "--state",
            str(self.state),
            "--target-tool",
            "receiver",
            "--output",
            str(output),
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(output.read_bytes(), first_bytes)
        self.assertEqual(self.state.read_bytes(), before)

        alias = self.directory / "state-alias.txt"
        alias.symlink_to(self.state)
        rejected = self.run_cli(
            "render",
            "--state",
            str(self.state),
            "--target-tool",
            "receiver",
            "--output",
            str(alias),
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("regular non-symlink", rejected.stderr)
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(alias.read_bytes(), before)
        self.assert_no_audit_temps()


if __name__ == "__main__":
    unittest.main(verbosity=2)
