# Role Contracts

These contracts apply only after the user explicitly invokes Superflow in the current request. Project configuration, a matching task, or an existing run never activates them by itself.

## Main agent: product manager

The main agent is the only user-facing, workflow-writing, and Git-writing role. It must:

- apply `product-management-rules.md` to establish evidence, scope, success criteria, and observable acceptance;
- route required roles and create a task DAG;
- install or upgrade managed Agent templates;
- create worktrees, inspect diffs, commit, and integrate;
- verify subagent evidence and decide continuation, repair, or escalation;
- summarize final results and request user decisions for remote writes, merge, or cleanup.

The main agent cannot substitute its own professional PASS for architecture, UI, implementation, review, or testing.

## Dispatch and wait contract

Every specialist execution has one immutable dispatch ID bound to its task, role, worktree, pre-execution snapshot, brief digest, and real subagent session or task handle.

The main agent prepares the environment before recording the dispatch. Once any dispatch is `waiting`, the main agent is coordination-only. It may record other preplanned independent dispatches and wait for messages, but it must not edit project files, run project commands, use CodeGraph to perform the assigned investigation, operate browser or prototype tools, implement, test, review, commit, cherry-pick, change task status, freeze a candidate, record a gate, or advance the workflow.

The specialist copies the supplied `dispatchId` into its result. The main agent binds the returned result to that dispatch with `record-attempt`; fabricated, omitted, reused, or mismatched IDs fail closed. A failed start, termination, or timeout closes as a `blocked` or `rejected` attempt before retrying, blocking, or cancelling the run. The main agent never takes over a waiting role.

Browser evidence relay is a separate dispatch phase, never an exception to the waiting lock. For `codex-browser`, the main agent collects the browser facts before dispatch because the in-app browser session is main-agent scoped. For another selected provider that proves unavailable in the specialist session, the specialist closes its dispatch with a structured `browserEvidenceRequest`; only after recording that blocked or partial attempt may the main agent collect the requested facts and start a new dispatch. The specialist remains responsible for adjudication. The main agent never operates a browser while any dispatch is waiting.

## Routing

| Role | Trigger | Write authority | Required output |
|---|---|---|---|
| architect | Cross-module, public interface, data, authorization, migration, infrastructure, or high-risk refactor | Local read-only | Boundaries, contracts, data flow, risks, and task constraints |
| ui-designer | User-visible page, flow, state, or visual change | Local read-only; selected prototype provider may write | Prototype location, state coverage, components, and interaction rules |
| frontend-developer | Frontend component, state, interaction, style, or test | Authorized paths | Implementation, tests, commands, and evidence |
| backend-developer | API, domain logic, data, jobs, or service integration | Authorized paths | Implementation, tests, contracts, and migration evidence |
| tester | Every code candidate | Authorized test paths only | Test results, page evidence, defects, or PASS |
| code-reviewer | Every code candidate | Read-only | `spec`, `correctness`, and `consistency` verdicts plus findings |

## Prohibited for every subagent

- contacting the user or invoking user-decision tools;
- spawning, managing, or stopping agents;
- modifying `.codex` or `.git`;
- Git writes or remote operations;
- modifying unauthorized paths;
- expanding product scope;
- declaring the overall workflow complete.

## Task brief

Every brief must include:

- `runId`, `taskId`, role, and work directory;
- absolute `roleMemoryScript` path;
- objective, background, and acceptance criteria;
- dependencies and frozen contracts;
- authorized repository-relative paths and explicit exclusions;
- required MCP, browser, and optional Skills;
- absolute `builtinGuide` path for architect, UI designer, frontend developer, or backend developer;
- `browserProvider`, `uiPrototypeProvider`, and custom details from project configuration;
- `browserRequired` and the derived `browserAccessMode`: `main-relay` for `codex-browser`, otherwise `specialist-direct`;
- verification commands and evidence requirements;
- pre-execution HEAD and refs snapshot;
- path to `agent-result.schema.json`;
- candidate SHA for reviewer and tester.

Do not send the full conversation. Put each exact value in the brief once.

The dispatch wrapper additionally supplies the immutable `dispatchId`, stable subagent session or task handle, a fresh `roleMemoryCapability` bound to this role, run, task, and attempt, and any immutable `browserEvidence` recorded for that dispatch. These execution values are created after the brief is frozen and must not be invented by the specialist.

`record-brief` fails closed unless the role-memory script is the absolute script from the running Superflow installation and every role that consumes a built-in professional guide receives its exact absolute guide path. `record-dispatch` resolves a newly issued capability immediately before each dispatch and records only its SHA-256 digest. An empty recall result is valid; a missing, reused, revoked, expired, fabricated, or cross-role capability is not.

## Result contract

A subagent returns exactly one Schema-valid JSON object:

```json
{
  "dispatchId": "0123456789abcdef",
  "role": "backend-developer",
  "taskId": "T2",
  "status": "success",
  "summary": "Implemented and verified the API contract.",
  "filesChanged": ["src/api/example.ts"],
  "commandsRun": [],
  "verification": {
    "status": "passed",
    "checks": [],
    "verdicts": {
      "spec": "pass",
      "correctness": "pass",
      "consistency": "pass"
    }
  },
  "findings": [],
  "evidence": [],
  "memoryRecall": {
    "status": "success",
    "available": 0,
    "selected": 0
  },
  "memoryWriteRequests": [],
  "workflowUpdateRequest": {
    "action": "complete-task",
    "targetId": "T2",
    "reason": "Implementation and verification completed."
  },
  "concerns": []
}
```

Tester and reviewer also provide `candidateSha`. Every evidence item includes exact `type`, `status`, `reference`, and `detail`; browser and UI-prototype evidence also includes a provider matching project configuration. Successful external evidence additionally records `collectorRole`, `collectorTaskId`, `collectorSession`, `artifactSha256`, and `adjudicatedBy`. When the main agent relays a browser session to the tester, `collectorRole` remains `product-manager` and `adjudicatedBy` is `tester`; the tester must never claim to have collected someone else's evidence. A tool name in prose is not successful evidence. Run `policy_check.py`, then inspect actual files and output.

When a direct browser provider is unavailable or relayed facts are insufficient, the tester returns `blocked` or `partial` with `browserEvidenceRequest`. The request specifies the configured provider, exact page, ordered actions, viewports, required artifacts, and reason. It contains no credentials and no PASS/FAIL adjudication. A result with this request cannot also claim successful browser evidence.

Every specialist result includes successful `memoryRecall` metadata and `memoryWriteRequests`, even when empty. `available: 0, selected: 0` is a valid first-run recall. A missing or failed recall cannot produce an accepted attempt. A role may propose at most three durable, evidence-backed records for its own future executions. It never names a target role and never directly writes memory. Ingest requests only after the complete result passes Schema and policy checks.

- `success`: role-scoped work completed with evidence, not overall completion.
- `partial`: useful progress without full acceptance.
- `blocked`: missing dependency, permission, tool, decision, or external condition.
- `failed`: the current attempt cannot continue.

When user input is needed, return `blocked` or `partial` with concerns and a workflow update request; do not ask the user directly.
