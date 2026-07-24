# Code Review Criteria

Code review is a read-only quality gate bound to a candidate SHA. Its purpose is to find defects that affect requirements, correctness, security, and maintainability.

## Prepare

- Verify candidate SHA, scope, requirements, plan, acceptance criteria, and actual diff.
- Read affected call paths, data flow, error paths, and downstream impact.
- Inspect added, removed, and unchanged-but-affected code.
- Use `unknown` or a concern when facts are missing.

## Fixed review order

1. `spec`: requirements, scope, interface, and acceptance compliance.
2. `correctness`: logic, state, data, security, errors, concurrency, performance, compatibility, and tests.
3. `consistency`: naming, structure, abstraction, duplication, dependency direction, and project conventions.

A specification failure cannot pass because the implementation is clean.

## Defect checks

- input validation, authorization, sensitive data, injection, paths, and supply chain;
- nulls, bounds, precision, time zones, ordering, pagination, and state transitions;
- transactions, idempotency, concurrency, retry, resource release, and partial failure;
- API, database, event, configuration, and test-double contract consistency;
- repeated scans, blocking calls, unbounded collections, and expensive hot paths;
- silent failure, broad catches, masking defaults, and hardcoded bypasses;
- missing failure tests, regression tests, or real-page verification.

## Finding quality

Every finding must include severity, precise file and minimal line location, reproducible trigger, actual impact, affected users or systems, evidence, and root-cause repair direction.

- Critical: material security, data, or unrecoverable failure that blocks delivery.
- High: common or critical-path functional, authorization, or severe regression.
- Medium: correctness, performance, or maintenance risk under concrete conditions.
- Low: limited impact with a clear behavior or maintenance cost.

Do not report style preferences, unreachable theory, or unlocatable general advice.

## Verdict

- Verify each finding against the current code.
- Do not modify code or present advice as a verified fix.
- PASS requires explicit `spec`, `correctness`, and `consistency` verdicts plus locatable success evidence.
- Blocking findings produce FAIL; missing evidence produces `unknown`.
- After a fix, retest the original trigger and inspect new regressions. A candidate change invalidates the old verdict.
