#!/usr/bin/env python3
"""Select the smallest safe Superflow execution profile."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROFILES = ("lite", "standard", "strict")
PROFILE_POLICIES = {
    "lite": {
        "qualityAgents": 1,
        "independentGates": False,
        "memoryLimit": 3,
        "memoryMaxBytes": 2048,
        "resultDetail": "compact",
        "parentConversation": False,
    },
    "standard": {
        "qualityAgents": 2,
        "independentGates": True,
        "memoryLimit": 5,
        "memoryMaxBytes": 4096,
        "resultDetail": "standard",
        "parentConversation": False,
    },
    "strict": {
        "qualityAgents": 2,
        "independentGates": True,
        "memoryLimit": 10,
        "memoryMaxBytes": 8192,
        "resultDetail": "full",
        "parentConversation": False,
    },
}
STANDARD_SIGNALS = {
    "browser",
    "crossModule",
    "publicInterface",
    "uiPrototype",
    "userVisible",
}
STRICT_SIGNALS = {
    "authorization",
    "dataMigration",
    "destructive",
    "production",
    "release",
    "security",
}


class ProfileError(RuntimeError):
    """The profile request is malformed."""


def select_profile(signals: Any, requested: str = "auto") -> dict[str, Any]:
    if requested not in {"auto", *PROFILES}:
        raise ProfileError("requested must be auto, lite, standard, or strict")
    if not isinstance(signals, dict) or any(
        key not in STANDARD_SIGNALS | STRICT_SIGNALS
        or not isinstance(value, bool)
        for key, value in signals.items()
    ):
        raise ProfileError("signals must contain only supported boolean risk flags")
    strict_reasons = sorted(key for key in STRICT_SIGNALS if signals.get(key))
    standard_reasons = sorted(key for key in STANDARD_SIGNALS if signals.get(key))
    minimum = "strict" if strict_reasons else "standard" if standard_reasons else "lite"
    requested_profile = "lite" if requested == "auto" else requested
    profile = PROFILES[max(PROFILES.index(minimum), PROFILES.index(requested_profile))]
    reasons = strict_reasons or standard_reasons or ["localized-low-risk"]
    return {
        "profile": profile,
        "minimum": minimum,
        "requested": requested,
        "upgraded": profile != requested_profile,
        "reasons": reasons,
        "policy": PROFILE_POLICIES[profile],
    }


def _json_argument(value: str) -> Any:
    path = Path(value)
    text = path.read_text(encoding="utf-8") if path.is_file() else value
    return json.loads(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signals", required=True, type=_json_argument)
    parser.add_argument(
        "--requested",
        choices=("auto", *PROFILES),
        default="auto",
    )
    args = parser.parse_args(argv)
    try:
        result = select_profile(args.signals, args.requested)
    except (ProfileError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps({"ok": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
