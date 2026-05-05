from __future__ import annotations

from typing import Any, Generator

from .agent import TravelPlanningPipeline
from .agent.text_slot_utils import detect_user_language


PIPELINE = TravelPlanningPipeline()


def _chunk_text(text: str, chunk_size: int = 140) -> Generator[str, None, None]:
    if not text:
        return
    for i in range(0, len(text), chunk_size):
        yield text[i : i + chunk_size]


def _fallback_response(user_input: str) -> str:
    language = detect_user_language(user_input, default="en")
    if language == "zh":
        return (
            "旅行 Agent 暂时不可用。\n"
            f"你的请求：{user_input}\n"
            "请稍后重试，或补充更明确的目的地和日期。"
        )
    return (
        "Travel agent pipeline is temporarily unavailable.\n"
        f"Your request: {user_input}\n"
        "Please retry shortly, or provide destination and dates explicitly."
    )


def _final_summary_text(response: str) -> str:
    compact = " ".join((response or "").split())
    if len(compact) <= 180:
        return compact
    return f"{compact[:180]}..."


def _extract_sources_from_payload(payload: dict[str, Any] | None) -> list[dict[str, str]]:
    if not isinstance(payload, dict):
        return []
    raw_sources = payload.get("sources", [])
    if not isinstance(raw_sources, list):
        return []

    items: list[dict[str, str]] = []
    for source in raw_sources:
        if not isinstance(source, dict):
            continue
        url = str(source.get("url", "")).strip()
        if not url:
            continue
        items.append(
            {
                "title": str(source.get("title", "Untitled")).strip() or "Untitled",
                "url": url,
                "platform": str(source.get("platform", "")).strip(),
                "snippet": str(source.get("snippet", "")).strip(),
            }
        )
    return items


def _run_pipeline(
    user_input: str,
    user_preferences: dict[str, Any] | None = None,
    conversation_history: list[dict[str, str]] | None = None,
    cancel_event: object | None = None,
) -> dict[str, Any]:
    return PIPELINE.run(
        user_input=user_input,
        user_preferences=user_preferences or {},
        conversation_history=conversation_history or [],
        cancel_event=cancel_event,
    )


def generate_assistant_reply(
    user_input: str,
    user_preferences: dict[str, Any] | None = None,
    conversation_history: list[dict[str, str]] | None = None,
) -> str:
    try:
        result = _run_pipeline(user_input, user_preferences, conversation_history)
        response = str(result.get("response_markdown", "")).strip()
        return response or "No response generated."
    except Exception:
        return _fallback_response(user_input)


def stream_assistant_events(
    user_input: str,
    user_preferences: dict[str, Any] | None = None,
    conversation_history: list[dict[str, str]] | None = None,
    cancel_event: object | None = None,
) -> Generator[dict[str, Any], None, None]:
    language = detect_user_language(user_input, default="en")
    if language == "zh":
        start_text = "正在理解你的真实需求，并规划检索任务..."
        searching_text = "正在执行联网检索与证据整合...\n"
    else:
        start_text = "Understanding your real travel needs and planning search tasks..."
        searching_text = "Running web search and evidence synthesis...\n"

    if cancel_event and getattr(cancel_event, "is_set", lambda: False)():
        return
    yield {
        "type": "message_start",
        "data": {
            "input": user_input,
            "preferences_applied": bool(user_preferences),
            "understanding": start_text,
            "response_language": language,
        },
    }
    yield {"type": "token", "data": {"text": searching_text}}

    try:
        result = _run_pipeline(
            user_input,
            user_preferences,
            conversation_history,
            cancel_event=cancel_event,
        )
        if cancel_event and getattr(cancel_event, "is_set", lambda: False)():
            return
        response_markdown = str(result.get("response_markdown", "")).strip() or "No response generated."
        response_json = result.get("response_json", {})
        tasks = result.get("search_tasks", result.get("search_plan", result.get("tasks", [])))
        requirement_understanding = result.get("requirement_understanding", {})
        timings = result.get("timings", {})
        search_diagnostics = result.get("search_diagnostics", {})
        sources = _extract_sources_from_payload(response_json if isinstance(response_json, dict) else {})
        if language == "zh":
            understanding_fallback = "需求分析完成。"
        else:
            understanding_fallback = "Analysis completed."
        understanding = str(result.get("understanding", "")).strip() or understanding_fallback

        yield {
            "type": "planner",
            "data": {
                "understanding": understanding,
                "search_tasks": tasks,
                "requirement_understanding": requirement_understanding,
                "timings": timings if isinstance(timings, dict) else {},
                "search_diagnostics": search_diagnostics if isinstance(search_diagnostics, dict) else {},
                "response_language": language,
            },
        }
        yield {
            "type": "structured_data",
            "data": {
                "json": response_json,
                "timings": timings if isinstance(timings, dict) else {},
                "search_diagnostics": search_diagnostics if isinstance(search_diagnostics, dict) else {},
                "response_language": language,
            },
        }

        if sources:
            yield {
                "type": "sources",
                "data": {
                    "items": sources,
                    "count": len(sources),
                    "response_language": language,
                },
            }

        for chunk in _chunk_text(response_markdown):
            yield {"type": "token", "data": {"text": chunk}}

        yield {
            "type": "message_end",
            "data": {
                "response": response_markdown,
                "final_summary": _final_summary_text(response_markdown),
                "source_count": len(sources),
                "structured_response": response_json,
                "timings": timings if isinstance(timings, dict) else {},
                "search_diagnostics": search_diagnostics if isinstance(search_diagnostics, dict) else {},
                "response_language": language,
            },
        }
        return
    except Exception as exc:
        fallback = _fallback_response(user_input)
        yield {"type": "error", "data": {"message": str(exc)}}
        for chunk in _chunk_text(fallback):
            yield {"type": "token", "data": {"text": chunk}}
        yield {
            "type": "message_end",
            "data": {
                "response": fallback,
                "final_summary": _final_summary_text(fallback),
                "source_count": 0,
                "structured_response": {},
            },
        }
