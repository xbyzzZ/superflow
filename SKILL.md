---
name: superflow
description: Orchestrate multi-step feature, defect, and refactor delivery through a product-manager main agent and specialist Codex subagents for requirements, architecture, UI, frontend, backend, review, testing, and verified handoff. Use only when the user explicitly invokes Superflow in the current request. Never trigger it merely because a task appears suitable. Do not use for implicit matches, simple answers, read-only lookup, or one-step mechanical edits.
---

# Superflow

## Explicit invocation gate

Apply this Skill only when the user explicitly invokes `superflow` in the current request. A matching task, prior use, project configuration, installed Agent templates, or an existing ledger does not authorize activation. Without an explicit current-request invocation, do not initialize Superflow, create its run or worktree, dispatch its roles, enforce its gates, or present ordinary work as a Superflow run.

Act only as the requirements-writing product manager and mechanical orchestrator. Own user communication, frozen requirements, task routing, workflow state, Git integration, approvals, and final reporting. Never perform a specialist's architecture, design, implementation, debugging, testing, browser, prototype, or review work. Six specialist subagents perform all authorized professional work and return structured results.

## Invariants

1. The main agent only writes requirements and controls workflow mechanics: user decisions, ledger updates, dispatch/wait, deterministic result validation, branches/worktrees, staging, commits, integration, candidate freeze, gate recording, and reporting.
2. Subagents never modify `.codex/agents/`, the shared workflow ledger, or Git state, and never spawn agents.
3. Every code candidate requires review and test gates against the same candidate SHA. `lite` may use one `code-reviewer` result for both; `standard` and `strict` require separate reviewer and tester results.
4. The product manager cannot override a failed gate. Only the user may accept risk; retain FAIL.
5. Allow at most three automatic repair rounds per task. Resume the original developer for rounds one and two; use a fresh developer for round three. Block after another failure.
6. Do not use Backlog MCP. The local workflow ledger is authoritative.
7. Completion claims, state transitions, and commits require fresh evidence from the current turn.
8. After recording a specialist dispatch, the main agent becomes coordination-only until that dispatch returns and its attempt is recorded. It must not overlap the specialist's work, perform project execution, or advance the workflow.
9. Outside waiting intervals, the main agent still must not read or analyze implementation for professional judgment, use CodeGraph, operate browser or prototype tools, edit project files, run project verification or runtime commands, diagnose defects, design solutions, review code, or test behavior. Route that work to the responsible specialist.

Read before execution:

- [Workflow state machine](references/workflow-state-machine.md)
- [Role contracts](references/role-contracts.md)
- [Tool and optional Skill policy](references/tool-and-skill-policy.md)
- [Superpowers-derived rules](references/superpowers-derived-rules.md)
- [Role-isolated memory](references/role-memory.md)

The main agent reads only the product-management guide. Link every other built-in guide into the corresponding specialist brief; do not load, summarize, or apply it in the main-agent context:

- Before freezing requirements: [product management rules](references/product-management-rules.md)
- Architect brief guide: [architecture design rules](references/architecture-design-rules.md)
- UI designer brief guide: [UI/UX design rules](references/ui-ux-design-rules.md)
- Frontend developer brief guide: [frontend engineering rules](references/frontend-engineering-rules.md)
- Backend developer brief guide: [backend engineering rules](references/backend-engineering-rules.md)
- Tester brief guide: [testing strategy](references/testing-strategy.md)
- Code reviewer brief guide: [code review criteria](references/code-review-criteria.md)

## 1. Initialize the target project

Resolve `git rev-parse --git-path info/superflow.json`. When configuration is absent, ask the user before any Superflow write:

- browser: Chrome MCP (`chrome-mcp`), a user-defined specialist-direct provider (`custom`), or the main-only Codex Browser plugin (`codex-browser`) for work that does not require delegated real-page acceptance.

Recommend `chrome-mcp` for normal delivery because frontend and tester subagents can operate it directly. Explain before selection that `codex-browser` is main-agent scoped and therefore unusable for Superflow professional work: the main agent never performs browser work, and any browser-required task will pause until the user reconfigures a specialist-direct provider for a new run.
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

1. read project instructions needed for safe orchestration plus user-provided or explicitly named product documents; do not inspect source, tests, recent implementation changes, or runtime behavior;
2. identify required specialist roles from the frozen product scope; delegate repository, dependency, implementation, and runtime discovery to those roles, and never probe CodeGraph, browser, prototype, or professional optional Skills from the main-agent context;
3. select the smallest safe execution profile:

```bash
python3 <superflow>/scripts/workflow_profile.py \
  --signals '{"userVisible":false,"crossModule":false}'
```

Use `lite` for localized low-risk work, `standard` for user-visible, browser, UI-prototype, cross-module, or public-interface work, and `strict` for authorization, security, data migration, production, release, or destructive work. The selector may upgrade an explicit request but never downgrade below detected risk.

4. create a complete task DAG. Every task has a stable ID, role, dependencies, authorized paths, acceptance criteria, exact verification commands, observable results, and an initial status;
5. initialize the run with the selected profile:

```bash
python3 <superflow>/scripts/workflow_state.py --project "$PWD" init \
  --profile auto \
  --risk-signals '{"userVisible":false,"crossModule":false}' \
  --document-language zh-CN \
  --plan plan.json
```

Use `zh-CN` when the user communicates in Simplified Chinese; otherwise use `en`. The CLI defaults to automatic profile selection and English documents. Pass the same complete signal set used for preview; `workflow_state.py` reruns the selector so a requested profile cannot bypass an upgrade. `plan.json` must contain complete task contracts, not titles or placeholders. Save `run_id`. The script freezes the selection, profile, project tools, and user-document language in shared state. Existing states without a profile remain `strict`; legacy runs without user-document configuration continue without the new document requirement. Update state only through `workflow_state.py`; never copy, edit, or synthesize ledger files manually.

Routine coordination reads the compact summary:

```bash
python3 <superflow>/scripts/workflow_state.py --project "$PWD" \
  summary <run-id>
```

Use full `show` only for recovery or audit. It may contain complete dispatch history and should not be added to model context during normal progression.

## 3. Freeze requirements and route conditionally

Before product work, issue a temporary `product-manager` memory capability, recall with a query derived from the current requirement, and revoke it after the product task:

```bash
python3 <superflow>/scripts/role_memory.py --project "$PWD" issue-capability \
  --role product-manager --run-id <run-id> --task-id <task-id> \
  --orchestrator-authorized

python3 <superflow>/scripts/role_memory.py --project "$PWD" recall \
  --capability <capability> --query '<requirement and project terms>' \
  --limit 3 --max-bytes 2048
```

Reverify recalled guidance against current facts. Record only durable, evidenced product knowledge with `record`; never retain transient progress or sensitive content.

Apply `product-management-rules.md` to freeze user, evidence, scenario, scope, failures, authorization, compatibility, success criteria, and observable acceptance.

Write the structured baseline defined by `assets/schemas/requirements.schema.json`, then freeze it before entering `requirements_ready`:

```bash
python3 <superflow>/scripts/workflow_state.py --project "$PWD" \
  record-requirements <run-id> --requirements requirements.json
```

The state script generates immutable `requirements.md` and an automatically refreshed `process-log.md` in the shared run directory under the Git common directory. These are user-facing derived documents, not business-repository files; never copy them into the candidate commit or edit them directly. Give the user their absolute paths after requirements freeze and in the final report.

Ask the user only for material product ambiguity, scope expansion, destructive or remote action, unresolved third-round failure, or final push, PR, merge, or cleanup.

Route by profile:

- cross-module, API, database, authorization, migration, infrastructure, or public contract -> `architect`;
- unresolved user-visible flow, state, interaction, or visual design decision -> `ui-designer`;
- frontend implementation -> `frontend-developer`;
- backend implementation -> `backend-developer`;
- `lite` candidate -> one `code-reviewer` quality task that runs frozen verification commands and performs review; use its accepted result for both gates;
- `standard` or `strict` candidate -> independent `code-reviewer` and `tester` tasks, dispatched in parallel after the candidate is frozen.

Do not dispatch roles merely to use them. `lite` does not route architect, UI, or tester. Any trigger requiring those roles raises the minimum profile. Architecture and UI may run in parallel only without dependency. Complete required prototype work before corresponding frontend implementation.

## 4. Plan, isolate, and dispatch

Every task specifies ID, role, dependencies, authorized paths, input and output contracts, acceptance commands, and observable results. Reject TBDs and unverifiable criteria.

```bash
python3 <superflow>/scripts/git_workspace.py create-worktree \
  <run-id> --project "$PWD" --base-ref HEAD
```

- Serialize writes by default.
- Parallel task worktrees require frozen interfaces, no order dependency, no shared state, and disjoint files.
- Subagents modify only authorized files and never run Git.
- Every brief is minimal and self-contained. Include only work directory, frozen task facts, paths, acceptance, baseline, result Schema path, execution profile, context controls, tool routing, and the absolute `builtinGuide` for architect, UI, frontend, or backend. Never include the parent conversation or duplicate shared prose.
- Record the exact brief before dispatch with `workflow_state.py record-brief`. Record every accepted, rejected, blocked, retry, and repair result with `record-attempt`; never overwrite a previous attempt.
- Put the absolute `roleMemoryScript` in the immutable task brief. Before every dispatch or retry, issue a fresh capability bound to the exact role, run, and task; pass it to `record-dispatch` and supply it to the subagent as `roleMemoryCapability` in the task dispatch wrapper. `record-brief` validates the script and required role guide; `record-dispatch` validates the capability scope and stores only its digest. Never omit, reuse, persist in the brief, or share a capability between roles, tasks, or attempts.
- Relevant briefs include `browserProvider`, `browserRequired`, the derived `browserAccessMode`, `uiPrototypeProvider`, and custom details. Use `main-only` for `codex-browser`; use `specialist-direct` for `chrome-mcp` and custom providers. Reject a browser-required brief that uses `main-only`.
- Set `contextMode=minimal`. Dispatch with no conversation fork. Set `memoryLimit`, `memoryMaxBytes`, and `resultDetail` from the frozen profile: `lite=3/2048/compact`, `standard=5/4096/standard`, `strict=10/8192/full`.
- Set `codeGraphRequired=true` only for cross-module, call-chain, data-flow, or blast-radius work. A localized `lite` task uses precise search without probing CodeGraph.
- Snapshot HEAD and refs before and after dispatch. `policy_check.py` may precheck output; gate recording rechecks policy and current repository facts.

Dispatch is a binding protocol, not a notification:

1. prepare only the worktree, immutable brief, role capability, and `before` snapshot before dispatch; the dispatched specialist owns its required dependencies, runtime, verification commands, and page operations;
2. reserve the stable subagent session or task handle;
3. record the dispatch and capture its returned `dispatch_id`;
4. dispatch with `fork_turns=none` or the platform's equivalent no-parent-conversation option; supply only the brief, execution wrapper, and required artifact paths;
5. supply that exact ID with the task dispatch and require it as `dispatchId` in the result;
6. record any other already-planned independent dispatches;
7. wait for the dispatched agents; while any dispatch is `waiting`, do not edit files, run project commands, inspect the implementation with CodeGraph, operate browser or prototype tools, implement, test, review, commit, cherry-pick, change task status, freeze a candidate, record a gate, or advance the workflow;
8. after a terminal result, record the attempt against the same dispatch ID before validating, integrating, retrying, or continuing.

```bash
python3 <superflow>/scripts/workflow_state.py --project <task-worktree> \
  record-dispatch <run-id> <task-id> frontend-developer \
  --session-id <reserved-session-id> --before before.json \
  --memory-capability <fresh-role-memory-capability>

python3 <superflow>/scripts/workflow_state.py --project <task-worktree> \
  record-attempt <run-id> <task-id> frontend-developer initial accepted \
  --dispatch-id <dispatch-id> \
  --agent-result result.json --before before.json --after after.json \
  --reason 'Schema, policy, and repository evidence passed'
```

If an agent fails to start, terminates, or times out, record a `blocked` or `rejected` attempt for that dispatch before retrying, blocking, or cancelling the run. If a browser-required task uses `codex-browser`, block before dispatch and ask the user to reconfigure `chrome-mcp` or a specialist-direct custom provider for a new run. Frontend developers may use the selected direct browser for reproduction, debugging, and self-checks; testers use it independently for final acceptance. If a direct provider is unavailable to either role, record its structured `browserEvidenceRequest` as blocked and request provider remediation; the main agent must not operate a browser on the specialist's behalf.

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

Add `--ui` for prototype results or `--browser` for real-page results. Provider evidence must match project configuration. Successful browser and prototype evidence names the actual collector role, task, session, artifact SHA-256, and the result role that adjudicated it. Never commit or mark success after a policy failure.

Require the specialist result to report successful `memoryRecall` metadata without the capability, query, or recalled content. After the result passes Schema and policy checks, ingest its role-bound memory requests, then revoke the capability:

```bash
python3 <superflow>/scripts/role_memory.py --project "$PWD" ingest-result \
  --role frontend-developer --run-id <run-id> --result <agent-result.json> \
  --orchestrator-authorized

python3 <superflow>/scripts/role_memory.py --project "$PWD" revoke-capability \
  --capability <capability> --orchestrator-authorized
```

Revoke the capability without ingestion when result or policy validation fails. A subagent never calls capability issuance, revocation, ingestion, export, import, deletion, or another role's recall.

## 5. Implement from root cause and tests

Require the responsible developer specialist to reproduce defects, inspect recent change and data flow, and establish one falsifiable root-cause hypothesis before repair. The main agent only records and routes this contract.

```text
RED: prove the smallest test fails for the missing behavior
GREEN: implement the smallest complete behavior and prove it passes
REFACTOR: simplify only after green without adding behavior
```

After a subagent result:

1. record the terminal attempt against its exact dispatch ID, which releases the waiting lock;
2. validate structure, authorized paths, tool evidence, and Git snapshots with deterministic policy checks;
3. if the implementation result is insufficient, reject it or dispatch repair instead of taking over its verification;
4. stage only authorized paths and create a local commit;
5. integrate approved implementation commits mechanically into the integration worktree;
6. freeze the resulting HEAD immediately and dispatch the required quality gate or dual gates.

Between an accepted implementation result and gate dispatch, the main agent must not run project tests, builds, linters, type checks, containers, servers, runtime probes, CodeGraph analysis, or browser/prototype operations; it must not create or edit temporary runtime configuration. Those checks belong to the implementation specialist before handoff and to the candidate-bound quality agents after handoff. The only permitted project operations are deterministic result/policy validation and the Git actions needed to commit, integrate, confirm a clean HEAD, and freeze the candidate.

## 6. Freeze the candidate and run dual gates

```bash
python3 <superflow>/scripts/workflow_state.py --project <integration-worktree> \
  set-candidate <run-id> <candidate-sha>
```

The SHA must resolve to the integration worktree's current HEAD.

For `lite`, dispatch one `code-reviewer` against the candidate. It runs every frozen verification command and then reviews specification, correctness, security, and consistency. After its attempt is accepted, record both gates from the same result; the test gate fails unless every command passed.

For `standard` and `strict`, dispatch reviewer and tester in parallel against the exact candidate SHA. Reviewer evaluates specification compliance, correctness and security, then consistency. Tester covers normal, failure, boundary, authorization, data, compatibility, responsive, and regression behavior, including real-page operation when required.

Do not preflight the candidate for these agents. The reviewer prepares only its read-only inspection context; the tester owns candidate runtime startup, frozen verification commands, and real-page setup. A gate blocked by environment or permission returns evidence and does not cause the main agent to rerun or replace that gate.

```bash
python3 <superflow>/scripts/workflow_state.py --project <integration-worktree> \
  record-gate <run-id> review <candidate-sha> <task-id> \
  --agent-result <review-result.json> --before <before.json> --after <after.json>

python3 <superflow>/scripts/workflow_state.py --project <integration-worktree> \
  record-gate <run-id> test <candidate-sha> <task-id> \
  --agent-result <test-result.json> --before <before.json> --after <after.json> \
  --allowed-path 'tests/**' --browser
```

For `lite`, pass the accepted reviewer result and reviewer task ID to both gate commands. Use `--browser` only for page tests and `--no-code` for non-code gates. Gate recording requires current HEAD, a clean business worktree, and no merge, rebase, or cherry-pick in progress. Any code change requires a new candidate and both gates.

## 7. Repair and circuit break

Validate each failed finding:

- a gate `FAIL` -> return its complete evidence to the responsible developer;
- unclear technical evidence -> redispatch the originating quality role for clarification;
- conflicting specialist conclusions -> dispatch an independent specialist recheck without deciding the technical issue;
- conflict with frozen product intent -> ask the user only for the product decision.

```bash
python3 <superflow>/scripts/workflow_state.py --project <integration-worktree> \
  transition <run-id> fixing --task-id T1
```

Fix only current findings, run relevant tests, and direct re-review. Block after a third-round failure; never force PASS.

## 8. Complete and request user decisions

Only after all tasks are done may the run finish: normally through same-SHA review and test PASS in `ready`, or through `risk_accepted` after the user explicitly accepts every current failed gate. Candidate and gate lifecycle restrictions are enforced by the state script.

For explicit user risk acceptance:

1. record user, gate, and reason with `record-risk`;
2. transition to `risk_accepted`;
3. highlight failed evidence and accepted scope in the final report;
4. retain FAIL.

Report delivered behavior, commit scope, role artifacts, verification commands, gate results, accepted risks, remaining issues, branch or worktree location, and absolute paths to `requirements.md` and `process-log.md`.

After passing, offer local merge, push and PR, or preservation. Do not push, create a PR, merge, delete a branch, or remove a worktree without explicit authorization. Preserve a PR worktree until feedback is resolved by default.

Keep user-facing progress at meaningful milestones: requirements frozen, a routed role completed or blocked, a candidate was frozen, a gate changed outcome, or user authority is required. Keep internal retries, ledger maintenance, and repeated unchanged status out of the conversation.

## Block conditions

Pause in the current non-terminal state and request user authority before a remote, release, destructive, or production action that was not explicitly authorized in the current request. Do not transition the run to terminal `blocked` merely while waiting for that answer.

Transition to terminal `blocked` only for a non-Git or dirty base worktree, Agent template conflict, undiscoverable required role, unresolved product scope, unavailable selected prototype or browser provider, an external or system permission that cannot be restored within the current run, subagent authority violation, third-round failure, or another condition that cannot be recovered within the current run.

Preserve the environment and ledger. Report evidence, impact, and recovery without silent degradation or fabricated completion.
