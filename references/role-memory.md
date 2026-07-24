# Role-Isolated Memory

Superflow stores durable execution knowledge per Git project and per role. Memory is historical guidance, never a substitute for current repository, requirement, tool, or runtime evidence.

## Isolation model

The seven memory roles are:

- `product-manager`
- `architect`
- `ui-designer`
- `frontend-developer`
- `backend-developer`
- `tester`
- `code-reviewer`

Resolve storage through `git rev-parse --git-common-dir`, then use `superflow/memory/`. Linked worktrees share that Git common directory. Memory is not committed, pushed, or copied by `git clone`.

Each dispatched task receives a temporary capability bound to one role, run ID, task ID, and expiry. A role calls `recall` with that capability and cannot select a role in the recall command. Revoke the capability after processing the result. Never include a capability in Agent results, logs, evidence, or memory.

Roles do not read each other's memory. Cross-role requirements, architecture, interface definitions, design rules, and findings travel through briefs, formal artifacts, or project documentation.

## Recall

At task start, let the role query its own memory. It may query again when the work reaches a distinct topic.

Recall combines:

- high-importance durable records;
- tag and content matches against the query;
- recent records as a deterministic tie-breaker.

Return at most 10 entries and 8 KiB. Recall is read-only and lock-free because writers atomically replace the active file; it must not create a lock or any other filesystem entry. Treat every recalled item as potentially stale. Reverify facts before relying on them.

## Write requests

Every specialist Agent result includes `memoryWriteRequests`, with at most three items. The product manager uses the same request shape when retaining its own knowledge.

A request contains:

- `category`: `constraint`, `decision`, `verified-pattern`, `pitfall`, or `tool-fact`;
- concise `summary` and bounded `detail`;
- unique `tags`;
- `importance`: `normal` or `high`;
- locatable `evidenceRefs`;
- `futureUse`, explaining when the memory should be recalled;
- `supersedes`, containing active memory IDs replaced by this record.

Retain only evidence-backed knowledge useful in future executions of the same role. Do not retain transient progress, unverified assumptions, credentials, secrets, personal data, complete logs, or large code excerpts.

`success`, `partial`, `failed`, and `blocked` results may all propose memory. Ingest requests only after the complete Agent result passes Schema and policy checks. A rejected result writes nothing.

## Revision, capacity, and recovery

Records are append-oriented. Correct stale knowledge by creating a new record whose `supersedes` list names the old active record. Recall ignores superseded records.

Each role retains at most 500 active records. Move superseded and overflow records into that role's local archive. Use a filesystem lock and atomic replacement; refuse corrupt JSONL, duplicate IDs, role mismatches, and symlinked memory paths.

Exact duplicate requests do not create another record.

## User-authorized management

Only an explicit user request authorizes list, delete, clear, export, or import operations. These commands require `--user-authorized`.

Export and import are explicit local operations. Imported records must match the selected role and complete record contract. Normal workflow execution never exports, imports, deletes, or clears memory.
