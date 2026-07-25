# Superflow

[![CI](https://github.com/xbyzzZ/superflow/actions/workflows/ci.yml/badge.svg)](https://github.com/xbyzzZ/superflow/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[简体中文](README_CN.md)

Superflow is a Codex Skill for orchestrating an end-to-end software delivery workflow with one product-manager main agent and six conditionally routed specialist agents.

It makes the main agent the single workflow coordinator, keeps recoverable project-local state, isolates implementation in Git worktrees, enforces structured agent contracts, and requires testing and code review to approve the same candidate commit before completion.

Superflow activates only when the user explicitly invokes it in the current request, such as with `$superflow`. A suitable-looking task, prior Superflow use, project configuration, or an unfinished ledger never triggers it implicitly.

## Why Superflow

Multi-agent coding often fails at the boundaries: agents change files outside their scope, approvals refer to different revisions, stale test results survive later edits, or no durable record explains how a run reached “done.” Superflow addresses these problems with deterministic scripts and fail-closed policies.

Key properties:

- One main agent owns user communication, workflow state, approvals, and Git writes.
- A recorded specialist dispatch puts the main agent into coordination-only waiting, preventing duplicate implementation, testing, review, browser work, and Git progression.
- Six project-level agents are routed only when their expertise is required.
- Three execution profiles control cost: `lite` uses one combined quality Agent, `standard` uses independent parallel gates, and `strict` retains the full high-risk workflow.
- New subagents receive a minimal brief without the parent conversation. Memory and CodeGraph usage are bounded by the frozen profile.
- Every agent returns a strict JSON result validated against a bundled Schema.
- Test and review gates must approve the same real Git commit.
- Any candidate change invalidates previous approvals.
- Repair attempts are bound to an immutable task lineage and stop after three rounds.
- Run state is recoverable from a project-local snapshot and hash-chained event log.
- Every new run produces a user-facing requirements baseline and an automatically refreshed delivery process log outside the business worktree.
- Routine orchestration uses a compact state summary; complete Agent results remain in immutable audit artifacts and are loaded only for recovery or evidence inspection.
- Every role can recall its own project history through a temporary role-bound capability and can propose durable memory without accessing another role.
- Brief recording validates the installed role-memory script, and every initial, retry, or repair dispatch requires a fresh role-bound capability. Missing, reused, expired, revoked, fabricated, or cross-role capabilities fail closed, while an empty role history remains valid.
- Accepted specialist results must report successful memory recall counts without exposing the capability, query, or recalled content.
- Remote Git operations, destructive cleanup, and final integration require explicit user authorization.

## Roles

| Role | Runtime | Responsibility |
|---|---|---|
| Product manager | Current main agent | Requirements, routing, state, Git, approvals, user decisions |
| Architect | gpt-5.6-sol, high | Architecture, boundaries, contracts, data flow, risk |
| UI designer | gpt-5.6-sol, medium | Deliver flows, states, components, and interaction rules in the prototype provider selected during project initialization |
| Frontend developer | gpt-5.6-sol, medium | Frontend implementation and focused tests |
| Backend developer | gpt-5.6-sol, medium | APIs, domain logic, data, services, migrations |
| Tester | gpt-5.6-terra, medium | Automated tests, regression coverage, real browser verification |
| Code reviewer | gpt-5.6-sol, high | Spec compliance, correctness, consistency, security, maintainability |

The six specialist agents do not form a mandatory linear pipeline. A localized low-risk task normally uses its developer plus one combined quality reviewer. A backend-only defect does not require UI, while a cross-module feature may require architecture, UI, frontend, backend, testing, and review.

## Execution Profiles

| Profile | Minimum use | Quality gates | Context budget |
|---|---|---|---|
| `lite` | Localized low-risk code, tests, or documentation | One code reviewer runs tests and review; one accepted result supports both gates | 3 memories, 2 KiB, compact output, CodeGraph only when material |
| `standard` | User-visible, browser, prototype, cross-module, or public-interface work | Independent tester and reviewer dispatched in parallel | 5 memories, 4 KiB, standard output |
| `strict` | Authorization, security, migration, production, release, or destructive work | Independent gates and all risk-triggered specialists | 10 memories, 8 KiB, full output |

The deterministic selector chooses the smallest safe profile. A user may request a higher profile, but neither the user request nor the main agent may lower it below detected risk. Legacy runs without a stored profile remain strict.

`workflow_state.py init` accepts `--profile auto`, a JSON `--risk-signals` object, and `--document-language en|zh-CN`. It reruns the selector before freezing the run and initializes the user-facing process log. With no risk signals, a new CLI run resolves to `lite`.

## Requirements

- Codex with project-level agent support
- Python 3.10 or newer
- Git
- A clean Git worktree for the target project

Conditional tools:

- CodeGraph is used for material code discovery when the frozen brief sets `codeGraphRequired`; localized `lite` work may use precise local inspection instead.
- First initialization asks the user to select Penpot MCP, the Codex Figma plugin, or a custom UI prototype provider.
- First initialization asks the user to select the Codex Browser plugin, Chrome MCP, or a custom browser provider.
- Git shared metadata preserves both selections for every worktree and subsequent run in the repository.
- Architecture, UI/UX design, frontend engineering, backend engineering, product management, testing, and code review have built-in professional guides; external Skills are optional enhancements.
- Optional enhancement Skills may improve individual roles but are not core dependencies.

## Installation

Copy this directory to ~/.codex/skills/superflow, or to $CODEX_HOME/skills/superflow when CODEX_HOME is configured:

~~~bash
mkdir -p ~/.codex/skills
cp -R ./superflow ~/.codex/skills/superflow
~~~

Restart Codex if the current session does not discover the Skill. You may also invoke Superflow by its absolute path when your Codex environment supports path-based Skill loading.

GitHub release archives contain an installable top-level `superflow/` directory:

~~~bash
unzip superflow-v0.2.0.zip -d ~/.codex/skills
~~~

## Quick Start

From a Git project, ask Codex to use Superflow:

~~~text
Use $superflow to implement this feature from requirements through verified delivery: ...
~~~

On first use, the main agent must ask the user to select browser and UI prototype providers before initializing managed configuration:

~~~bash
python3 <superflow>/scripts/init_project.py --project "$PWD" \
  --browser-provider codex-browser \
  --ui-provider penpot-mcp
~~~

The example providers are not silent defaults; replace them with the user's actual selections. The script installs or upgrades six project Agent templates under `.codex/agents/`, excludes the entire `.codex/` directory through Git's local `info/exclude`, and writes tool selections to shared Git metadata. Initialization refuses to continue if any `.codex` file is already tracked. It preserves conflicting user files and pauses when a restart is required to discover an Agent or plugin.

Existing configuration is reused. Only an explicit user request authorizes `--reconfigure`. Each run freezes its startup selection; after reconfiguration, an old run may only be blocked or cancelled and cannot mix provider evidence.

## Workflow

~~~text
initialize
  → preflight
  → discovery
  → requirements ready
  → architecture? / UI design?
  → plan
  → implementation
  → verification + review
  → repair (up to three rounds)
  → ready or explicitly accepted risk
  → finished
~~~

The main agent performs these core steps:

1. Initialize or upgrade the six managed agent templates.
2. Verify that the project is a clean Git worktree.
3. Clarify requirements, freeze a user-facing `requirements.md`, and define observable acceptance criteria.
4. Select and freeze the smallest safe execution profile, then route only its required specialist roles.
5. Create an integration worktree and optional independent task worktrees.
6. Route browser access explicitly: Codex Browser facts are collected by the main agent before dispatch and independently adjudicated by the tester; direct providers stay with the specialist.
7. Record each dispatch, pass its immutable ID and any SHA-256-bound browser artifact to the specialist, and wait without overlapping the assigned work.
8. Bind each returned result to its dispatch, then validate the result, actual diff, tool evidence, and Git snapshot.
9. Commit only explicitly authorized paths.
10. Freeze the integration worktree’s real HEAD as the candidate.
11. Record same-SHA gates: one combined quality result for `lite`, or independent parallel tester and reviewer results for `standard` and `strict`.
12. Finish only after every planned task is done and both gates pass, or after the user explicitly accepts each current failed gate.

The state script also maintains `process-log.md` from audited events. Both user-facing Markdown files live under the shared run directory, so every worktree can read them without changing the business candidate. Use compact `summary` for normal coordination and full `show` only for recovery or audit.

## Candidate and Dual-Gate Approval

A candidate must be the current, clean integration-worktree HEAD:

~~~bash
python3 <superflow>/scripts/workflow_state.py \
  --project <integration-worktree> \
  set-candidate <run-id> <candidate-sha>
~~~

Capture Git snapshots before and after a specialist run:

~~~bash
python3 <superflow>/scripts/git_workspace.py snapshot \
  --project <integration-worktree> > before.json
~~~

Gate results are derived from complete Agent result files. PASS or FAIL cannot be manually supplied:

~~~bash
python3 <superflow>/scripts/workflow_state.py \
  --project <integration-worktree> \
  record-gate <run-id> test <candidate-sha> <task-id> \
  --agent-result test-result.json \
  --before before.json \
  --after after.json \
  --allowed-path 'tests/**' \
  --browser

python3 <superflow>/scripts/workflow_state.py \
  --project <integration-worktree> \
  record-gate <run-id> review <candidate-sha> <task-id> \
  --agent-result review-result.json \
  --before before.json \
  --after after.json
~~~

The scripts verify the Agent role, task, candidate SHA, Schema, authorized paths, Git authority, tool evidence, verification checks, test commands, findings, and current repository state.

## Recoverable State

Each run is stored outside the worktree under the Git common directory at `superflow/workflows/<run-id>/`. Every linked worktree therefore reads the same ledger:

~~~text
state.json
events.jsonl
plan.json
routing.json
worktrees.json
requirements.json
requirements.md
process-log.md
briefs/
artifacts/
attempts/
gates/
~~~

Each planned task freezes its role, dependencies, authorized paths, acceptance criteria, exact verification commands, and observable results. Briefs, dispatch routing, worktree registrations, every accepted or rejected attempt, and candidate-bound gates are retained as audit artifacts. A waiting dispatch is bound to its task, role, worktree, brief, snapshot, and real subagent session; state progression and Git writes remain locked until its result is recorded. `state.json` is validated against the bundled Schema and cross-field invariants. `events.jsonl` is an append-only revision chain with state hashes and event-to-event hashes. Writes use a process lock and revision compare-and-swap to reject stale concurrent updates.

Browser access is also frozen in the brief. `codex-browser` uses a main-agent relay because the in-app browser session is not assumed to exist in a subagent: the main agent captures facts before dispatch, the state script copies and hashes the artifact, and the tester independently adjudicates it. `chrome-mcp` and custom providers are specialist-direct by default. If a direct provider is unavailable, the specialist closes its dispatch with a structured evidence request; the main agent may collect the requested facts only after the waiting lock is released and must start a new dispatch.

## Role-Isolated Memory

All seven roles have separate project memory under the Git common directory at `superflow/memory/`, shared by linked worktrees but never committed or pushed.

At dispatch, the main agent issues a temporary capability bound to one role, run, and task. The role uses that capability to query its own memory directly; `recall` does not accept a role selector and performs no filesystem write or lock creation. After the complete Agent result passes Schema and policy checks, the main agent ingests up to three structured `memoryWriteRequests` and revokes the capability.

Recall selects high-importance, query-relevant, and recent entries. Dispatch limits are profile-bound: 3 records/2 KiB for `lite`, 5/4 KiB for `standard`, and 10/8 KiB for `strict`. Each role keeps at most 500 active records. New records may supersede old records, while superseded and overflow records move to a role-local archive.

Memory never crosses roles. Shared contracts and project facts must travel through briefs, formal artifacts, or project documentation. Explicit user authorization is required to list, view, delete, clear, export, or import memory. See [role-isolated memory](references/role-memory.md).

## Safety Model

- Specialist agents cannot contact the user, update workflow state, or perform Git writes.
- The entire project-local `.codex/` directory is excluded through Git `info/exclude`; tracked `.codex` files block initialization.
- Only the main agent can stage, commit, or integrate changes.
- Push, PR creation, final merge, branch deletion, and worktree deletion are never automatic.
- The Git helper exposes only local preflight, worktree creation, snapshot, status, commit, and cherry-pick operations.
- Automatic Git writes stop when any executable Git hook is active; Superflow does not bypass hooks.
- Commit paths use literal pathspecs and reject broad or Git-metadata targets.
- Dirty worktrees, stale revisions, symlinked state paths, malformed state, broken event chains, and mismatched candidates fail closed.
- A product-manager decision cannot override a failed gate. Only the user may accept the current failed gate as an explicit recorded risk.

## Project Structure

~~~text
superflow/
├── SKILL.md
├── README.md
├── README_CN.md
├── VERSION
├── LICENSE
├── agents/openai.yaml
├── assets/
│   ├── agent-templates/
│   └── schemas/
├── licenses/
├── references/
├── scripts/
└── tests/
~~~

Detailed contracts:

- [Workflow state machine](references/workflow-state-machine.md)
- [Role contracts](references/role-contracts.md)
- [Tool and enhancement Skill policy](references/tool-and-skill-policy.md)
- [Role-isolated memory](references/role-memory.md)
- [Rules derived from Superpowers](references/superpowers-derived-rules.md)
- [Product management rules](references/product-management-rules.md)
- [Architecture design rules](references/architecture-design-rules.md)
- [UI/UX design rules](references/ui-ux-design-rules.md)
- [Frontend engineering rules](references/frontend-engineering-rules.md)
- [Backend engineering rules](references/backend-engineering-rules.md)
- [Testing strategy](references/testing-strategy.md)
- [Code review criteria](references/code-review-criteria.md)

## Versioning and Releases

Superflow follows Semantic Versioning. The current version is stored in [VERSION](VERSION).

Build a deterministic release archive and SHA-256 sidecar:

~~~bash
python3 scripts/build_release.py
~~~

CI runs the complete test suite and verifies the package on pushes and pull requests. Pushing a tag that exactly matches `v$(cat VERSION)` creates a GitHub release containing the ZIP and checksum.

## Validation

Run the complete test suite:

~~~bash
python3 -m unittest discover -s tests -v
~~~

The test suite covers project-level tool selection and drift rejection, dispatch-bound waiting and result identity, role-isolated memory, built-in professional guides, dual-gate integrity, candidate invalidation, repair limits, state recovery, event tampering, Git path safety, hook blocking, and policy enforcement.

Validate the Skill metadata with the Codex Skill Creator validator:

~~~bash
python3 /path/to/skill-creator/scripts/quick_validate.py .
~~~

## License

Superflow is released under the [MIT License](LICENSE). Third-party attributions and bundled upstream notices are preserved under [licenses/](licenses/).

## Author

- Author: beautiful boy
- Email: [xbyzzz0917@163.com](mailto:xbyzzz0917@163.com)

## Attribution

Superflow incorporates workflow ideas inspired by [obra/superpowers](https://github.com/obra/superpowers), including requirements-first planning, TDD, isolated worktrees, evidence-based verification, and code review discipline. It reimplements these ideas for a role-based Codex approval workflow.

The upstream Superpowers MIT notice is preserved in [licenses/superpowers-MIT.txt](licenses/superpowers-MIT.txt).
