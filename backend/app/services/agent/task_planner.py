from __future__ import annotations

import json
import os
import re
from typing import Any

from ..deepseek_client import DeepseekClient
from .llm_schemas import validate_search_plan
from .runtime_context import build_runtime_time_context
from .runtime_flags import get_agent_speed_mode, runtime_truthy
from .text_slot_utils import detect_user_language, extract_days_hint, extract_destination_city, extract_origin_city
from .types import SearchTask


CLIENT = DeepseekClient()

ALLOWED_TOOLS = {
    "web_search",
    "poi_search",
    "weather_search",
    "transport_search",
    "hotel_area_search",
}
PRIORITY_LEVELS = {"high", "medium", "low"}
FRESHNESS_LEVELS = {"high", "medium", "low"}


PROMPT_SEARCH_PLANNER = """
You are a travel search-planning agent.

Input: requirement-understanding JSON.
Goal: decide what information must be searched before final planning.

Rules:
1. Do not answer the user directly.
2. Do not generate the final itinerary.
3. Avoid fixed templates; plan tasks dynamically.
4. Every task must correspond to a clear information need.
5. Queries should be concrete and search-engine friendly.
6. For real-time info (opening hours, weather, transport, ticket price), freshness must be high.
7. Output strict JSON only.

Output JSON schema:
{
  "search_tasks": [
    {
      "task_id": string,
      "information_need": string,
      "why_needed": string,
      "tool": "web_search" | "poi_search" | "weather_search" | "transport_search" | "hotel_area_search",
      "queries": string[],
      "freshness": "low" | "medium" | "high",
      "priority": "low" | "medium" | "high",
      "expected_evidence": string[]
    }
  ]
}
""".strip()

PROMPT_QUERY_REWRITER = """
You are a travel-search query rewrite agent.

Input: search_tasks. For each task, generate 2-4 precise, retrievable queries.

Rules:
1. Do not copy the user sentence directly.
2. Include destination, date/day-range, and task semantics whenever possible.
3. For freshness=high tasks, emphasize latest/realtime/official constraints.
4. Deduplicate semantically similar queries.
5. Output JSON only.

Output JSON schema:
{
  "task_queries": [
    {
      "task_id": string,
      "queries": string[]
    }
  ]
}
""".strip()

PROMPT_QUERY_REFINER_AFTER_RESULTS = """
You are a travel-search query refinement agent.

Input: underperforming tasks and round-1 diagnostics.
Goal: rewrite stronger round-2 queries with better recall and precision.

Rules:
1. Do not reuse failed queries verbatim.
2. Generate 2-4 complementary queries per task.
3. For time-sensitive tasks (weather/transport/opening hours/ticket price), add official/realtime/latest constraints.
4. International destinations may use bilingual queries when helpful.
5. Output JSON only.

Output JSON schema:
{
  "task_queries": [
    {
      "task_id": string,
      "queries": string[],
      "why_rewritten": string
    }
  ]
}
""".strip()


def _speed_mode() -> str:
    return get_agent_speed_mode(default="quality")


def _safe_int(raw: str | None, default: int) -> int:
    try:
        value = int(str(raw or "").strip())
    except Exception:
        return default
    return value if value > 0 else default


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


def _planner_timeout_sec(complexity: str) -> float:
    defaults = {
        "fast": 4.0,
        "short": 8.0,
        "medium": 11.0,
        "long": 14.0,
    }
    return _safe_float(os.getenv("AGENT_PLANNER_TIMEOUT_SEC"), defaults.get(complexity, 10.0))


def _rewrite_timeout_sec(complexity: str) -> float:
    defaults = {
        "fast": 3.0,
        "short": 6.0,
        "medium": 8.0,
        "long": 11.0,
    }
    return _safe_float(os.getenv("AGENT_QUERY_REWRITE_TIMEOUT_SEC"), defaults.get(complexity, 8.0))


def _planner_extra_body(speed_mode: str, complexity: str) -> dict[str, Any]:
    if speed_mode == "fast":
        return {"thinking": {"type": "disabled"}}
    enable_thinking = runtime_truthy("agent_enable_thinking", "AGENT_ENABLE_THINKING", False)
    if enable_thinking and complexity == "long":
        return {"thinking": {"type": "enabled"}}
    return {"thinking": {"type": "disabled"}}


def _planner_model(speed_mode: str, complexity: str) -> str | None:
    if speed_mode == "fast":
        return (os.getenv("AGENT_PLANNER_MODEL_FAST") or "").strip() or None
    if complexity == "long":
        configured = (os.getenv("AGENT_PLANNER_MODEL_LONG") or "").strip()
        return configured or "deepseek-v4-flash"
    return (os.getenv("AGENT_PLANNER_MODEL") or "").strip() or None


def _use_query_rewrite() -> bool:
    raw = os.getenv("AGENT_ENABLE_QUERY_REWRITE")
    if raw is not None:
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    return False


def _normalize_queries(raw: Any, fallback_query: str) -> list[str]:
    if not isinstance(raw, list):
        return [fallback_query]
    queries: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        queries.append(text)
    return queries or [fallback_query]


def _normalize_priority(raw: Any) -> str:
    value = str(raw).strip().lower()
    return value if value in PRIORITY_LEVELS else "medium"


def _normalize_freshness(raw: Any) -> str:
    value = str(raw).strip().lower()
    return value if value in FRESHNESS_LEVELS else "medium"


def _normalize_expected_evidence(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        text = str(item).strip()
        if text:
            out.append(text)
    return out[:12]


def _task_from_dict(index: int, data: dict[str, Any], fallback_query: str) -> SearchTask:
    task_id = str(data.get("task_id", "")).strip() or f"t{index}"
    information_need = str(data.get("information_need", "")).strip() or f"Information need {index}"
    why_needed = str(data.get("why_needed", "")).strip() or "Needed to answer user request."

    tool = str(data.get("tool", "web_search")).strip()
    if tool not in ALLOWED_TOOLS:
        tool = "web_search"

    return SearchTask(
        task_id=task_id,
        information_need=information_need,
        why_needed=why_needed,
        tool=tool,
        queries=_normalize_queries(data.get("queries", []), fallback_query=fallback_query),
        freshness=_normalize_freshness(data.get("freshness", "medium")),
        priority=_normalize_priority(data.get("priority", "medium")),
        expected_evidence=_normalize_expected_evidence(data.get("expected_evidence", [])),
    )


def _task_from_schema(index: int, task: Any, fallback_query: str) -> SearchTask:
    raw = task.model_dump() if hasattr(task, "model_dump") else dict(task)
    return _task_from_dict(index=index, data=raw, fallback_query=fallback_query)


def _extract_destination_from_understanding(requirement_understanding: dict[str, object]) -> str:
    explicit = requirement_understanding.get("explicit_requirements", [])
    if not isinstance(explicit, list):
        return ""
    for row in explicit:
        if not isinstance(row, dict):
            continue
        req_type = str(row.get("type", "")).strip().lower()
        value = str(row.get("value", "")).strip()
        if not value:
            continue
        if "destination" in req_type or "目的地" in req_type or req_type in {"destination", "dest"}:
            return value
    return ""


def _extract_duration_from_understanding(requirement_understanding: dict[str, object]) -> str:
    explicit = requirement_understanding.get("explicit_requirements", [])
    if not isinstance(explicit, list):
        return ""
    for row in explicit:
        if not isinstance(row, dict):
            continue
        req_type = str(row.get("type", "")).strip().lower()
        value = str(row.get("value", "")).strip()
        if not value:
            continue
        if "duration" in req_type or "天数" in req_type or "行程天数" in req_type or req_type in {"duration"}:
            return value
    return ""


def _extract_origin_from_understanding(requirement_understanding: dict[str, object]) -> str:
    explicit = requirement_understanding.get("explicit_requirements", [])
    if not isinstance(explicit, list):
        return ""
    for row in explicit:
        if not isinstance(row, dict):
            continue
        req_type = str(row.get("type", "")).strip().lower()
        value = str(row.get("value", "")).strip()
        if not value:
            continue
        if req_type in {"origin", "departure_city", "from_city", "departure"}:
            return value
        if "origin" in req_type or "departure" in req_type or "出发" in req_type:
            return value
    return ""


def _extract_duration_days_from_text(user_input: str) -> int:
    return extract_days_hint(user_input or "")


def _extract_destination_from_text(user_input: str) -> str:
    def _clean(value: str) -> str:
        text = (value or "").strip()
        for prefix in (
            "帮我规划一个",
            "帮我规划",
            "我想去",
            "想去",
            "情侣",
            "亲子",
            "家庭",
            "一个人",
            "我一个人",
            "去",
        ):
            if text.startswith(prefix) and len(text) > len(prefix):
                text = text[len(prefix) :].strip()
                break
        for suffix in ("旅游", "旅行", "行程", "攻略", "度假", "出发的"):
            if text.endswith(suffix) and len(text) > len(suffix):
                text = text[: -len(suffix)].strip()
        return text

    raw = extract_destination_city(user_input or "")
    return _clean(raw)


def _extract_origin_from_text(user_input: str) -> str:
    return extract_origin_city(user_input or "")


def _extract_duration_days_int(requirement_understanding: dict[str, object]) -> int:
    duration = _extract_duration_from_understanding(requirement_understanding)
    if not duration:
        return 0
    digits = "".join(ch for ch in duration if ch.isdigit())
    if not digits:
        return 0
    try:
        value = int(digits)
    except Exception:
        return 0
    return value if value > 0 else 0


def _is_short_trip(requirement_understanding: dict[str, object], user_input: str) -> bool:
    days = _extract_duration_days_int(requirement_understanding)
    if days <= 0:
        days = _extract_duration_days_from_text(user_input)
    return 0 < days <= 4


def _build_fast_tasks(requirement_understanding: dict[str, object], user_input: str) -> list[SearchTask]:
    destination = _extract_destination_from_understanding(requirement_understanding)
    if not destination:
        destination = _extract_destination_from_text(user_input)
    duration_days = _extract_duration_days_int(requirement_understanding)
    if duration_days <= 0:
        duration_days = _extract_duration_days_from_text(user_input)
    origin = _extract_origin_from_understanding(requirement_understanding)
    if not origin:
        origin = _extract_origin_from_text(user_input)

    language = detect_user_language(user_input, default="en")
    days_text = f"{duration_days}天" if duration_days > 0 else ""
    days_text_en = f"{duration_days}-day" if duration_days > 0 else ""
    base = destination or user_input.strip() or ("旅游" if language == "zh" else "travel")

    if language == "zh":
        t1_need = "整体行程框架"
        t1_why = "先确定每日路线骨架"
        t1_query = f"{base} {days_text} 行程 推荐".strip()
        t1_evidence = ["经典路线", "分日安排"]
        t2_need = "景点与游玩时长"
        t2_why = "安排每天节奏与动线"
        t2_query = f"{base} 必去景点 开放时间 游玩时长".strip()
        t2_evidence = ["开放时间", "游玩时长"]
        t3_need = "住宿区域建议"
        t3_why = "降低通勤与换乘时间"
        t3_query = f"{base} 住哪里方便 住宿区域 推荐".strip()
        t3_evidence = ["区域优缺点", "适合人群"]
        t4_need = "美食与餐饮区域"
        t4_why = "补齐饮食体验"
        t4_query = f"{base} 美食 推荐 餐饮区域".strip()
        t4_evidence = ["餐饮聚集区", "代表性菜品"]
        t5_need = "天气与出行窗口"
        t5_why = "调整行程顺序与备选方案"
        t5_query = f"{base} 未来7天天气".strip()
        t5_evidence = ["降雨概率", "温度范围"]
        t6_need = "大交通方案"
        t6_why = "确定往返交通时间和成本"
        t6_query = f"{origin} 到 {destination} 机票 高铁 时间 价格"
        t6_evidence = ["班次", "时长", "价格区间"]
    else:
        t1_need = "Overall itinerary framework"
        t1_why = "Establish a reliable day-by-day route skeleton first."
        t1_query = f"{base} {days_text_en} itinerary recommendation".strip()
        t1_evidence = ["daily route structure", "coverage by area"]
        t2_need = "Attractions and visit duration"
        t2_why = "Plan realistic daily pacing and routing."
        t2_query = f"{base} top attractions opening hours visit duration".strip()
        t2_evidence = ["opening hours", "recommended visit duration"]
        t3_need = "Hotel area recommendations"
        t3_why = "Reduce commute and transfers."
        t3_query = f"{base} best areas to stay for tourists".strip()
        t3_evidence = ["area pros and cons", "who each area suits"]
        t4_need = "Food and dining zones"
        t4_why = "Add meaningful local food experiences."
        t4_query = f"{base} local food areas and must-try dishes".strip()
        t4_evidence = ["food clusters", "signature dishes"]
        t5_need = "Weather and travel window"
        t5_why = "Adjust indoor/outdoor sequencing and backups."
        t5_query = f"{base} weather forecast next 10 days".strip()
        t5_evidence = ["temperature range", "precipitation probability"]
        t6_need = "Intercity transport options"
        t6_why = "Confirm departure/return timing and budget."
        t6_query = f"{origin} to {destination} flights train duration price"
        t6_evidence = ["schedule", "duration", "price range"]

    tasks: list[SearchTask] = [
        SearchTask(
            task_id="t1",
            information_need=t1_need,
            why_needed=t1_why,
            tool="web_search",
            queries=[t1_query],
            freshness="medium",
            priority="high",
            expected_evidence=t1_evidence,
        ),
        SearchTask(
            task_id="t2",
            information_need=t2_need,
            why_needed=t2_why,
            tool="poi_search",
            queries=[t2_query],
            freshness="high",
            priority="high",
            expected_evidence=t2_evidence,
        ),
        SearchTask(
            task_id="t3",
            information_need=t3_need,
            why_needed=t3_why,
            tool="hotel_area_search",
            queries=[t3_query],
            freshness="medium",
            priority="medium",
            expected_evidence=t3_evidence,
        ),
        SearchTask(
            task_id="t4",
            information_need=t4_need,
            why_needed=t4_why,
            tool="web_search",
            queries=[t4_query],
            freshness="medium",
            priority="medium",
            expected_evidence=t4_evidence,
        ),
        SearchTask(
            task_id="t5",
            information_need=t5_need,
            why_needed=t5_why,
            tool="weather_search",
            queries=[t5_query],
            freshness="high",
            priority="medium",
            expected_evidence=t5_evidence,
        ),
    ]

    if origin and destination and origin != destination:
        tasks.append(
            SearchTask(
                task_id="t6",
                information_need=t6_need,
                why_needed=t6_why,
                tool="transport_search",
                queries=[t6_query],
                freshness="high",
                priority="high",
                expected_evidence=t6_evidence,
            )
        )

    if duration_days > 0 and duration_days <= 3:
        preferred = {"t1", "t2", "t3", "t6"}
    elif duration_days > 0 and duration_days <= 7:
        preferred = {"t1", "t2", "t3", "t4", "t6"}
    else:
        preferred = {"t1", "t2", "t3", "t4", "t5", "t6"}

    selected = [task for task in tasks if task.task_id in preferred]
    max_tasks = _safe_int(os.getenv("AGENT_MAX_TASKS_FAST"), 5)
    return selected[:max_tasks]


def _augment_task_coverage(
    tasks: list[SearchTask],
    requirement_understanding: dict[str, object],
    user_input: str,
    max_tasks: int,
) -> list[SearchTask]:
    if not tasks:
        return tasks
    threshold = max(3, min(6, max_tasks))
    existing_tools_full = {task.tool for task in tasks}
    if len(tasks) >= threshold and len(existing_tools_full) >= 3:
        return tasks[:max_tasks]

    destination = _extract_destination_from_understanding(requirement_understanding) or _extract_destination_from_text(user_input)
    duration_days = _extract_duration_days_int(requirement_understanding)
    if duration_days <= 0:
        duration_days = _extract_duration_days_from_text(user_input)
    origin = _extract_origin_from_understanding(requirement_understanding) or _extract_origin_from_text(user_input)
    language = detect_user_language(user_input, default="en")

    days_text = f"{duration_days}天" if duration_days > 0 else ""
    days_text_en = f"{duration_days}-day" if duration_days > 0 else ""
    base = destination or user_input.strip() or ("旅游" if language == "zh" else "travel")
    existing_tools = {task.tool for task in tasks}
    next_id = len(tasks) + 1
    out = list(tasks[:max_tasks])

    def _evict_for_diversity() -> bool:
        if len(out) < max_tasks:
            return True
        def is_itinerary_anchor(item: SearchTask) -> bool:
            need = str(item.information_need or "").lower()
            return ("行程" in item.information_need) or ("路线" in item.information_need) or ("itinerary" in need) or ("route" in need)

        drop_index = -1
        for idx in range(len(out) - 1, -1, -1):
            item = out[idx]
            if item.tool == "web_search" and (not is_itinerary_anchor(item)) and item.priority.lower() != "high":
                drop_index = idx
                break
        if drop_index < 0:
            for idx in range(len(out) - 1, -1, -1):
                item = out[idx]
                if item.tool == "web_search" and not is_itinerary_anchor(item):
                    drop_index = idx
                    break
        if drop_index < 0:
            for idx in range(len(out) - 1, -1, -1):
                item = out[idx]
                if item.tool == "web_search" and item.priority.lower() != "high":
                    drop_index = idx
                    break
        if drop_index < 0:
            return False
        out.pop(drop_index)
        return True

    def append_task(tool: str, need: str, why: str, query: str, freshness: str = "medium", priority: str = "medium") -> None:
        nonlocal next_id
        if len(out) >= max_tasks and not _evict_for_diversity():
            return
        if any(item.tool == tool and item.information_need == need for item in out):
            return
        out.append(
            SearchTask(
                task_id=f"t{next_id}",
                information_need=need,
                why_needed=why,
                tool=tool,
                queries=[query],
                freshness=freshness,
                priority=priority,
                expected_evidence=[],
            )
        )
        next_id += 1

    if "poi_search" not in existing_tools:
        append_task(
            "poi_search",
            "热门景点与游玩时长" if language == "zh" else "Attractions and visit duration",
            "用于安排分日动线" if language == "zh" else "Used to shape daily routing and pacing.",
            (f"{base} 必去景点 开放时间 游玩时长".strip() if language == "zh" else f"{base} top attractions opening hours visit duration".strip()),
            freshness="high",
            priority="high",
        )
    if "hotel_area_search" not in existing_tools:
        append_task(
            "hotel_area_search",
            "住宿区域建议" if language == "zh" else "Hotel area recommendations",
            "用于降低通勤时间" if language == "zh" else "Used to reduce commute time.",
            (f"{base} 住哪里方便 住宿区域 推荐".strip() if language == "zh" else f"{base} best areas to stay for tourists".strip()),
            freshness="medium",
            priority="medium",
        )
    if "weather_search" not in existing_tools:
        append_task(
            "weather_search",
            "出行天气信息" if language == "zh" else "Weather outlook",
            "用于安排室内外活动" if language == "zh" else "Used to sequence indoor and outdoor activities.",
            (f"{base} 未来7天天气".strip() if language == "zh" else f"{base} weather forecast next 10 days".strip()),
            freshness="high",
            priority="medium",
        )
    if "transport_search" not in existing_tools and origin and destination and origin != destination:
        append_task(
            "transport_search",
            "往返交通方案" if language == "zh" else "Intercity transport options",
            "用于确认出发与返程时间窗口" if language == "zh" else "Used to confirm departure and return windows.",
            (f"{origin} 到 {destination} 机票 高铁 时间 价格".strip() if language == "zh" else f"{origin} to {destination} flights train duration price".strip()),
            freshness="high",
            priority="high",
        )
    if "web_search" not in existing_tools:
        append_task(
            "web_search",
            "行程框架参考" if language == "zh" else "Itinerary framework references",
            "用于生成每日安排框架" if language == "zh" else "Used to draft daily structure.",
            (f"{base} {days_text} 行程 推荐".strip() if language == "zh" else f"{base} {days_text_en} itinerary recommendation".strip()),
            freshness="medium",
            priority="high",
        )

    return out[:max_tasks]


def _fallback_tasks(requirement_understanding: dict[str, object], user_input: str) -> list[SearchTask]:
    destination = _extract_destination_from_understanding(requirement_understanding)
    duration = _extract_duration_from_understanding(requirement_understanding)
    if not destination:
        destination = _extract_destination_from_text(user_input)
    origin = _extract_origin_from_understanding(requirement_understanding) or _extract_origin_from_text(user_input)
    language = detect_user_language(user_input, default="en")
    if destination and duration:
        guide_query = (
            f"{destination} {duration} 旅游 行程 经典 路线"
            if language == "zh"
            else f"{destination} {duration} travel itinerary classic route"
        )
    elif destination:
        guide_query = f"{destination} 旅游 行程 推荐" if language == "zh" else f"{destination} travel itinerary recommendation"
    else:
        guide_query = (
            "出境旅游 10天 行程 推荐 预算 5万 情侣"
            if language == "zh"
            else "international 10 day couple trip itinerary budget 50000 CNY"
        )

    tasks: list[SearchTask] = [
        SearchTask(
            task_id="t1",
            information_need="整体行程框架参考" if language == "zh" else "Itinerary framework references",
            why_needed=(
                "需要可靠的分日路线样本作为初版骨架。"
                if language == "zh"
                else "Need reliable day-by-day route samples for the first draft."
            ),
            tool="web_search",
            queries=[guide_query],
            freshness="medium",
            priority="high",
            expected_evidence=(["分日路线", "区域覆盖", "路线节奏"] if language == "zh" else ["daily routes", "area coverage", "pace balance"]),
        )
    ]
    if destination:
        tasks.append(
            SearchTask(
                task_id="t2",
                information_need="景点与游玩时长" if language == "zh" else "Attractions and visit duration",
                why_needed="用于安排每天节奏和动线。" if language == "zh" else "Used to plan daily pacing and routing.",
                tool="poi_search",
                queries=[f"{destination} 必去景点 开放时间 游玩时长" if language == "zh" else f"{destination} top attractions opening hours visit duration"],
                freshness="high",
                priority="high",
                expected_evidence=(["景点开放时间", "推荐停留时长"] if language == "zh" else ["opening hours", "recommended duration"]),
            )
        )
        tasks.append(
            SearchTask(
                task_id="t3",
                information_need="住宿区域建议" if language == "zh" else "Hotel area recommendations",
                why_needed="用于优化通勤成本与体验。" if language == "zh" else "Used to optimize commute and stay experience.",
                tool="hotel_area_search",
                queries=[f"{destination} 住哪里方便 住宿区域 推荐" if language == "zh" else f"{destination} best areas to stay for tourists"],
                freshness="medium",
                priority="medium",
                expected_evidence=(["区域优缺点", "适合人群"] if language == "zh" else ["area pros/cons", "suitable traveler types"]),
            )
        )
        tasks.append(
            SearchTask(
                task_id="t4",
                information_need="天气趋势" if language == "zh" else "Weather trend",
                why_needed="用于调整户外与室内活动比例。" if language == "zh" else "Used to balance indoor and outdoor activities.",
                tool="weather_search",
                queries=[f"{destination} 未来7天天气" if language == "zh" else f"{destination} weather forecast next 10 days"],
                freshness="high",
                priority="medium",
                expected_evidence=(["温度", "降雨概率"] if language == "zh" else ["temperature", "precipitation probability"]),
            )
        )
    if origin and destination and origin != destination:
        tasks.append(
            SearchTask(
                task_id=f"t{len(tasks) + 1}",
                information_need="往返交通方案" if language == "zh" else "Intercity transport options",
                why_needed="用于确认出发和返程时窗及预算。" if language == "zh" else "Used to confirm departure/return windows and cost.",
                tool="transport_search",
                queries=[f"{origin} 到 {destination} 机票 时间 价格" if language == "zh" else f"{origin} to {destination} flights train duration price"],
                freshness="high",
                priority="high",
                expected_evidence=(["航班时长", "价格区间"] if language == "zh" else ["duration", "price range"]),
            )
        )
    return tasks


def _coerce_tasks_from_raw(data: dict[str, Any], fallback_query: str) -> list[SearchTask]:
    if not isinstance(data, dict):
        return []

    raw_tasks: Any = data.get("search_tasks")
    if not isinstance(raw_tasks, list):
        raw_tasks = data.get("search_plan")
    if not isinstance(raw_tasks, list):
        raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list):
        if isinstance(data.get("search_task"), dict):
            raw_tasks = [data.get("search_task")]
        elif isinstance(data.get("task"), dict):
            raw_tasks = [data.get("task")]
        elif any(key in data for key in ("task_id", "information_need", "tool", "queries")):
            raw_tasks = [data]
        else:
            return []

    tasks: list[SearchTask] = []
    seen: set[tuple[str, str]] = set()
    for idx, row in enumerate(raw_tasks[:10], start=1):
        if not isinstance(row, dict):
            continue
        if "information_need" not in row and "info_need" in row:
            row = {**row, "information_need": row.get("info_need")}
        if "why_needed" not in row and "reason" in row:
            row = {**row, "why_needed": row.get("reason")}
        if "queries" not in row:
            q = row.get("query")
            if isinstance(q, str):
                row = {**row, "queries": [q]}
            elif isinstance(q, list):
                row = {**row, "queries": q}
        task = _task_from_dict(idx, row, fallback_query=fallback_query)
        dedupe_key = (task.tool, task.information_need.lower())
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        tasks.append(task)
    return tasks


def _normalize_query_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "").strip())
    return cleaned


def _rewrite_queries_by_llm(
    tasks: list[SearchTask],
    user_input: str,
    requirement_understanding: dict[str, object],
    conversation_history: list[dict[str, str]] | None,
) -> list[SearchTask]:
    if not tasks:
        return tasks
    if not _use_query_rewrite():
        return tasks
    if not CLIENT.enabled:
        return tasks

    query_limit = max(1, min(5, _safe_int(os.getenv("AGENT_MAX_REWRITTEN_QUERIES"), 3)))
    response_language = detect_user_language(user_input, default="en")
    messages = CLIENT.build_messages(
        system_prompt="You are a rigorous travel query rewrite model. Output JSON only.",
        developer_prompt=PROMPT_QUERY_REWRITER,
        user_prompt=(
            "User input:\n"
            f"{user_input}\n\n"
            "response_language:\n"
            f"{response_language}\n\n"
            "Requirement understanding JSON:\n"
            f"{json.dumps(requirement_understanding, ensure_ascii=False)}\n\n"
            "search_tasks:\n"
            f"{json.dumps([task.to_dict() for task in tasks], ensure_ascii=False)}\n\n"
            "Recent conversation context:\n"
            f"{json.dumps((conversation_history or [])[-8:], ensure_ascii=False)}\n\n"
            "Output JSON only."
        ),
        conversation_history=None,
    )
    speed_mode = _speed_mode()
    trip_days = _extract_duration_days_int(requirement_understanding)
    if trip_days <= 0:
        trip_days = _extract_duration_days_from_text(user_input)
    complexity = _trip_complexity(trip_days, speed_mode)
    extra_body = _planner_extra_body(speed_mode, complexity)
    thinking_enabled = str(extra_body.get("thinking", {}).get("type", "")).lower() == "enabled"
    timeout_backup = CLIENT.timeout_sec
    try:
        CLIENT.timeout_sec = _rewrite_timeout_sec(complexity)
        result = CLIENT.json_completion(
            messages=messages,
            temperature=0.1,
            max_tokens=1400 if complexity in {"medium", "long"} else 1100,
            reasoning_effort=os.getenv("AGENT_REASONING_EFFORT", "medium") if thinking_enabled else None,
            extra_body=extra_body,
            model=_planner_model(speed_mode, complexity),
            allow_model_fallback=False,
        )
    finally:
        CLIENT.timeout_sec = timeout_backup
    if not result.get("ok"):
        return tasks

    payload = result.get("json", {})
    if not isinstance(payload, dict):
        return tasks

    raw_rows = payload.get("task_queries", [])
    if not isinstance(raw_rows, list):
        return tasks

    query_by_task: dict[str, list[str]] = {}
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        task_id = str(row.get("task_id", "")).strip()
        if not task_id:
            continue
        raw_queries = row.get("queries", [])
        if not isinstance(raw_queries, list):
            continue
        dedup: list[str] = []
        seen: set[str] = set()
        for item in raw_queries:
            query = _normalize_query_text(str(item))
            if not query:
                continue
            key = query.lower()
            if key in seen:
                continue
            if key == _normalize_query_text(user_input).lower():
                continue
            seen.add(key)
            dedup.append(query)
            if len(dedup) >= query_limit:
                break
        if dedup:
            query_by_task[task_id] = dedup

    if not query_by_task:
        return tasks

    rewritten: list[SearchTask] = []
    for task in tasks:
        new_queries = query_by_task.get(task.task_id, task.queries)
        rewritten.append(
            SearchTask(
                task_id=task.task_id,
                information_need=task.information_need,
                why_needed=task.why_needed,
                tool=task.tool,
                queries=new_queries,
                freshness=task.freshness,
                priority=task.priority,
                expected_evidence=task.expected_evidence,
            )
        )
    return rewritten


def _refine_query_fallback(
    task: SearchTask,
    attempted_queries: list[str],
    destination_hint: str,
    duration_days: int,
    language: str,
) -> list[str]:
    base = destination_hint.strip() or task.information_need.strip() or "旅游"
    days_text = f"{duration_days}天" if duration_days > 0 else ""
    candidates: list[str] = []

    if language == "zh":
        if task.tool == "weather_search":
            candidates = [
                f"{base} {days_text} 天气 预报 最新".strip(),
                f"{base} 气温 降雨 实时".strip(),
                f"{base} weather forecast next 10 days".strip(),
            ]
        elif task.tool == "transport_search":
            candidates = [
                f"{base} 机票 最新 价格 航班 时间".strip(),
                f"{base} flight price schedule official".strip(),
                f"{base} 高铁 航班 时刻".strip(),
            ]
        elif task.tool in {"poi_search", "hotel_area_search"}:
            candidates = [
                f"{base} {task.information_need} 官方 开放时间 门票".strip(),
                f"{base} top attractions opening hours ticket price".strip(),
                f"{base} 地图 热门 点位 推荐".strip(),
            ]
        else:
            candidates = [
                f"{base} {days_text} 行程 经典 路线".strip(),
                f"{base} travel itinerary {max(3, duration_days or 7)} days".strip(),
                f"{base} 攻略 官方 最新".strip(),
            ]
    else:
        if task.tool == "weather_search":
            candidates = [
                f"{base} weather forecast next 10 days latest".strip(),
                f"{base} temperature precipitation hourly official".strip(),
                f"{base} weather alerts realtime".strip(),
            ]
        elif task.tool == "transport_search":
            candidates = [
                f"{base} flight train schedule latest fares".strip(),
                f"{base} transport duration price official".strip(),
                f"{base} flight booking trend next 7 days".strip(),
            ]
        elif task.tool in {"poi_search", "hotel_area_search"}:
            candidates = [
                f"{base} top attractions opening hours ticket price official".strip(),
                f"{base} best areas to stay pros cons".strip(),
                f"{base} must visit places map routing".strip(),
            ]
        else:
            candidates = [
                f"{base} {max(3, duration_days or 7)} day itinerary route guide".strip(),
                f"{base} travel plan first time visitors".strip(),
                f"{base} practical travel guide latest".strip(),
            ]

    attempted_lc = {str(item).strip().lower() for item in attempted_queries if str(item).strip()}
    refined: list[str] = []
    seen: set[str] = set()
    for query in candidates:
        q = _normalize_query_text(query)
        if not q:
            continue
        q_lc = q.lower()
        if q_lc in seen or q_lc in attempted_lc:
            continue
        seen.add(q_lc)
        refined.append(q)
        if len(refined) >= 3:
            break
    return refined


def _parse_refine_payload(payload: dict[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    rows = payload.get("task_queries", [])
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        task_id = str(row.get("task_id", "")).strip()
        if not task_id:
            continue
        raw_queries = row.get("queries", [])
        if not isinstance(raw_queries, list):
            continue
        dedup: list[str] = []
        seen: set[str] = set()
        for item in raw_queries:
            query = _normalize_query_text(str(item))
            if not query:
                continue
            key = query.lower()
            if key in seen:
                continue
            seen.add(key)
            dedup.append(query)
            if len(dedup) >= 4:
                break
        if dedup:
            out[task_id] = dedup
    return out


def refine_tasks_after_search(
    requirement_understanding: dict[str, object],
    user_input: str,
    failed_tasks: list[SearchTask],
    diagnostics: list[dict[str, Any]] | None = None,
    conversation_history: list[dict[str, str]] | None = None,
) -> list[SearchTask]:
    if not failed_tasks:
        return []

    destination = _extract_destination_from_understanding(requirement_understanding)
    if not destination:
        destination = _extract_destination_from_text(user_input)
    duration_days = _extract_duration_days_int(requirement_understanding)
    if duration_days <= 0:
        duration_days = _extract_duration_days_from_text(user_input)

    diagnostics_rows = diagnostics if isinstance(diagnostics, list) else []
    attempted_by_task: dict[str, list[str]] = {}
    for row in diagnostics_rows:
        if not isinstance(row, dict):
            continue
        task_id = str(row.get("task_id", "")).strip()
        attempted = row.get("attempted_queries", [])
        if not task_id or not isinstance(attempted, list):
            continue
        attempted_by_task[task_id] = [str(item).strip() for item in attempted if str(item).strip()]

    if not CLIENT.enabled:
        refined_fallback: list[SearchTask] = []
        for task in failed_tasks:
            attempted = attempted_by_task.get(task.task_id, list(task.queries))
            queries = _refine_query_fallback(task, attempted, destination, duration_days, language=detect_user_language(user_input, default="en"))
            if not queries:
                continue
            refined_fallback.append(
                SearchTask(
                    task_id=task.task_id,
                    information_need=task.information_need,
                    why_needed=task.why_needed,
                    tool=task.tool,
                    queries=queries,
                    freshness=task.freshness,
                    priority=task.priority,
                    expected_evidence=task.expected_evidence,
                )
            )
        return refined_fallback

    mode = _speed_mode()
    complexity = _trip_complexity(duration_days, mode)
    extra_body = _planner_extra_body(mode, complexity)
    thinking_enabled = str(extra_body.get("thinking", {}).get("type", "")).lower() == "enabled"
    response_language = detect_user_language(user_input, default="en")

    llm_payload = {
        "user_input": user_input,
        "requirement_understanding": requirement_understanding,
        "failed_tasks": [task.to_dict() for task in failed_tasks],
        "diagnostics": diagnostics_rows,
        "conversation_context": (conversation_history or [])[-8:],
    }
    messages = CLIENT.build_messages(
        system_prompt="You are a rigorous travel query refinement model. Output JSON only.",
        developer_prompt=PROMPT_QUERY_REFINER_AFTER_RESULTS,
        user_prompt=(
            "response_language:\n"
            f"{response_language}\n\n"
            "Input data:\n"
            f"{json.dumps(llm_payload, ensure_ascii=False)}\n\n"
            "Output JSON only."
        ),
        conversation_history=None,
    )

    timeout_backup = CLIENT.timeout_sec
    try:
        CLIENT.timeout_sec = _rewrite_timeout_sec(complexity)
        result = CLIENT.json_completion(
            messages=messages,
            temperature=0.1,
            max_tokens=1300 if complexity in {"short", "fast"} else 1700,
            reasoning_effort=os.getenv("AGENT_REASONING_EFFORT", "medium") if thinking_enabled else None,
            extra_body=extra_body,
            model=_planner_model(mode, complexity),
            allow_model_fallback=False,
        )
    finally:
        CLIENT.timeout_sec = timeout_backup

    refined_by_task: dict[str, list[str]] = {}
    if result.get("ok") and isinstance(result.get("json", {}), dict):
        refined_by_task = _parse_refine_payload(result.get("json", {}))

    refined_tasks: list[SearchTask] = []
    for task in failed_tasks:
        attempted = attempted_by_task.get(task.task_id, list(task.queries))
        queries = refined_by_task.get(task.task_id, [])
        filtered: list[str] = []
        seen: set[str] = set()
        attempted_lc = {str(item).strip().lower() for item in attempted if str(item).strip()}
        for query in queries:
            q = _normalize_query_text(query)
            if not q:
                continue
            q_lc = q.lower()
            if q_lc in seen or q_lc in attempted_lc:
                continue
            seen.add(q_lc)
            filtered.append(q)
            if len(filtered) >= 4:
                break
        if not filtered:
            filtered = _refine_query_fallback(task, attempted, destination, duration_days, language=response_language)
        if not filtered:
            continue
        refined_tasks.append(
            SearchTask(
                task_id=task.task_id,
                information_need=task.information_need,
                why_needed=task.why_needed,
                tool=task.tool,
                queries=filtered,
                freshness=task.freshness,
                priority=task.priority,
                expected_evidence=task.expected_evidence,
            )
        )
    return refined_tasks


def plan_tasks(
    requirement_understanding: dict[str, object],
    user_input: str,
    conversation_history: list[dict[str, str]] | None = None,
    user_preferences: dict[str, object] | None = None,
    cancel_event: object | None = None,
) -> list[SearchTask]:
    if cancel_event and getattr(cancel_event, "is_set", lambda: False)():
        return []
    mode = _speed_mode()
    trip_days = _extract_duration_days_int(requirement_understanding)
    if trip_days <= 0:
        trip_days = _extract_duration_days_from_text(user_input)
    complexity = _trip_complexity(trip_days, mode)
    if not CLIENT.enabled:
        return []

    history = (conversation_history or [])[-12:]
    time_context = build_runtime_time_context(user_preferences if isinstance(user_preferences, dict) else None)
    response_language = detect_user_language(user_input, default="en")
    messages = CLIENT.build_messages(
        system_prompt="You are a rigorous travel search-planning model. Output JSON only.",
        developer_prompt=PROMPT_SEARCH_PLANNER,
        user_prompt=(
            "Requirement understanding JSON:\n"
            f"{json.dumps(requirement_understanding, ensure_ascii=False)}\n\n"
            "response_language:\n"
            f"{response_language}\n\n"
            "Current time context (must be used for relative time expressions):\n"
            f"{json.dumps(time_context, ensure_ascii=False)}\n\n"
            "Recent conversation context:\n"
            f"{json.dumps(history, ensure_ascii=False)}\n\n"
            "Output JSON only."
        ),
        conversation_history=None,
    )

    attempts = [(0.0, 900)]
    if response_language == "zh":
        fallback_query = (
            f"{_extract_destination_from_understanding(requirement_understanding) or _extract_destination_from_text(user_input)} "
            f"{_extract_duration_from_understanding(requirement_understanding) or ''} 旅游 行程 推荐"
        ).strip() or "旅游 行程 推荐"
    else:
        fallback_query = (
            f"{_extract_destination_from_understanding(requirement_understanding) or _extract_destination_from_text(user_input)} "
            f"{_extract_duration_from_understanding(requirement_understanding) or ''} travel itinerary recommendation"
        ).strip() or "travel itinerary recommendation"
    max_tasks = _safe_int(os.getenv("AGENT_MAX_TASKS"), 5)
    extra_body = _planner_extra_body(mode, complexity)
    thinking_enabled = str(extra_body.get("thinking", {}).get("type", "")).lower() == "enabled"
    model_name = _planner_model(mode, complexity)
    best_effort_tasks: list[SearchTask] = []

    for temperature, max_tokens in attempts:
        if cancel_event and getattr(cancel_event, "is_set", lambda: False)():
            return []
        timeout_backup = CLIENT.timeout_sec
        try:
            CLIENT.timeout_sec = _planner_timeout_sec(complexity)
            result = CLIENT.json_completion(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                reasoning_effort=os.getenv("AGENT_REASONING_EFFORT", "medium") if thinking_enabled else None,
                extra_body=extra_body,
                model=model_name,
                allow_model_fallback=False,
            )
        finally:
            CLIENT.timeout_sec = timeout_backup
        if not result.get("ok"):
            continue

        data = result.get("json", {})
        if not isinstance(data, dict):
            continue

        try:
            parsed = validate_search_plan(data)
            raw_tasks = parsed.search_tasks
            planned: list[SearchTask] = []
            seen: set[tuple[str, str]] = set()
            for idx, item in enumerate(raw_tasks[:max_tasks], start=1):
                task = _task_from_schema(idx, item, fallback_query=fallback_query)
                dedupe_key = (task.tool, task.information_need.lower())
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                planned.append(task)
            min_task_count = 2 if mode == "fast" else 3
            if len(planned) >= min_task_count:
                rewritten = _rewrite_queries_by_llm(
                    tasks=planned,
                    user_input=user_input,
                    requirement_understanding=requirement_understanding,
                    conversation_history=conversation_history,
                )
                rewritten = _augment_task_coverage(
                    tasks=rewritten[:max_tasks],
                    requirement_understanding=requirement_understanding,
                    user_input=user_input,
                    max_tasks=max_tasks,
                )
                return rewritten[:max_tasks]
            if planned and not best_effort_tasks:
                best_effort_tasks = planned[:max_tasks]
        except Exception:
            pass

        coerced = _coerce_tasks_from_raw(data, fallback_query=fallback_query)
        min_task_count = 2 if mode == "fast" else 3
        if len(coerced) >= min_task_count:
            rewritten = _rewrite_queries_by_llm(
                tasks=coerced[:max_tasks],
                user_input=user_input,
                requirement_understanding=requirement_understanding,
                conversation_history=conversation_history,
            )
            rewritten = _augment_task_coverage(
                tasks=rewritten[:max_tasks],
                requirement_understanding=requirement_understanding,
                user_input=user_input,
                max_tasks=max_tasks,
            )
            return rewritten[:max_tasks]
        if coerced and not best_effort_tasks:
            best_effort_tasks = coerced[:max_tasks]

    # Rescue pass: compact prompt and relaxed constraints to avoid all-or-nothing fallback.
    rescue_messages = CLIENT.build_messages(
        system_prompt="You are a travel search-task planning model. Output JSON only.",
        developer_prompt=(
            "Output search_tasks with task_id, information_need, why_needed, tool, queries, freshness, priority."
            "Task count should be 4-6 and aligned with user needs."
        ),
        user_prompt=(
            "User input:\n"
            f"{user_input}\n\n"
            "Requirement understanding:\n"
            f"{json.dumps(requirement_understanding, ensure_ascii=False)}\n\n"
            "Output JSON only."
        ),
        conversation_history=None,
    )
    timeout_backup = CLIENT.timeout_sec
    try:
        CLIENT.timeout_sec = max(8.0, _planner_timeout_sec(complexity) - 2.0)
        rescue = CLIENT.json_completion(
            messages=rescue_messages,
            temperature=0.0,
            max_tokens=700,
            reasoning_effort=None,
            extra_body={"thinking": {"type": "disabled"}},
            model=model_name,
            allow_model_fallback=False,
        )
    finally:
        CLIENT.timeout_sec = timeout_backup

    if rescue.get("ok") and isinstance(rescue.get("json", {}), dict):
        rescue_data = rescue.get("json", {})
        coerced = _coerce_tasks_from_raw(rescue_data, fallback_query=fallback_query)
        min_task_count = 2 if mode == "fast" else 3
        if len(coerced) >= min_task_count:
            rewritten = _rewrite_queries_by_llm(
                tasks=coerced[:max_tasks],
                user_input=user_input,
                requirement_understanding=requirement_understanding,
                conversation_history=conversation_history,
            )
            rewritten = _augment_task_coverage(
                tasks=rewritten[:max_tasks],
                requirement_understanding=requirement_understanding,
                user_input=user_input,
                max_tasks=max_tasks,
            )
            return rewritten[:max_tasks]
        if coerced and not best_effort_tasks:
            best_effort_tasks = coerced[:max_tasks]

    if best_effort_tasks:
        return best_effort_tasks[:max_tasks]

    fallback = _build_fast_tasks(requirement_understanding=requirement_understanding, user_input=user_input)
    if not fallback:
        fallback = _fallback_tasks(requirement_understanding=requirement_understanding, user_input=user_input)
    fallback = _augment_task_coverage(
        tasks=fallback,
        requirement_understanding=requirement_understanding,
        user_input=user_input,
        max_tasks=max_tasks,
    )
    return fallback[:max_tasks]
