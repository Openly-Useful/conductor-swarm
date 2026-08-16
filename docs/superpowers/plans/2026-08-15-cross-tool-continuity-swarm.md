# Cross-Tool Continuity Swarm 1.1.0 implementation plan

## Goal

Add a portable continuity skill to Conductor Swarm so a bounded project can be
audited, prepared, synchronized, switched, reviewed, and resumed across tools
without importing a transcript, local machine state, or provider assumptions.

## Scope and contracts

1. Keep the core provider-neutral and compose recovery as Pickup Swarm →
   Conductor Swarm. Adapter-specific labels stay in the adapter reference.
2. Define a versioned JSON checkpoint with provenance-bearing evidence,
   acceptance criteria, verification, artifacts, audit status, and sync events.
3. Enforce a 32 KiB UTF-8 transferable context cap and reject prohibited
   local-only values (absolute paths, host/machine/process details, sockets,
   credentials, tokens, and secrets).
4. Provide deterministic standard-library operations: `init`, `audit`,
   `validate`, `capture`, `render`, `prepare-switch`, `record-sync`, and
   `status`. Canonical JSON/SHA-256 identifiers make retries idempotent.
5. Generate a bounded, marked text-block launch prompt that instructs the
   receiving tool to validate and review before editing.

## Artifacts

- `skills/cross-tool-continuity-swarm/SKILL.md`: concise operating contract,
  workflow, invariants, failure handling, and references.
- `scripts/continuity.py`: portable CLI and invariant enforcement.
- `schemas/`: state, handoff, and sync-event schemas.
- `examples/`: minimal state and sync inputs.
- `references/`: adapter, tracker-sync, and privacy/authority guidance.
- `evals/cases.yaml`: deterministic behavior contracts for the new skill.
- repository version, README, compatibility, plugin manifest, and validator
  updates to 1.1.0.

## Verification plan

- Run skill quick validation, repository validation, and deterministic evals.
- Smoke-test every CLI operation in a temporary directory.
- Run duplicate capture and duplicate sync inputs and compare canonical state
  bytes to prove idempotency.
- Test rejection of an absolute path and a context over 32 KiB.
- Render and inspect the launch prompt markers and required next-action text.
- Run `git diff --check`, review the complete diff, and commit only changes in
  this conductor repository.

## Out of scope

No connector installation, external tracker writes, automatic upload,
credential handling, model routing implementation, or changes to Local Command
Center. Hosts may provide adapters, but the portable core records only what is
actually available and verified.
