#!/usr/bin/env python3
"""Validate Conductor Swarm's portable Agent Skills and plugin manifest."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ALLOWED_FRONTMATTER = {
    "name",
    "description",
    "license",
    "compatibility",
    "metadata",
    "allowed-tools",
}


def parse_frontmatter(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        raise AssertionError(f"{path}: missing YAML frontmatter")
    try:
        values = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise AssertionError(f"{path}: invalid YAML frontmatter: {exc}") from exc
    if not isinstance(values, dict):
        raise AssertionError(f"{path}: frontmatter must be a YAML mapping")
    if "[TODO" in text:
        raise AssertionError(f"{path}: unresolved TODO placeholder")
    if len(text.splitlines()) >= 500:
        raise AssertionError(f"{path}: SKILL.md must stay under 500 lines")
    return values


def validate_skill(skill_dir: Path) -> None:
    metadata = parse_frontmatter(skill_dir / "SKILL.md")
    missing = {"name", "description"} - set(metadata)
    unexpected = set(metadata) - ALLOWED_FRONTMATTER
    if missing or unexpected:
        raise AssertionError(
            f"{skill_dir}: missing fields {sorted(missing)}; unsupported fields {sorted(unexpected)}"
        )
    name = metadata["name"]
    if not isinstance(name, str) or name != skill_dir.name or not NAME_RE.fullmatch(name) or len(name) > 64:
        raise AssertionError(f"{skill_dir}: invalid or mismatched skill name")
    description = metadata["description"]
    if not isinstance(description, str) or not description or len(description) > 1024:
        raise AssertionError(f"{skill_dir}: invalid description")
    for optional_string in ("license", "compatibility", "allowed-tools"):
        value = metadata.get(optional_string)
        if value is not None and not isinstance(value, str):
            raise AssertionError(f"{skill_dir}: {optional_string} must be a string")
    extra_metadata = metadata.get("metadata")
    if extra_metadata is not None and (
        not isinstance(extra_metadata, dict)
        or not all(isinstance(key, str) and isinstance(value, str) for key, value in extra_metadata.items())
    ):
        raise AssertionError(f"{skill_dir}: metadata must map strings to strings")

    agent_yaml = skill_dir / "agents" / "openai.yaml"
    if not agent_yaml.is_file():
        raise AssertionError(f"{skill_dir}: missing agents/openai.yaml")
    try:
        agent_metadata = yaml.safe_load(agent_yaml.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise AssertionError(f"{agent_yaml}: invalid YAML: {exc}") from exc
    if not isinstance(agent_metadata, dict) or not isinstance(agent_metadata.get("interface"), dict):
        raise AssertionError(f"{agent_yaml}: interface must be a mapping")
    interface = agent_metadata["interface"]
    display_name = interface.get("display_name")
    short_description = interface.get("short_description")
    default_prompt = interface.get("default_prompt")
    if not isinstance(display_name, str) or not display_name.strip():
        raise AssertionError(f"{agent_yaml}: display_name must be a nonempty string")
    if not isinstance(short_description, str) or not 25 <= len(short_description) <= 64:
        raise AssertionError(f"{agent_yaml}: short_description must be 25-64 characters")
    if not isinstance(default_prompt, str) or f"${name}" not in default_prompt:
        raise AssertionError(f"{agent_yaml}: default_prompt must reference ${name}")
    if not (skill_dir / "LICENSE.txt").is_file():
        raise AssertionError(f"{skill_dir}: missing LICENSE.txt")


def validate_plugin() -> None:
    manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    if manifest["name"] != "conductor-swarm" or manifest["version"] != "1.0.0":
        raise AssertionError("plugin identity/version mismatch")
    if manifest.get("skills") != "./skills/":
        raise AssertionError("plugin must expose ./skills/")
    if manifest["interface"]["displayName"] != "Conductor Swarm":
        raise AssertionError("plugin display name mismatch")
    for policy in ("PRIVACY.md", "TERMS.md", "SUPPORT.md", "SECURITY.md", "LICENSE"):
        if not (ROOT / policy).is_file():
            raise AssertionError(f"missing {policy}")


def main() -> None:
    skills = sorted((ROOT / "skills").glob("*/SKILL.md"))
    if [path.parent.name for path in skills] != ["conductor-swarm", "pickup-swarm"]:
        raise AssertionError("expected conductor-swarm and pickup-swarm skills")
    for skill_file in skills:
        validate_skill(skill_file.parent)
    validate_plugin()
    print("Validated conductor-swarm and pickup-swarm skills plus plugin manifest")


if __name__ == "__main__":
    main()
