#!/usr/bin/env python3
"""Deterministic, provider-neutral continuity checkpoint operations.

The script deliberately uses only the Python standard library so a checkpoint
can be produced and consumed in a minimal host environment.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


VERSION = "1.1.0"
CONTEXT_CAP = 32 * 1024
PHASES = ("audit", "prepare", "sync", "switch", "review", "resume")
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
    r"(?:-----BEGIN .*PRIVATE KEY-----|(?:sk|ghp|xox[baprs]-|AIza)[A-Za-z0-9_-]{8,})"
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


def validate_state(state: Any, *, require_prepared: bool = False) -> list[str]:
    errors: list[str] = []
    if not isinstance(state, dict):
        return ["state must be a JSON object"]
    required = {"schema_version", "kind", "project", "continuity", "context", "acceptance", "verification", "artifacts", "evidence", "sync", "audit", "idempotency"}
    missing = sorted(required - set(state))
    if missing:
        errors.append("missing fields: " + ", ".join(missing))
    if state.get("schema_version") != VERSION:
        errors.append(f"schema_version must be {VERSION}")
    if state.get("kind") != "cross-tool-continuity":
        errors.append("kind must be cross-tool-continuity")
    project = state.get("project")
    if not isinstance(project, dict) or not isinstance(project.get("id"), str) or not project.get("id"):
        errors.append("project.id must be a nonempty string")
    if not isinstance(project, dict) or not isinstance(project.get("objective"), str) or not project.get("objective"):
        errors.append("project.objective must be a nonempty string")
    continuity = state.get("continuity")
    if not isinstance(continuity, dict):
        errors.append("continuity must be an object")
    else:
        if continuity.get("phase") not in PHASES:
            errors.append("continuity.phase must be one of " + ", ".join(PHASES))
        if continuity.get("status") not in {"initialized", "ready", "blocked", "in_progress", "complete"}:
            errors.append("continuity.status is invalid")
        for field in ("source_tool", "target_tool"):
            if field in continuity and continuity[field] is not None and not isinstance(continuity[field], str):
                errors.append(f"continuity.{field} must be a string or null")
    for field in ("context", "acceptance", "verification", "artifacts", "evidence", "sync", "audit", "idempotency"):
        if field in state and not isinstance(state[field], (dict, list)):
            errors.append(f"{field} must be an object or list")
    if isinstance(state.get("acceptance"), list):
        for index, item in enumerate(state["acceptance"]):
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("criterion"), str):
                errors.append(f"acceptance[{index}] needs string id and criterion")
    if isinstance(state.get("verification"), list):
        for index, item in enumerate(state["verification"]):
            if not isinstance(item, dict) or not isinstance(item.get("status"), str):
                errors.append(f"verification[{index}] needs a status")
    if context_bytes(state) > CONTEXT_CAP:
        errors.append(f"transferable context exceeds {CONTEXT_CAP} UTF-8 bytes")
    errors.extend(walk_violations(state))
    if require_prepared:
        if continuity.get("phase") not in {"prepare", "switch", "review", "resume"}:
            errors.append("state must be prepared before switching")
        if not isinstance(state.get("context"), dict) or not state["context"].get("next_action"):
            errors.append("prepared state needs context.next_action")
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
        raise ContinuityError("evidence requires a nonempty summary")
    if "provenance" not in result:
        result["provenance"] = "user-supplied"
    if "confidence" not in result:
        result["confidence"] = "unknown"
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
    state = read_json(args.state)
    errors = validate_state(state)
    evidence = state.get("evidence", []) if isinstance(state, dict) else []
    unknowns = [item.get("summary", "") for item in evidence if item.get("confidence") == "unknown"] if isinstance(evidence, list) else []
    claims = [item.get("summary", "") for item in evidence if item.get("confidence") == "claimed"] if isinstance(evidence, list) else []
    verified = [item.get("summary", "") for item in evidence if item.get("confidence") == "verified"] if isinstance(evidence, list) else []
    report = {
        "contract": "audit",
        "state_id": state.get("idempotency", {}).get("state_id") if isinstance(state, dict) else None,
        "ok": not errors,
        "validation_errors": errors,
        "verified": sorted(verified),
        "claims": sorted(claims),
        "unknowns": sorted(unknowns),
        "next_action": state.get("context", {}).get("next_action") if isinstance(state, dict) else None,
    }
    write_json(args.output, report)
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
    write_json(args.output or args.state, state)
    return 0


def command_prepare(args: argparse.Namespace) -> int:
    state = read_json(args.state)
    errors = validate_state(state)
    if errors:
        raise ContinuityError("cannot prepare invalid state: " + "; ".join(errors))
    state["continuity"]["phase"] = "prepare"
    state["continuity"]["status"] = "ready"
    state["continuity"]["target_tool"] = args.target_tool
    if args.owner:
        state["context"]["owner"] = args.owner
    if args.next_action:
        state["context"]["next_action"] = args.next_action
    state["audit"] = {"status": "passed", "findings": []}
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
    if len(prompt.encode("utf-8")) > CONTEXT_CAP:
        raise ContinuityError(f"launch prompt exceeds {CONTEXT_CAP} UTF-8 bytes")
    return prompt + "\n"


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


def command_record_sync(args: argparse.Namespace) -> int:
    state = read_json(args.state)
    errors = validate_state(state)
    if errors:
        raise ContinuityError("cannot sync invalid state: " + "; ".join(errors))
    raw = read_json(args.input)
    if not isinstance(raw, dict):
        raise ContinuityError("sync input must be an object")
    event = copy.deepcopy(raw)
    for field in ("source_tool", "target_tool", "status", "summary"):
        if not isinstance(event.get(field), str) or not event[field].strip():
            raise ContinuityError(f"sync input requires nonempty {field}")
    event.setdefault("provenance", "user-supplied")
    event["idempotency_key"] = event.get("idempotency_key") or "sync_" + digest({k: v for k, v in event.items() if k not in {"id", "idempotency_key"}})[:24]
    event["id"] = "sync_" + digest({k: v for k, v in event.items() if k != "id"})[:24]
    violations = walk_violations(event)
    if violations:
        raise ContinuityError("; ".join(violations))
    insert_by_id(state["sync"], event)
    state["continuity"]["phase"] = "sync"
    state["continuity"]["status"] = "in_progress" if event.get("status") not in {"applied", "complete"} else "ready"
    write_json(args.output or args.state, state)
    return 0


def command_status(args: argparse.Namespace) -> int:
    state = read_json(args.state)
    errors = validate_state(state)
    continuity = state.get("continuity", {}) if isinstance(state, dict) else {}
    result = {
        "schema_version": state.get("schema_version") if isinstance(state, dict) else None,
        "project_id": state.get("project", {}).get("id") if isinstance(state, dict) else None,
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
