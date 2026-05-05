from __future__ import annotations

from typing import Any


def _trim_history(history: list[dict[str, str]], max_turns: int = 16) -> list[dict[str, str]]:
    if not history:
        return []

    trimmed: list[dict[str, str]] = []
    for message in history[-max_turns:]:
        role = str(message.get("role", "")).strip()
        content = str(message.get("content", "")).strip()
        if role not in {"user", "assistant"}:
            continue
        if not content:
            continue
        trimmed.append({"role": role, "content": content})
    return trimmed


def _history_to_text(history: list[dict[str, str]]) -> str:
    if not history:
        return ""
    lines: list[str] = []
    for idx, item in enumerate(history, start=1):
        role = item.get("role", "assistant")
        content = item.get("content", "")
        lines.append(f"{idx}. {role}: {content}")
    return "\n".join(lines)


def build_context(
    user_input: str,
    conversation_history: list[dict[str, str]] | None = None,
    user_preferences: dict[str, Any] | None = None,
) -> dict[str, Any]:
    recent_history = _trim_history(conversation_history or [])

    return {
        "user_input": user_input,
        "recent_history": recent_history,
        "history_text": _history_to_text(recent_history),
        "memory_slots": {},
        "user_preferences": user_preferences or {},
    }
