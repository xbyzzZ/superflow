#!/usr/bin/env python3
"""Render user-facing Superflow run documents from structured state."""

from __future__ import annotations

from typing import Any


HEADINGS = {
    "en": {
        "requirements": "Requirements",
        "background": "Background",
        "objective": "Objective",
        "users": "Users",
        "scenarios": "Scenarios",
        "inScope": "In Scope",
        "outOfScope": "Out of Scope",
        "acceptanceCriteria": "Acceptance Criteria",
        "constraints": "Constraints",
        "decisions": "Decisions",
        "openQuestions": "Open Questions",
        "none": "None",
        "run": "Run",
        "process": "Delivery Process Log",
        "profile": "Execution profile",
        "status": "Current status",
        "updated": "Last updated",
        "requirementsStatus": "Requirements",
        "frozen": "Frozen",
        "pending": "Pending",
        "plan": "Plan",
        "task": "Task",
        "role": "Role",
        "candidate": "Candidate",
        "gates": "Quality Gates",
        "gate": "Gate",
        "result": "Result",
        "valid": "Valid",
        "risks": "Accepted Risks",
        "acceptedBy": "Accepted by",
        "reason": "Reason",
        "timeline": "Milestones",
        "yes": "yes",
        "no": "no",
        "generatedNotice": "Generated from the audited Superflow ledger. Do not edit this derived view.",
    },
    "zh-CN": {
        "requirements": "\u9700\u6c42\u6587\u6863",
        "background": "\u80cc\u666f",
        "objective": "\u76ee\u6807",
        "users": "\u76ee\u6807\u7528\u6237",
        "scenarios": "\u4f7f\u7528\u573a\u666f",
        "inScope": "\u8303\u56f4\u5185",
        "outOfScope": "\u8303\u56f4\u5916",
        "acceptanceCriteria": "\u9a8c\u6536\u6807\u51c6",
        "constraints": "\u7ea6\u675f",
        "decisions": "\u5df2\u786e\u8ba4\u51b3\u7b56",
        "openQuestions": "\u5f85\u786e\u8ba4\u95ee\u9898",
        "none": "\u65e0",
        "run": "\u8fd0\u884c",
        "process": "\u5904\u7406\u8fc7\u7a0b\u8bb0\u5f55",
        "profile": "\u6267\u884c\u6863\u4f4d",
        "status": "\u5f53\u524d\u72b6\u6001",
        "updated": "\u6700\u540e\u66f4\u65b0",
        "requirementsStatus": "\u9700\u6c42\u72b6\u6001",
        "frozen": "\u5df2\u51bb\u7ed3",
        "pending": "\u5f85\u51bb\u7ed3",
        "plan": "\u4efb\u52a1\u8ba1\u5212",
        "task": "\u4efb\u52a1",
        "role": "\u89d2\u8272",
        "candidate": "\u5019\u9009\u63d0\u4ea4",
        "gates": "\u8d28\u91cf\u95e8\u7981",
        "gate": "\u95e8\u7981",
        "result": "\u7ed3\u679c",
        "valid": "\u6709\u6548",
        "risks": "\u5df2\u63a5\u53d7\u98ce\u9669",
        "acceptedBy": "\u63a5\u53d7\u4eba",
        "reason": "\u539f\u56e0",
        "timeline": "\u5173\u952e\u8fc7\u7a0b",
        "yes": "\u662f",
        "no": "\u5426",
        "generatedNotice": "\u672c\u6587\u6863\u7531 Superflow \u5ba1\u8ba1\u8d26\u672c\u81ea\u52a8\u751f\u6210\uff0c\u8bf7\u52ff\u76f4\u63a5\u7f16\u8f91\u3002",
    },
}

EVENT_LABELS = {
    "en": {
        "init": "Run initialized",
        "record_requirements": "Requirements frozen",
        "register_worktree": "Worktree registered",
        "record_brief": "Task brief frozen",
        "record_dispatch": "Specialist dispatched",
        "record_attempt": "Specialist result recorded",
        "transition": "Workflow state changed",
        "set_task": "Task status changed",
        "set_candidate": "Candidate commit frozen",
        "record_gate": "Quality gate recorded",
        "record_risk": "Gate risk accepted",
        "finish": "Run finished",
    },
    "zh-CN": {
        "init": "\u8fd0\u884c\u5df2\u521d\u59cb\u5316",
        "record_requirements": "\u9700\u6c42\u5df2\u51bb\u7ed3",
        "register_worktree": "\u5de5\u4f5c\u6811\u5df2\u767b\u8bb0",
        "record_brief": "\u4efb\u52a1\u7b80\u62a5\u5df2\u51bb\u7ed3",
        "record_dispatch": "\u4e13\u4e1a\u89d2\u8272\u5df2\u6d3e\u53d1",
        "record_attempt": "\u4e13\u4e1a\u89d2\u8272\u7ed3\u679c\u5df2\u8bb0\u5f55",
        "transition": "\u6d41\u7a0b\u72b6\u6001\u5df2\u66f4\u65b0",
        "set_task": "\u4efb\u52a1\u72b6\u6001\u5df2\u66f4\u65b0",
        "set_candidate": "\u5019\u9009\u63d0\u4ea4\u5df2\u51bb\u7ed3",
        "record_gate": "\u8d28\u91cf\u95e8\u7981\u5df2\u8bb0\u5f55",
        "record_risk": "\u95e8\u7981\u98ce\u9669\u5df2\u63a5\u53d7",
        "finish": "\u8fd0\u884c\u5df2\u5b8c\u6210",
    },
}


def _inline(value: Any) -> str:
    return str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|").strip()


def _list_section(title: str, values: list[str], none_label: str) -> list[str]:
    lines = [f"## {title}", ""]
    lines.extend(f"- {_inline(value)}" for value in values)
    if not values:
        lines.append(f"- {none_label}")
    lines.append("")
    return lines


def render_requirements(
    requirements: dict[str, Any],
    run_id: str,
    profile: str,
    recorded_at: str,
    language: str,
) -> str:
    labels = HEADINGS[language]
    lines = [
        f"# {labels['requirements']}: {_inline(requirements['title'])}",
        "",
        f"- {labels['run']}: `{run_id}`",
        f"- {labels['profile']}: `{profile}`",
        f"- {labels['frozen']}: `{recorded_at}`",
        "",
        f"> {labels['generatedNotice']}",
        "",
        f"## {labels['background']}",
        "",
        requirements["background"].strip(),
        "",
        f"## {labels['objective']}",
        "",
        requirements["objective"].strip(),
        "",
    ]
    for key in (
        "users",
        "scenarios",
        "inScope",
        "outOfScope",
        "acceptanceCriteria",
        "constraints",
        "decisions",
        "openQuestions",
    ):
        lines.extend(_list_section(labels[key], requirements[key], labels["none"]))
    return "\n".join(lines).rstrip() + "\n"


def _event_detail(record: dict[str, Any]) -> str:
    event = record["event"]
    detail = record.get("detail", {})
    fields_by_event = {
        "transition": ("from", "to", "task_id"),
        "record_requirements": ("title",),
        "record_brief": ("task_id", "role"),
        "record_dispatch": ("task_id", "role", "dispatch_id"),
        "record_attempt": ("task_id", "role", "kind", "outcome"),
        "set_task": ("task_id", "status"),
        "set_candidate": ("to",),
        "record_gate": ("gate", "result", "sha"),
        "record_risk": ("gate", "accepted_by"),
    }
    pairs = [
        f"{key}={_inline(detail[key])}"
        for key in fields_by_event.get(event, ())
        if detail.get(key) is not None
    ]
    return ", ".join(pairs)


def render_process_log(
    state: dict[str, Any],
    events: list[dict[str, Any]],
    language: str,
) -> str:
    labels = HEADINGS[language]
    event_labels = EVENT_LABELS[language]
    requirements_status = labels["frozen"] if state.get("requirements") else labels["pending"]
    lines = [
        f"# {labels['process']}",
        "",
        f"- {labels['run']}: `{state['run_id']}`",
        f"- {labels['profile']}: `{state.get('profile', 'strict')}`",
        f"- {labels['status']}: `{state['status']}`",
        f"- {labels['requirementsStatus']}: {requirements_status}",
        f"- {labels['updated']}: `{state['updated_at']}`",
        "",
        f"> {labels['generatedNotice']}",
        "",
        f"## {labels['plan']}",
        "",
        f"| {labels['task']} | {labels['role']} | {labels['status']} |",
        "|---|---|---|",
    ]
    for task in state["plan"]:
        lines.append(
            f"| {_inline(task['id'])}: {_inline(task['title'])} | "
            f"{_inline(task.get('role', '-'))} | {_inline(task['status'])} |"
        )
    lines.extend(
        [
            "",
            f"## {labels['candidate']}",
            "",
            f"`{state.get('candidate_sha') or labels['none']}`",
            "",
            f"## {labels['gates']}",
            "",
            f"| {labels['gate']} | {labels['result']} | {labels['valid']} |",
            "|---|---|---|",
        ]
    )
    for gate in ("test", "review"):
        record = state.get("gates", {}).get(gate)
        lines.append(
            f"| {gate} | {_inline(record.get('result') if record else labels['pending'])} | "
            f"{labels['yes'] if record and record.get('valid') else labels['no']} |"
        )
    lines.extend(["", f"## {labels['risks']}", ""])
    risks = state.get("risks", [])
    if risks:
        for risk in risks:
            lines.append(
                f"- `{_inline(risk['gate'])}` \u2014 {labels['acceptedBy']}: "
                f"{_inline(risk['accepted_by'])}; {labels['reason']}: {_inline(risk['reason'])}"
            )
    else:
        lines.append(f"- {labels['none']}")
    lines.extend(["", f"## {labels['timeline']}", ""])
    for record in events:
        label = event_labels.get(record["event"])
        if label is None:
            continue
        detail = _event_detail(record)
        suffix = f" \u2014 {detail}" if detail else ""
        lines.append(f"- `{record['at']}` {label}{suffix}")
    return "\n".join(lines).rstrip() + "\n"
