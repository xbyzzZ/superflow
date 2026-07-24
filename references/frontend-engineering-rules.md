# Frontend Engineering Rules

Use these rules to implement frozen requirements and approved design as reliable, accessible, and maintainable frontend behavior.

## Align contracts

- Verify acceptance criteria, prototype states, API shapes, errors, authorization, and compatibility before implementation.
- Stop guessing when design, interface, and repository facts conflict. Report the exact conflict and impact.
- Do not invent business definitions, authorization rules, or server defaults in the client.

## Components and state

- Split components by stable responsibility and prefer composition. Generalize only after real reuse or complexity appears.
- Keep state with the nearest common consumer. Separate server, form, navigation, and presentation state.
- Give every effect an explicit trigger, cleanup, and race policy. Stale async results must not overwrite newer state.
- Implement loading, empty, partial, error, retry, disabled, authorization, and optimistic-update failure states from the design.
- Keep component interfaces small and explicit. Avoid boolean combinations that create an implicit state machine.

## Semantics and interaction

- Prefer semantic elements and native behavior. Custom interactions must provide keyboard handling, focus, name, role, and value.
- Target WCAG 2.2 AA for labels, error association, focus order, status announcements, contrast, and touch targets.
- Verify reflow, overflow, zoom, long content, and touch behavior rather than a few fixed viewport snapshots.
- Respect reduced-motion preferences and never let animation block input or comprehension.

## Data, security, and performance

- Treat external data as untrusted. Avoid uncontrolled HTML, URLs, redirects, and sensitive-data exposure.
- Follow project semantics for cancellation, duplicate submission, cache invalidation, pagination, time zones, precision, and null values.
- Measure before optimizing. Inspect critical rendering, unbounded lists, duplicate requests, large dependencies, and unnecessary rerenders.
- Before adding a dependency, verify necessity, maintenance, size, security, and license.

## Verification

- Follow `testing-strategy.md`: RED, GREEN, REFACTOR, with assertions on observable behavior.
- Run narrow tests, then affected type checks, static checks, regression tests, and build.
- For page work, use the selected browser provider and inspect console, network, critical interactions, and responsive behavior.
- Report commands, exit codes, page evidence, and changed files truthfully.

Frontend work is complete only when requirements, design, and interfaces agree; critical states work; accessibility and responsiveness are verified; runtime errors are explained; and relevant tests and build pass.
