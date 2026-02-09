"""Core policy helpers."""

from __future__ import annotations


def resolve_house_state(
    *,
    anyone_home: bool,
    vacation_mode: bool,
    guest_mode: bool,
    sleep_window: bool,
    relax_mode: bool,
    work_window: bool,
) -> tuple[str, str]:
    """Resolve canonical house_state with v1 priority order."""
    if vacation_mode:
        return "vacation", "vacation_mode"
    if guest_mode:
        return "guest", "guest_mode"
    if not anyone_home:
        return "away", "no_presence"
    if sleep_window:
        return "sleeping", "sleep_window"
    if relax_mode:
        return "relax", "relax_mode"
    if work_window:
        return "working", "work_window"
    return "home", "default"
