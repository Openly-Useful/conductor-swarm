#!/usr/bin/env python3
"""Deterministic, provider-neutral continuity checkpoint operations.

The script deliberately uses only the Python standard library so a checkpoint
can be produced and consumed in a minimal host environment.
"""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from pathlib import Path
from typing import Any


VERSION = "1.1.0"
CONTEXT_CAP = 32 * 1024
PHASES = ("audit", "prepare", "sync", "switch", "review", "resume")
CONTINUITY_STATUSES = ("initialized", "ready", "blocked", "in_progress", "complete")
SYNC_STATUSES = ("recorded", "applied", "conflict", "blocked", "complete")
SAFE_DIR_FD_SUPPORTED = os.open in os.supports_dir_fd and os.mkdir in os.supports_dir_fd
STATE_FIELDS = {
    "schema_version",
    "kind",
    "project",
    "continuity",
    "context",
    "acceptance",
    "verification",
    "artifacts",
    "evidence",
    "sync",
    "audit",
    "idempotency",
}
PROJECT_FIELDS = {"id", "objective", "scope"}
CONTINUITY_FIELDS = {"phase", "status", "source_tool", "target_tool"}
SYNC_INPUT_FIELDS = {
    "id",
    "idempotency_key",
    "source_tool",
    "target_tool",
    "status",
    "summary",
    "provenance",
    "evidence",
}
PROHIBITED_KEY_PARTS = (
    "absolute_path",
    "credential",
    "environment",
    "hostname",
    "machine_id",
    "password",
    "private_key",
    "secret",
    "socket",
    "token",
)
ABSOLUTE_PATH = re.compile(r"^(?:/|[A-Za-z]:[\\/]|\\\\)")
LOCAL_VALUE = re.compile(
    r"(?:^|[\s=/])(?:file://|localhost(?:$|[:/])|127\.0\.0\.1(?:$|[:/])|0\.0\.0\.0(?:$|[:/])|[a-z0-9-]+\.local(?:$|[:/]))",
    re.IGNORECASE,
)
SECRET_VALUE = re.compile(
    r"(?:"
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"
    r"|\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"
    r"|\bsk_(?:live|test)_[A-Za-z0-9]{16,}\b"
    r"|\bghp_[A-Za-z0-9]{36}\b"
    r"|\bgithub_pat_[A-Za-z0-9_]{22,}\b"
    r"|\bxox[baprs]-\d{6,}-[A-Za-z0-9-]{10,}\b"
    r"|\bAIza[A-Za-z0-9_-]{35}\b"
    r")"
)


class ContinuityError(ValueError):
    """A user-correctable checkpoint or command error."""


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def read_json(path: str | None, *, default: Any = None) -> Any:
    if path in (None, "-"):
        if path == "-" or not sys.stdin.isatty():
            raw = sys.stdin.read()
            if raw.strip():
                try:
                    return json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ContinuityError(f"invalid JSON on stdin: {exc}") from exc
        if default is not None:
            return copy.deepcopy(default)
        raise ContinuityError("JSON input is required")
    source = Path(path)
    try:
        return json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContinuityError(f"input file not found: {source}") from exc
    except json.JSONDecodeError as exc:
        raise ContinuityError(f"invalid JSON in {source}: {exc}") from exc


def write_json(path: str | None, value: Any) -> None:
    output = canonical(value) + "\n"
    if path in (None, "-"):
        sys.stdout.write(output)
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


@dataclass
class AuditTarget:
    parent_descriptor: int
    name: str
    identity: tuple[int, int] | None
    parent_identity: tuple[int, int]
    lexical_path: str
    checkpoint: bool = False


@dataclass
class AuditStage:
    target: AuditTarget
    temporary_name: str
    replacement_identity: tuple[int, int]
    payload_digest: str


def audit_output_targets_state(state_path: str, output_path: str) -> bool:
    """Fast lexical guard; held descriptor checks enforce actual identity."""
    state = Path(state_path)
    output = Path(output_path)
    if not state.is_absolute():
        state = Path.cwd() / state
    if not output.is_absolute():
        output = Path.cwd() / output
    return state == output


def file_identity(stat_result: os.stat_result) -> tuple[int, int]:
    return stat_result.st_dev, stat_result.st_ino


def require_safe_descriptor_support() -> tuple[int, int]:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory is None or not SAFE_DIR_FD_SUPPORTED or os.rename not in os.supports_dir_fd:
        raise ContinuityError("safe audit file operations are unavailable on this platform")
    return nofollow, directory


def lexical_absolute_path(path: str) -> Path:
    value = Path(path)
    return value if value.is_absolute() else Path.cwd() / value


def open_target_parent(path: str, *, create_missing: bool, label: str) -> tuple[int, str]:
    """Traverse lexical components with dir-FDs; reject links and '..'."""
    nofollow, directory = require_safe_descriptor_support()
    target = lexical_absolute_path(path)
    parts = target.parts
    if len(parts) < 2 or any(part == ".." for part in parts):
        raise ContinuityError(f"{label} path is invalid")
    name = parts[-1]
    if name in {"", ".", ".."}:
        raise ContinuityError(f"{label} path is invalid")
    try:
        descriptor = os.open("/", os.O_RDONLY | directory | nofollow)
    except OSError as exc:
        raise ContinuityError(f"cannot safely open the {label} parent directory") from exc
    try:
        for component in parts[1:-1]:
            if component in {"", "."}:
                continue
            try:
                next_descriptor = os.open(component, os.O_RDONLY | directory | nofollow, dir_fd=descriptor)
            except FileNotFoundError:
                if not create_missing:
                    raise ContinuityError(f"cannot safely open the {label} parent directory")
                try:
                    os.mkdir(component, 0o777, dir_fd=descriptor)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise ContinuityError(f"cannot safely create the {label} parent directory") from exc
                try:
                    next_descriptor = os.open(component, os.O_RDONLY | directory | nofollow, dir_fd=descriptor)
                except OSError as exc:
                    raise ContinuityError(f"cannot safely open the {label} parent directory") from exc
            except OSError as exc:
                raise ContinuityError(f"cannot safely open the {label} parent directory") from exc
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor, name
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def inspect_regular_target(parent_descriptor: int, name: str, *, label: str) -> os.stat_result | None:
    try:
        target_stat = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ContinuityError(f"cannot safely inspect the {label}") from exc
    if stat.S_ISLNK(target_stat.st_mode) or not stat.S_ISREG(target_stat.st_mode):
        raise ContinuityError(f"{label} must be a regular non-symlink file")
    return target_stat


def open_checkpoint_state(path: str) -> tuple[AuditTarget, int]:
    """Hold a non-aliased checkpoint descriptor from read through commit."""
    nofollow, _ = require_safe_descriptor_support()
    parent_descriptor, name = open_target_parent(path, create_missing=False, label="audit checkpoint state")
    try:
        expected = inspect_regular_target(parent_descriptor, name, label="audit checkpoint state")
        if expected is None:
            raise ContinuityError("cannot safely open the audit checkpoint state")
        if expected.st_nlink != 1:
            raise ContinuityError("audit checkpoint state must not have hardlink aliases")
        descriptor = os.open(name, os.O_RDONLY | nofollow, dir_fd=parent_descriptor)
    except Exception:
        os.close(parent_descriptor)
        raise
    try:
        actual = os.fstat(descriptor)
        if file_identity(actual) != file_identity(expected) or actual.st_nlink != 1:
            raise ContinuityError("audit checkpoint state changed while opening")
        return AuditTarget(
            parent_descriptor,
            name,
            file_identity(actual),
            file_identity(os.fstat(parent_descriptor)),
            str(lexical_absolute_path(path)),
            checkpoint=True,
        ), descriptor
    except Exception:
        os.close(descriptor)
        os.close(parent_descriptor)
        raise


def open_report_target(path: str, state_identity: tuple[int, int]) -> AuditTarget:
    parent_descriptor, name = open_target_parent(path, create_missing=True, label="audit report output")
    try:
        target_stat = inspect_regular_target(parent_descriptor, name, label="audit report output")
        identity = file_identity(target_stat) if target_stat is not None else None
        if identity == state_identity:
            raise ContinuityError("audit output must not overwrite the checkpoint state")
        return AuditTarget(
            parent_descriptor,
            name,
            identity,
            file_identity(os.fstat(parent_descriptor)),
            str(lexical_absolute_path(path)),
        )
    except Exception:
        os.close(parent_descriptor)
        raise


def read_json_descriptor(descriptor: int, *, label: str) -> Any:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return json.loads(b"".join(chunks).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContinuityError(f"invalid JSON in {label}") from exc


def write_descriptor_payload(descriptor: int, payload: bytes, *, label: str) -> None:
    try:
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            if count <= 0:
                raise OSError(f"could not write {label}")
            written += count
        os.fsync(descriptor)
    except OSError as exc:
        raise ContinuityError(f"cannot safely stage the {label}") from exc


def stage_json(target: AuditTarget, value: Any, *, label: str) -> AuditStage:
    """Write/fsync a unique same-directory replacement without touching target."""
    nofollow, _ = require_safe_descriptor_support()
    name = f".continuity-{target.name}.{os.getpid()}.{secrets.token_hex(12)}.tmp"
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    descriptor: int | None = None
    try:
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow, 0o600, dir_fd=target.parent_descriptor)
        write_descriptor_payload(descriptor, payload, label=label)
        replacement_identity = file_identity(os.fstat(descriptor))
        os.close(descriptor)
        descriptor = None
        return AuditStage(target, name, replacement_identity, hashlib.sha256(payload).hexdigest())
    except Exception:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            os.unlink(name, dir_fd=target.parent_descriptor)
        except OSError:
            pass
        raise


def target_matches_stage(stage: AuditStage, *, label: str) -> bool:
    current = inspect_regular_target(stage.target.parent_descriptor, stage.target.name, label=label)
    if stage.target.identity is None:
        return current is None
    if current is None or file_identity(current) != stage.target.identity:
        return False
    return not stage.target.checkpoint or current.st_nlink == 1


def current_parent_matches_target(target: AuditTarget, *, label: str) -> bool:
    """Ensure the lexical parent still names the held directory before use."""
    descriptor: int | None = None
    try:
        descriptor, name = open_target_parent(target.lexical_path, create_missing=False, label=label)
        return name == target.name and file_identity(os.fstat(descriptor)) == target.parent_identity
    except (ContinuityError, OSError):
        return False
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def committed_target_matches_stage(stage: AuditStage, *, label: str) -> bool:
    """Confirm the lexical name still resolves to our replacement bytes."""
    nofollow, _ = require_safe_descriptor_support()
    descriptor: int | None = None
    try:
        current = inspect_regular_target(stage.target.parent_descriptor, stage.target.name, label=label)
        if current is None or file_identity(current) != stage.replacement_identity:
            return False
        descriptor = os.open(stage.target.name, os.O_RDONLY | nofollow, dir_fd=stage.target.parent_descriptor)
        if file_identity(os.fstat(descriptor)) != stage.replacement_identity:
            return False
        payload = bytearray()
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            payload.extend(chunk)
        return hashlib.sha256(payload).hexdigest() == stage.payload_digest
    except OSError:
        return False
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def commit_stage(stage: AuditStage, *, label: str) -> None:
    if not current_parent_matches_target(stage.target, label=label):
        raise ContinuityError(f"{label} parent directory changed before it could be committed")
    if not target_matches_stage(stage, label=label):
        raise ContinuityError(f"{label} changed before it could be committed")
    try:
        os.rename(stage.temporary_name, stage.target.name, src_dir_fd=stage.target.parent_descriptor, dst_dir_fd=stage.target.parent_descriptor)
        stage.temporary_name = ""
        if not current_parent_matches_target(stage.target, label=label):
            raise ContinuityError(f"{label} parent directory changed during commit")
        os.fsync(stage.target.parent_descriptor)
    except OSError as exc:
        raise ContinuityError(f"cannot safely commit the {label}") from exc
    if not current_parent_matches_target(stage.target, label=label):
        raise ContinuityError(f"{label} parent directory changed during commit")
    if not committed_target_matches_stage(stage, label=label):
        raise ContinuityError(f"{label} changed during commit")


def cleanup_stage(stage: AuditStage | None) -> None:
    if stage is None or not stage.temporary_name:
        return
    try:
        os.unlink(stage.temporary_name, dir_fd=stage.target.parent_descriptor)
    except OSError:
        pass


def text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return canonical(value)


def walk_violations(value: Any, path: str = "$", *, key: str = "") -> list[str]:
    violations: list[str] = []
    key_lower = key.casefold().replace("-", "_")
    if any(part in key_lower for part in PROHIBITED_KEY_PARTS):
        violations.append(f"{path}: prohibited local-only field name")
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            if not isinstance(child_key, str):
                violations.append(f"{path}: object keys must be strings")
                continue
            violations.extend(walk_violations(child_value, f"{path}.{child_key}", key=child_key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            violations.extend(walk_violations(child, f"{path}[{index}]", key=key))
    elif isinstance(value, str):
        if ABSOLUTE_PATH.search(value) or LOCAL_VALUE.search(value) or SECRET_VALUE.search(value):
            violations.append(f"{path}: prohibited local-only value")
        if key_lower in {"path", "file", "artifact_path", "working_directory"} and (".." in Path(value).parts):
            violations.append(f"{path}: path must stay repository-relative")
    return violations


def context_bytes(state: dict[str, Any]) -> int:
    # Evidence can be large, but the transferable context must remain bounded.
    transferable = {
        "project": state.get("project"),
        "continuity": state.get("continuity"),
        "context": state.get("context"),
        "acceptance": state.get("acceptance"),
        "verification": state.get("verification"),
        "artifacts": state.get("artifacts"),
        "audit": state.get("audit"),
        "sync": state.get("sync"),
    }
    return len(canonical(transferable).encode("utf-8"))


def required_field_errors(value: dict[str, Any], required: set[str], path: str) -> list[str]:
    return [f"{path} missing required field: {field}" for field in sorted(required - set(value))]


def unexpected_field_errors(value: dict[str, Any], allowed: set[str], path: str) -> list[str]:
    return [f"{path} has unexpected field: {field}" for field in sorted(set(value) - allowed)]


def string_field_errors(value: dict[str, Any], fields: tuple[str, ...], path: str) -> list[str]:
    return [f"{path}.{field} must be a string" for field in fields if field in value and not isinstance(value[field], str)]


def nonempty_string_field_errors(value: dict[str, Any], fields: tuple[str, ...], path: str) -> list[str]:
    return [
        f"{path}.{field} must be a nonempty string"
        for field in fields
        if isinstance(value.get(field), str) and not value[field].strip()
    ]


def validate_evidence_item(item: Any, path: str) -> list[str]:
    if not isinstance(item, dict):
        return [f"{path} must be an object"]
    errors = required_field_errors(item, {"id", "kind", "summary", "provenance", "confidence"}, path)
    errors.extend(string_field_errors(item, ("id", "kind", "summary", "provenance", "confidence"), path))
    errors.extend(nonempty_string_field_errors(item, ("id", "kind", "summary", "provenance"), path))
    if item.get("confidence") not in {"verified", "claimed", "unknown"}:
        errors.append(f"{path}.confidence must be one of verified, claimed, unknown")
    return errors


def validate_sync_item(item: Any, path: str) -> list[str]:
    if not isinstance(item, dict):
        return [f"{path} must be an object"]
    errors = required_field_errors(item, {"id", "idempotency_key", "source_tool", "target_tool", "status", "summary"}, path)
    errors.extend(string_field_errors(item, ("id", "idempotency_key", "source_tool", "target_tool", "status", "summary"), path))
    errors.extend(
        nonempty_string_field_errors(item, ("id", "idempotency_key", "source_tool", "target_tool", "summary"), path)
    )
    if item.get("status") not in SYNC_STATUSES:
        errors.append(f"{path}.status must be one of " + ", ".join(SYNC_STATUSES))
    if item.get("status") in {"applied", "complete"} and not has_destination_verification_evidence(item):
        errors.append(f"{path} with status {item.get('status')} requires destination or verification evidence")
    return errors


def has_verified_evidence(state: dict[str, Any]) -> bool:
    evidence = state.get("evidence")
    return isinstance(evidence, list) and any(
        isinstance(item, dict)
        and item.get("confidence") == "verified"
        and all(isinstance(item.get(field), str) and item[field].strip() for field in ("id", "kind", "summary", "provenance"))
        for item in evidence
    )


def has_destination_verification_evidence(event: dict[str, Any]) -> bool:
    """Return whether a terminal sync has persisted evidence that can support it."""
    evidence = event.get("evidence")
    if not isinstance(evidence, list):
        return False
    target = event.get("target_tool")
    if not isinstance(target, str) or not target.strip():
        return False
    for item in evidence:
        if not isinstance(item, dict):
            continue
        if (
            item.get("confidence") == "verified"
            and all(
                isinstance(item.get(field), str) and item[field].strip()
                for field in ("provenance", "destination", "verification")
            )
            and item["destination"] == target
        ):
            return True
    return False


def preparation_basis_errors(state: dict[str, Any]) -> list[str]:
    """Require a persisted audit result and verified evidence before readiness."""
    errors: list[str] = []
    audit = state.get("audit")
    if not isinstance(audit, dict) or audit.get("status") != "passed":
        errors.append("prepared state requires persisted audit.status 'passed'")
    if not has_verified_evidence(state):
        errors.append("prepared state requires persisted verified evidence")
    return errors


def verified_evidence_ids(state: dict[str, Any]) -> list[str]:
    evidence = state.get("evidence")
    if not isinstance(evidence, list):
        return []
    return sorted(
        {
            item["id"]
            for item in evidence
            if isinstance(item, dict)
            and item.get("confidence") == "verified"
            and all(isinstance(item.get(field), str) and item[field].strip() for field in ("id", "kind", "summary", "provenance"))
        }
    )


def validate_state(state: Any, *, require_prepared: bool = False) -> list[str]:
    """Validate the published checkpoint schema plus safety-critical lifecycle rules."""
    errors: list[str] = []
    if not isinstance(state, dict):
        return ["state must be a JSON object"]

    errors.extend(required_field_errors(state, STATE_FIELDS, "state"))
    errors.extend(unexpected_field_errors(state, STATE_FIELDS, "state"))
    if state.get("schema_version") != VERSION:
        errors.append(f"schema_version must be {VERSION}")
    if state.get("kind") != "cross-tool-continuity":
        errors.append("kind must be cross-tool-continuity")

    project = state.get("project")
    if not isinstance(project, dict):
        errors.append("project must be an object")
    else:
        errors.extend(required_field_errors(project, {"id", "objective"}, "project"))
        errors.extend(unexpected_field_errors(project, PROJECT_FIELDS, "project"))
        errors.extend(string_field_errors(project, ("id", "objective", "scope"), "project"))
        if isinstance(project.get("id"), str) and not project["id"]:
            errors.append("project.id must be a nonempty string")
        if isinstance(project.get("objective"), str) and not project["objective"]:
            errors.append("project.objective must be a nonempty string")

    continuity = state.get("continuity")
    if not isinstance(continuity, dict):
        errors.append("continuity must be an object")
    else:
        errors.extend(required_field_errors(continuity, CONTINUITY_FIELDS, "continuity"))
        errors.extend(unexpected_field_errors(continuity, CONTINUITY_FIELDS, "continuity"))
        if continuity.get("phase") not in PHASES:
            errors.append("continuity.phase must be one of " + ", ".join(PHASES))
        if continuity.get("status") not in CONTINUITY_STATUSES:
            errors.append("continuity.status must be one of " + ", ".join(CONTINUITY_STATUSES))
        for field in ("source_tool", "target_tool"):
            if field in continuity and continuity[field] is not None and not isinstance(continuity[field], str):
                errors.append(f"continuity.{field} must be a string or null")

    context = state.get("context")
    if not isinstance(context, dict):
        errors.append("context must be an object")

    for field in ("acceptance", "verification", "artifacts", "evidence", "sync"):
        if not isinstance(state.get(field), list):
            errors.append(f"{field} must be an array")
    if not isinstance(state.get("audit"), dict):
        errors.append("audit must be an object")
    idempotency = state.get("idempotency")
    if not isinstance(idempotency, dict):
        errors.append("idempotency must be an object")
    else:
        errors.extend(required_field_errors(idempotency, {"algorithm", "state_id"}, "idempotency"))

    acceptance = state.get("acceptance")
    if isinstance(acceptance, list):
        for index, item in enumerate(acceptance):
            path = f"acceptance[{index}]"
            if not isinstance(item, dict):
                errors.append(f"{path} must be an object")
                continue
            errors.extend(required_field_errors(item, {"id", "criterion"}, path))
            errors.extend(string_field_errors(item, ("id", "criterion"), path))

    for field in ("verification", "artifacts"):
        items = state.get(field)
        if isinstance(items, list):
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    errors.append(f"{field}[{index}] must be an object")

    evidence = state.get("evidence")
    if isinstance(evidence, list):
        for index, item in enumerate(evidence):
            errors.extend(validate_evidence_item(item, f"evidence[{index}]"))

    sync = state.get("sync")
    if isinstance(sync, list):
        for index, item in enumerate(sync):
            errors.extend(validate_sync_item(item, f"sync[{index}]"))

    if context_bytes(state) > CONTEXT_CAP:
        errors.append(f"transferable context exceeds {CONTEXT_CAP} UTF-8 bytes")
    errors.extend(walk_violations(state))

    readiness_claimed = isinstance(continuity, dict) and continuity.get("status") == "ready"
    if require_prepared or readiness_claimed:
        errors.extend(preparation_basis_errors(state))
    if require_prepared:
        if not isinstance(continuity, dict) or continuity.get("phase") not in {"prepare", "switch", "review", "resume"}:
            errors.append("state must be prepared before switching")
        if not isinstance(continuity, dict) or continuity.get("status") != "ready":
            errors.append("prepared state must have continuity.status ready")
        if not isinstance(context, dict) or not isinstance(context.get("next_action"), str) or not context["next_action"].strip():
            errors.append("prepared state needs context.next_action")
    if isinstance(continuity, dict) and continuity.get("phase") == "sync" and continuity.get("status") == "ready":
        if not isinstance(sync, list) or not any(
            isinstance(item, dict)
            and item.get("status") in {"applied", "complete"}
            and has_destination_verification_evidence(item)
            for item in sync
        ):
            errors.append("ready sync state requires applied or complete destination verification evidence")
    return sorted(set(errors))


def base_state(args: argparse.Namespace) -> dict[str, Any]:
    project = {"id": args.project_id, "objective": args.objective}
    if args.scope:
        project["scope"] = args.scope
    state: dict[str, Any] = {
        "schema_version": VERSION,
        "kind": "cross-tool-continuity",
        "project": project,
        "continuity": {"phase": "audit", "status": "initialized", "source_tool": args.source_tool, "target_tool": None},
        "context": {"summary": "", "decisions": [], "constraints": [], "next_action": "Audit primary artifacts before editing.", "open_questions": []},
        "acceptance": [],
        "verification": [],
        "artifacts": [],
        "evidence": [],
        "sync": [],
        "audit": {"status": "pending", "findings": []},
        "idempotency": {"algorithm": "sha256-canonical-json", "state_id": digest(project)},
    }
    return state


def evidence_from_input(value: Any, *, default_kind: str = "observation") -> dict[str, Any]:
    if isinstance(value, str):
        value = {"summary": value}
    if not isinstance(value, dict):
        raise ContinuityError("evidence or sync input must be an object or string")
    result = copy.deepcopy(value)
    result.setdefault("kind", default_kind)
    if not isinstance(result.get("summary"), str) or not result["summary"].strip():
        raise ContinuityError("evidence.summary must be a nonempty string")
    if "provenance" not in result:
        result["provenance"] = "user-supplied"
    if "confidence" not in result:
        result["confidence"] = "unknown"
    for field in ("kind", "provenance", "confidence"):
        if not isinstance(result.get(field), str):
            raise ContinuityError(f"evidence.{field} must be a string")
    for field in ("kind", "provenance"):
        if not result[field].strip():
            raise ContinuityError(f"evidence.{field} must be a nonempty string")
    if result["confidence"] not in {"verified", "claimed", "unknown"}:
        raise ContinuityError("evidence.confidence must be one of verified, claimed, unknown")
    result["id"] = "ev_" + digest({k: v for k, v in result.items() if k != "id"})[:24]
    return result


def insert_by_id(items: list[dict[str, Any]], item: dict[str, Any]) -> bool:
    for existing in items:
        if existing.get("id") == item.get("id"):
            return False
    items.append(item)
    items.sort(key=lambda entry: str(entry.get("id", "")))
    return True


def command_init(args: argparse.Namespace) -> int:
    state = base_state(args)
    errors = validate_state(state)
    if errors:
        raise ContinuityError("; ".join(errors))
    write_json(args.output, state)
    return 0


def command_validate(args: argparse.Namespace) -> int:
    state = read_json(args.state)
    errors = validate_state(state)
    result = {"valid": not errors, "schema_version": VERSION, "errors": errors}
    write_json(args.output, result)
    return 0 if not errors else 1


def command_audit(args: argparse.Namespace) -> int:
    if args.state in (None, "-"):
        raise ContinuityError("audit requires a state file so its result can be persisted")
    state_target, state_descriptor = open_checkpoint_state(args.state)
    report_target: AuditTarget | None = None
    state_stage: AuditStage | None = None
    report_stage: AuditStage | None = None
    try:
        state = read_json_descriptor(state_descriptor, label="audit checkpoint state")
        errors = validate_state(state)
        if isinstance(state, dict) and not has_verified_evidence(state):
            errors.append("audit requires persisted verified evidence")
        if args.output not in (None, "-") and audit_output_targets_state(args.state, args.output):
            raise ContinuityError("audit output must not overwrite the checkpoint state")
        errors = sorted(set(errors))
        evidence = state.get("evidence", []) if isinstance(state, dict) else []
        evidence_items = [
            item for item in evidence if isinstance(item, dict) and isinstance(item.get("summary"), str)
        ] if isinstance(evidence, list) else []
        unknowns = [item.get("summary", "") for item in evidence_items if item.get("confidence") == "unknown"]
        claims = [item.get("summary", "") for item in evidence_items if item.get("confidence") == "claimed"]
        verified = [item.get("summary", "") for item in evidence_items if item.get("confidence") == "verified"]
        idempotency = state.get("idempotency") if isinstance(state, dict) else None
        context = state.get("context") if isinstance(state, dict) else None
        report = {
            "contract": "audit",
            "state_id": idempotency.get("state_id") if isinstance(idempotency, dict) else None,
            "ok": not errors,
            "validation_errors": errors,
            "verified": sorted(verified),
            "claims": sorted(claims),
            "unknowns": sorted(unknowns),
            "next_action": context.get("next_action") if isinstance(context, dict) else None,
        }
        if args.output not in (None, "-"):
            report_target = open_report_target(args.output, state_target.identity or (-1, -1))
            report_stage = stage_json(report_target, report, label="audit report output")
        if not errors:
            state["audit"] = {
                "status": "passed",
                "findings": [],
                "evidence_ids": verified_evidence_ids(state),
            }
            state_stage = stage_json(state_target, state, label="audit checkpoint state")
        # Both durable artifacts are fully staged before authoritative state commits.
        if state_stage is not None:
            commit_stage(state_stage, label="audit checkpoint state")
        if report_stage is not None:
            commit_stage(report_stage, label="audit report output")
        if args.output in (None, "-"):
            write_json(args.output, report)
    finally:
        cleanup_stage(report_stage)
        cleanup_stage(state_stage)
        os.close(state_descriptor)
        if report_target is not None:
            os.close(report_target.parent_descriptor)
        os.close(state_target.parent_descriptor)
    return 0 if not errors else 1


def command_capture(args: argparse.Namespace) -> int:
    state = read_json(args.state)
    errors = validate_state(state)
    if errors:
        raise ContinuityError("cannot capture into invalid state: " + "; ".join(errors))
    evidence_input = read_json(args.input)
    item = evidence_from_input(evidence_input)
    violations = walk_violations(item)
    if violations:
        raise ContinuityError("; ".join(violations))
    changed = insert_by_id(state["evidence"], item)
    if changed:
        state["audit"] = {"status": "pending", "findings": []}
        state["continuity"]["phase"] = "audit"
        state["continuity"]["status"] = "in_progress"
    errors = validate_state(state)
    if errors:
        raise ContinuityError("cannot capture invalid evidence: " + "; ".join(errors))
    write_json(args.output or args.state, state)
    return 0


def command_prepare(args: argparse.Namespace) -> int:
    state = read_json(args.state)
    errors = validate_state(state)
    if errors:
        raise ContinuityError("cannot prepare invalid state: " + "; ".join(errors))
    readiness_errors = preparation_basis_errors(state)
    if readiness_errors:
        raise ContinuityError("cannot prepare state: " + "; ".join(readiness_errors))
    state["continuity"]["phase"] = "prepare"
    state["continuity"]["status"] = "ready"
    state["continuity"]["target_tool"] = args.target_tool
    if args.owner:
        state["context"]["owner"] = args.owner
    if args.next_action:
        state["context"]["next_action"] = args.next_action
    errors = validate_state(state, require_prepared=True)
    if errors:
        raise ContinuityError("cannot prepare state: " + "; ".join(errors))
    write_json(args.output or args.state, state)
    return 0


def launch_prompt(state: dict[str, Any], target_tool: str) -> str:
    transferable = {
        "schema_version": state["schema_version"],
        "project": state["project"],
        "continuity": state["continuity"],
        "context": state["context"],
        "acceptance": state["acceptance"],
        "verification": state["verification"],
        "artifacts": state["artifacts"],
        "audit": state["audit"],
    }
    payload = canonical(transferable)
    prompt = "\n".join(
        [
            "BEGIN CONTINUITY LAUNCH PROMPT",
            f"TARGET_TOOL: {target_tool}",
            "ROLE: receiving agent",
            "CONTRACT: review the checkpoint and primary artifacts before editing; preserve scope and authority.",
            "REQUIRED_ORDER: validate -> review -> resume -> capture evidence -> audit",
            "NEXT_SAFE_ACTION: " + str(state["context"].get("next_action", "Review the checkpoint.")),
            "CHECKPOINT_JSON:",
            payload,
            "DO_NOT: treat claims, handoffs, or confidence as proof; use absolute paths, secrets, or unapproved external actions.",
            "END CONTINUITY LAUNCH PROMPT",
        ]
    )
    prompt += "\n"
    if len(prompt.encode("utf-8")) > CONTEXT_CAP:
        raise ContinuityError(f"launch prompt exceeds {CONTEXT_CAP} UTF-8 bytes")
    return prompt


def command_render(args: argparse.Namespace) -> int:
    state = read_json(args.state)
    errors = validate_state(state, require_prepared=True)
    if errors:
        raise ContinuityError("cannot render unprepared state: " + "; ".join(errors))
    target = args.target_tool or state["continuity"].get("target_tool")
    if not target:
        raise ContinuityError("target tool is required")
    sys.stdout.write(launch_prompt(state, target) if args.output in (None, "-") else "")
    if args.output not in (None, "-"):
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(launch_prompt(state, target), encoding="utf-8")
    return 0


def validate_sync_input(value: Any) -> list[str]:
    """Validate the published sync-event schema before deriving identifiers."""
    if not isinstance(value, dict):
        return ["sync input must be an object"]
    errors = required_field_errors(value, {"source_tool", "target_tool", "status", "summary"}, "sync input")
    errors.extend(unexpected_field_errors(value, SYNC_INPUT_FIELDS, "sync input"))
    for field in ("id", "idempotency_key", "source_tool", "target_tool", "status", "summary", "provenance"):
        if field in value and not isinstance(value[field], str):
            errors.append(f"sync input.{field} must be a string")
    for field in ("source_tool", "target_tool", "summary"):
        if isinstance(value.get(field), str) and not value[field].strip():
            errors.append(f"sync input.{field} must be a nonempty string")
    if value.get("status") not in SYNC_STATUSES:
        errors.append("sync input.status must be one of " + ", ".join(SYNC_STATUSES))
    if "evidence" in value and not isinstance(value["evidence"], list):
        errors.append("sync input.evidence must be an array")
    return sorted(set(errors))


def sync_payload(event: dict[str, Any]) -> str:
    """Compare client-visible semantics without the deterministic derived identifier."""
    return canonical({key: value for key, value in event.items() if key != "id"})


def command_record_sync(args: argparse.Namespace) -> int:
    state = read_json(args.state)
    errors = validate_state(state)
    if errors:
        raise ContinuityError("cannot sync invalid state: " + "; ".join(errors))
    raw = read_json(args.input)
    input_errors = validate_sync_input(raw)
    if input_errors:
        raise ContinuityError("invalid sync input: " + "; ".join(input_errors))
    event = copy.deepcopy(raw)
    event.setdefault("provenance", "user-supplied")
    if "idempotency_key" not in event:
        event["idempotency_key"] = "sync_" + digest({k: v for k, v in event.items() if k not in {"id", "idempotency_key"}})[:24]
    event["id"] = "sync_" + digest({k: v for k, v in event.items() if k != "id"})[:24]
    violations = walk_violations(event)
    if violations:
        raise ContinuityError("; ".join(violations))
    if event["status"] in {"applied", "complete"} and not has_destination_verification_evidence(event):
        raise ContinuityError(f"sync status {event['status']} requires destination or verification evidence")
    payload = sync_payload(event)
    for existing in state["sync"]:
        if existing["idempotency_key"] != event["idempotency_key"]:
            continue
        if sync_payload(existing) == payload:
            write_json(args.output or args.state, state)
            return 0
        raise ContinuityError(f"idempotency_key conflicts with existing sync event: {event['idempotency_key']!r}")
    if any(existing["id"] == event["id"] for existing in state["sync"]):
        raise ContinuityError("generated sync event id conflicts with an existing event")
    state["sync"].append(event)
    state["sync"].sort(key=lambda entry: str(entry["id"]))
    state["continuity"]["phase"] = "sync"
    state["continuity"]["status"] = "in_progress" if event.get("status") not in {"applied", "complete"} else "ready"
    errors = validate_state(state)
    if errors:
        raise ContinuityError("cannot record invalid sync state: " + "; ".join(errors))
    write_json(args.output or args.state, state)
    return 0


def command_status(args: argparse.Namespace) -> int:
    state = read_json(args.state)
    errors = validate_state(state)
    continuity_value = state.get("continuity") if isinstance(state, dict) else None
    continuity = continuity_value if isinstance(continuity_value, dict) else {}
    project_value = state.get("project") if isinstance(state, dict) else None
    project = project_value if isinstance(project_value, dict) else {}
    result = {
        "schema_version": state.get("schema_version") if isinstance(state, dict) else None,
        "project_id": project.get("id"),
        "phase": continuity.get("phase"),
        "status": continuity.get("status"),
        "source_tool": continuity.get("source_tool"),
        "target_tool": continuity.get("target_tool"),
        "evidence_count": len(state.get("evidence", [])) if isinstance(state, dict) and isinstance(state.get("evidence"), list) else 0,
        "sync_count": len(state.get("sync", [])) if isinstance(state, dict) and isinstance(state.get("sync"), list) else 0,
        "context_bytes": context_bytes(state) if isinstance(state, dict) else None,
        "context_cap": CONTEXT_CAP,
        "valid": not errors,
        "errors": errors,
    }
    write_json(args.output, result)
    return 0 if not errors else 1


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="create a deterministic empty checkpoint")
    init.add_argument("--output", required=True)
    init.add_argument("--project-id", required=True)
    init.add_argument("--objective", required=True)
    init.add_argument("--scope")
    init.add_argument("--source-tool", default="unknown")
    init.set_defaults(func=command_init)
    for name, function in (("validate", command_validate), ("audit", command_audit), ("status", command_status)):
        command = sub.add_parser(name)
        command.add_argument("--state", required=True)
        command.add_argument("--output", default="-")
        command.set_defaults(func=function)
    capture = sub.add_parser("capture", help="idempotently add evidence")
    capture.add_argument("--state", required=True)
    capture.add_argument("--input", required=True)
    capture.add_argument("--output")
    capture.set_defaults(func=command_capture)
    prepare = sub.add_parser("prepare-switch", aliases=["prepare"])
    prepare.add_argument("--state", required=True)
    prepare.add_argument("--target-tool", "--target", required=True)
    prepare.add_argument("--owner")
    prepare.add_argument("--next-action")
    prepare.add_argument("--output")
    prepare.set_defaults(func=command_prepare)
    render = sub.add_parser("render", help="render a text-block launch prompt")
    render.add_argument("--state", required=True)
    render.add_argument("--target-tool", "--target")
    render.add_argument("--output", default="-")
    render.set_defaults(func=command_render)
    sync = sub.add_parser("record-sync", help="record an idempotent sync event")
    sync.add_argument("--state", required=True)
    sync.add_argument("--input", required=True)
    sync.add_argument("--output")
    sync.set_defaults(func=command_record_sync)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.func(args)
    except (ContinuityError, OSError) as exc:
        print(f"continuity: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
