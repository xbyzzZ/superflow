# Testing Strategy

Use the smallest sufficient test combination that proves candidate behavior. Every gate is bound to the current candidate SHA.

## Choose tests by risk

- Read requirements, acceptance criteria, architecture, design, diff, and existing tests.
- Use unit tests for pure logic, integration tests for collaborating modules, contract tests for public interfaces, and end-to-end tests for critical journeys.
- Verify high-risk data, authorization, concurrency, migration, compatibility, and recovery paths explicitly.
- Page work requires automated tests plus real-page operation through the project-selected browser provider.

## Test structure

- Name the condition and behavior, not the private implementation.
- Let each test prove one clear behavior; shared setup must not hide critical preconditions.
- Cover normal, empty, boundary, failure, authorization, and regression paths.
- Assert observable contracts rather than private methods, internal call counts, or incidental structure.
- Use coverage only to identify untested paths; it cannot prove quality alone.

## Test doubles

- Mock only uncontrollable boundaries such as networks, clocks, randomness, external services, or expensive storage.
- Never mock the core behavior under test.
- Keep doubles compatible with real interfaces, errors, and key data shapes.
- Isolate and clean state; do not depend on test order.
- When a contract changes, verify both the real implementation and doubles.

## Async and flaky tests

- Wait for observable conditions or events; do not guess with fixed sleeps.
- Control clocks, randomness, scheduling, and external state.
- Do not mask instability with larger timeouts, automatic retries, serialized execution, or ignored failures.
- Preserve the original error and investigate shared state, missing waits, time, ordering, leaks, services, and environment.
- Reproduce and verify the root cause before changing test or implementation.

## RED, GREEN, REFACTOR

1. RED: add or adjust the smallest test and prove it fails because the target behavior is absent.
2. GREEN: implement the smallest complete behavior and prove the target test passes.
3. REFACTOR: simplify only after green, then rerun relevant regression.

If a test cannot be written first, record the concrete reason and repeatable equivalent verification. Never add a proof test that was never observed failing.

## Execution order

1. narrow target test;
2. affected-module regression;
3. required type checks, static checks, build, integration, or end-to-end tests;
4. real-page verification when applicable;
5. command, exit-code, failure-count, skip, and environment review.

PASS requires the correct candidate SHA, at least one successful real test command, evidence for every acceptance criterion, no failed, partial, skipped, not-run, or nonzero recorded command, no unexplained flaky test, real-page evidence when required, and no unauthorized test changes. A later successful command never erases an earlier failed command from the same gate attempt.

The tester owns candidate runtime preparation and every candidate-bound verification command. It must not rely on a main-agent preflight, and the main agent must not duplicate these checks before dispatch. Environment or permission failures remain explicit gate evidence and never authorize the main agent to take over testing.
