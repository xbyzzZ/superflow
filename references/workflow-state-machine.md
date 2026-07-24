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

```text
.codex/workflows/<run-id>/
├── state.json
├── events.jsonl
├── plan.json
├── routing.json
├── worktrees.json
├── briefs/
├── artifacts/
└── gates/
```

- `state.json`: atomically replaced current snapshot.
- `events.jsonl`: append-only revision and hash chain.
- `plan.json`: task snapshot.
- `briefs/`: self-contained subagent inputs.
- `artifacts/`: structured subagent results.
- `gates/`: candidate-bound review and test evidence.

Only the main agent calls the state script. A subagent `workflowUpdateRequest` is a proposal.

A new run freezes project browser and UI-provider selection in `state.json.tool_config`. If project configuration changes, the old run may only become `blocked` or `cancelled`.

## Commands

```bash
python3 scripts/workflow_state.py --project <repo> init \
  --plan '[{"id":"T1","title":"Implement the API"}]'

python3 scripts/workflow_state.py --project <repo> transition <run-id> preflight
python3 scripts/workflow_state.py --project <repo> set-task <run-id> T1 in_progress
python3 scripts/workflow_state.py --project <repo> set-candidate <run-id> <sha>

python3 scripts/workflow_state.py --project <repo> record-gate \
  <run-id> review <sha> <task-id> --agent-result <result.json> \
  --before <before.json> --after <after.json>

python3 scripts/workflow_state.py --project <repo> record-risk \
  <run-id> test --accepted-by '<user>' --reason '<reason>'
```

## Dual gate

Review and test gates may be recorded only during verifying or reviewing. Both must:

- exist and remain valid;
- reference the current `candidate_sha`;
- be PASS, or have each current immutable failure gate explicitly accepted by the user;
- contain non-empty verification checks and locatable success evidence;
- include a successful real test command for tester PASS.

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
2. verify worktree, branch, HEAD, and `worktrees.json`;
3. verify Agent template versions, required tools, and `state.json.tool_config`;
4. continue from the first unfinished task without redispatching completed work;
5. validate Schema, cross-field invariants, event hashes, and safe paths.

When files, Git state, and ledger disagree, enter `blocked`; never guess recovery.
