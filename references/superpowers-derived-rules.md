# Superpowers-Derived Rules

Superflow incorporates proven engineering discipline from Superpowers but reimplements it for a seven-role approval workflow without copying its Skill tree or requiring it at runtime.

## Clarify before design

- Understand the existing project, target user, constraints, and success criteria before code changes.
- Resolve one material ambiguity at a time, preferably with comparable options.
- Present recommendation, alternatives, and rationale for real tradeoffs.
- Do not expand product scope without user confirmation.
- Architecture and UI are conditional branches, not mandatory for every task.

## Plan quality

- Split work into independently verifiable tasks with role, files, dependencies, and acceptance.
- Describe observable results; avoid vague verbs such as "improve" or "adapt."
- Make each brief self-contained.
- Only the main agent updates the plan; subagents propose changes.

## Test-driven development

For testable behavior:

1. RED: write the smallest failing test and prove the failure is the missing target behavior.
2. GREEN: implement the smallest complete behavior.
3. REFACTOR: simplify only after green while preserving behavior.

When a test cannot come first, record the reason and repeatable equivalent verification. Never add a proof test that was never seen failing.

## Systematic debugging

Before a fix:

- reproduce and record conditions;
- read complete errors, logs, and call paths;
- inspect recent changes and upstream and downstream data;
- compare working and failing paths;
- form a falsifiable root-cause hypothesis and test it minimally.

Stop stacking patches when attempts add no explanatory power. Revisit the hypothesis. Never mask failures with meaningless catches, silent behavior, defaults, or hardcoded bypasses.

## Isolated workspaces

- Execute code work in an integration worktree, with task worktrees only for truly independent work.
- Verify target directory, branch, and base worktree before creation.
- Do not auto-stash, overwrite user changes, or run destructive reset.
- Subagents never operate Git; only the main agent commits or cherry-picks.
- Reverify integrated results instead of reusing branch-local conclusions.

## Dispatch

- Give each subagent one bounded professional task.
- Parallelize only when interfaces are frozen and write sets do not overlap.
- Include context, authorized paths, acceptance, and result Schema.
- Inspect actual diff, commands, and tool evidence.
- Use a fresh agent for the third repair round to avoid repeating the same reasoning.

## Two-stage review

Review in order:

1. requirement, plan, and acceptance compliance;
2. correctness, security, errors, tests, performance, and maintainability;
3. naming, structure, duplication, and consistency.

A specification failure cannot pass because code quality is otherwise good. Findings require location, impact, evidence, and an actionable root-cause direction.

## Handle feedback

- Verify a finding against the current code before acting.
- Clarify interconnected or ambiguous feedback through the main agent.
- Reject technically incorrect feedback with code, tests, or authoritative documentation.
- Ask the user when feedback conflicts with confirmed scope.

## Directed re-review

- Freeze candidate SHA before review.
- Reviewer and tester inspect the same SHA.
- After repair, inspect the fix diff, retest the original trigger, and run regression.
- A candidate change invalidates both old gates.

## Verify before completion

Before any completion claim, the main agent must identify the proving command or action, run it on the current candidate, read complete output and exit status, verify it supports the claim, and report evidence and limitations.

Past passes, subagent summaries, static reading, and expected success are not completion evidence.

## Finish branches

After same-SHA dual PASS, offer local merge, push or PR, or preservation options. Remote writes, final merge, branch deletion, and worktree deletion require explicit user authorization. Keep PR worktrees until feedback is resolved by default.
