# UI/UX Design Rules

Use these rules to convert product requirements into editable, implementable, and verifiable user experiences. Start from user tasks and the existing design system, not a random style catalog.

## Start from the user task

- Identify the target user, context, entry point, primary task, completion signal, and recovery path.
- Define information hierarchy, navigation, and critical flows before visual styling.
- Preserve established product language and interaction models. Explain the benefit and migration cost when changing them.
- Replace subjective goals such as "modern" or "premium" with observable design outcomes.

## Cover the complete state model

For every critical page or component, inspect:

- default, hover, focus, pressed, selected, and disabled;
- initial loading, incremental loading, empty, partial data, error, retry, and success feedback;
- unauthorized, read-only, stale, conflict, and destructive confirmation;
- long content, extremes, localization, narrow viewport, wide viewport, and zoom.

Each state must define its trigger, available user action, system feedback, and recovery.

## Build a visual system

- Reuse existing components, tokens, typography, color, spacing, radius, icons, and content conventions.
- Establish reading order through hierarchy, alignment, spacing, density, and contrast. Decoration must not obscure tasks or state.
- Never use color as the only signal.
- Add a visual pattern only when the existing system cannot express the requirement, then define its name, variants, states, and usage boundary.
- Use motion only to explain state change, spatial relationship, or feedback, with a reduced-motion path.

## Accessibility and responsiveness

- Target WCAG 2.2 AA for contrast, keyboard path, visible focus, target size, labels, error feedback, and status messages.
- Design visible labels, semantic structure, and logical focus order instead of delegating accessibility entirely to implementation.
- Define priority, wrapping, collapse, reflow, scrolling, and touch behavior. Responsive design is not uniform scaling.
- Verify the narrowest and widest required layouts plus continuous behavior between breakpoints.

## Prototype delivery

- Use the project-selected UI prototype provider and reuse assets from the real project file.
- Connect critical flows and cover the state model, not only the ideal path.
- Annotate behavior, data conditions, validation, errors, authorization, responsiveness, and rules that cannot be inferred from pixels.
- Provide evidence locating the real file, page, frame, component, or flow and record the provider.

Design is complete only when the core task is achievable, failures are recoverable, states are complete, the design system remains coherent, accessibility and responsiveness are verifiable, and implementation guidance is unambiguous.
