# Backend Engineering Rules

Use these rules to implement stable, compatible, secure, and observable backend behavior. Center business contracts and data invariants rather than framework patterns.

## Contract first

- Define input, output, errors, authorization, idempotency, timeout, and compatibility before implementation.
- Validate type, range, format, identity, and resource ownership at trust boundaries.
- Follow HTTP method, status, cache, and conditional-request semantics. Prefer an RFC 9457-compatible shape when a common machine-readable error format is required.
- Define pagination, sorting, filtering, limits, and stable cursors for collections.
- Do not permanently collapse transport DTOs, persistence models, and domain objects into one contract for convenience.

## Domain and dependencies

- Keep business invariants in a testable domain or application boundary. Controllers translate protocol, invoke behavior, and map responses.
- Depend on stable concepts. Add interface layers only when replacement, isolated testing, or boundary evolution justifies them.
- Do not introduce Clean Architecture, DDD, CQRS, event sourcing, or microservices by default.
- For external calls, define timeout, retry eligibility, idempotency, and partial-failure behavior. Never retry unsafe operations automatically.

## Data and concurrency

- Define the authoritative source, invariants, transaction boundary, isolation needs, and conflict policy.
- For cross-resource writes and messages, address atomicity, compensation, deduplication, and duplicate delivery.
- Use compatible incremental migrations with deployment order, backfill, validation, rollback, and cleanup criteria.
- Bound queries and batches. Inspect indexes, N+1 behavior, repeated scans, lock scope, memory growth, and resource release.

## Security and observability

- Enforce object-, property-, and function-level authorization, not merely authentication.
- Check relevant OWASP API Security risks, especially object authorization, resource consumption, and sensitive business flows.
- Keep secrets out of code, logs, and errors. Apply least privilege and retain necessary audit evidence.
- Provide structured logs, correlation identifiers, latency, error-rate, and business-outcome signals without excessive private-data collection.
- Preserve root cause, distinguish retryable from terminal failures, and return stable caller-facing semantics.

## Verification

- Follow `testing-strategy.md`: establish failing evidence, implement the smallest complete behavior, then refactor.
- Use unit tests for invariants, integration tests for storage and transactions, and contract tests for public compatibility.
- Add explicit failure-path verification for migration, authorization, concurrency, idempotency, and recovery.
- Run relevant tests, type or static checks, and build; report commands, exit codes, and limitations accurately.

Backend work is complete only when contracts are stable, authorization and data boundaries are explicit, failures are diagnosable, migration is executable, key risks have tests, and resources are bounded.
