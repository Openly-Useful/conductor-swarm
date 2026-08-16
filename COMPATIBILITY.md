# Compatibility

Conductor Swarm uses the portable Agent Skills directory contract: each skill lives under `skills/<name>/SKILL.md` and does not hard-code a model provider. Compatibility means the host can discover the skill and present its instructions to an agent; it does not imply that every host exposes model switching, sub-agents, connectors, or identical permissions.

## Tested hosts

| Host target | Install surface | Result | Capability notes |
|---|---|---|---|
| Codex | `gh skill install ... --agent codex` | Pass | User-scope public release install verified. |
| Claude Code | `gh skill install ... --agent claude-code` | Pass | Skill discovery and copied files verified; runtime behavior depends on exposed tools and agents. |
| GitHub Copilot | `gh skill install ... --agent github-copilot` | Pass | Skill discovery and copied files verified. |
| Cursor | `gh skill install ... --agent cursor` | Pass | Skill discovery and copied files verified. |
| Universal / `.agents/skills` | `gh skill install ... --agent universal` | Pass | Portable skill files and both public CLI installation paths verified. |

Tests use the public tagged release and verify that `conductor-swarm`, `pickup-swarm`, and `cross-tool-continuity-swarm` are discovered with their `SKILL.md`, `LICENSE.txt`, and `agents/openai.yaml` files intact. Host-specific model and agent routing is always capability-gated at runtime.

## Install

```bash
gh skill install Openly-Useful/conductor-swarm --all --agent <host> --scope user --pin v1.1.0
```

Supported `<host>` values include `codex`, `claude-code`, `github-copilot`, `cursor`, and `universal`.

The v1.1.0 package additionally includes `cross-tool-continuity-swarm`. Its
portable checkpoint and standard-library CLI are host-neutral. Adapter details,
model/profile labels, switching controls, tracker connectors, and permissions
remain capability-gated by the receiving host; installation does not imply
that a host can switch tools or models.
