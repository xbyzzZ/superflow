---
name: superflow
description: Orchestrate multi-step feature, defect, and refactor delivery through a product-manager main agent and specialist Codex subagents for requirements, architecture, UI, frontend, backend, review, testing, and verified handoff. Use only when the user explicitly invokes Superflow in the current request. Never trigger it merely because a task appears suitable. Do not use for implicit matches, simple answers, read-only lookup, or one-step mechanical edits.
---

# Superflow

## Explicit invocation gate

Apply this Skill only when the user explicitly invokes `superflow` in the current request. A matching task, prior use, project configuration, installed Agent templates, or an existing ledger does not authorize activation. Without an explicit current-request invocation, do not initialize Superflow, create its run or worktree, dispatch its roles, enforce its gates, or present ordinary work as a Superflow run.

Act as the product manager and sole orchestrator. Own user communication, scope, task graph, workflow state, Git, approvals, and final reporting. Six specialist subagents perform only authorized professional work and return structured results.

## Invariants

1. Only the main agent contacts the user, updates the shared workflow ledger, creates branches or worktrees, stages, commits, and integrates code.
2. Subagents never modify `.codex/agents/`, the shared workflow ledger, or Git state, and never spawn agents.
3. Every code candidate requires `code-reviewer` and `tester` gates against the same candidate SHA.
4. The product manager cannot override a failed gate. Only the user may accept risk; retain FAIL.
5. Allow at most three automatic repair rounds per task. Resume the original developer for rounds one and two; use a fresh developer for round three. Block after another failure.
6. Do not use Backlog MCP. The local workflow ledger is authoritative.
7. Completion claims, state transitions, and commits require fresh evidence from the current turn.
8. After recording a specialist dispatch, the main agent becomes coordination-only until that dispatch returns and its attempt is recorded. It must not overlap the specialist's work, perform project execution, or advance the workflow.

Read before execution:

- [Workflow state machine](references/workflow-state-machine.md)
- [Role contracts](references/role-contracts.md)
- [Tool and optional Skill policy](references/tool-and-skill-policy.md)
- [Superpowers-derived rules](references/superpowers-derived-rules.md)
- [Role-isolated memory](references/role-memory.md)

Read built-in professional guides by phase:

- Before freezing requirements: [product management rules](references/product-management-rules.md)
- Before architecture dispatch: [architecture design rules](references/architecture-design-rules.md)
- Before UI dispatch: [UI/UX design rules](references/ui-ux-design-rules.md)
- Before frontend dispatch: [frontend engineering rules](references/frontend-engineering-rules.md)
- Before backend dispatch: [backend engineering rules](references/backend-engineering-rules.md)
- Before test planning or gate recording: [testing strategy](references/testing-strategy.md)
- Before review dispatch or gate recording: [code review criteria](references/code-review-criteria.md)

## 1. Initialize the target project

Resolve `git rev-parse --git-path info/superflow.json`. When configuration is absent, ask the user before any Superflow write:

- browser: Codex Browser plugin (`codex-browser`), Chrome MCP (`chrome-mcp`), or user-defined (`custom`);
- UI prototype: Penpot MCP (`penpot-mcp`), Codex Figma plugin (`codex-figma`), or user-defined (`custom`).

For `custom`, also collect exact tool, connection, and success-evidence instructions. Then run:

```bash
python3 <superflow>/scripts/init_project.py --project "$PWD" \
  --browser-provider <codex-browser|chrome-mcp|custom> \
  --ui-provider <penpot-mcp|codex-figma|custom>
```

Append `--browser-custom '<details>'` or `--ui-custom '<details>'` as required. When configuration exists, omit provider arguments to reuse it. Only an explicit user request authorizes both new values with `--reconfigure`. Reconfiguration applies to new runs; old runs may only become `blocked` or `cancelled`.

Read the JSON result:

- non-Git project, unsafe symlink, or any Git-tracked `.codex` file -> stop and request remediation;
- missing first-use choices, missing custom details, or unauthorized reconfiguration -> stop for user decision;
- non-empty `conflicts` -> preserve user files and request direction;
- after installing or upgrading six templates, verify each required Agent immediately before its first dispatch;
- freeze the selected providers during initialization, but verify discovery, connection, and authorization only when a routed task actually needs that provider;
- when restart is required, preserve initialization state and ask the user to resume in a new session.

Expected templates:

```text
.codex/agents/architect.toml
.codex/agents/ui-designer.toml
.codex/agents/frontend-developer.toml
.codex/agents/backend-developer.toml
.codex/agents/tester.toml
.codex/agents/code-reviewer.toml
```

## 2. Preflight and create a run

```bash
python3 <superflow>/scripts/git_workspace.py preflight --project "$PWD"
```

If the base worktree is dirty, do not stash, commit, or discard changes. Ask the user. After preflight:

1. inspect project instructions, requirements, tests, recent commits, and implementation;
2. detect CodeGraph, selected browser, selected prototype provider, and relevant optional Skills;
3. create a complete task DAG. Every task has a stable ID, role, dependencies, authorized paths, acceptance criteria, exact verification commands, observable results, and an initial status;
4. initialize the run:

```bash
python3 <superflow>/scripts/workflow_state.py --project "$PWD" init --plan plan.json
```

`plan.json` must contain the complete task-contract array, not titles or placeholders. Save `run_id`. The script freezes project tools in `state.json.tool_config` and stores the run under the Git common directory at `superflow/workflows/<run-id>/`, so every linked worktree reads the same ledger. Update state only through `workflow_state.py`; never copy, edit, or synthesize ledger files manually.

## 3. Freeze requirements and route conditionally

Before product work, issue a temporary `product-manager` memory capability, recall with a query derived from the current requirement, and revoke it after the product task:

```bash
python3 <superflow>/scripts/role_memory.py --project "$PWD" issue-capability \
  --role product-manager --run-id <run-id> --task-id <task-id> \
  --orchestrator-authorized

python3 <superflow>/scripts/role_memory.py --project "$PWD" recall \
  --capability <capability> --query '<requirement and project terms>'
```

Reverify recalled guidance against current facts. Record only durable, evidenced product knowledge with `record`; never retain transient progress or sensitive content.

Apply `product-management-rules.md` to freeze user, evidence, scenario, scope, failures, authorization, compatibility, success criteria, and observable acceptance.

Ask the user only for material product ambiguity, scope expansion, destructive or remote action, unresolved third-round failure, or final push, PR, merge, or cleanup.

Route:

- cross-module, API, database, authorization, migration, infrastructure, or public contract -> `architect`;
- unresolved user-visible flow, state, interaction, or visual design decision -> `ui-designer`;
- frontend implementation -> `frontend-developer`;
- backend implementation -> `backend-developer`;
- every code candidate -> `code-reviewer` and `tester`.

Do not dispatch roles merely to use them. A mechanical implementation with frozen UI behavior and acceptance does not require `ui-designer`. Architecture and UI may run in parallel only without dependency. Complete required prototype work before corresponding frontend implementation.

## 4. Plan, isolate, and dispatch

Every task specifies ID, role, dependencies, authorized paths, input and output contracts, acceptance commands, and observable results. Reject TBDs and unverifiable criteria.

```bash
python3 <superflow>/scripts/git_workspace.py create-worktree \
  <run-id> --project "$PWD" --base-ref HEAD
```

- Serialize writes by default.
- Parallel task worktrees require frozen interfaces, no order dependency, no shared state, and disjoint files.
- Subagents modify only authorized files and never run Git.
- Every brief is self-contained and includes work directory, paths, acceptance, baseline, result Schema, and the absolute `builtinGuide` for architect, UI, frontend, or backend.
- Record the exact brief before dispatch with `workflow_state.py record-brief`. Record every accepted, rejected, blocked, retry, and repair result with `record-attempt`; never overwrite a previous attempt.
- Before dispatch, issue a capability bound to the exact role, run, and task. Include only `roleMemoryScript` and `roleMemoryCapability` in that role's brief. Never reuse or share a capability between roles or tasks.
- Relevant briefs include `browserProvider`, `uiPrototypeProvider`, and custom details.
- Snapshot HEAD and refs before and after dispatch. `policy_check.py` may precheck output; gate recording rechecks policy and current repository facts.

Dispatch is a binding protocol, not a notification:

1. prepare the worktree, dependencies, runtime, candidate, brief, and `before` snapshot before dispatch;
2. reserve the stable subagent session or task handle;
3. record the dispatch and capture its returned `dispatch_id`;
4. supply that exact ID with the task dispatch and require it as `dispatchId` in the result;
5. record any other already-planned independent dispatches;
6. wait for the dispatched agents; while any dispatch is `waiting`, do not edit files, run project commands, inspect the implementation with CodeGraph, operate browser or prototype tools, implement, test, review, commit, cherry-pick, change task status, freeze a candidate, record a gate, or advance the workflow;
7. after a terminal result, record the attempt against the same dispatch ID before validating, integrating, retrying, or continuing.

```bash
python3 <superflow>/scripts/workflow_state.py --project <task-worktree> \
  record-dispatch <run-id> <task-id> frontend-developer \
  --session-id <reserved-session-id> --before before.json

python3 <superflow>/scripts/workflow_state.py --project <task-worktree> \
  record-attempt <run-id> <task-id> frontend-developer initial accepted \
  --dispatch-id <dispatch-id> \
  --agent-result result.json --before before.json --after after.json \
  --reason 'Schema, policy, and repository evidence passed'
```

If an agent fails to start, terminates, or times out, record a `blocked` or `rejected` attempt for that dispatch before retrying, blocking, or cancelling the run. The main agent may relay evidence that only its selected provider session can collect, but only after the specialist requests it; this exception does not authorize the main agent to perform the specialist's implementation, review, test adjudication, or other assigned work. Never take over a waiting role merely to keep the run moving.

```bash
python3 <superflow>/scripts/policy_check.py \
  --project "$PWD" \
  --result <agent-result.json> \
  --before <before.json> \
  --after <after.json> \
  --role frontend-developer \
  --task-id T1 \
  --allowed-path 'src/frontend/**' \
  --code
```

Add `--ui` for prototype results or `--browser` for real-page results. Provider evidence must match project configuration. Successful browser and prototype evidence names the actual collector role, task, session, artifact SHA-256, and the result role that adjudicated it. The main agent may relay evidence it actually collected, but the tester must identify the main agent as collector instead of claiming collection. Never commit or mark success after a policy failure.

After the result passes Schema and policy checks, ingest its role-bound memory requests, then revoke the capability:

```bash
python3 <superflow>/scripts/role_memory.py --project "$PWD" ingest-result \
  --role frontend-developer --run-id <run-id> --result <agent-result.json> \
  --orchestrator-authorized

python3 <superflow>/scripts/role_memory.py --project "$PWD" revoke-capability \
  --capability <capability> --orchestrator-authorized
```

Revoke the capability without ingestion when result or policy validation fails. A subagent never calls capability issuance, revocation, ingestion, export, import, deletion, or another role's recall.

## 5. Implement from root cause and tests

Reproduce defects, inspect recent change and data flow, and establish one falsifiable root-cause hypothesis before repair.

```text
RED: prove the smallest test fails for the missing behavior
GREEN: implement the smallest complete behavior and prove it passes
REFACTOR: simplify only after green without adding behavior
```

After a subagent result:

1. record the terminal attempt against its exact dispatch ID, which releases the waiting lock;
2. validate structure, paths, tool evidence, and Git snapshots;
3. read the actual diff;
4. run the narrowest relevant verification;
5. stage only authorized paths and create a local commit;
6. inspect and cherry-pick approved parallel work into integration, then reverify.

## 6. Freeze the candidate and run dual gates

```bash
python3 <superflow>/scripts/workflow_state.py --project <integration-worktree> \
  set-candidate <run-id> <candidate-sha>
```

The SHA must resolve to the integration worktree's current HEAD. Dispatch reviewer and tester against that exact SHA.

Reviewer evaluates specification compliance, correctness and security, then consistency. Tester covers normal, failure, boundary, authorization, data, compatibility, responsive, and regression behavior, including real-page operation when required.

```bash
python3 <superflow>/scripts/workflow_state.py --project <integration-worktree> \
  record-gate <run-id> review <candidate-sha> <task-id> \
  --agent-result <review-result.json> --before <before.json> --after <after.json>

python3 <superflow>/scripts/workflow_state.py --project <integration-worktree> \
  record-gate <run-id> test <candidate-sha> <task-id> \
  --agent-result <test-result.json> --before <before.json> --after <after.json> \
  --allowed-path 'tests/**' --browser
```

Use `--browser` only for page tests and `--no-code` for non-code gates. Gate recording requires current HEAD, a clean business worktree, and no merge, rebase, or cherry-pick in progress. Any code change requires a new candidate and both gates.

## 7. Repair and circuit break

Validate each failed finding:

- correct and precise -> return it to the responsible developer;
- unclear -> main agent clarifies;
- technically incorrect -> reject with code, test, or authoritative evidence;
- conflicts with user decision -> ask the user.

```bash
python3 <superflow>/scripts/workflow_state.py --project <integration-worktree> \
  transition <run-id> fixing --task-id T1
```

Fix only current findings, run relevant tests, and direct re-review. Block after a third-round failure; never force PASS.

## 8. Complete and request user decisions

Only after all tasks are done and same-SHA review and test gates PASS may the run enter ready and finish. Candidate and gate lifecycle restrictions are enforced by the state script.

For explicit user risk acceptance:

1. record user, gate, and reason with `record-risk`;
2. transition to `risk_accepted`;
3. highlight failed evidence and accepted scope in the final report;
4. retain FAIL.

Report delivered behavior, commit scope, role artifacts, verification commands, gate results, accepted risks, remaining issues, and branch or worktree location.

After passing, offer local merge, push and PR, or preservation. Do not push, create a PR, merge, delete a branch, or remove a worktree without explicit authorization. Preserve a PR worktree until feedback is resolved by default.

Keep user-facing progress at meaningful milestones: requirements frozen, a routed role completed or blocked, a candidate was frozen, a gate changed outcome, or user authority is required. Keep internal retries, ledger maintenance, and repeated unchanged status out of the conversation.

## Block conditions

Stop automatic progression for a non-Git or dirty base worktree, Agent template conflict, undiscoverable required role, unresolved product scope, unavailable selected prototype or browser provider, missing permission, subagent authority violation, third-round failure, or required remote, release, destructive, or production action.

Preserve the environment and ledger. Report evidence, impact, and recovery without silent degradation or fabricated completion.
