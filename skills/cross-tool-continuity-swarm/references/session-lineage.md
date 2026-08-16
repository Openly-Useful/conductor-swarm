# Local session lineage contract

The portable checkpoint and the local transport ledger have different privacy
boundaries. The checkpoint may move between tools; the ledger identifies the
provider-native sessions that participated in that movement and remains local.

## Invocation preflight

Every skill invocation captures the current source identity before recovery:

1. Ask the supported host lifecycle interface for the active provider and exact
   current thread, task, conversation, or session identifier.
2. Reject identifiers inferred from titles, paths, transcript filenames, prompt
   text, or model output.
3. Upsert the provider identity, native identifier, approved workspace, and
   continuity-project association in the protected local store.
4. Read the record back and verify the provider, source identity, workspace,
   and project association before marking lineage captured.
5. When supported, checkpoint the source event cursor and boundary digest in
   that same local ledger. Do not invent either value.

Retries are idempotent. A repeated invocation updates the same link. A different
native identifier for an already linked active source is divergence and requires
audit or reconciliation; it is not silently reassigned.

## Phase 1 adapters

- Codex obtains the current task/thread identifier from the Codex host lifecycle
  or App Server metadata. A user-supplied `codex://threads/<id>` reference may be
  used only after the host verifies it names the current source task.
- Claude Code obtains the session identifier from supported lifecycle hooks,
  stream events, or an adapter checkpoint. `--continue` is not proof of which
  session was selected and must not be used as the capture mechanism.
- Local Command Center stores raw provider identity in its protected provider
  session record. Its `continuity_session_links` row contains only the opaque
  local conversation or external-session UUID.

Standalone Codex capture uses the bundled helper, which reads `CODEX_THREAD_ID`
from the host environment and emits only an opaque receipt:

```text
python scripts/lineage.py capture --provider codex --project-id <project-id> --workspace <approved-workspace>
python scripts/lineage.py verify --provider codex --project-id <project-id> --workspace <approved-workspace>
python scripts/lineage.py status --project-id <project-id> --workspace <approved-workspace>
```

Claude Code adapters pipe the lifecycle-provided identifier to the same commands
with `--session-id-stdin`. Never place a provider identifier in a command-line
argument. The default ledger is outside the repository, owned by the current
user, and mode `0600`; an injected store path is intended only for a protected
host integration or isolated test.

Capture receiving and reviewing provider sessions with `--relation successor`
or `--relation reviewer`. `status` returns the project's opaque local references
without exposing the native provider identifiers.

## Unavailable identity

If the host cannot expose a trustworthy current identity, report lineage capture
as unavailable. Audit can remain read-only, but switch, review, resume, and
one-command handoff readiness are blocked until a verified native reference is
available. Do not put a guessed identifier or an `unknown` placeholder into the
portable checkpoint.

## Privacy gate

Raw provider identifiers, transcript locations, event cursors, local workspace
paths, and active-writer state never enter `.continuity/`, rendered prompts,
Git, trackers, or sync events. Portable state may contain only project evidence
and boundary hashes that are not reversible provider identifiers.
