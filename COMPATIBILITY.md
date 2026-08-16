# Compatibility

Conductor Swarm uses the portable Agent Skills directory contract: each skill lives under `skills/<name>/SKILL.md` and does not hard-code a model provider. Compatibility means the host can discover the skill and present its instructions to an agent; it does not imply that every host exposes model switching, sub-agents, connectors, or identical permissions.

## Tested hosts

| Host target | Install surface | Result | Capability notes |
|---|---|---|---|
| Codex | `gh skill install ... --agent codex` | Public release pass | Pinned public install verified for `v1.1.1`. |
| Claude Code | `gh skill install ... --agent claude-code` | Public release pass | Pinned public install verified for `v1.1.1`. |
| GitHub Copilot | `gh skill install ... --agent github-copilot` | Pass | Skill discovery and copied files verified. |
| Cursor | `gh skill install ... --agent cursor` | Pass | Skill discovery and copied files verified. |
| Universal / `.agents/skills` | `gh skill install ... --agent universal` | Pass | Portable skill files and both public CLI installation paths verified. |

Release tests verify that `conductor-swarm`, `pickup-swarm`, and `cross-tool-continuity-swarm` are discovered with their `SKILL.md`, `LICENSE.txt`, and `agents/openai.yaml` files intact. Pinned Codex and Claude Code installs are repeated against the public release tag before release closure. Host-specific model and agent routing is always capability-gated at runtime.

## Install

```bash
gh skill install Openly-Useful/agent-workflow-swarms --all --agent <host> --scope user --pin v1.1.1
```

Supported `<host>` values include `codex`, `claude-code`, `github-copilot`, `cursor`, and `universal`.

The v1.1.0 package added `cross-tool-continuity-swarm`; v1.1.1 updates the
canonical repository identity to `Openly-Useful/agent-workflow-swarms`. Its
portable checkpoint and standard-library CLI are host-neutral. Adapter details,
model/profile labels, switching controls, tracker connectors, and permissions
remain capability-gated by the receiving host; installation does not imply
that a host can switch tools or models.
