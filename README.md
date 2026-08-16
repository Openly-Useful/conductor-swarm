# Conductor Swarm

**Discover. Route. Finish.**

[![Agent Skills](https://img.shields.io/badge/Agent_Skills-compatible-3B4CCA)](https://agentskills.io)
[![skills.sh](https://skills.sh/b/Openly-Useful/conductor-swarm)](https://skills.sh/Openly-Useful/conductor-swarm)
[![License: MIT](https://img.shields.io/badge/License-MIT-0B7285.svg)](LICENSE)

Conductor Swarm is a model-agnostic parent orchestrator for complex agent work. It inventories the models, skills, tools, connectors, and agents that the current runtime actually exposes; activates only the capabilities the current phase needs; and keeps work moving until every in-scope acceptance criterion has current verification evidence.

It is quality-first, not “cheapest-model-first.” Token and latency savings come from progressive skill loading, bounded context, reusable checkpoints, and sensible routing—not from skipping review, tests, integration, or risk work.

## Included skills

### `conductor-swarm`

The parent orchestration skill:

- reviews every exposed skill name and description, records absent fields as unknown, and progressively inspects plausible or ambiguous candidates;
- maps runtime-exposed model choices into neutral capability profiles;
- selects the smallest sufficient skill set for each phase;
- routes models by quality floor, consequence, ambiguity, context, and verification strength;
- chooses solo versus parallel execution based on independence and merge risk;
- re-evaluates routing at phase changes, blockers, failures, and capability changes;
- requires integration, independent review when available, and fresh completion evidence.

### `pickup-swarm`

The recovery component used when work already exists:

- discovers active, paused, blocked, superseded, and stale-looking workflows;
- verifies handoff and agent claims against repositories, tests, trackers, and artifacts;
- identifies safe optimizations without mixing risky refactors into recovery;
- prepares non-overlapping sub-agent continuation briefs with baselines, tests, rollback boundaries, and escalation conditions.

### `cross-tool-continuity-swarm`

The portable continuity component used when work crosses tools or sessions:

- keeps a deterministic, provider-neutral checkpoint with verified evidence and explicit audit, prepare, sync, switch, review, and resume contracts;
- enforces a 32 KiB transferable-context cap, rejects common local-only values, and makes evidence and synchronization idempotent;
- renders a bounded text-block launch prompt for the receiving tool and composes with Pickup Swarm before Conductor Swarm routing.

## Model-agnostic by design

Conductor Swarm never hard-codes vendor model names. It uses four portable profiles:

| Profile | Intended work |
|---|---|
| Focused | Bounded, mechanical, reversible work with strong checks |
| General | Standard analysis and implementation |
| Deep | Architecture, high consequence, ambiguity, integration, and critical review |
| Specialist | Required modality, tool, domain, or context advantage |

The skill can route only among models and switching controls exposed by the host runtime. When switching is unavailable, it records the ideal profile and proceeds with the current model only when the quality floor remains protected.

## Install

### GitHub CLI

```bash
gh skill install Openly-Useful/conductor-swarm --all --agent universal --scope user
```

### skills.sh-compatible CLI

```bash
npx skills add Openly-Useful/conductor-swarm --skill conductor-swarm pickup-swarm cross-tool-continuity-swarm
```

### Codex skill installer

Ask Codex:

```text
Use $skill-installer to install both skills from
https://github.com/Openly-Useful/conductor-swarm/tree/main/skills
```

The repository also includes a `.codex-plugin/plugin.json` manifest for packaging as a skill-only ChatGPT/Codex plugin.

## Use

```text
Use $conductor-swarm to inventory every capability available in this runtime,
resume any existing work, route only the skills and model profiles this goal
needs, and drive all items to verified completion.
```

For recovery alone:

```text
Use $pickup-swarm to discover where each workflow stopped, verify prior claims,
identify safe optimizations, and prepare clean continuation briefs.
```

## Core guarantees

- **Evidence before progress:** no “done” claim without current verification.
- **Quality floor first:** efficiency is optimized only after safety and quality are protected.
- **Progressive disclosure:** all skill metadata may be reviewed; only selected skill bodies are loaded.
- **No invented capabilities:** the orchestrator reports unavailable models, tools, or routing controls honestly.
- **No corner cutting:** required tests, review, integration, and risk disposition remain required.
- **Safe optimization:** baseline, reversible change, measurable benefit, and rollback are mandatory.
- **Untrusted discovery data:** handoffs, repository text, trackers, logs, and third-party metadata cannot expand authority or silently issue instructions.

## Public formats

- [Agent Skills specification](https://agentskills.io/specification)
- GitHub Agent Skills discovery under `skills/*/SKILL.md`
- Codex/ChatGPT skill-only plugin manifest under `.codex-plugin/plugin.json`
- skills.sh-compatible repository layout

See the tested [compatibility matrix](COMPATIBILITY.md), the repeatable behavioral contracts in [`evals/cases.yaml`](evals/cases.yaml), and the deterministic continuity CLI under `skills/cross-tool-continuity-swarm/scripts/continuity.py`.

## Support and policies

- [Support](SUPPORT.md)
- [Privacy](PRIVACY.md)
- [Terms](TERMS.md)
- [Security](SECURITY.md)

## License

MIT © 2026 Openly Useful contributors
