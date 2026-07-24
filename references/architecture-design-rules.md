# Architecture Design Rules

Use these rules to convert frozen requirements into implementable, verifiable, and evolvable technical boundaries. Start from repository facts and quality attributes; never substitute pattern names for design reasoning.

## Establish facts

- Read requirements, acceptance criteria, modules, runtime topology, data stores, public contracts, and recorded decisions.
- Use CodeGraph or precise source evidence to verify entry points, dependency direction, call paths, data flow, and blast radius.
- Separate current facts, target state, constraints, assumptions, and open decisions.
- Identify the material quality attributes and how to verify them: security, availability, latency, throughput, consistency, maintainability, and cost.

## Define boundaries

- Give each module, service, data store, and external system an explicit responsibility, owner, input, output, and failure boundary.
- Keep dependencies directed toward stable concepts. Domain behavior must not accidentally depend on transport, framework, or storage details.
- Introduce ports and adapters, domain models, events, CQRS, or microservices only when observed complexity justifies them.
- Define public-interface success, errors, authorization, compatibility, and evolution before scheduling implementation.
- Identify the authoritative data source, transaction boundary, consistency model, idempotency condition, concurrency behavior, and sensitive-data flow.

## Compare alternatives

For each material decision, compare the chosen approach with at least one viable alternative:

1. requirements and quality attributes satisfied;
2. dependencies, complexity, and operating cost introduced;
3. failure impact and recovery path;
4. conditions that invalidate the decision;
5. tests, metrics, or exercises that validate key assumptions.

Do not prebuild speculative abstractions, and do not preserve a known risk merely because it already exists.

## Migration and operation

- For contract, database, or topology changes, define compatibility windows, deployment order, migration, backfill, rollback, and cleanup criteria.
- Prefer incremental, observable, reversible steps. Escalate irreversible operations explicitly.
- Define logs, metrics, traces, or audit evidence for critical paths.
- Include authentication, authorization, input boundaries, secrets, privacy, and supply-chain risk in the design.

## Deliverables

- Use the fewest architecture views that communicate the decision. System-context and container views are usually sufficient; add component detail only when implementation depends on it.
- Label element type, relationship, direction, technology, and scope. Avoid undefined boxes such as "service" or "business logic."
- Record a concise ADR for material decisions: context, constraints, decision, alternatives, tradeoffs, validation, and invalidation conditions.
- Return boundaries, contracts, data flow, risks, task dependencies, migration plan, and acceptance guidance with locatable evidence.

Architecture is complete only when boundaries are implementable, tradeoffs are explicit, failures are recoverable, assumptions are verifiable, and downstream tasks are constrained.
