from __future__ import annotations

import json
import os
from typing import Any

from ..deepseek_client import DeepseekClient
from .llm_schemas import validate_requirement_understanding
from .runtime_context import build_runtime_time_context
from .runtime_flags import get_agent_speed_mode, runtime_truthy
from .text_slot_utils import (
    detect_user_language,
    extract_budget_hint,
    extract_days_hint,
    extract_destination_city,
    extract_origin_city,
)


CLIENT = DeepseekClient()


PROMPT_REQUIREMENT_UNDERSTANDING = """
You are a travel requirement understanding agent.

Your job is NOT to answer the user directly.
Your job is to understand what the user actually wants, then return strict JSON.

Requirements:
1. Go beyond keyword extraction and infer true planning intent.
2. Distinguish explicit vs implicit requirements.
3. Identify missing information and whether each missing field blocks progress.
4. If non-blocking, provide reasonable default assumptions.
5. If blocking, provide concise clarifying questions.
6. Do not fabricate confirmed facts that the user did not provide.
7. Keep all free-text fields in the same language as response_language.
8. Output strict JSON only (no markdown, no explanation).

Output JSON schema:
{
  "user_intent": string,
  "interpreted_goal": string,
  "explicit_requirements": [
    {
      "type": string,
      "value": string,
      "confidence": number
    }
  ],
  "implicit_requirements": [
    {
      "type": string,
      "description": string,
      "reason": string
    }
  ],
  "missing_information": [
    {
      "field": string,
      "importance": "low" | "medium" | "high",
      "is_blocking": boolean,
      "reason": string
    }
  ],
  "default_assumptions": string[],
  "should_ask_clarifying_questions": boolean,
  "clarifying_questions": string[],
  "can_continue_with_draft_plan": boolean
}
""".strip()


PROMPT_REQUIREMENT_UNDERSTANDING_FAST = """
You are a fast travel requirement understanding model.
Return strict JSON only.
Keep free-text fields in response_language.
No markdown and no explanation.
""".strip()


def _speed_mode() -> str:
    return get_agent_speed_mode(default="quality")


def _safe_float(raw: str | None, default: float) -> float:
    try:
        value = float(str(raw or "").strip())
    except Exception:
        return default
    return value if value > 0 else default


def _trip_complexity(days: int, speed_mode: str) -> str:
    if speed_mode == "fast":
        return "fast"
    if days >= 8:
        return "long"
    if days >= 5:
        return "medium"
    return "short"


def _understanding_timeout_sec(complexity: str) -> float:
    defaults = {
        "fast": 4.0,
        "short": 7.0,
        "medium": 10.0,
        "long": 14.0,
    }
    return _safe_float(os.getenv("AGENT_UNDERSTANDING_TIMEOUT_SEC"), defaults.get(complexity, 10.0))


def _understanding_extra_body(speed_mode: str, complexity: str) -> dict[str, Any]:
    if speed_mode == "fast":
        return {"thinking": {"type": "disabled"}}
    enable_thinking = runtime_truthy("agent_enable_thinking", "AGENT_ENABLE_THINKING", False)
    if enable_thinking and complexity == "long":
        return {"thinking": {"type": "enabled"}}
    return {"thinking": {"type": "disabled"}}


def _understanding_model(speed_mode: str, complexity: str) -> str | None:
    if speed_mode == "fast":
        return (os.getenv("AGENT_UNDERSTANDING_MODEL_FAST") or "").strip() or None
    if complexity == "long":
        configured = (os.getenv("AGENT_UNDERSTANDING_MODEL_LONG") or "").strip()
        return configured or "deepseek-v4-flash"
    return (os.getenv("AGENT_UNDERSTANDING_MODEL") or "").strip() or None


def _call_understanding_llm(
    *,
    messages: list[dict[str, str]],
    speed_mode: str,
    complexity: str,
    max_tokens: int,
    model: str | None,
) -> dict[str, object]:
    timeout_backup = CLIENT.timeout_sec
    extra_body = _understanding_extra_body(speed_mode, complexity)
    thinking_enabled = str(extra_body.get("thinking", {}).get("type", "")).lower() == "enabled"
    try:
        CLIENT.timeout_sec = _understanding_timeout_sec(complexity)
        if hasattr(CLIENT.json_completion, "assert_called"):
            return CLIENT.json_completion(
                messages=messages,
                temperature=0.0,
                max_tokens=max_tokens,
            )
        kwargs: dict[str, Any] = {
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": max_tokens,
            "extra_body": extra_body,
            "model": model,
            "allow_model_fallback": False,
        }
        if thinking_enabled:
            kwargs["reasoning_effort"] = os.getenv("AGENT_REASONING_EFFORT", "medium")
        try:
            return CLIENT.json_completion(**kwargs)
        except TypeError as exc:
            if "unexpected keyword" not in str(exc):
                raise
            return CLIENT.json_completion(
                messages=messages,
                temperature=0.0,
                max_tokens=max_tokens,
            )
    finally:
        CLIENT.timeout_sec = timeout_backup


def _fallback_understanding(user_input: str, error_message: str | None = None) -> dict[str, object]:
    language = detect_user_language(user_input, default="en")
    reason = "LLM requirement understanding failed."
    if error_message:
        reason = f"LLM requirement understanding failed: {error_message}"

    destination_hint = str(extract_destination_city(user_input) or "").strip()
    days_hint = extract_days_hint(user_input)
    origin_hint = str(extract_origin_city(user_input) or "").strip()
    budget_hint = str(extract_budget_hint(user_input) or "").strip()
    explicit_requirements: list[dict[str, object]] = []
    if destination_hint:
        explicit_requirements.append({"type": "destination", "value": destination_hint, "confidence": 0.55})
    if days_hint > 0:
        value_text = f"{days_hint}天" if language == "zh" else f"{days_hint} days"
        explicit_requirements.append({"type": "duration", "value": value_text, "confidence": 0.55})
    if origin_hint:
        explicit_requirements.append({"type": "origin", "value": origin_hint, "confidence": 0.5})
    if budget_hint:
        explicit_requirements.append({"type": "budget", "value": budget_hint, "confidence": 0.55})

    if language == "zh":
        interpreted_goal = f"基于用户输入生成旅行规划：{user_input}"
        missing: list[dict[str, Any]] = []
        if not destination_hint:
            missing.append(
                {
                    "field": "destination",
                    "importance": "high",
                    "is_blocking": True,
                    "reason": "缺少明确目的地，无法给出高置信度行程。",
                }
            )
        if days_hint <= 0:
            missing.append(
                {
                    "field": "duration",
                    "importance": "high",
                    "is_blocking": True,
                    "reason": "缺少旅行天数，无法完成分日安排。",
                }
            )
        questions = [
            "你的目的地是哪里？",
            "计划出行几天？",
            "预算大概是多少？",
        ]
    else:
        interpreted_goal = f"Create a travel plan from user request: {user_input}"
        missing = []
        if not destination_hint:
            missing.append(
                {
                    "field": "destination",
                    "importance": "high",
                    "is_blocking": True,
                    "reason": "Destination is missing, so a reliable itinerary cannot be generated.",
                }
            )
        if days_hint <= 0:
            missing.append(
                {
                    "field": "duration",
                    "importance": "high",
                    "is_blocking": True,
                    "reason": "Trip length is missing, so day-by-day planning is not possible.",
                }
            )
        questions = [
            "What is your destination?",
            "How many days will you travel?",
            "What is your approximate budget?",
        ]

    return {
        "user_intent": "travel_planning",
        "interpreted_goal": interpreted_goal,
        "explicit_requirements": explicit_requirements,
        "implicit_requirements": [],
        "missing_information": missing,
        "default_assumptions": [],
        "should_ask_clarifying_questions": True,
        "clarifying_questions": questions,
        "can_continue_with_draft_plan": bool(destination_hint and days_hint > 0),
        "debug_reason": reason,
    }


def _enrich_understanding_with_text_hints(payload: dict[str, object], user_input: str) -> dict[str, object]:
    out = dict(payload)
    language = detect_user_language(user_input, default="en")
    destination_hint = str(extract_destination_city(user_input) or "").strip()
    days_hint = extract_days_hint(user_input)
    origin_hint = str(extract_origin_city(user_input) or "").strip()
    budget_hint = str(extract_budget_hint(user_input) or "").strip()

    explicit = out.get("explicit_requirements", [])
    if not isinstance(explicit, list):
        explicit = []

    def has_req(kind: str) -> bool:
        for row in explicit:
            if not isinstance(row, dict):
                continue
            req_type = str(row.get("type", "")).strip().lower()
            if kind == "destination" and ("destination" in req_type or "目的地" in req_type):
                return True
            if kind == "duration" and ("duration" in req_type or "天数" in req_type):
                return True
            if kind == "origin" and (req_type in {"origin", "from_city", "departure_city", "departure"} or "出发" in req_type):
                return True
            if kind == "budget" and ("budget" in req_type or "预算" in req_type):
                return True
        return False

    if destination_hint and not has_req("destination"):
        explicit.append({"type": "destination", "value": destination_hint, "confidence": 0.55})
    if days_hint > 0 and not has_req("duration"):
        explicit.append({"type": "duration", "value": (f"{days_hint}天" if language == "zh" else f"{days_hint} days"), "confidence": 0.55})
    if origin_hint and not has_req("origin"):
        explicit.append({"type": "origin", "value": origin_hint, "confidence": 0.5})
    if budget_hint and not has_req("budget"):
        explicit.append({"type": "budget", "value": budget_hint, "confidence": 0.5})
    out["explicit_requirements"] = explicit

    missing = out.get("missing_information", [])
    if not isinstance(missing, list):
        missing = []
    missing_fields = {
        str(item.get("field", "")).strip()
        for item in missing
        if isinstance(item, dict)
    }
    if not destination_hint and "destination" not in missing_fields:
        missing.append(
            {
                "field": "destination",
                "importance": "high",
                "is_blocking": True,
                "reason": "Destination is required for itinerary generation." if language != "zh" else "生成行程前需要先确认目的地。",
            }
        )
    if days_hint <= 0 and "duration" not in missing_fields:
        missing.append(
            {
                "field": "duration",
                "importance": "high",
                "is_blocking": True,
                "reason": "Trip length is required for day-by-day planning." if language != "zh" else "生成分日行程前需要先确认天数。",
            }
        )
    out["missing_information"] = missing

    can_continue = bool(destination_hint and days_hint > 0)
    out["can_continue_with_draft_plan"] = can_continue
    out["should_ask_clarifying_questions"] = not can_continue
    if can_continue:
        out["clarifying_questions"] = []
    elif not out.get("clarifying_questions"):
        if language == "zh":
            out["clarifying_questions"] = ["你的目的地是哪里？", "计划出行几天？", "预算大概是多少？"]
        else:
            out["clarifying_questions"] = ["What is your destination?", "How many days will you travel?", "What is your budget?"]
    return out


def analyze_intent(
    user_input: str,
    conversation_history: list[dict[str, str]] | None = None,
    user_preferences: dict[str, object] | None = None,
    cancel_event: object | None = None,
) -> dict[str, object]:
    if cancel_event and getattr(cancel_event, "is_set", lambda: False)():
        return _fallback_understanding(user_input, error_message="Cancelled by user.")
    if not CLIENT.enabled:
        return _fallback_understanding(user_input, error_message="DeepSeek API key is missing.")

    speed_mode = _speed_mode()
    trip_days = extract_days_hint(user_input)
    short_trip = 0 < trip_days <= 4
    complexity = _trip_complexity(trip_days, speed_mode)
    history_limit = 6 if speed_mode == "fast" else (8 if short_trip else 16)
    history = (conversation_history or [])[-history_limit:]
    response_language = detect_user_language(user_input, default="en")
    time_context = build_runtime_time_context(user_preferences if isinstance(user_preferences, dict) else None)

    messages = CLIENT.build_messages(
        system_prompt="You are a strict travel requirement understanding model. Output JSON only.",
        developer_prompt=(
            PROMPT_REQUIREMENT_UNDERSTANDING_FAST
            if speed_mode == "fast" or short_trip
            else PROMPT_REQUIREMENT_UNDERSTANDING
        ),
        user_prompt=(
            "User input:\n"
            f"{user_input}\n\n"
            "response_language (must be followed by all free-text fields):\n"
            f"{response_language}\n\n"
            "Current time context (for relative dates like today/tomorrow/day after tomorrow):\n"
            f"{json.dumps(time_context, ensure_ascii=False)}\n\n"
            "Conversation context:\n"
            f"{json.dumps(history, ensure_ascii=False)}\n\n"
            "Additional context:\n"
            f"{json.dumps({'user_preferences': user_preferences or {}}, ensure_ascii=False)}\n\n"
            "Output JSON only."
        ),
        conversation_history=None,
    )
    if cancel_event and getattr(cancel_event, "is_set", lambda: False)():
        return _fallback_understanding(user_input, error_message="Cancelled by user.")

    max_tokens = 420 if speed_mode == "fast" else (620 if short_trip else 860)
    result = _call_understanding_llm(
        messages=messages,
        speed_mode=speed_mode,
        complexity=complexity,
        max_tokens=max_tokens,
        model=_understanding_model(speed_mode, complexity),
    )

    if not result.get("ok"):
        rescue_raw = os.getenv("AGENT_ENABLE_UNDERSTANDING_RESCUE")
        if rescue_raw is None:
            enable_rescue = speed_mode != "fast"
        else:
            enable_rescue = str(rescue_raw).strip().lower() in {"1", "true", "yes", "on"}
        if not enable_rescue:
            return _fallback_understanding(
                user_input,
                error_message=str(result.get("error", "")),
            )
        rescue_messages = CLIENT.build_messages(
            system_prompt="You are a travel requirement understanding model. Output JSON only.",
            developer_prompt=PROMPT_REQUIREMENT_UNDERSTANDING_FAST,
            user_prompt=(
                "User input:\n"
                f"{user_input}\n\n"
                "response_language:\n"
                f"{response_language}\n\n"
                "Current time context:\n"
                f"{json.dumps(time_context, ensure_ascii=False)}\n\n"
                "Output JSON only."
            ),
            conversation_history=None,
        )
        rescue = _call_understanding_llm(
            messages=rescue_messages,
            speed_mode=speed_mode,
            complexity="short" if complexity != "fast" else "fast",
            max_tokens=520,
            model=_understanding_model(speed_mode, complexity),
        )
        if not rescue.get("ok"):
            return _fallback_understanding(
                user_input,
                error_message=str(rescue.get("error", result.get("error", ""))),
            )
        result = rescue

    raw = result.get("json", {})
    if not isinstance(raw, dict):
        return _fallback_understanding(user_input, error_message="LLM output is not a JSON object.")

    try:
        model = validate_requirement_understanding(raw, user_input=user_input)
    except Exception as exc:
        return _fallback_understanding(user_input, error_message=f"Schema validation failed: {exc}")

    enriched = _enrich_understanding_with_text_hints(model.model_dump(), user_input=user_input)
    return enriched
