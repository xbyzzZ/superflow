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
- absolute `roleMemoryScript` path and a temporary `roleMemoryCapability` bound to the same role, run, and task;
- objective, background, and acceptance criteria;
- dependencies and frozen contracts;
- authorized repository-relative paths and explicit exclusions;
- required MCP, browser, and optional Skills;
- absolute `builtinGuide` path for architect, UI designer, frontend developer, or backend developer;
- `browserProvider`, `uiPrototypeProvider`, and custom details from project configuration;
- verification commands and evidence requirements;
- pre-execution HEAD and refs snapshot;
- path to `agent-result.schema.json`;
- candidate SHA for reviewer and tester.

Do not send the full conversation. Put each exact value in the brief once.

## Result contract

A subagent returns exactly one Schema-valid JSON object:

```json
{
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

Every specialist result includes `memoryWriteRequests`, even when empty. A role may propose at most three durable, evidence-backed records for its own future executions. It never names a target role and never directly writes memory. Ingest requests only after the complete result passes Schema and policy checks.

- `success`: role-scoped work completed with evidence, not overall completion.
- `partial`: useful progress without full acceptance.
- `blocked`: missing dependency, permission, tool, decision, or external condition.
- `failed`: the current attempt cannot continue.

When user input is needed, return `blocked` or `partial` with concerns and a workflow update request; do not ask the user directly.
