"""Notification routing: which channels get an event, and thread placement.

Pure functions over plain dicts so they unit-test without a database.
- resolve_targets: guild subscriptions that want this event (module + category).
- resolve_thread: ordered route rules, first enabled match wins (thread placement).
"""

from __future__ import annotations

from typing import Any


def event_module(event: Any) -> str:
    et = event.event_type
    return et.split(".", 1)[0] if "." in et else et


def event_category(event: Any) -> str | None:
    return event.payload.get("category") or event.payload.get("source")


def _rule_matches(rule: dict, payload: dict) -> bool:
    for k, v in (rule or {}).items():
        pv = payload.get(k)
        if isinstance(v, (list, tuple, set)):
            if pv not in v:
                return False
        elif pv != v:
            return False
    return True


def resolve_targets(subscriptions: list[dict], event: Any) -> list[dict]:
    """Enabled subscriptions whose module matches and whose optional category
    filter (if set) matches the event. A sub with category=None matches any."""
    mod = event_module(event)
    cat = event_category(event)
    out: list[dict] = []
    for s in subscriptions:
        if not s.get("enabled", True):
            continue
        if s.get("module") != mod:
            continue
        sub_cat = s.get("category")
        if sub_cat and cat and sub_cat != cat:
            continue
        out.append(s)
    return out


def resolve_thread(routes: list[dict], event: Any) -> int | None:
    """First enabled route for this event_type whose rule matches, by priority."""
    et = event.event_type
    candidates = [r for r in routes if r.get("enabled", True) and r.get("event_type") == et]
    for r in sorted(candidates, key=lambda x: x.get("priority", 100)):
        if _rule_matches(r.get("rule") or {}, event.payload):
            return r.get("thread_id")
    return None
