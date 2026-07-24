# Tool and Optional Skill Policy

## Capability state

Track external capabilities independently:

- `configured`: present on disk;
- `available`: discoverable and connected in the current session;
- `projectReady`: indexed, authorized, or attached to the active project artifact.

Never treat configured as available, or an uninitialized project as an unavailable service.

## Project-level tool selection

Before first initialization, ask the user to select:

| Capability | Built-in choices | Custom choice |
|---|---|---|
| Browser | `codex-browser`, `chrome-mcp` | `custom` with exact tool and invocation details |
| UI prototype | `penpot-mcp`, `codex-figma` | `custom` with exact tool and invocation details |

Store the selections in shared Git metadata at `info/superflow.json`; all worktrees use the same values. Include providers and custom details in every relevant brief. A run freezes the current values in `state.json.tool_config`.

Configuration does not make a provider mandatory for every run. Verify browser readiness only for routed real-page work, and UI-provider readiness only for routed prototype work. A backend-only or otherwise unrelated task must not be blocked by an unused provider.

Only an explicit user request authorizes `init_project.py --reconfigure`. New selections apply to new runs. A run whose frozen configuration differs from the project configuration may only transition to `blocked` or `cancelled`; never reinterpret evidence from one provider as evidence from another.

If a selected provider is unavailable:

1. record the actual discovery or connection error;
2. use host plugin or connection management to request installation, connection, or login;
3. preserve the ledger and pause when a restart is required;
4. verify the same provider after recovery;
5. block the task if it remains unavailable. Never switch silently.

## CodeGraph

For code structure, symbols, calls, data flow, or blast radius:

1. discover CodeGraph and perform its read-only connection or status call;
2. set `available=true` only after a successful MCP call;
3. if the project is not indexed, set `projectReady=false` and use the service's real initialization capability;
4. when MCP exposes no initializer, verify the local CodeGraph CLI before the main agent initializes or updates;
5. query CodeGraph again after initialization;
6. fall back to `rg` and precise file reading only after recording an actual connection, initialization, or query failure;
7. rewrite an empty query or use another graph view before declaring failure.

All code-facing roles follow this order.

## UI prototype provider

- Read `ui_prototype.provider` from project configuration.
- For `penpot-mcp`, read Penpot High-Level Overview before editing the real Penpot file.
- For `codex-figma`, use the connected Codex Figma plugin and real Figma file.
- For `custom`, follow details exactly.
- Reuse project components, styles, variables, and page structure.
- Cover required states and verify structure, reuse, and flow links.
- Record `type=ui-prototype` evidence with the exact provider, collector role, task, session, artifact SHA-256, adjudicator, and locatable artifact reference.

Unavailable tools, missing authorization, missing active files, or save failures block UI work. Prose, HTML, or a local static image cannot substitute for an editable prototype.

## Browser provider

For navigation, clicks, input, screenshots, DOM, visual, responsive, or real-page acceptance:

- read `browser.provider` from project configuration;
- use the Codex Browser plugin for `codex-browser`;
- use the selected Chrome MCP for `chrome-mcp`;
- follow details exactly for `custom`;
- read current provider instructions and verify connection, page context, and login state;
- record `type=browser` evidence with the exact provider, page, action, result, collector role, task, session, artifact SHA-256, and adjudicator.

Request user login when authentication blocks work. Do not bypass the selection with standalone Playwright or Selenium, Computer Use, shell-launched browsers, or another provider.

## Built-in professional capability

Superflow does not require same-named external Skills for its core workflow:

- product manager: `product-management-rules.md`;
- architect: `architecture-design-rules.md`;
- UI designer: `ui-ux-design-rules.md`;
- frontend developer: `frontend-engineering-rules.md`;
- backend developer: `backend-engineering-rules.md`;
- developers and tester: `testing-strategy.md`;
- code reviewer: `code-review-criteria.md`.

These guides are workflow contracts. An external Skill cannot override role authority, gates, or evidence requirements.

## Optional enhancement Skills

Use an external Skill only when installed, currently discoverable, and matched to the task:

| Role | Example enhancement | Boundary |
|---|---|---|
| architect | `deep-research` | External research only; project facts require project evidence |
| UI designer | `product-design`, `design-blueprint` | Design reasoning only; selected prototype provider remains the execution surface |
| frontend developer | `frontend-design`, `design-taste-frontend` | Must follow approved prototype and product contract |
| tester | `web-design-guidelines` for UI work | May enhance accessibility review but cannot change business implementation |

Missing optional Skills never block the core workflow.

## Agent TOML limits

Agent configuration is not a complete security boundary:

- `workspace-write` cannot restrict tester writes to tests; inspect the diff.
- Instructions and command-text checks are not a sandbox; compare HEAD, refs, index content and flags, and worktree status before and after.
- MCP visibility does not prove successful use; require typed evidence and structured status.
- Skill visibility does not prove compliance; inspect artifacts and verification.

Write only verified absolute Skill paths to `[[skills.config]]`. Never generate stale machine-specific paths or credentials.
