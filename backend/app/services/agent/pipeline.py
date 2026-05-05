from __future__ import annotations

import os
from time import perf_counter
from typing import Any

from .context_manager import build_context
from .intent_analyzer import analyze_intent
from .itinerary_generator import generate_itinerary_payload, render_markdown
from .result_synthesizer import interpret_search_results
from .runtime_flags import reset_runtime_preferences, set_runtime_preferences
from .search_executor import SearchExecutor
from .task_planner import plan_tasks, refine_tasks_after_search
from .text_slot_utils import detect_user_language
from .types import SearchTask, ToolExecutionResult


def _safe_int(raw: str | None, default: int) -> int:
    try:
        value = int(str(raw or "").strip())
    except Exception:
        return default
    return value if value >= 0 else default


def _safe_float(raw: str | None, default: float) -> float:
    try:
        value = float(str(raw or "").strip())
    except Exception:
        return default
    return value if value >= 0 else default


def _relevance_threshold() -> float:
    return min(0.95, max(0.3, _safe_float(os.getenv("AGENT_MIN_RELEVANCE_SCORE"), 0.70)))


def _extract_scored_count(rows: list[dict[str, Any]], threshold: float) -> tuple[int, int]:
    scored = 0
    high = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_score = row.get("query_relevance")
        if raw_score in (None, ""):
            continue
        try:
            score = float(raw_score)
        except Exception:
            continue
        scored += 1
        if score >= threshold:
            high += 1
    return high, scored


def _task_quality_from_rows(task: SearchTask, rows: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    high_relevance = 0
    total_candidates = 0
    scored_candidates = 0
    failed_queries = 0
    attempted_queries: list[str] = []
    top_urls: list[str] = []
    seen_urls: set[str] = set()

    for row in rows:
        if not isinstance(row, dict):
            continue
        query = str(row.get("query", "")).strip()
        if query:
            attempted_queries.append(query)
        if not bool(row.get("success")):
            failed_queries += 1
            continue
        output = row.get("output", {})
        if not isinstance(output, dict):
            continue

        items: list[dict[str, Any]] = []
        if task.tool == "web_search":
            raw = output.get("items", [])
            items = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
        elif task.tool == "transport_search":
            raw = output.get("transport_items", [])
            items = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
        elif task.tool in {"poi_search", "place_search"}:
            raw = output.get("pois", [])
            items = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
        elif task.tool in {"hotel_area_search", "hotel_search"}:
            raw = output.get("areas", [])
            items = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
        elif task.tool == "weather_search":
            raw = output.get("forecast", [])
            if isinstance(raw, list):
                total_candidates += len(raw)
                high_relevance += 1 if len(raw) >= 3 else 0
            continue

        total_candidates += len(items)
        high, scored = _extract_scored_count(items, threshold)
        high_relevance += high
        scored_candidates += scored

        for item in items[:5]:
            url = str(item.get("url", "")).strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            top_urls.append(url)
            if len(top_urls) >= 5:
                break

    if task.tool == "weather_search":
        sufficient = high_relevance >= 1
    elif task.priority.lower() == "high":
        sufficient = high_relevance >= 2 or (total_candidates >= 3 and scored_candidates == 0)
    else:
        sufficient = high_relevance >= 1 or (total_candidates >= 2 and scored_candidates == 0)

    reason = "enough_high_relevance_evidence" if sufficient else "insufficient_high_relevance_evidence"
    return {
        "task_id": task.task_id,
        "tool": task.tool,
        "priority": task.priority,
        "attempted_queries": attempted_queries[:12],
        "failed_queries": failed_queries,
        "total_candidates": total_candidates,
        "scored_candidates": scored_candidates,
        "high_relevance_results": high_relevance,
        "threshold": threshold,
        "top_urls": top_urls,
        "is_sufficient": sufficient,
        "insufficient_reason": "" if sufficient else reason,
    }


def _build_round_quality(
    search_plan: list[SearchTask],
    tool_results: list[ToolExecutionResult],
    round_id: int,
) -> dict[str, Any]:
    threshold = _relevance_threshold()
    by_task_id: dict[str, list[dict[str, Any]]] = {}
    for item in tool_results:
        if getattr(item, "round", 1) != round_id:
            continue
        by_task_id.setdefault(item.task_id, []).append(item.to_dict())

    task_rows: list[dict[str, Any]] = []
    insufficient_task_ids: list[str] = []
    for task in search_plan:
        row = _task_quality_from_rows(task, by_task_id.get(task.task_id, []), threshold=threshold)
        task_rows.append(row)
        if not bool(row.get("is_sufficient")):
            insufficient_task_ids.append(task.task_id)

    return {
        "round": round_id,
        "threshold": threshold,
        "task_quality": task_rows,
        "insufficient_task_ids": insufficient_task_ids,
    }


class TravelPlanningPipeline:
    def __init__(self, search_executor: SearchExecutor | None = None) -> None:
        self.search_executor = search_executor or SearchExecutor()

    def run(
        self,
        user_input: str,
        user_preferences: dict[str, Any] | None = None,
        conversation_history: list[dict[str, str]] | None = None,
        cancel_event: object | None = None,
    ) -> dict[str, Any]:
        preferences = dict(user_preferences or {})
        preferences["language"] = detect_user_language(user_input, default="en")
        history = conversation_history or []
        pipeline_start = perf_counter()
        timings: dict[str, float] = {}
        runtime_token = set_runtime_preferences(preferences)
        max_search_rounds = max(1, min(2, _safe_int(os.getenv("AGENT_SEARCH_MAX_ROUNDS"), 1)))
        search_diagnostics: dict[str, Any] = {
            "max_rounds": max_search_rounds,
            "executed_rounds": 0,
            "rounds": [],
            "refinement_triggered": False,
            "final_insufficient_task_ids": [],
        }

        def is_cancelled() -> bool:
            return bool(cancel_event and getattr(cancel_event, "is_set", lambda: False)())

        def cancelled_payload(
            requirement_understanding: dict[str, Any] | None = None,
            search_plan: list[Any] | None = None,
            tool_results: list[Any] | None = None,
            interpretation: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            if "total_sec" not in timings:
                timings["total_sec"] = round(perf_counter() - pipeline_start, 3)
            requirement = requirement_understanding or {}
            plan_rows = [task.to_dict() for task in (search_plan or []) if hasattr(task, "to_dict")]
            result_rows = [row.to_dict() for row in (tool_results or []) if hasattr(row, "to_dict")]
            interpreted = interpretation or {}
            language = detect_user_language(user_input, default="en")
            cancelled_text = "Generation cancelled by user." if language != "zh" else "已停止本次生成。"
            understanding_text = str(requirement.get("interpreted_goal", "")).strip() or cancelled_text
            return {
                "understanding": understanding_text,
                "requirement_understanding": requirement,
                "search_tasks": plan_rows,
                "search_plan": plan_rows,
                "tasks": plan_rows,
                "task_results": result_rows,
                "search_result_interpretation": interpreted.get("task_result_interpretations", []),
                "search_results_by_task": interpreted.get("search_results_by_task", {}),
                "search_diagnostics": search_diagnostics,
                "response_json": {
                    "frontend_json": {
                        "destination": "",
                        "duration_days": 0,
                        "assumptions": [],
                        "missing_information": [],
                        "itinerary": [],
                        "hotel_area_suggestions": [],
                        "budget_estimate": {},
                        "tips": [],
                        "follow_up_questions": [],
                    },
                    "markdown_answer": cancelled_text,
                    "sources": interpreted.get("sources", []),
                    "response_stage": "cancelled",
                    "search_diagnostics": search_diagnostics,
                },
                "response_markdown": cancelled_text,
                "cancelled": True,
                "timings": timings,
            }

        try:
            context = build_context(
                user_input=user_input,
                conversation_history=history,
                user_preferences=preferences,
            )
            recent_history = context.get("recent_history", [])
            if is_cancelled():
                return cancelled_payload()

            # Step 1: LLM requirement understanding.
            t0 = perf_counter()
            requirement_understanding = analyze_intent(
                user_input=user_input,
                conversation_history=recent_history,
                user_preferences=preferences,
                cancel_event=cancel_event,
            )
            timings["step1_requirements_understanding_sec"] = round(perf_counter() - t0, 3)
            if is_cancelled():
                return cancelled_payload(requirement_understanding=requirement_understanding)

            # Step 2: LLM search-task planning.
            t0 = perf_counter()
            search_plan = plan_tasks(
                requirement_understanding=requirement_understanding,
                user_input=user_input,
                conversation_history=recent_history,
                user_preferences=preferences,
                cancel_event=cancel_event,
            )
            timings["step2_search_task_planning_sec"] = round(perf_counter() - t0, 3)
            if is_cancelled():
                return cancelled_payload(
                    requirement_understanding=requirement_understanding,
                    search_plan=search_plan,
                )

            # Step 3: Program executes LLM-generated search plan (with at most one refinement retry).
            t0 = perf_counter()
            tool_results: list[ToolExecutionResult] = self.search_executor.execute(
                search_plan,
                requirement_understanding=requirement_understanding,
                cancel_event=cancel_event,
                round_id=1,
            )
            round1_quality = _build_round_quality(search_plan=search_plan, tool_results=tool_results, round_id=1)
            search_diagnostics["rounds"].append(round1_quality)
            search_diagnostics["executed_rounds"] = 1

            if not is_cancelled() and max_search_rounds >= 2:
                insufficient_ids = round1_quality.get("insufficient_task_ids", [])
                failed_tasks = [
                    task for task in search_plan if task.task_id in insufficient_ids
                ]
                if failed_tasks:
                    search_diagnostics["refinement_triggered"] = True
                    refined_tasks = refine_tasks_after_search(
                        requirement_understanding=requirement_understanding,
                        user_input=user_input,
                        failed_tasks=failed_tasks,
                        diagnostics=round1_quality.get("task_quality", []),
                        conversation_history=recent_history,
                    )
                    if refined_tasks:
                        round2_start = perf_counter()
                        round2_results = self.search_executor.execute(
                            refined_tasks,
                            requirement_understanding=requirement_understanding,
                            cancel_event=cancel_event,
                            round_id=2,
                        )
                        timings["step3_search_execution_round2_sec"] = round(perf_counter() - round2_start, 3)
                        tool_results.extend(round2_results)
                        round2_quality = _build_round_quality(
                            search_plan=refined_tasks,
                            tool_results=tool_results,
                            round_id=2,
                        )
                        search_diagnostics["rounds"].append(round2_quality)
                        search_diagnostics["executed_rounds"] = 2

            if search_diagnostics["rounds"]:
                search_diagnostics["final_insufficient_task_ids"] = list(
                    search_diagnostics["rounds"][-1].get("insufficient_task_ids", [])
                )
            timings["step3_search_execution_sec"] = round(perf_counter() - t0, 3)
            if is_cancelled():
                return cancelled_payload(
                    requirement_understanding=requirement_understanding,
                    search_plan=search_plan,
                    tool_results=tool_results,
                )

            # Step 4: LLM interprets search results by task.
            t0 = perf_counter()
            interpretation = interpret_search_results(
                user_input=user_input,
                requirement_understanding=requirement_understanding,
                search_plan=search_plan,
                tool_results=tool_results,
                conversation_history=recent_history,
                cancel_event=cancel_event,
            )
            timings["step4_search_result_interpretation_sec"] = round(perf_counter() - t0, 3)
            if is_cancelled():
                return cancelled_payload(
                    requirement_understanding=requirement_understanding,
                    search_plan=search_plan,
                    tool_results=tool_results,
                    interpretation=interpretation,
                )

            # Step 5: LLM generates structured answer + user markdown.
            t0 = perf_counter()
            final_json = generate_itinerary_payload(
                user_input=user_input,
                requirement_understanding=requirement_understanding,
                search_plan=search_plan,
                tool_results=tool_results,
                interpretation_payload=interpretation,
                user_preferences=preferences,
                conversation_history=recent_history,
                cancel_event=cancel_event,
            )
            if isinstance(final_json, dict):
                final_json["search_diagnostics"] = search_diagnostics
            timings["step5_final_answer_generation_sec"] = round(perf_counter() - t0, 3)
            timings["total_sec"] = round(perf_counter() - pipeline_start, 3)
            if is_cancelled():
                return cancelled_payload(
                    requirement_understanding=requirement_understanding,
                    search_plan=search_plan,
                    tool_results=tool_results,
                    interpretation=interpretation,
                )
            markdown = render_markdown(final_json)

            understanding = str(requirement_understanding.get("interpreted_goal", "")).strip()
            if not understanding:
                frontend = final_json.get("frontend_json", {})
                if isinstance(frontend, dict):
                    destination = str(frontend.get("destination", "")).strip()
                    days = frontend.get("duration_days")
                    if destination and isinstance(days, int) and days > 0:
                        understanding = f"Travel planning for {destination} ({days} days)."
                    elif destination:
                        understanding = f"Travel planning for {destination}."
            if not understanding:
                understanding = "Travel requirement understanding completed."

            search_plan_rows = [task.to_dict() for task in search_plan]
            tool_result_rows = [row.to_dict() for row in tool_results]

            return {
                "understanding": understanding,
                "requirement_understanding": requirement_understanding,
                "search_tasks": search_plan_rows,
                "search_plan": search_plan_rows,
                "tasks": search_plan_rows,
                "task_results": tool_result_rows,
                "search_result_interpretation": interpretation.get("task_result_interpretations", []),
                "search_results_by_task": interpretation.get("search_results_by_task", {}),
                "search_diagnostics": search_diagnostics,
                "response_json": final_json,
                "response_markdown": markdown,
                "timings": timings,
            }
        finally:
            reset_runtime_preferences(runtime_token)

