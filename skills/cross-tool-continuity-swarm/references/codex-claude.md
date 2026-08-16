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

1. On the source side run `validate`, then `audit`.
2. Run `prepare-switch --target-tool <portable target identifier>` and keep the
   generated JSON checkpoint under the user's approved project boundary.
3. Run `render` and transfer the complete text block through an approved
   channel. Do not transfer secrets or local-only values.
4. On the receiving side validate the checkpoint and review repository state,
   tests, acceptance criteria, and rollback boundary before editing.
5. Record a sync event only after the receiving side reports what it actually
   read or applied. A launch prompt alone is not synchronization evidence.
