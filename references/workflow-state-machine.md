# Workflow State Machine

Only `scripts/workflow_state.py` manages state files.

```text
initialized
  -> preflight
  -> discovery
  -> requirements_ready
  -> architecting? / designing?
  -> planned
  -> implementing
  -> verifying / reviewing
  -> fixing -> implementing / verifying / reviewing
  -> ready | risk_accepted
  -> finished
```

Any non-terminal state may become `blocked` or `cancelled`. `blocked`, `cancelled`, and `finished` are terminal.

## Run directory

Resolve `git rev-parse --git-common-dir`, then use:

```text
superflow/workflows/<run-id>/
├── state.json
├── events.jsonl
├── plan.json
├── routing.json
├── worktrees.json
├── requirements.json
├── requirements.md
├── process-log.md
├── briefs/
├── artifacts/
├── attempts/
└── gates/
```

- `state.json`: atomically replaced current snapshot.
- `events.jsonl`: append-only revision and hash chain.
- `plan.json`: task snapshot.
- `routing.json`: role assignments and immutable brief digests.
- `worktrees.json`: integration and task worktrees registered for this run.
- `requirements.json`: immutable structured requirements baseline.
- `requirements.md`: user-facing requirements document rendered from the baseline.
- `process-log.md`: user-facing delivery record automatically rendered from audited events.
- `briefs/`: self-contained subagent inputs.
- `artifacts/`: structured subagent results.
- `attempts/`: immutable accepted, rejected, blocked, retry, and repair records.
- `gates/`: candidate-bound review and test evidence.

Every linked worktree resolves the same directory; never copy ledger files between worktrees. Only the main agent calls the state script. A subagent `workflowUpdateRequest` is a proposal.

New runs configure `en` or `zh-CN` user documents. Freeze `requirements.json` during discovery before entering `requirements_ready`; the state script rejects missing or modified requirements artifacts. `process-log.md` is refreshed transactionally on every state event. Both Markdown files are derived views outside the business worktree and never change the candidate SHA.

`state.json.dispatches` is the current dispatch registry. Each entry binds an immutable dispatch ID to a task, role, worktree, brief digest, pre-execution snapshot, and actual subagent session or task handle:

```text
brief recorded
  -> dispatch waiting
  -> result received
  -> accepted | rejected | blocked attempt
```

While any dispatch is `waiting`, the main agent is coordination-only. State transitions, task updates, candidate changes, gate or risk recording, finish, commits, and cherry-picks fail closed. Project execution and professional role work are also prohibited by contract. Additional independent dispatches may be recorded before the main agent waits.

The same professional-role boundary applies when no dispatch is waiting. The main agent may write requirements and perform workflow/Git mechanics only. Source analysis, technical discovery, diagnosis, design, implementation, debugging, test execution, runtime preparation, browser/prototype operation, and code review always require a specialist dispatch.

Main-agent browser relay is disabled. A browser-required task cannot record a brief with the main-only `codex-browser` provider and must pause until the user selects a specialist-direct provider for a new run. Frontend and tester roles may use that provider directly for their separate responsibilities. If it is unavailable, record the affected role's blocked evidence request and remediate the provider rather than asking the main agent to browse.

An accepted implementation attempt hands off directly to candidate freeze and gate dispatch. The orchestration interval between them permits only deterministic result/policy validation and necessary Git commit, integration, clean-HEAD, and candidate-freeze operations. It forbids main-agent project commands, runtime or container preparation, temporary project edits, CodeGraph investigation, and browser or prototype work. Gate specialists prepare and verify the frozen candidate themselves.

A new run freezes its `lite`, `standard`, or `strict` profile plus project browser and UI-provider selection. New CLI runs default to `lite`; the deterministic profile selector raises risk-triggered work to `standard` or `strict`. Legacy states without a profile are interpreted as `strict`. If project configuration changes, the old run may only become `blocked` or `cancelled`.

## Commands

```bash
python3 scripts/workflow_state.py --project <repo> init \
  --profile <auto|lite|standard|strict> \
  --risk-signals <signals.json> --document-language <en|zh-CN> \
  --plan plan.json

python3 scripts/workflow_state.py --project <repo> record-requirements \
  <run-id> --requirements requirements.json

python3 scripts/workflow_state.py --project <repo> summary <run-id>

python3 scripts/workflow_state.py --project <repo> transition <run-id> preflight
python3 scripts/workflow_state.py --project <repo> set-task <run-id> T1 in_progress
python3 scripts/workflow_state.py --project <repo> record-brief \
  <run-id> T1 --brief brief.json
python3 scripts/workflow_state.py --project <repo> record-dispatch \
  <run-id> T1 backend-developer \
  --session-id <reserved-session-id> --before before.json \
  --memory-capability <fresh-role-memory-capability>
python3 scripts/workflow_state.py --project <repo> record-attempt \
  <run-id> T1 backend-developer initial accepted \
  --dispatch-id <dispatch-id> \
  --agent-result result.json --before before.json --after after.json \
  --reason 'Schema and policy checks passed'
python3 scripts/workflow_state.py --project <repo> set-candidate <run-id> <sha>

python3 scripts/workflow_state.py --project <repo> record-gate \
  <run-id> review <sha> <task-id> --agent-result <result.json> \
  --before <before.json> --after <after.json>

python3 scripts/workflow_state.py --project <repo> record-risk \
  <run-id> test --accepted-by '<user>' --reason '<reason>'
```

`plan.json` is an array of complete task contracts. Each task requires `id`, `title`, `role`, `dependencies`, `authorizedPaths`, `acceptanceCriteria`, `verificationCommands`, `observableResults`, and `status`; dependencies must form an acyclic graph.

Use `summary` for routine orchestration. Full `show` is reserved for recovery and audit because it includes complete dispatch history. Contracted gate records keep only the immutable attempt ID and result/policy digests in `state.json`; the complete Agent result, snapshots, and policy evidence remain in the attempt artifact.

Every new brief binds the selected profile to minimal context controls. `lite` uses at most three recalled memories and 2 KiB, compact result prose, optional CodeGraph, and one combined `code-reviewer` quality result for both gates. `standard` uses five memories and 4 KiB with independent parallel gates. `strict` retains ten memories, 8 KiB, full result detail, and independent gates. The state script rejects a brief whose limits do not match the run.

## Dual gate

Review and test gates may be recorded only during verifying or reviewing. Both must:

- exist and remain valid;
- reference the current `candidate_sha`;
- be PASS, or have each current immutable failure gate explicitly accepted by the user;
- contain non-empty verification checks and locatable success evidence;
- include a successful real test command for tester PASS.
- contain no failed, partial, skipped, not-run, or nonzero tester command.

The candidate must equal the integration worktree's current HEAD. Gate recording, ready or risk acceptance, and finish recheck HEAD, clean business worktree, and active Git operations. A candidate change invalidates old gates; rerecording a gate creates a new ID and invalidates old risk acceptance.

## Repair

Repair rounds are bound to an immutable task lineage, maximum three:

1. resume the original developer for rounds one and two;
2. use a fresh developer for round three and rederive from the brief and evidence;
3. after a third-round failure, block and request user direction.

Fix only confirmed findings, freeze a new SHA, and rerun both gates.

## Recovery

On resume:

1. validate `state.json` against the complete `events.jsonl` hash chain with revision consistency;
2. inspect every `waiting` dispatch and reconnect to its recorded subagent session or task handle;
3. wait for live dispatched work instead of redispatching it or taking it over;
4. only after a dispatch is confirmed terminated, record a `blocked` attempt and then decide whether to retry;
5. verify worktree, branch, HEAD, and `worktrees.json`;
6. verify Agent template versions, required tools, and `state.json.tool_config`;
7. continue from the first unfinished task without redispatching completed work;
8. validate Schema, cross-field invariants, event hashes, and safe paths.

When files, Git state, and ledger disagree, enter `blocked`; never guess recovery.
