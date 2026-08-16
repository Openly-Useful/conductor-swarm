#!/usr/bin/env python3
"""Capture provider-native session lineage in a protected local-only ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any


VERSION = "1.0.0"
SESSION_LIMIT = 1024
PROJECT_LIMIT = 256
SESSION_PATTERN = re.compile(r"^[A-Za-z0-9._:@/-]+$")
PROVIDER_ENV = {"codex": "CODEX_THREAD_ID"}


class LineageError(ValueError):
    """A user-correctable local-lineage error."""


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def default_store() -> Path:
    override = os.environ.get("CONTINUITY_LINEAGE_STORE")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return root / "Openly Useful" / "Cross Tool Continuity" / "session-lineage.json"


def validate_identifier(value: str, *, label: str, limit: int) -> str:
    candidate = value.strip()
    if not candidate or len(candidate.encode("utf-8")) > limit:
        raise LineageError(f"{label} must be between 1 and {limit} UTF-8 bytes")
    if any(ord(character) < 32 or ord(character) == 127 for character in candidate):
        raise LineageError(f"{label} contains control characters")
    return candidate


def session_identifier(provider: str, *, stdin: bool) -> str:
    if stdin:
        value = sys.stdin.read()
    else:
        variable = PROVIDER_ENV.get(provider)
        if variable is None:
            raise LineageError(f"{provider} requires --session-id-stdin from supported lifecycle metadata")
        value = os.environ.get(variable, "")
        if not value:
            raise LineageError(f"host did not expose {variable}")
    identifier = validate_identifier(value, label="provider session identifier", limit=SESSION_LIMIT)
    if not SESSION_PATTERN.fullmatch(identifier):
        raise LineageError("provider session identifier contains unsupported characters")
    return identifier


def workspace_digest(path: str) -> str:
    workspace = Path(path).expanduser().resolve(strict=True)
    if not workspace.is_dir():
        raise LineageError("approved workspace must be a directory")
    return "sha256:" + sha256_text(str(workspace))


def require_store_outside_workspace(store: Path, workspace: str) -> None:
    workspace_path = Path(workspace).expanduser().resolve(strict=True)
    store_path = store.expanduser().resolve(strict=False)
    if store_path == workspace_path or workspace_path in store_path.parents:
        raise LineageError("local lineage store must remain outside the approved workspace")


def ensure_private_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise LineageError("local lineage parent must be a non-symlink directory")
    os.chmod(path.parent, 0o700)


def read_store(path: Path) -> dict[str, Any]:
    ensure_private_parent(path)
    if not path.exists():
        return {"schema_version": VERSION, "links": []}
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise LineageError("local lineage store must be a regular non-symlink file")
    if info.st_uid != os.getuid():
        raise LineageError("local lineage store must be owned by the current user")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise LineageError("local lineage store permissions must be 0600")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LineageError("local lineage store contains invalid JSON") from exc
    if not isinstance(value, dict) or value.get("schema_version") != VERSION or not isinstance(value.get("links"), list):
        raise LineageError("local lineage store has an unsupported schema")
    return value


def write_store(path: Path, value: dict[str, Any]) -> None:
    ensure_private_parent(path)
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=".session-lineage-", suffix=".tmp", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            descriptor = -1
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        parent_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def link_for(args: argparse.Namespace) -> dict[str, str]:
    provider = validate_identifier(args.provider, label="provider", limit=64)
    project_id = validate_identifier(args.project_id, label="project identifier", limit=PROJECT_LIMIT)
    identifier = session_identifier(provider, stdin=args.session_id_stdin)
    workspace = workspace_digest(args.workspace)
    reference = "local_" + sha256_text(canonical({
        "project_id": project_id,
        "provider": provider,
        "provider_session_id": identifier,
        "workspace_digest": workspace,
    }))[:24]
    return {
        "project_id": project_id,
        "provider": provider,
        "provider_session_id": identifier,
        "reference_id": reference,
        "relation": args.relation,
        "workspace_digest": workspace,
    }


def receipt(link: dict[str, str], *, captured: bool) -> dict[str, Any]:
    return {
        "captured": captured,
        "project_id": link["project_id"],
        "provider": link["provider"],
        "reference_id": link["reference_id"],
        "verified": True,
        "workspace_digest": link["workspace_digest"],
    }


def command_capture(args: argparse.Namespace) -> int:
    path = Path(args.store).expanduser()
    require_store_outside_workspace(path, args.workspace)
    link = link_for(args)
    store = read_store(path)
    existing = next((item for item in store["links"] if isinstance(item, dict) and item.get("reference_id") == link["reference_id"]), None)
    if existing is None:
        store["links"].append(link)
        store["links"].sort(key=lambda item: str(item.get("reference_id", "")))
        write_store(path, store)
    elif existing != link:
        raise LineageError("local lineage reference conflicts with its stored value")
    print(canonical(receipt(link, captured=True)))
    return 0


def command_verify(args: argparse.Namespace) -> int:
    path = Path(args.store).expanduser()
    require_store_outside_workspace(path, args.workspace)
    link = link_for(args)
    store = read_store(path)
    if link not in store["links"]:
        raise LineageError("current provider session is not captured in the local lineage store")
    print(canonical(receipt(link, captured=True)))
    return 0


def command_status(args: argparse.Namespace) -> int:
    path = Path(args.store).expanduser()
    require_store_outside_workspace(path, args.workspace)
    project_id = validate_identifier(args.project_id, label="project identifier", limit=PROJECT_LIMIT)
    workspace = workspace_digest(args.workspace)
    store = read_store(path)
    links = [
        {
            "project_id": item["project_id"],
            "provider": item["provider"],
            "reference_id": item["reference_id"],
            "relation": item["relation"],
            "workspace_digest": item["workspace_digest"],
        }
        for item in store["links"]
        if isinstance(item, dict)
        and item.get("project_id") == project_id
        and item.get("workspace_digest") == workspace
    ]
    links.sort(key=lambda item: (item["provider"], item["relation"], item["reference_id"]))
    print(canonical({"captured_count": len(links), "links": links, "project_id": project_id, "workspace_digest": workspace}))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    for name, function in (("capture", command_capture), ("verify", command_verify)):
        command = commands.add_parser(name)
        command.add_argument("--provider", required=True, choices=("codex", "claude-code"))
        command.add_argument("--project-id", required=True)
        command.add_argument("--workspace", required=True)
        command.add_argument("--store", default=str(default_store()))
        command.add_argument("--session-id-stdin", action="store_true")
        command.add_argument("--relation", choices=("source", "successor", "reviewer"), default="source")
        command.set_defaults(func=function)
    status = commands.add_parser("status")
    status.add_argument("--project-id", required=True)
    status.add_argument("--workspace", required=True)
    status.add_argument("--store", default=str(default_store()))
    status.set_defaults(func=command_status)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.func(args)
    except (LineageError, OSError) as exc:
        print(f"lineage: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
