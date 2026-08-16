# Adapter reference: two tool families

This file is intentionally the only adapter reference that names concrete
tool/model families. The checkpoint format and script remain provider-neutral.

## Mapping

| Portable concept | Source-side adapter | Receiving-side adapter |
|---|---|---|
| source/target tool | `codex` | `claude-code` |
| focused execution profile | Sol | Fable |
| general execution profile | Luna | Opus |
| deep review profile | Terra | Sonnet |
| launch prompt | copy the generated text block into the target session | validate the JSON block before any edit |

The names above are adapter labels, not capabilities the core may assume. Ask
the host what profiles, switching controls, tools, and permissions actually
exist. If a profile or switch is unavailable, record `unavailable` and keep the
quality floor protected; never claim that a switch occurred.

## Switch procedure

1. Capture and read back the current source session through the protected local
   lineage store described in `session-lineage.md`.
2. On the source side run `validate`, then `audit`.
3. Run `prepare-switch --target-tool <portable target identifier>` and keep the
   generated JSON checkpoint under the user's approved project boundary.
4. Run `render` and transfer the complete text block through an approved
   channel. Do not transfer secrets or local-only values.
5. On the receiving side validate the checkpoint and review repository state,
   tests, acceptance criteria, and rollback boundary before editing.
6. Record a sync event only after the receiving side reports what it actually
   read or applied. A launch prompt alone is not synchronization evidence.

## One-command Claude Code continuation

After the source chat has completed audit, validation, and preparation, render
the prepared prompt inside the repository:

```text
python scripts/continuity.py render --state .continuity/continuity.json --target-tool claude-code --output .continuity/launch/claude.txt
```

Before returning a command, the source chat verifies that local source lineage
was captured and read back, the launch file was rendered for the prepared
target, and the checkpoint still validates. It must not launch Claude Code
itself. The source chat then returns exactly one shell command. Use the
existing-session form only when the user intends to continue the most recent
native Claude Code conversation in that repository:

```text
cd '<approved workspace>' && claude --continue "$(cat .continuity/launch/claude.txt)"
```

Otherwise start a new Claude Code session:

```text
cd '<approved workspace>' && claude "$(cat .continuity/launch/claude.txt)"
```

The current chat must not claim that either form imports its transcript. In
particular, a Codex/ChatGPT chat and an ordinary Claude.ai conversation cannot
be resumed as that native Claude Code session. The rendered checkpoint is the
transfer boundary. Do not put the workspace path, native session ID, or raw
transcript in the checkpoint or launch file.
