#!/usr/bin/env python3
"""Validate Conductor Swarm's portable Agent Skills and plugin manifest."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
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


def read_json(relative_path: str) -> dict[str, object]:
    path = ROOT / relative_path
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssertionError(f"{relative_path}: missing or invalid JSON") from exc
    if not isinstance(value, dict):
        raise AssertionError(f"{relative_path}: expected a JSON object")
    return value


def validate_registration() -> None:
    publisher = read_json("publisher/publisher.json")
    if publisher.get("schemaVersion") != 1 or publisher.get("authorityManifest") != "https://openlyuseful.org/publisher/manifest.json":
        raise AssertionError("publisher authority contract mismatch")
    identity = publisher.get("publisher")
    if identity != {
        "displayName": "Openly Useful",
        "homepage": "https://openlyuseful.org",
        "studio": "https://openlyuseful.com",
        "publicContact": "hello@openlyuseful.org",
    }:
        raise AssertionError("publisher identity mismatch")
    legal = publisher.get("plannedLegalEntity")
    if not isinstance(legal, dict) or legal.get("name") != "Openly Useful LLC" or legal.get("status") != "formation-pending":
        raise AssertionError("planned entity must remain formation-pending")
    if sorted(legal.get("roles", [])) != ["licensee", "operator", "publisher"]:
        raise AssertionError("planned entity roles mismatch")
    component = publisher.get("component")
    if not isinstance(component, dict):
        raise AssertionError("publisher component metadata is required")
    if component.get("name") != "conductor-swarm" or not SEMVER_RE.fullmatch(str(component.get("version", ""))):
        raise AssertionError("component identity/version mismatch")
    expected_skills = ["conductor-swarm", "cross-tool-continuity-swarm", "pickup-swarm"]
    if sorted(component.get("skillNames", [])) != expected_skills:
        raise AssertionError("component skill inventory mismatch")
    if component.get("mcp") is not False:
        raise AssertionError("skill-only component must explicitly remain MCP-free")
    publication = publisher.get("externalPublication")
    if publication != {"allowed": False, "authorization": "withheld"}:
        raise AssertionError("external publication must remain withheld")

    repo_blob = "https://github.com/Openly-Useful/agent-workflow-swarms/blob/main/"
    policies = publisher.get("policies", {})
    mirrors = publisher.get("policyMirrors")
    expected_mirrors = {
        "privacy": repo_blob + "PRIVACY.md",
        "terms": repo_blob + "TERMS.md",
        "security": repo_blob + "SECURITY.md",
        "support": repo_blob + "SUPPORT.md",
    }
    if mirrors != expected_mirrors:
        raise AssertionError("policy mirrors must reference the version-controlled repository pages")
    if sorted(mirrors) != sorted(policies):
        raise AssertionError("policy mirrors must map 1:1 to the declared live policy pages")
    if publisher.get("authorityManifestMirror") != repo_blob + "publisher/manifest.json":
        raise AssertionError("authority manifest mirror must reference the version-controlled repository copy")
    authority = read_json("publisher/manifest.json")
    if authority.get("schemaVersion") != 1 or authority.get("canonicalURL") != publisher.get("authorityManifest"):
        raise AssertionError("version-controlled authority manifest must declare its live canonical URL")
    if authority.get("publisher") != publisher.get("publisher"):
        raise AssertionError("authority manifest publisher identity must match publisher.json")
    if authority.get("policies") != policies or authority.get("policyMirrors") != mirrors:
        raise AssertionError("authority manifest policy pages and mirrors must match publisher.json")
    components = authority.get("components")
    if components != [{"name": "conductor-swarm", "repository": "https://github.com/Openly-Useful/agent-workflow-swarms"}]:
        raise AssertionError("authority manifest component listing mismatch")

    author = {"name": identity["displayName"], "email": identity["publicContact"], "url": identity["homepage"]}
    common = {
        "name": component["name"],
        "version": component["version"],
        "author": author,
        "homepage": component["repository"],
        "repository": component["repository"],
        "license": component["license"],
        "skills": component["skillsPath"],
    }
    codex = read_json(".codex-plugin/plugin.json")
    claude = read_json(".claude-plugin/plugin.json")
    for label, manifest in (("Codex", codex), ("Claude", claude)):
        for field, expected in common.items():
            if manifest.get(field) != expected:
                raise AssertionError(f"{label} plugin {field} does not derive from publisher metadata")
        if "mcpServers" in manifest or "apps" in manifest:
            raise AssertionError(f"{label} skill-only plugin cannot declare MCP or apps")
    if codex.get("interface", {}).get("displayName") != component.get("displayName"):
        raise AssertionError("Codex plugin display name mismatch")
    if codex.get("interface", {}).get("developerName") != identity["displayName"]:
        raise AssertionError("Codex developer name mismatch")
    if codex.get("interface", {}).get("privacyPolicyURL") != policies.get("privacy"):
        raise AssertionError("Codex privacy URL mismatch")
    if codex.get("interface", {}).get("termsOfServiceURL") != policies.get("terms"):
        raise AssertionError("Codex terms URL mismatch")

    codex_marketplace = read_json(".agents/plugins/marketplace.json")
    if codex_marketplace.get("interface") != {"displayName": identity["displayName"]}:
        raise AssertionError("Codex marketplace publisher mismatch")
    codex_entries = codex_marketplace.get("plugins")
    if not isinstance(codex_entries, list) or len(codex_entries) != 1:
        raise AssertionError("Codex marketplace must expose exactly one root plugin")
    codex_entry = codex_entries[0]
    if codex_entry.get("name") != component["name"] or codex_entry.get("source") != {"source": "local", "path": "./"}:
        raise AssertionError("Codex marketplace root source mismatch")
    if codex_entry.get("policy") != {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}:
        raise AssertionError("Codex marketplace policy mismatch")

    claude_marketplace = read_json(".claude-plugin/marketplace.json")
    if claude_marketplace.get("owner") != author or claude_marketplace.get("version") != component["version"]:
        raise AssertionError("Claude marketplace publisher/version mismatch")
    claude_entries = claude_marketplace.get("plugins")
    if not isinstance(claude_entries, list) or len(claude_entries) != 1:
        raise AssertionError("Claude marketplace must expose exactly one root plugin")
    claude_entry = claude_entries[0]
    if claude_entry.get("name") != component["name"] or claude_entry.get("source") != "./" or claude_entry.get("strict") is not True:
        raise AssertionError("Claude marketplace root source mismatch")
    if claude_entry.get("author") != author or claude_entry.get("version") != component["version"]:
        raise AssertionError("Claude marketplace component metadata mismatch")

    for policy in ("PRIVACY.md", "TERMS.md", "SUPPORT.md", "SECURITY.md", "LICENSE"):
        if not (ROOT / policy).is_file():
            raise AssertionError(f"missing {policy}")


def main() -> None:
    skills = sorted((ROOT / "skills").glob("*/SKILL.md"))
    if [path.parent.name for path in skills] != ["conductor-swarm", "cross-tool-continuity-swarm", "pickup-swarm"]:
        raise AssertionError("expected conductor-swarm, cross-tool-continuity-swarm, and pickup-swarm skills")
    for skill_file in skills:
        validate_skill(skill_file.parent)
    skill_artifacts = sorted(path.relative_to(ROOT).as_posix() for path in (ROOT / "skills").rglob("SKILL*.md"))
    expected_artifacts = [f"skills/{name}/SKILL.md" for name in ["conductor-swarm", "cross-tool-continuity-swarm", "pickup-swarm"]]
    if skill_artifacts != expected_artifacts:
        raise AssertionError(f"duplicate or unexpected SKILL artifacts: {skill_artifacts}")
    validate_registration()
    print("Validated three canonical skills plus Codex, Claude, marketplace, and publisher registration")


if __name__ == "__main__":
    main()
