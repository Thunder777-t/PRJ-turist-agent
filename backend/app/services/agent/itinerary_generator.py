from __future__ import annotations

import json
import os
import re
from typing import Any

from ..deepseek_client import DeepseekClient
from .llm_schemas import validate_final_answer
from .runtime_context import build_runtime_time_context
from .runtime_flags import get_agent_speed_mode, runtime_truthy
from .text_slot_utils import detect_user_language, extract_days_hint, extract_destination_city, extract_origin_city
from .types import SearchTask, ToolExecutionResult


CLIENT = DeepseekClient()

PROMPT_FINAL_PLANNER = """
You are a professional travel planning agent with natural, concise writing.

You will receive:
1. User raw input
2. Requirement understanding JSON
3. Search-task JSON
4. Search-result analysis JSON
5. Conversation context

Your task is to generate the final answer.

Requirements:
1. The answer must match the user’s real request.
2. Explicitly list default assumptions.
3. If information is incomplete, do not pretend certainty.
4. Generate an executable plan with rational day pacing.
5. Do not stack random attractions; explain arrangement logic.
6. Resolve relative dates (today/tomorrow/day after tomorrow/next weekend) into absolute dates using provided time context.
7. frontend_json must be complete and usable by UI components.
8. markdown_answer must be a full, polished answer (not empty), including clear headings and practical details.
9. Language rule: markdown_answer and all free-text fields must follow response_language exactly.
10. Output must include frontend_json and markdown_answer.
11. Keep each itinerary text field concise; avoid long paragraphs.
12. Do not output extra keys outside the schema.

Output JSON schema:
{
  "frontend_json": {
    "destination": string,
    "duration_days": number,
    "assumptions": string[],
    "missing_information": string[],
    "itinerary": [
      {
        "day": number,
        "theme": string,
        "morning": string,
        "afternoon": string,
        "evening": string,
        "food_recommendations": string[],
        "transport_notes": string[],
        "why_this_day_works": string
      }
    ],
    "hotel_area_suggestions": [],
    "budget_estimate": {},
    "tips": [],
    "follow_up_questions": []
  },
  "markdown_answer": string
}
""".strip()


RELATIVE_TIME_MARKERS = (
    "今天",
    "明天",
    "后天",
    "大后天",
    "下周",
    "下个月",
    "周末",
    "today",
    "tomorrow",
    "day after tomorrow",
    "next weekend",
    "next week",
    "next month",
)


def _speed_mode() -> str:
    return get_agent_speed_mode(default="quality")


def _enable_thinking() -> bool:
    return runtime_truthy("agent_enable_thinking", "AGENT_ENABLE_THINKING", False)


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


def _final_timeout_sec(complexity: str) -> float:
    defaults = {
        "fast": 5.0,
        "short": 12.0,
        "medium": 15.0,
        "long": 22.0,
    }
    return _safe_float(os.getenv("AGENT_FINAL_TIMEOUT_SEC"), defaults.get(complexity, 14.0))


def _final_max_tokens(complexity: str) -> int:
    defaults = {
        "fast": 1200,
        "short": 1800,
        "medium": 2400,
        "long": 3200,
    }
    configured = _safe_int(os.getenv("AGENT_FINAL_MAX_TOKENS"), defaults.get(complexity, 2000))
    return max(configured, defaults.get(complexity, 2000))


def _final_model(speed_mode: str, complexity: str) -> str | None:
    if speed_mode == "fast":
        return (os.getenv("AGENT_FINAL_MODEL_FAST") or "").strip() or None
    if complexity == "long":
        configured = (os.getenv("AGENT_FINAL_MODEL_LONG") or "").strip()
        return configured or "deepseek-v4-flash"
    return (os.getenv("AGENT_FINAL_MODEL") or "").strip() or None


def _final_extra_body(speed_mode: str, complexity: str) -> dict[str, Any]:
    enable_thinking = runtime_truthy("agent_enable_thinking", "AGENT_ENABLE_THINKING", False)
    if speed_mode == "fast":
        return {"thinking": {"type": "disabled"}}
    if enable_thinking and complexity in {"medium", "long"}:
        return {"thinking": {"type": "enabled"}}
    return {"thinking": {"type": "disabled"}}


def _normalize_sources(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    items: list[dict[str, str]] = []
    seen = set()
    for source in raw:
        if not isinstance(source, dict):
            continue
        url = str(source.get("url", "")).strip()
        if not url or url in seen:
            continue
        seen.add(url)
        items.append(
            {
                "title": str(source.get("title", "Untitled")).strip() or "Untitled",
                "url": url,
                "platform": str(source.get("platform", "")).strip(),
                "snippet": str(source.get("snippet", "")).strip(),
            }
        )
    return items


def _truncate_text(value: Any, max_len: int = 180) -> str:
    text = str(value or "").strip()
    if len(text) <= max_len:
        return text
    return f"{text[: max_len - 3]}..."


def _prepend_time_anchor_if_needed(
    markdown: str,
    user_input: str,
    time_context: dict[str, str],
) -> str:
    text = str(markdown or "").strip()
    if not text:
        return text

    if not any(marker in user_input for marker in RELATIVE_TIME_MARKERS):
        return text

    language = detect_user_language(user_input, default="en")
    if language == "zh":
        anchor_line = (
            f"时间基准：当前日期 {time_context.get('relative_today', '')} "
            f"（{time_context.get('timezone', '')}），明天 {time_context.get('relative_tomorrow', '')}，"
            f"后天 {time_context.get('relative_day_after_tomorrow', '')}。"
        ).strip()
    else:
        anchor_line = (
            f"Time anchor: today is {time_context.get('relative_today', '')} "
            f"({time_context.get('timezone', '')}), tomorrow is {time_context.get('relative_tomorrow', '')}, "
            f"and the day after tomorrow is {time_context.get('relative_day_after_tomorrow', '')}."
        ).strip()
    if not anchor_line:
        return text
    if anchor_line in text:
        return text
    if language == "zh" and re.search(r"时间基准[:：]", text):
        return text
    if language != "zh" and re.search(r"Time anchor:", text, flags=re.IGNORECASE):
        return text
    return f"{anchor_line}\n\n{text}"


def _language_stats(text: str) -> tuple[int, int]:
    raw = str(text or "")
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", raw))
    latin_count = len(re.findall(r"[A-Za-z]", raw))
    return cjk_count, latin_count


def _collect_frontend_text_blob(frontend_json: dict[str, Any]) -> str:
    chunks: list[str] = []
    chunks.append(str(frontend_json.get("destination", "")).strip())
    chunks.extend([str(item).strip() for item in frontend_json.get("assumptions", []) if str(item).strip()])
    chunks.extend([str(item).strip() for item in frontend_json.get("missing_information", []) if str(item).strip()])
    days = frontend_json.get("itinerary", [])
    if isinstance(days, list):
        for day in days[:10]:
            if not isinstance(day, dict):
                continue
            for key in ("theme", "morning", "afternoon", "evening", "why_this_day_works"):
                value = str(day.get(key, "")).strip()
                if value:
                    chunks.append(value)
            for key in ("food_recommendations", "transport_notes"):
                rows = day.get(key, [])
                if isinstance(rows, list):
                    chunks.extend([str(item).strip() for item in rows[:6] if str(item).strip()])
    return "\n".join(chunks)


def _is_output_language_mismatch(frontend_json: dict[str, Any], markdown_answer: str, target_language: str) -> bool:
    text_blob = f"{_collect_frontend_text_blob(frontend_json)}\n{str(markdown_answer or '')[:2000]}"
    cjk_count, latin_count = _language_stats(text_blob)
    if target_language == "en":
        # Do not over-trigger on proper nouns in Chinese.
        return cjk_count >= 24 and cjk_count > max(15, int(latin_count * 0.35))
    # Chinese output can contain many English place names/brands; keep threshold conservative.
    return latin_count >= 220 and latin_count > max(160, int(cjk_count * 2.8))


def _looks_placeholder_markdown(markdown_answer: str) -> bool:
    text = str(markdown_answer or "").strip().lower()
    if not text:
        return True
    markers = (
        "tbd destination",
        "tbd pace",
        "no recommendation yet",
        "no transport notes yet",
        "当前尚未返回可靠的分日行程数据",
        "可靠的分日行程数据",
        "reliable day-by-day itinerary data is currently unavailable",
    )
    return any(marker in text for marker in markers)


def _rewrite_payload_language_if_needed(
    *,
    user_input: str,
    frontend_json: dict[str, Any],
    markdown_answer: str,
    time_context: dict[str, str],
) -> tuple[dict[str, Any], str]:
    target_language = detect_user_language(user_input, default="en")
    if not _is_output_language_mismatch(frontend_json, markdown_answer, target_language):
        return frontend_json, markdown_answer
    if not CLIENT.enabled:
        return frontend_json, markdown_answer

    prompt = (
        "Rewrite the existing travel-plan payload into target response_language.\n"
        "Requirements:\n"
        "1) Keep exact JSON schema with keys unchanged.\n"
        "2) Keep numbers, dates, route order, and budget values unchanged.\n"
        "3) Translate all free-text fields in frontend_json and markdown_answer into response_language.\n"
        "4) Do not invent new attractions or facts.\n"
        "5) Output JSON only.\n\n"
        f"response_language:\n{target_language}\n\n"
        f"user_input:\n{user_input}\n\n"
        f"time_context:\n{json.dumps(time_context, ensure_ascii=False)}\n\n"
        f"current_frontend_json:\n{json.dumps(frontend_json, ensure_ascii=False)}\n\n"
        f"current_markdown_answer:\n{markdown_answer}\n\n"
        "Output schema:\n"
        "{\n"
        '  "frontend_json": {...},\n'
        '  "markdown_answer": string\n'
        "}\n"
    )
    messages = CLIENT.build_messages(
        system_prompt="You are a travel-plan language normalizer. Output JSON only.",
        developer_prompt="Preserve structure and facts. Translate only language style.",
        user_prompt=prompt,
        conversation_history=None,
    )

    timeout_backup = CLIENT.timeout_sec
    try:
        CLIENT.timeout_sec = _safe_float(os.getenv("AGENT_LANGUAGE_REWRITE_TIMEOUT_SEC"), 8.0)
        result = CLIENT.json_completion(
            messages=messages,
            temperature=0.0,
            max_tokens=_safe_int(os.getenv("AGENT_LANGUAGE_REWRITE_MAX_TOKENS"), 1800),
            reasoning_effort=None,
            extra_body={"thinking": {"type": "disabled"}},
            model=(os.getenv("AGENT_LANGUAGE_REWRITE_MODEL") or "").strip() or "deepseek-v4-flash",
            allow_model_fallback=False,
        )
    finally:
        CLIENT.timeout_sec = timeout_backup

    if not result.get("ok"):
        return frontend_json, markdown_answer

    payload = result.get("json", {})
    if not isinstance(payload, dict):
        return frontend_json, markdown_answer

    new_frontend = payload.get("frontend_json", {})
    new_markdown = str(payload.get("markdown_answer", "")).strip()
    if not isinstance(new_frontend, dict):
        new_frontend = frontend_json
    if not new_markdown:
        new_markdown = markdown_answer

    return new_frontend, new_markdown


def _compact_tool_output(tool: str, output: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(output, dict):
        return {}

    if tool == "web_search":
        rows = output.get("items", []) if isinstance(output.get("items", []), list) else []
        return {
            "source": output.get("source", ""),
            "count": int(output.get("count", 0) or 0),
            "items": [
                {
                    "title": _truncate_text(item.get("title", ""), 90),
                    "url": str(item.get("url", "")).strip(),
                    "snippet": _truncate_text(item.get("snippet", ""), 90),
                }
                for item in rows[:2]
                if isinstance(item, dict)
            ],
            "error": _truncate_text(output.get("error", ""), 200),
        }

    if tool == "transport_search":
        rows = (
            output.get("transport_items", [])
            if isinstance(output.get("transport_items", []), list)
            else []
        )
        return {
            "source": output.get("source", ""),
            "count": int(output.get("count", 0) or 0),
            "transport_items": [
                {
                    "title": _truncate_text(item.get("title", ""), 90),
                    "url": str(item.get("url", "")).strip(),
                    "snippet": _truncate_text(item.get("snippet", ""), 90),
                }
                for item in rows[:2]
                if isinstance(item, dict)
            ],
            "error": _truncate_text(output.get("error", ""), 200),
        }

    if tool in {"poi_search", "place_search"}:
        rows = output.get("pois", []) if isinstance(output.get("pois", []), list) else []
        return {
            "source": output.get("source", ""),
            "count": int(output.get("count", 0) or 0),
            "pois": [
                {
                    "name": _truncate_text(item.get("name", ""), 56),
                    "address": _truncate_text(item.get("address", ""), 88),
                    "url": str(item.get("url", "")).strip(),
                }
                for item in rows[:3]
                if isinstance(item, dict)
            ],
            "error": _truncate_text(output.get("error", ""), 200),
        }

    if tool in {"hotel_area_search", "hotel_search"}:
        rows = output.get("areas", []) if isinstance(output.get("areas", []), list) else []
        return {
            "source": output.get("source", ""),
            "count": int(output.get("count", 0) or 0),
            "areas": [
                {
                    "name": _truncate_text(item.get("name", ""), 56),
                    "address": _truncate_text(item.get("address", ""), 88),
                    "url": str(item.get("url", "")).strip(),
                }
                for item in rows[:3]
                if isinstance(item, dict)
            ],
            "error": _truncate_text(output.get("error", ""), 200),
        }

    if tool == "weather_search":
        rows = output.get("forecast", []) if isinstance(output.get("forecast", []), list) else []
        return {
            "source": output.get("source", ""),
            "location": _truncate_text(output.get("location", ""), 100),
            "forecast": [
                {
                    "date": str(item.get("date", "")).strip(),
                    "temp_max_c": item.get("temp_max_c"),
                    "temp_min_c": item.get("temp_min_c"),
                    "precip_prob_max": item.get("precip_prob_max"),
                }
                for item in rows[:7]
                if isinstance(item, dict)
            ],
            "error": _truncate_text(output.get("error", ""), 200),
        }

    compact = {
        "source": output.get("source", ""),
        "error": _truncate_text(output.get("error", ""), 200),
    }
    return compact


def _compact_tool_results_for_prompt(tool_results: list[ToolExecutionResult]) -> list[dict[str, Any]]:
    compact_rows: list[dict[str, Any]] = []
    for item in tool_results:
        compact_rows.append(
            {
                "task_id": item.task_id,
                "tool": item.tool,
                "query": _truncate_text(item.query, 120),
                "success": item.success,
                "output": _compact_tool_output(item.tool, item.output),
                "source": item.source,
            }
        )
    return compact_rows


def _extract_destination_duration_budget(requirement_understanding: dict[str, Any]) -> tuple[str, int, str]:
    destination = ""
    duration_days = 0
    budget = ""
    explicit = requirement_understanding.get("explicit_requirements", [])
    if not isinstance(explicit, list):
        return destination, duration_days, budget
    for item in explicit:
        if not isinstance(item, dict):
            continue
        r_type = str(item.get("type", "")).strip().lower()
        value = str(item.get("value", "")).strip()
        if not value:
            continue
        if ("destination" in r_type or "目的地" in r_type or r_type in {"destination", "dest"}) and not destination:
            destination = value
            continue
        if ("duration" in r_type or "天数" in r_type or r_type in {"duration", "duration_days"}) and duration_days <= 0:
            digits = "".join(ch for ch in value if ch.isdigit())
            if digits:
                try:
                    duration_days = int(digits)
                except Exception:
                    duration_days = 0
            continue
        if ("budget" in r_type or "预算" in r_type) and not budget:
            budget = value
    return destination, duration_days, budget


def _extract_origin(requirement_understanding: dict[str, Any], user_input: str) -> str:
    explicit = requirement_understanding.get("explicit_requirements", [])
    if isinstance(explicit, list):
        for item in explicit:
            if not isinstance(item, dict):
                continue
            r_type = str(item.get("type", "")).strip().lower()
            value = str(item.get("value", "")).strip()
            if not value:
                continue
            if r_type in {"origin", "from_city", "departure_city", "departure"}:
                return value
            if "出发" in r_type or "origin" in r_type or "departure" in r_type:
                return value
    inferred = extract_origin_city(user_input or "")
    return str(inferred or "").strip()


def _extract_destination_duration_budget_from_text(user_input: str) -> tuple[str, int, str]:
    text = user_input or ""
    destination = str(extract_destination_city(text) or "").strip()
    duration_days = extract_days_hint(text)
    budget = ""
    budget_patterns = [
        r"预算\s*([0-9]+(?:\.[0-9]+)?\s*[wW万亿元]*)",
        r"\bbudget\s*(?:is|around|about|:)?\s*([0-9][0-9,]*(?:\.[0-9]+)?\s*(?:k|w|m|usd|rmb|cny)?)",
        r"\b([0-9][0-9,]*(?:\.[0-9]+)?\s*(?:usd|rmb|cny|k|w|m))\s*budget\b",
    ]
    for pattern in budget_patterns:
        bm = re.search(pattern, text, flags=re.IGNORECASE)
        if bm:
            budget = str(bm.group(1) or "").strip()
            if budget:
                break

    return destination, duration_days, budget


def _to_budget_number(raw_budget: str) -> float:
    text = str(raw_budget or "").strip().lower().replace(",", "")
    if not text:
        return 0.0
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", text)
    if not match:
        return 0.0
    value = float(match.group(1))
    if any(token in text for token in ("万", "w")):
        value *= 10000.0
    elif text.endswith("k"):
        value *= 1000.0
    return max(0.0, value)


def _build_budget_estimate(
    *,
    budget_raw: str,
    duration_days: int,
    language: str,
) -> dict[str, Any]:
    budget_raw = str(budget_raw or "").strip()
    if not budget_raw:
        return {}

    total_value = _to_budget_number(budget_raw)
    if total_value <= 0:
        return {"total": budget_raw}

    if language == "zh":
        return {
            "total": budget_raw,
            "transport": f"约 ¥{int(total_value * 0.35):,}",
            "accommodation": f"约 ¥{int(total_value * 0.33):,}",
            "food": f"约 ¥{int(total_value * 0.18):,}",
            "activities": f"约 ¥{int(total_value * 0.10):,}",
            "buffer": f"约 ¥{int(total_value * 0.04):,}",
        }

    per_day = total_value / max(1, duration_days)
    return {
        "total": budget_raw,
        "transport": f"about ¥{int(total_value * 0.35):,}",
        "accommodation": f"about ¥{int(total_value * 0.33):,}",
        "food": f"about ¥{int(total_value * 0.18):,}",
        "activities": f"about ¥{int(total_value * 0.10):,}",
        "buffer": f"about ¥{int(total_value * 0.04):,}",
        "avg_per_day": f"about ¥{int(per_day):,}/day",
    }


def _build_fast_tips(
    *,
    user_input: str,
    destination: str,
    interpretation_payload: dict[str, Any],
    language: str,
) -> list[str]:
    tips: list[str] = []
    lower_input = str(user_input or "").lower()
    destination_lc = str(destination or "").lower()

    soon_departure = any(
        token in lower_input
        for token in ("tomorrow", "day after tomorrow", "following day", "tonight", "后天", "明天")
    )
    if soon_departure:
        if language == "zh":
            tips.append("出发时间很近，建议今晚优先锁定机票、首晚住宿和关键交通。")
        else:
            tips.append("Departure is very close. Lock flights, first-night hotel, and key transport tonight.")

    if "new zealand" in destination_lc:
        if language == "zh":
            tips.append("新西兰为左侧行驶，建议提前准备驾照翻译件并熟悉交规。")
        else:
            tips.append("New Zealand drives on the left. Prepare license translation and review road rules.")

    interpretations = interpretation_payload.get("task_result_interpretations", [])
    seen: set[str] = set()

    def normalize_tip(raw: str) -> str:
        text = str(raw or "").strip()
        if not text:
            return ""
        if "http://" in text or "https://" in text:
            return ""
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"^[\-\*\d\.\)\s]+", "", text)
        if len(text) > 120:
            text = text[:117].rstrip() + "..."
        noisy_fragments = ("trip.com", "ctrip", "booking.com", "知乎", "百度", "旅游攻略", "best places to stay")
        if any(fragment in text.lower() for fragment in noisy_fragments):
            return ""
        return text

    if isinstance(interpretations, list):
        for row in interpretations:
            if not isinstance(row, dict):
                continue
            findings = row.get("key_findings", [])
            if not isinstance(findings, list):
                continue
            for item in findings[:4]:
                if not isinstance(item, dict):
                    continue
                finding = normalize_tip(item.get("finding", ""))
                if not finding:
                    continue
                key = finding.lower()
                if key in seen:
                    continue
                seen.add(key)
                tips.append(finding)
                if len(tips) >= 6:
                    return tips
    return tips[:6]


def _collect_place_candidates(
    interpretation_payload: dict[str, Any],
    tool_results: list[ToolExecutionResult],
    banned_terms: set[str] | None = None,
    destination_hint: str = "",
    user_input: str = "",
    language: str | None = None,
) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    banned = {str(item).strip().lower() for item in (banned_terms or set()) if str(item).strip()}
    response_language = language or detect_user_language(user_input or destination_hint, default="en")
    threshold = _safe_float(os.getenv("AGENT_MIN_RELEVANCE_SCORE"), 0.70)
    destination_text = str(destination_hint or "").strip()
    destination_has_cjk = bool(re.search(r"[\u4e00-\u9fff]", destination_text))
    destination_tokens = [token.lower() for token in re.findall(r"[A-Za-z]{3,}", destination_text)]

    def normalize_candidate(raw: str) -> str:
        text = str(raw or "").strip()
        if not text:
            return ""
        text = text.split("：", 1)[0].split("(", 1)[0].split("（", 1)[0].strip()
        text = re.sub(r"\s+", " ", text)
        noisy_marks = (
            "攻略",
            "机票",
            "携程",
            "知乎",
            "专栏",
            "查询",
            "预订",
            "价格",
            "票",
            "自由行",
            "推荐",
            "机场",
            "百度知道",
            "订阅",
            "问答",
            "天气",
            "温度",
            "降雨",
            "未来",
            "时刻表",
            "航班",
            "高铁",
            "火车",
            "实时",
            "最新",
            "智能助手",
            "出发",
            "返程",
        )
        if any(mark in text for mark in noisy_marks):
            return ""
        if re.search(r"\d", text):
            return ""
        if any(term in text for term in ("怎么", "建议", "安排", "行程", "方案")):
            return ""
        if len(text) > 18:
            return ""
        if text.lower() in banned:
            return ""
        if response_language == "en" and not destination_has_cjk and re.search(r"[\u4e00-\u9fff]", text):
            return ""
        return text

    def query_matches_destination(query: str) -> bool:
        query_text = str(query or "").strip()
        if not destination_text:
            return True
        if destination_has_cjk or response_language == "zh":
            return destination_text in query_text
        query_lc = query_text.lower()
        if not destination_tokens:
            return True
        return any(token in query_lc for token in destination_tokens)

    analyses = interpretation_payload.get("task_result_interpretations", [])
    if isinstance(analyses, list):
        for analysis in analyses:
            if not isinstance(analysis, dict):
                continue
            findings = analysis.get("key_findings", [])
            if not isinstance(findings, list):
                continue
            for row in findings:
                if not isinstance(row, dict):
                    continue
                text = str(row.get("finding", "")).strip()
                if not text:
                    continue
                short = normalize_candidate(text)
                key = short.lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                candidates.append(short)
                if len(candidates) >= 30:
                    return candidates

    for result in tool_results:
        if not result.success or not isinstance(result.output, dict):
            continue
        if result.tool in {"weather_search", "transport_search"}:
            continue
        if not query_matches_destination(result.query):
            continue
        rows: list[dict[str, Any]] = []
        if result.tool in {"web_search", "transport_search"}:
            key = "items" if result.tool == "web_search" else "transport_items"
            rows = [row for row in result.output.get(key, []) if isinstance(row, dict)]
            for row in rows[:6]:
                raw_score = row.get("query_relevance")
                score = _safe_float(raw_score, -1.0)
                if score >= 0 and score < threshold:
                    continue
                title = str(row.get("title", "")).strip()
                title = normalize_candidate(title)
                if not title:
                    continue
                key = title.lower()
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(title)
        elif result.tool in {"poi_search", "place_search"}:
            rows = [row for row in result.output.get("pois", []) if isinstance(row, dict)]
            for row in rows[:8]:
                raw_score = row.get("query_relevance")
                score = _safe_float(raw_score, -1.0)
                if score >= 0 and score < threshold:
                    continue
                name = str(row.get("name", "")).strip()
                name = normalize_candidate(name)
                if not name:
                    continue
                key = name.lower()
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(name)
        elif result.tool in {"hotel_area_search", "hotel_search"}:
            rows = [row for row in result.output.get("areas", []) if isinstance(row, dict)]
            for row in rows[:6]:
                raw_score = row.get("query_relevance")
                score = _safe_float(raw_score, -1.0)
                if score >= 0 and score < threshold:
                    continue
                name = str(row.get("name", "")).strip()
                name = normalize_candidate(name)
                if not name:
                    continue
                key = name.lower()
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(name)
        if len(candidates) >= 30:
            break
    return candidates


def _build_fast_itinerary(destination: str, duration_days: int, candidates: list[str], language: str = "en") -> list[dict[str, Any]]:
    if duration_days <= 0:
        return []
    names = [str(item).strip() for item in candidates if str(item).strip()]
    destination_has_cjk = bool(re.search(r"[\u4e00-\u9fff]", destination or ""))
    if language == "en" and not destination_has_cjk:
        names = [item for item in names if not re.search(r"[\u4e00-\u9fff]", item)]
    dedup: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        dedup.append(name)
        if len(dedup) >= 24:
            break
    if not dedup:
        if language == "zh":
            return []
        base = destination.strip() or "Destination"
        dedup = [
            f"{base} City Center",
            f"{base} Waterfront",
            f"{base} Cultural District",
            f"{base} Nature Reserve",
            f"{base} Old Town",
        ]

    def pick(index: int) -> str:
        return dedup[index % len(dedup)]

    itinerary: list[dict[str, Any]] = []
    for day in range(1, duration_days + 1):
        morning_spot = pick((day - 1) * 2)
        afternoon_spot = pick((day - 1) * 2 + 1)
        evening_spot = pick((day - 1) * 2 + 2)
        theme = f"{morning_spot} + {afternoon_spot}"
        if language == "zh":
            morning_text = f"前往 {morning_spot}，建议预留 2-3 小时。"
            afternoon_text = f"继续前往 {afternoon_spot}，根据体力灵活调整。"
            evening_text = f"在 {evening_spot} 附近安排晚餐与夜间散步。"
            foods = [f"{destination or '目的地'} 本地特色餐", f"{destination or '目的地'} 人气小吃"]
            transport_notes = ["优先公共交通或拼车，跨区提前预留通勤时间", "热门点位建议错峰出行"]
            why_text = "同日动线集中，减少往返时间，并保留晚间放松窗口。"
        else:
            morning_text = f"Visit {morning_spot} (about 2-3 hours)."
            afternoon_text = f"Continue to {afternoon_spot}, pacing by energy level."
            evening_text = f"Dinner and a relaxed evening walk around {evening_spot}."
            foods = [f"{destination or 'Destination'} local signature dishes", f"{destination or 'Destination'} popular snacks"]
            transport_notes = ["Use public transport/rideshare first and buffer cross-area transit", "Visit popular places in off-peak windows"]
            why_text = "Concentrated routing reduces backtracking while preserving a relaxed evening slot."
        itinerary.append(
            {
                "day": day,
                "theme": theme,
                "morning": morning_text,
                "afternoon": afternoon_text,
                "evening": evening_text,
                "food_recommendations": foods,
                "transport_notes": transport_notes,
                "why_this_day_works": why_text,
            }
        )
    return itinerary


def _normalize_itinerary_rows(raw_rows: Any, duration_days: int, language: str) -> list[dict[str, Any]]:
    if not isinstance(raw_rows, list):
        return []
    out: list[dict[str, Any]] = []
    fallback_slot = "自由安排" if language == "zh" else "Free block"
    fallback_why = "基于动线与节奏平衡安排。" if language == "zh" else "Balanced by route and pacing."
    for idx, row in enumerate(raw_rows):
        if not isinstance(row, dict):
            continue
        day_raw = row.get("day", idx + 1)
        try:
            day = int(day_raw)
        except Exception:
            day = idx + 1
        if day <= 0:
            day = idx + 1
        theme = str(row.get("theme", "")).strip() or f"Day {day}"
        morning = str(row.get("morning", "")).strip() or fallback_slot
        afternoon = str(row.get("afternoon", "")).strip() or fallback_slot
        evening = str(row.get("evening", "")).strip() or fallback_slot
        why = str(row.get("why_this_day_works", "")).strip() or fallback_why

        foods_raw = row.get("food_recommendations", [])
        if isinstance(foods_raw, list):
            foods = [str(item).strip() for item in foods_raw if str(item).strip()][:5]
        else:
            foods = []
        transport_raw = row.get("transport_notes", [])
        if isinstance(transport_raw, list):
            transports = [str(item).strip() for item in transport_raw if str(item).strip()][:5]
        else:
            transports = []

        out.append(
            {
                "day": day,
                "theme": theme,
                "morning": morning,
                "afternoon": afternoon,
                "evening": evening,
                "food_recommendations": foods,
                "transport_notes": transports,
                "why_this_day_works": why,
            }
        )
        if duration_days > 0 and len(out) >= duration_days:
            break
    return out


def _is_repetitive_itinerary(itinerary: list[dict[str, Any]]) -> bool:
    if not itinerary or len(itinerary) < 3:
        return False
    key_fields = ("morning", "afternoon", "evening", "why_this_day_works")
    repeated_score = 0
    total_fields = 0
    for field in key_fields:
        values: list[str] = []
        for day in itinerary:
            if not isinstance(day, dict):
                continue
            text = str(day.get(field, "")).strip().lower()
            if text:
                values.append(text)
        if not values:
            continue
        total_fields += 1
        unique_ratio = len(set(values)) / max(1, len(values))
        if unique_ratio < 0.55:
            repeated_score += 1
    flat = "\n".join(
        str(day.get(field, "")).strip().lower()
        for day in itinerary
        if isinstance(day, dict)
        for field in key_fields
    )
    template_markers = (
        "pacing by energy level",
        "concentrated routing reduces backtracking",
        "visit popular places in off-peak windows",
        "根据体力灵活调整",
        "减少往返时间，并保留晚间放松窗口",
    )
    marker_hits = sum(flat.count(marker) for marker in template_markers)
    if marker_hits >= max(3, len(itinerary)):
        return True
    return total_fields > 0 and repeated_score >= 2


def _llm_build_fallback_itinerary(
    *,
    user_input: str,
    destination: str,
    duration_days: int,
    language: str,
    candidates: list[str],
    evidence_digest: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not CLIENT.enabled or not destination or duration_days <= 0:
        return []

    condensed_candidates = [str(item).strip() for item in candidates if str(item).strip()][:20]
    prompt = (
        "Generate a practical day-by-day itinerary JSON only.\n"
        "No template boilerplate, no repeated generic phrasing.\n"
        "Use evidence and candidate places as anchors; keep each day distinct.\n"
        "If uncertain, be explicit but still actionable.\n\n"
        "Output schema:\n"
        "{\n"
        '  "itinerary": [\n'
        "    {\n"
        '      "day": number,\n'
        '      "theme": string,\n'
        '      "morning": string,\n'
        '      "afternoon": string,\n'
        '      "evening": string,\n'
        '      "food_recommendations": string[],\n'
        '      "transport_notes": string[],\n'
        '      "why_this_day_works": string\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        f"response_language:\n{language}\n\n"
        f"user_input:\n{user_input}\n\n"
        f"destination:\n{destination}\n\n"
        f"duration_days:\n{duration_days}\n\n"
        f"candidate_places:\n{json.dumps(condensed_candidates, ensure_ascii=False)}\n\n"
        f"evidence_digest:\n{json.dumps(evidence_digest[:6], ensure_ascii=False)}\n\n"
        "Output JSON only."
    )
    messages = CLIENT.build_messages(
        system_prompt="You are a travel itinerary generation model. Output JSON only.",
        developer_prompt="Avoid repetitive sentence templates. Keep each day concrete and distinct.",
        user_prompt=prompt,
        conversation_history=None,
    )
    timeout_backup = CLIENT.timeout_sec
    try:
        CLIENT.timeout_sec = _safe_float(
            os.getenv("AGENT_FALLBACK_ITINERARY_TIMEOUT_SEC"),
            14.0 if duration_days >= 7 else 10.0,
        )
        result = CLIENT.json_completion(
            messages=messages,
            temperature=0.2,
            max_tokens=_safe_int(
                os.getenv("AGENT_FALLBACK_ITINERARY_MAX_TOKENS"),
                2400 if duration_days >= 10 else (1900 if duration_days >= 7 else 1500),
            ),
            reasoning_effort=None,
            extra_body={"thinking": {"type": "disabled"}},
            model=(os.getenv("AGENT_FALLBACK_ITINERARY_MODEL") or "").strip() or "deepseek-v4-flash",
            allow_model_fallback=True,
        )
    finally:
        CLIENT.timeout_sec = timeout_backup

    if not result.get("ok"):
        return []
    data = result.get("json", {})
    if not isinstance(data, dict):
        return []
    rows = _normalize_itinerary_rows(data.get("itinerary", []), duration_days=duration_days, language=language)
    if len(rows) < max(2, min(duration_days, 4)):
        return []
    if _is_repetitive_itinerary(rows):
        return []
    return rows[:duration_days]


def _build_fast_markdown(frontend_json: dict[str, Any], user_input: str) -> str:
    language = detect_user_language(user_input, default="en")
    destination = str(frontend_json.get("destination", "")).strip()
    duration_days = int(frontend_json.get("duration_days", 0) or 0)
    assumptions = frontend_json.get("assumptions", [])
    missing_information = frontend_json.get("missing_information", [])
    itinerary = frontend_json.get("itinerary", [])
    budget = frontend_json.get("budget_estimate", {})
    hotel_areas = frontend_json.get("hotel_area_suggestions", [])
    tips = frontend_json.get("tips", [])
    follow_up = frontend_json.get("follow_up_questions", [])
    has_itinerary = isinstance(itinerary, list) and len(itinerary) > 0

    if language == "zh":
        lines: list[str] = [
            "### 我理解你的需求",
            f"- 原始请求：{user_input}",
            f"- 目的地：{destination or '待确认'}",
            f"- 行程天数：{duration_days if duration_days > 0 else '待确认'}",
            "",
        ]
        if isinstance(assumptions, list) and assumptions:
            lines.append("### 我基于以下假设生成方案")
            for item in assumptions[:8]:
                text = str(item).strip()
                if text:
                    lines.append(f"- {text}")
            lines.append("")
        if isinstance(missing_information, list) and missing_information:
            lines.append("### 仍待确认的信息")
            for item in missing_information[:8]:
                text = str(item).strip()
                if text:
                    lines.append(f"- {text}")
            lines.append("")
        if has_itinerary:
            lines.append(f"### {destination or '目的地'}{duration_days if duration_days > 0 else len(itinerary)}天行程建议")
            for row in itinerary:
                if not isinstance(row, dict):
                    continue
                day = int(row.get("day", 0) or 0)
                theme = str(row.get("theme", "")).strip()
                lines.append(f"- **Day {day if day > 0 else '?'}：{theme or '当日安排'}**")
                morning = str(row.get("morning", "")).strip()
                afternoon = str(row.get("afternoon", "")).strip()
                evening = str(row.get("evening", "")).strip()
                if morning:
                    lines.append(f"  - **上午**：{morning}")
                if afternoon:
                    lines.append(f"  - **下午**：{afternoon}")
                if evening:
                    lines.append(f"  - **晚上**：{evening}")
                foods = row.get("food_recommendations", [])
                if isinstance(foods, list) and foods:
                    lines.append(f"  - **美食**：{'；'.join([str(x).strip() for x in foods[:4] if str(x).strip()])}")
                transports = row.get("transport_notes", [])
                if isinstance(transports, list) and transports:
                    lines.append(f"  - **交通**：{'；'.join([str(x).strip() for x in transports[:4] if str(x).strip()])}")
                why = str(row.get("why_this_day_works", "")).strip()
                if why:
                    lines.append(f"  - **安排理由**：{why}")
            lines.append("")
        else:
            lines.append("当前尚未返回可靠的分日行程数据，我不会用模板强行生成结果。")
            lines.append("")

        if isinstance(budget, dict) and budget:
            lines.append("### 预算粗估")
            lines.append("| 项目 | 预算参考 |")
            lines.append("|---|---|")
            for key, value in list(budget.items())[:12]:
                k = str(key).strip()
                v = str(value).strip()
                if k and v:
                    lines.append(f"| {k} | {v} |")
            lines.append("")

        if isinstance(hotel_areas, list) and hotel_areas:
            lines.append("### 住宿区域建议")
            for item in hotel_areas[:4]:
                if not isinstance(item, dict):
                    continue
                area = str(item.get("area", "") or item.get("name", "")).strip()
                if area:
                    lines.append(f"- **{area}**")
                pros = item.get("pros", [])
                if isinstance(pros, list) and pros:
                    lines.append(f"  - **优点**：{'；'.join([str(x).strip() for x in pros[:3] if str(x).strip()])}")
                cons = item.get("cons", [])
                if isinstance(cons, list) and cons:
                    lines.append(f"  - **注意**：{'；'.join([str(x).strip() for x in cons[:3] if str(x).strip()])}")
            lines.append("")

        if isinstance(tips, list) and tips:
            lines.append("### 实用建议")
            for item in tips[:6]:
                text = str(item).strip()
                if text:
                    lines.append(f"- {text}")
            lines.append("")

        suggestions = [str(item).strip() for item in (follow_up if isinstance(follow_up, list) else []) if str(item).strip()]
        if suggestions:
            lines.append("### 你可以继续告诉我")
            for item in suggestions[:2]:
                lines.append(f"- {item}")

        return "\n".join(lines).strip()

    lines = [
        "### I understand your request",
        f"- Original request: {user_input}",
        f"- Destination: {destination or 'TBD'}",
        f"- Trip length: {duration_days if duration_days > 0 else 'TBD'} days",
        "",
    ]
    if isinstance(assumptions, list) and assumptions:
        lines.append("### Assumptions")
        for item in assumptions[:8]:
            text = str(item).strip()
            if text:
                lines.append(f"- {text}")
        lines.append("")
    if isinstance(missing_information, list) and missing_information:
        lines.append("### Still missing")
        for item in missing_information[:8]:
            text = str(item).strip()
            if text:
                lines.append(f"- {text}")
        lines.append("")
    if has_itinerary:
        lines.append(f"### {destination or 'Destination'} {duration_days if duration_days > 0 else len(itinerary)}-day itinerary")
        for row in itinerary:
            if not isinstance(row, dict):
                continue
            day = int(row.get("day", 0) or 0)
            theme = str(row.get("theme", "")).strip()
            lines.append(f"- **Day {day if day > 0 else '?'}: {theme or 'Daily arrangement'}**")
            morning = str(row.get("morning", "")).strip()
            afternoon = str(row.get("afternoon", "")).strip()
            evening = str(row.get("evening", "")).strip()
            if morning:
                lines.append(f"  - **Morning:** {morning}")
            if afternoon:
                lines.append(f"  - **Afternoon:** {afternoon}")
            if evening:
                lines.append(f"  - **Evening:** {evening}")
            foods = row.get("food_recommendations", [])
            if isinstance(foods, list) and foods:
                lines.append(f"  - **Food:** {'; '.join([str(x).strip() for x in foods[:4] if str(x).strip()])}")
            transports = row.get("transport_notes", [])
            if isinstance(transports, list) and transports:
                lines.append(f"  - **Transport:** {'; '.join([str(x).strip() for x in transports[:4] if str(x).strip()])}")
            why = str(row.get("why_this_day_works", "")).strip()
            if why:
                lines.append(f"  - **Why this works:** {why}")
        lines.append("")
    else:
        lines.append("Reliable day-by-day itinerary data is currently unavailable, so I will not fabricate a template plan.")
        lines.append("")
    if isinstance(budget, dict) and budget:
        lines.append("### Budget estimate")
        lines.append("| Category | Estimate |")
        lines.append("|---|---|")
        for key, value in list(budget.items())[:12]:
            k = str(key).strip()
            v = str(value).strip()
            if k and v:
                lines.append(f"| {k} | {v} |")
        lines.append("")
    if isinstance(hotel_areas, list) and hotel_areas:
        lines.append("### Hotel area suggestions")
        for item in hotel_areas[:4]:
            if not isinstance(item, dict):
                continue
            area = str(item.get("area", "") or item.get("name", "")).strip()
            if area:
                lines.append(f"- **{area}**")
            pros = item.get("pros", [])
            if isinstance(pros, list) and pros:
                lines.append(f"  - **Pros:** {'; '.join([str(x).strip() for x in pros[:3] if str(x).strip()])}")
            cons = item.get("cons", [])
            if isinstance(cons, list) and cons:
                lines.append(f"  - **Cons:** {'; '.join([str(x).strip() for x in cons[:3] if str(x).strip()])}")
        lines.append("")
    if isinstance(tips, list) and tips:
        lines.append("### Tips")
        for item in tips[:6]:
            text = str(item).strip()
            if text:
                lines.append(f"- {text}")
        lines.append("")

    suggestions = [str(item).strip() for item in (follow_up if isinstance(follow_up, list) else []) if str(item).strip()]
    if suggestions:
        lines.append("### Next, you can tell me")
        for item in suggestions[:2]:
            lines.append(f"- {item}")

    return "\n".join(lines).strip()

def _polish_markdown_with_llm(
    user_input: str,
    frontend_json: dict[str, Any],
    sources: list[dict[str, str]],
    fallback_markdown: str,
    time_context: dict[str, str],
    force_enable: bool = False,
) -> str:
    if not CLIENT.enabled:
        return fallback_markdown

    if (not force_enable) and str(os.getenv("AGENT_ENABLE_MARKDOWN_POLISH", "0")).strip().lower() not in {"1", "true", "yes", "on"}:
        return fallback_markdown

    itinerary = frontend_json.get("itinerary", [])
    days = len(itinerary) if isinstance(itinerary, list) else 0
    # Keep 3-day plan ultra-fast to satisfy latency target.
    if days and days <= 3 and not force_enable:
        return fallback_markdown

    source_rows = []
    for item in sources[:10]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        url = str(item.get("url", "")).strip()
        if title and url:
            source_rows.append({"title": title, "url": url})

    response_language = detect_user_language(user_input, default="en")
    prompt = (
        "Rewrite the structured travel output into concise, readable Markdown.\n"
        "Requirements:\n"
        "1) Use natural and direct style.\n"
        "2) Keep sections stable: need understanding, assumptions, day-by-day plan, budget table, sources, follow-up questions.\n"
        "3) Budget must be rendered as a Markdown table.\n"
        "4) Each day should stay within 6 lines.\n"
        "5) Do not invent new facts; only use provided JSON and sources.\n"
        "6) Resolve relative dates (today/tomorrow/day after tomorrow) using the provided time context.\n"
        "7) Output language must be exactly response_language.\n\n"
        f"response_language:\n{response_language}\n\n"
        f"User input:\n{user_input}\n\n"
        f"Time context:\n{json.dumps(time_context, ensure_ascii=False)}\n\n"
        f"Structured plan JSON:\n{json.dumps(frontend_json, ensure_ascii=False)}\n\n"
        f"Sources:\n{json.dumps(source_rows, ensure_ascii=False)}\n\n"
        "Output Markdown only."
    )
    messages = CLIENT.build_messages(
        system_prompt="You are a travel-plan markdown editor. Output Markdown only.",
        developer_prompt="No JSON and no extra explanations.",
        user_prompt=prompt,
        conversation_history=None,
    )
    max_tokens = _safe_int(os.getenv("AGENT_POLISH_MAX_TOKENS"), 1200)
    timeout_backup = CLIENT.timeout_sec
    try:
        CLIENT.timeout_sec = _safe_float(os.getenv("AGENT_POLISH_TIMEOUT_SEC"), 5.0)
        result = CLIENT.chat_completion(
            messages=messages,
            temperature=0.1,
            max_tokens=max_tokens,
            reasoning_effort=os.getenv("AGENT_REASONING_EFFORT", "medium"),
            extra_body=(
                {"thinking": {"type": "enabled"}}
                if _enable_thinking() and _speed_mode() != "fast"
                else {"thinking": {"type": "disabled"}}
            ),
            model=(os.getenv("AGENT_POLISH_MODEL") or "").strip() or None,
            allow_model_fallback=False,
        )
    finally:
        CLIENT.timeout_sec = timeout_backup

    if not result.get("ok"):
        return fallback_markdown
    text = str(result.get("content", "") or "").strip()
    if not text:
        return fallback_markdown
    return text


def _build_fast_payload(
    user_input: str,
    requirement_understanding: dict[str, Any],
    search_plan: list[SearchTask],
    tool_results: list[ToolExecutionResult],
    interpretation_payload: dict[str, Any],
    response_stage: str,
    llm_error: str | None = None,
    allow_llm_enrichment: bool = True,
) -> dict[str, Any]:
    destination, duration_days, budget = _extract_destination_duration_budget(requirement_understanding)
    if not destination or duration_days <= 0 or not budget:
        d2, days2, b2 = _extract_destination_duration_budget_from_text(user_input)
        if not destination and d2:
            destination = d2
        if duration_days <= 0 and days2 > 0:
            duration_days = days2
        if not budget and b2:
            budget = b2
    language = detect_user_language(user_input, default="en")
    assumptions = requirement_understanding.get("default_assumptions", [])
    if not isinstance(assumptions, list):
        assumptions = []
    assumptions = [str(item).strip() for item in assumptions if str(item).strip()]
    if not assumptions:
        if language == "zh":
            assumptions = ["优先保证行程可执行和交通可达。", "每日节奏默认中等强度。"]
        else:
            assumptions = ["Prioritize feasibility and realistic transport timing.", "Assume moderate travel pace."]

    missing_rows = requirement_understanding.get("missing_information", [])
    missing_information: list[str] = []
    if isinstance(missing_rows, list):
        for row in missing_rows:
            if not isinstance(row, dict):
                continue
            field = str(row.get("field", "")).strip()
            if field:
                missing_information.append(field)

    follow_up_questions = requirement_understanding.get("clarifying_questions", [])
    if not isinstance(follow_up_questions, list):
        follow_up_questions = []
    follow_up_questions = [str(item).strip() for item in follow_up_questions if str(item).strip()]
    if destination and duration_days > 0:
        generic_markers = {
            "你的目的地是哪里",
            "计划出行几天",
            "预算大概是多少",
            "what is your destination",
            "how many days will you travel",
            "what is your approximate budget",
            "what is your budget",
        }
        filtered_questions: list[str] = []
        for question in follow_up_questions:
            ql = question.strip().lower()
            if any(marker in ql for marker in generic_markers):
                continue
            filtered_questions.append(question)
        follow_up_questions = filtered_questions

    final_days = max(0, min(duration_days, 14)) if duration_days > 0 else 0
    banned_terms = {destination.strip().lower()} if destination else set()
    origin_hint = str(extract_origin_city(user_input) or "").strip().lower()
    if origin_hint:
        banned_terms.add(origin_hint)
    candidates = _collect_place_candidates(
        interpretation_payload=interpretation_payload,
        tool_results=tool_results,
        banned_terms=banned_terms,
        destination_hint=destination or "",
        user_input=user_input,
        language=language,
    )
    evidence_digest = _collect_evidence_digest(
        search_plan=search_plan,
        tool_results=tool_results,
        interpretation_payload=interpretation_payload,
    )
    if allow_llm_enrichment and final_days > 0 and evidence_digest:
        llm_places = _llm_select_place_candidates(
            user_input=user_input,
            destination=destination or "",
            duration_days=final_days,
            evidence_digest=evidence_digest,
        )
        if llm_places:
            candidates = llm_places
    itinerary: list[dict[str, Any]] = []
    if final_days > 0 and destination:
        if allow_llm_enrichment and CLIENT.enabled:
            itinerary = _llm_build_fallback_itinerary(
                user_input=user_input,
                destination=destination or "",
                duration_days=final_days,
                language=language,
                candidates=candidates,
                evidence_digest=evidence_digest,
            )
        # Never auto-fill with rigid templates when the LLM path fails.
        # If fallback LLM cannot produce reliable day plans, keep itinerary empty and surface uncertainty.
        if not itinerary and not CLIENT.enabled:
            itinerary = _build_fast_itinerary(destination or "", final_days, candidates, language=language)
    hotel_suggestions: list[dict[str, Any]] = []

    budget_estimate = _build_budget_estimate(
        budget_raw=budget,
        duration_days=final_days if final_days > 0 else max(1, duration_days),
        language=language,
    )

    tips = _build_fast_tips(
        user_input=user_input,
        destination=destination or "",
        interpretation_payload=interpretation_payload,
        language=language,
    )

    frontend_json = {
        "destination": destination or ("待确认目的地" if language == "zh" else "TBD destination"),
        "duration_days": final_days,
        "assumptions": assumptions,
        "missing_information": missing_information,
        "itinerary": itinerary,
        "hotel_area_suggestions": hotel_suggestions,
        "budget_estimate": budget_estimate,
        "tips": tips,
        "follow_up_questions": follow_up_questions,
    }
    markdown_answer = _build_fast_markdown(frontend_json, user_input=user_input)
    payload = {
        "requirement_understanding": requirement_understanding,
        "search_tasks": [task.to_dict() for task in search_plan],
        "search_plan": [task.to_dict() for task in search_plan],
        "search_results": [result.to_dict() for result in tool_results],
        "search_results_interpretation": interpretation_payload.get("task_result_interpretations", []),
        "sources": _normalize_sources(interpretation_payload.get("sources", [])),
        "frontend_json": frontend_json,
        "markdown_answer": markdown_answer,
        "response_stage": response_stage,
    }
    if llm_error:
        payload["llm_error"] = _truncate_text(llm_error, 300)
    return payload


def _fallback_payload(
    user_input: str,
    requirement_understanding: dict[str, Any],
    search_plan: list[SearchTask],
    tool_results: list[ToolExecutionResult],
    interpretation_payload: dict[str, Any],
    time_context: dict[str, str],
    error_message: str | None = None,
    response_stage: str = "llm_fallback",
    allow_llm_enrichment: bool = False,
) -> dict[str, Any]:
    payload = _build_fast_payload(
        user_input=user_input,
        requirement_understanding=requirement_understanding,
        search_plan=search_plan,
        tool_results=tool_results,
        interpretation_payload=interpretation_payload,
        response_stage=response_stage,
        llm_error=error_message,
        allow_llm_enrichment=allow_llm_enrichment,
    )
    markdown = _prepend_time_anchor_if_needed(
        str(payload.get("markdown_answer", "")).strip(),
        user_input=user_input,
        time_context=time_context,
    )
    payload["markdown_answer"] = markdown
    return payload


def _build_final_user_prompt(
    user_input: str,
    requirement_understanding: dict[str, Any],
    search_plan: list[SearchTask],
    interpretation_payload: dict[str, Any],
    evidence_digest: list[dict[str, Any]],
    conversation_history: list[dict[str, str]] | None,
    user_preferences: dict[str, Any] | None,
    time_context: dict[str, str],
) -> str:
    light_tasks = [
        {
            "task_id": task.task_id,
            "information_need": task.information_need,
            "tool": task.tool,
            "freshness": task.freshness,
            "priority": task.priority,
        }
        for task in search_plan[:6]
    ]
    compact_sources = _normalize_sources(interpretation_payload.get("sources", []))[:6]

    response_language = detect_user_language(user_input, default="en")
    return (
        "User raw input:\n"
        f"{user_input}\n\n"
        "response_language (must be followed in markdown_answer and free-text fields):\n"
        f"{response_language}\n\n"
        "Current time context (must be used to resolve relative dates):\n"
        f"{json.dumps(time_context, ensure_ascii=False)}\n\n"
        "Requirement understanding JSON:\n"
        f"{json.dumps(requirement_understanding, ensure_ascii=False)}\n\n"
        "Search tasks JSON (compact):\n"
        f"{json.dumps(light_tasks, ensure_ascii=False)}\n\n"
        "Evidence digest JSON (high-signal only):\n"
        f"{json.dumps(evidence_digest[:6], ensure_ascii=False)}\n\n"
        "Citable sources:\n"
        f"{json.dumps(compact_sources, ensure_ascii=False)}\n\n"
        "Conversation context:\n"
        f"{json.dumps((conversation_history or [])[-2:], ensure_ascii=False)}\n\n"
        "Additional context:\n"
        f"{json.dumps({'user_preferences': user_preferences or {}}, ensure_ascii=False)}\n\n"
        "Output JSON only."
    )


def _quick_final_rescue(
    user_input: str,
    requirement_understanding: dict[str, Any],
    search_plan: list[SearchTask],
    interpretation_payload: dict[str, Any],
    time_context: dict[str, str],
    evidence_digest: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if not CLIENT.enabled:
        return None

    response_language = detect_user_language(user_input, default="en")
    light_tasks = [
        {
            "task_id": task.task_id,
            "information_need": task.information_need,
            "tool": task.tool,
            "priority": task.priority,
        }
        for task in search_plan[:4]
    ]
    sources = _normalize_sources(interpretation_payload.get("sources", []))[:6]
    compact_evidence = list(evidence_digest or [])[:6]

    prompt = (
        "Build a valid final travel-plan JSON quickly.\n"
        "Focus on frontend_json completeness and day-by-day itinerary quality.\n"
        "Use concise text. Do not output extra keys.\n\n"
        f"response_language:\n{response_language}\n\n"
        f"user_input:\n{user_input}\n\n"
        f"time_context:\n{json.dumps(time_context, ensure_ascii=False)}\n\n"
        f"requirement_understanding:\n{json.dumps(requirement_understanding, ensure_ascii=False)}\n\n"
        f"search_tasks:\n{json.dumps(light_tasks, ensure_ascii=False)}\n\n"
        f"evidence_digest:\n{json.dumps(compact_evidence, ensure_ascii=False)}\n\n"
        f"sources:\n{json.dumps(sources, ensure_ascii=False)}\n\n"
        "Output JSON only."
    )
    messages = CLIENT.build_messages(
        system_prompt="You are a travel final-planner model. Output JSON only.",
        developer_prompt=PROMPT_FINAL_PLANNER,
        user_prompt=prompt,
        conversation_history=None,
    )

    timeout_backup = CLIENT.timeout_sec
    try:
        CLIENT.timeout_sec = _safe_float(os.getenv("AGENT_FINAL_RESCUE_TIMEOUT_SEC"), 26.0)
        result = CLIENT.json_completion(
            messages=messages,
            temperature=0.0,
            max_tokens=_safe_int(os.getenv("AGENT_FINAL_RESCUE_MAX_TOKENS"), 3200),
            reasoning_effort=None,
            extra_body={"thinking": {"type": "disabled"}},
            model=(os.getenv("AGENT_FINAL_RESCUE_MODEL") or "").strip() or "deepseek-v4-flash",
            allow_model_fallback=True,
        )
    finally:
        CLIENT.timeout_sec = timeout_backup

    if not result.get("ok"):
        return None
    data = result.get("json", {})
    if not isinstance(data, dict):
        return None

    try:
        validated = validate_final_answer(
            data,
            requirement_understanding=requirement_understanding,
            user_input=user_input,
        )
    except Exception:
        return None

    frontend_json = validated.frontend_json.model_dump()
    itinerary_rows = frontend_json.get("itinerary", [])
    if not isinstance(itinerary_rows, list) or not itinerary_rows:
        return None

    markdown_answer = str(validated.markdown_answer or "").strip()
    if not markdown_answer or _looks_placeholder_markdown(markdown_answer):
        markdown_answer = _build_fast_markdown(frontend_json, user_input=user_input)
    markdown_answer = _prepend_time_anchor_if_needed(
        markdown_answer,
        user_input=user_input,
        time_context=time_context,
    )

    return {
        "frontend_json": frontend_json,
        "markdown_answer": markdown_answer,
        "response_stage": "finalized_by_llm_rescue",
    }


def _compact_output_items(output: dict[str, Any], tool: str) -> list[dict[str, str]]:
    if not isinstance(output, dict):
        return []
    if tool == "web_search":
        rows = output.get("items", [])
    elif tool == "transport_search":
        rows = output.get("transport_items", [])
    elif tool in {"poi_search", "place_search"}:
        rows = output.get("pois", [])
    elif tool in {"hotel_area_search", "hotel_search"}:
        rows = output.get("areas", [])
    elif tool == "weather_search":
        rows = output.get("forecast", [])
    else:
        rows = []
    if not isinstance(rows, list):
        return []
    out: list[dict[str, str]] = []
    for row in rows[:4]:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title", "") or row.get("name", "") or row.get("date", "")).strip()
        snippet = str(row.get("snippet", "") or row.get("address", "")).strip()
        url = str(row.get("url", "")).strip()
        if not (title or snippet or url):
            continue
        out.append(
            {
                "title": _truncate_text(title, 120),
                "snippet": _truncate_text(snippet, 180),
                "url": url,
            }
        )
    return out


def _collect_evidence_digest(
    search_plan: list[SearchTask],
    tool_results: list[ToolExecutionResult],
    interpretation_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    digest: list[dict[str, Any]] = []
    interp_rows = interpretation_payload.get("task_result_interpretations", [])
    interp_by_task: dict[str, dict[str, Any]] = {}
    if isinstance(interp_rows, list):
        for row in interp_rows:
            if not isinstance(row, dict):
                continue
            task_id = str(row.get("task_id", "")).strip()
            if task_id:
                interp_by_task[task_id] = row

    results_by_task: dict[str, list[ToolExecutionResult]] = {}
    for item in tool_results:
        results_by_task.setdefault(item.task_id, []).append(item)

    for task in search_plan[:8]:
        task_id = task.task_id
        row: dict[str, Any] = {
            "task_id": task_id,
            "information_need": task.information_need,
            "tool": task.tool,
            "evidence": [],
            "sources": [],
        }
        interp = interp_by_task.get(task_id, {})
        findings = interp.get("key_findings", []) if isinstance(interp, dict) else []
        if isinstance(findings, list):
            for finding in findings[:4]:
                if not isinstance(finding, dict):
                    continue
                row["evidence"].append(
                    {
                        "finding": _truncate_text(finding.get("finding", ""), 120),
                        "how_to_use": _truncate_text(finding.get("how_to_use", ""), 80),
                        "source": _truncate_text(finding.get("source", ""), 72),
                    }
                )

        candidates = results_by_task.get(task_id, [])
        for result_item in candidates[:4]:
            if not result_item.success:
                continue
            items = _compact_output_items(result_item.output, result_item.tool)
            for item in items[:2]:
                row["sources"].append(item)
                if len(row["sources"]) >= 4:
                    break
            if len(row["sources"]) >= 4:
                break

        if row["evidence"] or row["sources"]:
            digest.append(row)
    return digest


def _llm_select_place_candidates(
    user_input: str,
    destination: str,
    duration_days: int,
    evidence_digest: list[dict[str, Any]],
) -> list[str]:
    if not CLIENT.enabled or not evidence_digest:
        return []

    prompt = (
        "Extract concrete attractions/areas suitable for itinerary scheduling.\n"
        "Rules:\n"
        "1) Keep only place names (no prices, no travel websites, no generic phrases).\n"
        "2) Exclude origin city if it is not a visit destination.\n"
        "3) Prefer places in destination country/city context.\n"
        "4) Keep natural route order when possible.\n"
        "5) Prioritize evidence; if evidence is sparse, infer a classic route for the destination.\n"
        "6) Output JSON only.\n\n"
        "Output schema:\n"
        "{\n"
        '  "places": string[]\n'
        "}\n\n"
        f"user_input:\n{user_input}\n\n"
        f"destination:\n{destination}\n\n"
        f"origin_city:\n{extract_origin_city(user_input) or ''}\n\n"
        f"duration_days:\n{duration_days}\n\n"
        f"evidence_digest:\n{json.dumps(evidence_digest[:6], ensure_ascii=False)}\n\n"
        "Output JSON only."
    )
    messages = CLIENT.build_messages(
        system_prompt="You are a travel evidence extractor. Output JSON only.",
        developer_prompt="Return only concrete place names in order.",
        user_prompt=prompt,
        conversation_history=None,
    )

    timeout_backup = CLIENT.timeout_sec
    try:
        CLIENT.timeout_sec = _safe_float(os.getenv("AGENT_PLACE_EXTRACT_TIMEOUT_SEC"), 6.0)
        result = CLIENT.json_completion(
            messages=messages,
            temperature=0.0,
            max_tokens=_safe_int(os.getenv("AGENT_PLACE_EXTRACT_MAX_TOKENS"), 320),
            reasoning_effort=None,
            extra_body={"thinking": {"type": "disabled"}},
            model=(os.getenv("AGENT_PLACE_EXTRACT_MODEL") or "").strip() or "deepseek-v4-flash",
            allow_model_fallback=False,
        )
    finally:
        CLIENT.timeout_sec = timeout_backup

    if not result.get("ok"):
        return []
    payload = result.get("json", {})
    if not isinstance(payload, dict):
        return []
    rows = payload.get("places", [])
    if not isinstance(rows, list):
        return []

    out: list[str] = []
    seen: set[str] = set()
    dest_lc = str(destination or "").strip().lower()
    origin_lc = str(extract_origin_city(user_input) or "").strip().lower()
    for item in rows:
        name = str(item or "").strip()
        if not name:
            continue
        if len(name) > 28:
            continue
        lc = name.lower()
        if lc in seen:
            continue
        if dest_lc and lc == dest_lc:
            continue
        if origin_lc and lc == origin_lc:
            continue
        seen.add(lc)
        out.append(name)
        if len(out) >= 20:
            break

    min_needed = max(4, min(10, duration_days))
    if len(out) >= min_needed:
        return out

    # Sparse-evidence fallback: ask for canonical route anchors for the destination.
    enrich_prompt = (
        "List iconic places suitable for building a multi-day travel itinerary.\n"
        "Output JSON only.\n"
        "Schema: {\"places\": string[]}\n"
        "Rules: place names only, no generic labels, no websites, no prices.\n\n"
        f"destination:\n{destination}\n\n"
        f"origin_city:\n{extract_origin_city(user_input) or ''}\n\n"
        f"duration_days:\n{duration_days}\n\n"
        "Output JSON only."
    )
    enrich_messages = CLIENT.build_messages(
        system_prompt="You are a travel destination spot suggester. Output JSON only.",
        developer_prompt="Return place names only.",
        user_prompt=enrich_prompt,
        conversation_history=None,
    )
    timeout_backup = CLIENT.timeout_sec
    try:
        CLIENT.timeout_sec = _safe_float(os.getenv("AGENT_PLACE_EXTRACT_TIMEOUT_SEC"), 6.0)
        enrich = CLIENT.json_completion(
            messages=enrich_messages,
            temperature=0.0,
            max_tokens=_safe_int(os.getenv("AGENT_PLACE_EXTRACT_MAX_TOKENS"), 320),
            reasoning_effort=None,
            extra_body={"thinking": {"type": "disabled"}},
            model=(os.getenv("AGENT_PLACE_EXTRACT_MODEL") or "").strip() or "deepseek-v4-flash",
            allow_model_fallback=False,
        )
    finally:
        CLIENT.timeout_sec = timeout_backup

    if not enrich.get("ok"):
        return out
    enrich_payload = enrich.get("json", {})
    if not isinstance(enrich_payload, dict):
        return out
    enrich_rows = enrich_payload.get("places", [])
    if not isinstance(enrich_rows, list):
        return out
    for item in enrich_rows:
        name = str(item or "").strip()
        if not name or len(name) > 28:
            continue
        lc = name.lower()
        if lc in seen:
            continue
        if dest_lc and lc == dest_lc:
            continue
        if origin_lc and lc == origin_lc:
            continue
        seen.add(lc)
        out.append(name)
        if len(out) >= 20:
            break
    return out


def _parse_markdown_itinerary(markdown: str, destination: str, language: str) -> list[dict[str, Any]]:
    text = str(markdown or "").strip()
    if not text:
        return []
    lines = text.splitlines()
    blocks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    day_patterns = (
        re.compile(r"^\s*(?:[-*]\s*)?Day\s*([0-9]{1,2})\s*[:：\-]?\s*(.*)$", flags=re.IGNORECASE),
        re.compile(r"^\s*(?:[-*]\s*)?第\s*([0-9]{1,2})\s*天\s*[:：\-]?\s*(.*)$"),
    )

    for raw_line in lines:
        line = raw_line.rstrip()
        line_for_match = line.strip()
        if line_for_match.startswith("#"):
            line_for_match = line_for_match.lstrip("#").strip()
        line_for_match = re.sub(r"^\*+", "", line_for_match)
        line_for_match = re.sub(r"\*+$", "", line_for_match).strip()
        matched = None
        for pattern in day_patterns:
            m = pattern.match(line_for_match)
            if m:
                matched = m
                break
        if matched:
            if current:
                blocks.append(current)
            current = {
                "day": int(matched.group(1)),
                "theme": str(matched.group(2) or "").strip(),
                "lines": [],
            }
            continue
        if current is not None:
            current["lines"].append(line)
    if current:
        blocks.append(current)

    if not blocks:
        return []

    out: list[dict[str, Any]] = []
    for block in blocks:
        day = int(block.get("day", 0) or 0)
        theme = str(block.get("theme", "")).strip() or (f"Day {day}" if language != "zh" else f"第{day}天")
        body_lines = [str(item).strip(" -*\t") for item in block.get("lines", []) if str(item).strip()]

        morning = ""
        afternoon = ""
        evening = ""
        why = ""
        foods: list[str] = []
        transport: list[str] = []
        free_slots: list[str] = []

        for line in body_lines:
            if not line:
                continue
            if re.match(r"^(上午|早上|Morning)\s*[:：]", line, flags=re.IGNORECASE):
                morning = re.split(r"[:：]", line, maxsplit=1)[-1].strip()
                continue
            if re.match(r"^(下午|Afternoon)\s*[:：]", line, flags=re.IGNORECASE):
                afternoon = re.split(r"[:：]", line, maxsplit=1)[-1].strip()
                continue
            if re.match(r"^(晚上|夜间|Evening|Night)\s*[:：]", line, flags=re.IGNORECASE):
                evening = re.split(r"[:：]", line, maxsplit=1)[-1].strip()
                continue
            if re.match(r"^(安排理由|理由|Why|Why this day works)\s*[:：]", line, flags=re.IGNORECASE):
                why = re.split(r"[:：]", line, maxsplit=1)[-1].strip()
                continue
            if re.match(r"^(美食|餐饮|Food)\s*[:：]", line, flags=re.IGNORECASE):
                value = re.split(r"[:：]", line, maxsplit=1)[-1].strip()
                if value:
                    foods.extend([item.strip() for item in re.split(r"[；;、,，]", value) if item.strip()])
                continue
            if re.match(r"^(交通|出行|Transport)\s*[:：]", line, flags=re.IGNORECASE):
                value = re.split(r"[:：]", line, maxsplit=1)[-1].strip()
                if value:
                    transport.extend([item.strip() for item in re.split(r"[；;、,，]", value) if item.strip()])
                continue
            free_slots.append(line)

        if not morning and free_slots:
            morning = free_slots.pop(0)
        if not afternoon and free_slots:
            afternoon = free_slots.pop(0)
        if not evening and free_slots:
            evening = free_slots.pop(0)
        if not why and free_slots:
            why = free_slots[-1]

        fallback_slot = "自由安排" if language == "zh" else "Free block"
        fallback_why = "基于动线与节奏平衡安排。" if language == "zh" else "Balanced by route efficiency and pacing."
        out.append(
            {
                "day": day if day > 0 else len(out) + 1,
                "theme": theme,
                "morning": morning or fallback_slot,
                "afternoon": afternoon or fallback_slot,
                "evening": evening or fallback_slot,
                "food_recommendations": foods[:4],
                "transport_notes": transport[:4],
                "why_this_day_works": why or fallback_why,
            }
        )
    return out


def _markdown_rescue_max_tokens(duration_days: int) -> int:
    env = _safe_int(os.getenv("AGENT_FINAL_MARKDOWN_RESCUE_MAX_TOKENS"), 0)
    if env > 0:
        return env
    if duration_days >= 10:
        return 3200
    if duration_days >= 7:
        return 2600
    if duration_days >= 4:
        return 2000
    return 1600


def _markdown_rescue_timeout_sec(duration_days: int) -> float:
    env = _safe_float(os.getenv("AGENT_FINAL_MARKDOWN_RESCUE_TIMEOUT_SEC"), 0.0)
    if env > 0:
        return env
    if duration_days >= 10:
        return 28.0
    if duration_days >= 7:
        return 24.0
    return 16.0


def _merge_non_overlapping_markdown(base: str, continuation: str) -> str:
    a = str(base or "")
    b = str(continuation or "")
    if not a:
        return b
    if not b:
        return a
    if b in a:
        return a

    max_overlap = min(240, len(a), len(b))
    overlap = 0
    for n in range(max_overlap, 0, -1):
        if a.endswith(b[:n]):
            overlap = n
            break
    if overlap > 0:
        b = b[overlap:]
    if not b.strip():
        return a
    return f"{a.rstrip()}\n{b.lstrip()}".strip()


def _continue_markdown_rescue(
    *,
    user_input: str,
    language: str,
    destination: str,
    duration_days: int,
    requirement_understanding: dict[str, Any],
    search_plan: list[SearchTask],
    evidence_digest: list[dict[str, Any]],
    sources: list[dict[str, str]],
    partial_markdown: str,
) -> dict[str, Any]:
    prompt = (
        "The previous markdown answer was truncated because of token limit.\n"
        "Continue writing from exactly where it stopped.\n"
        "Rules:\n"
        "1) Do not restart from the beginning.\n"
        "2) Do not repeat existing paragraphs.\n"
        "3) Keep the same language, style, and structure.\n"
        "4) Finish remaining day blocks and ending sections.\n"
        "5) Output markdown continuation only.\n\n"
        f"response_language:\n{language}\n\n"
        f"user_input:\n{user_input}\n\n"
        f"destination:\n{destination}\n\n"
        f"duration_days:\n{duration_days}\n\n"
        f"requirement_understanding:\n{json.dumps(requirement_understanding, ensure_ascii=False)}\n\n"
        f"search_tasks:\n{json.dumps([task.to_dict() for task in search_plan], ensure_ascii=False)}\n\n"
        f"evidence_digest:\n{json.dumps(evidence_digest, ensure_ascii=False)}\n\n"
        f"sources:\n{json.dumps(sources, ensure_ascii=False)}\n\n"
        "Current partial markdown (do not repeat it):\n"
        f"{partial_markdown}\n\n"
        "Output markdown continuation only."
    )
    messages = CLIENT.build_messages(
        system_prompt="You are a reliable travel final planner. Output markdown only.",
        developer_prompt="Continue the truncated markdown faithfully. No JSON.",
        user_prompt=prompt,
        conversation_history=None,
    )
    timeout_backup = CLIENT.timeout_sec
    try:
        CLIENT.timeout_sec = max(10.0, _markdown_rescue_timeout_sec(duration_days) - 2.0)
        result = CLIENT.chat_completion(
            messages=messages,
            temperature=0.2,
            max_tokens=max(700, int(_markdown_rescue_max_tokens(duration_days) * 0.75)),
            reasoning_effort=None,
            extra_body={"thinking": {"type": "disabled"}},
            model=(os.getenv("AGENT_FINAL_MARKDOWN_RESCUE_MODEL") or "").strip() or "deepseek-v4-flash",
            allow_model_fallback=True,
        )
    finally:
        CLIENT.timeout_sec = timeout_backup
    return result if isinstance(result, dict) else {"ok": False, "error": "invalid continuation response"}


def _markdown_rescue_payload(
    user_input: str,
    requirement_understanding: dict[str, Any],
    search_plan: list[SearchTask],
    tool_results: list[ToolExecutionResult],
    interpretation_payload: dict[str, Any],
    time_context: dict[str, str],
) -> dict[str, Any] | None:
    if not CLIENT.enabled:
        return None

    language = detect_user_language(user_input, default="en")
    destination, duration_days, budget = _extract_destination_duration_budget(requirement_understanding)
    if not destination or duration_days <= 0 or not budget:
        d2, days2, b2 = _extract_destination_duration_budget_from_text(user_input)
        if not destination and d2:
            destination = d2
        if duration_days <= 0 and days2 > 0:
            duration_days = days2
        if not budget and b2:
            budget = b2

    evidence_digest = _collect_evidence_digest(
        search_plan=search_plan,
        tool_results=tool_results,
        interpretation_payload=interpretation_payload,
    )
    if not evidence_digest:
        return None

    sources = _normalize_sources(interpretation_payload.get("sources", []))[:10]
    prompt = (
        "Generate a practical travel-plan markdown answer from the evidence.\n"
        "Do not output JSON.\n"
        "Use only information supported by the provided evidence; if uncertain, say uncertainty.\n"
        "The answer must include: need understanding, assumptions, executable day-by-day itinerary, budget table, tips, follow-up questions.\n"
        "Language must follow response_language exactly.\n"
        "Resolve relative dates using time_context.\n\n"
        f"response_language:\n{language}\n\n"
        f"user_input:\n{user_input}\n\n"
        f"time_context:\n{json.dumps(time_context, ensure_ascii=False)}\n\n"
        f"requirement_understanding:\n{json.dumps(requirement_understanding, ensure_ascii=False)}\n\n"
        f"search_tasks:\n{json.dumps([task.to_dict() for task in search_plan], ensure_ascii=False)}\n\n"
        f"evidence_digest:\n{json.dumps(evidence_digest, ensure_ascii=False)}\n\n"
        f"sources:\n{json.dumps(sources, ensure_ascii=False)}\n\n"
        "Output markdown only."
    )
    messages = CLIENT.build_messages(
        system_prompt="You are a reliable travel final planner. Output markdown only.",
        developer_prompt=(
            "No JSON. No generic placeholder itinerary."
            " If evidence is insufficient, say what is missing and avoid pretending certainty."
        ),
        user_prompt=prompt,
        conversation_history=None,
    )

    timeout_backup = CLIENT.timeout_sec
    try:
        CLIENT.timeout_sec = _markdown_rescue_timeout_sec(duration_days)
        result = CLIENT.chat_completion(
            messages=messages,
            temperature=0.2,
            max_tokens=_markdown_rescue_max_tokens(duration_days),
            reasoning_effort=None,
            extra_body={"thinking": {"type": "disabled"}},
            model=(os.getenv("AGENT_FINAL_MARKDOWN_RESCUE_MODEL") or "").strip() or "deepseek-v4-flash",
            allow_model_fallback=True,
        )
    finally:
        CLIENT.timeout_sec = timeout_backup

    if not result.get("ok"):
        return None
    markdown = str(result.get("content", "") or "").strip()
    if not markdown:
        return None
    finish_reason = str(result.get("finish_reason", "") or "").strip().lower()
    continue_rounds = max(0, min(2, _safe_int(os.getenv("AGENT_FINAL_MARKDOWN_CONTINUE_ROUNDS"), 2)))
    for _ in range(continue_rounds):
        if finish_reason != "length":
            break
        continuation = _continue_markdown_rescue(
            user_input=user_input,
            language=language,
            destination=destination or "",
            duration_days=duration_days,
            requirement_understanding=requirement_understanding,
            search_plan=search_plan,
            evidence_digest=evidence_digest,
            sources=sources,
            partial_markdown=markdown,
        )
        if not continuation.get("ok"):
            break
        add_on = str(continuation.get("content", "") or "").strip()
        if not add_on:
            break
        markdown = _merge_non_overlapping_markdown(markdown, add_on)
        finish_reason = str(continuation.get("finish_reason", "") or "").strip().lower()

    parsed_itinerary = _parse_markdown_itinerary(markdown, destination=destination or "", language=language)
    itinerary = list(parsed_itinerary)
    assumptions = requirement_understanding.get("default_assumptions", [])
    if not isinstance(assumptions, list):
        assumptions = []
    assumptions = [str(item).strip() for item in assumptions if str(item).strip()]
    if not assumptions:
        assumptions = (
            ["优先保证行程可执行和交通可达。", "每日节奏默认中等强度。"]
            if language == "zh"
            else ["Prioritize feasibility and realistic transport timing.", "Assume moderate travel pace."]
        )

    missing_info = requirement_understanding.get("missing_information", [])
    missing_fields: list[str] = []
    if isinstance(missing_info, list):
        for row in missing_info:
            if not isinstance(row, dict):
                continue
            field = str(row.get("field", "")).strip()
            if field:
                missing_fields.append(field)

    follow_up = requirement_understanding.get("clarifying_questions", [])
    if not isinstance(follow_up, list):
        follow_up = []
    follow_up = [str(item).strip() for item in follow_up if str(item).strip()]
    if duration_days > 0 and destination:
        if all(any(key in q.lower() for key in ("destination", "目的地")) for q in follow_up):
            follow_up = []

    budget_estimate: dict[str, Any] = {}
    if budget:
        budget_estimate["total"] = budget

    frontend_json = {
        "destination": destination or ("待确认目的地" if language == "zh" else "TBD destination"),
        "duration_days": duration_days if duration_days > 0 else len(itinerary),
        "assumptions": assumptions,
        "missing_information": missing_fields,
        "itinerary": itinerary,
        "hotel_area_suggestions": [],
        "budget_estimate": budget_estimate,
        "tips": [],
        "follow_up_questions": follow_up,
    }

    markdown = _prepend_time_anchor_if_needed(markdown, user_input=user_input, time_context=time_context)
    return {
        "frontend_json": frontend_json,
        "markdown_answer": markdown,
        "response_stage": "finalized_by_markdown_rescue",
    }


def generate_itinerary_payload(
    user_input: str,
    requirement_understanding: dict[str, Any],
    search_plan: list[SearchTask],
    tool_results: list[ToolExecutionResult],
    interpretation_payload: dict[str, Any],
    user_preferences: dict[str, Any] | None = None,
    conversation_history: list[dict[str, str]] | None = None,
    cancel_event: object | None = None,
) -> dict[str, Any]:
    def is_cancelled() -> bool:
        return bool(cancel_event and getattr(cancel_event, "is_set", lambda: False)())

    time_context = build_runtime_time_context(user_preferences if isinstance(user_preferences, dict) else None)
    if is_cancelled():
        return _fallback_payload(
            user_input,
            requirement_understanding,
            search_plan,
            tool_results,
            interpretation_payload,
            time_context=time_context,
            error_message="Cancelled by user.",
            response_stage="cancelled",
        )
    if not CLIENT.enabled:
        return _fallback_payload(
            user_input,
            requirement_understanding,
            search_plan,
            tool_results,
            interpretation_payload,
            time_context=time_context,
            error_message="DeepSeek API key is missing.",
            response_stage="llm_key_missing",
        )

    speed_mode = _speed_mode()
    _, days_hint, _ = _extract_destination_duration_budget(requirement_understanding)
    if days_hint <= 0:
        _, days_text_hint, _ = _extract_destination_duration_budget_from_text(user_input)
        days_hint = days_text_hint
    complexity = _trip_complexity(days_hint, speed_mode)
    mocked_call = hasattr(CLIENT.json_completion, "assert_called")
    compact_tool_results = _compact_tool_results_for_prompt(tool_results)
    evidence_digest = _collect_evidence_digest(
        search_plan=search_plan,
        tool_results=tool_results,
        interpretation_payload=interpretation_payload,
    )
    if not evidence_digest:
        evidence_digest = compact_tool_results[:4]

    quick_first_raw = os.getenv("AGENT_ENABLE_QUICK_FINAL_FIRST")
    if quick_first_raw is None:
        # Default to main final-planner path first.
        # Quick-rescue can be explicitly enabled by env flag when latency is prioritized.
        quick_first = False
    else:
        quick_first = str(quick_first_raw).strip().lower() in {"1", "true", "yes", "on"}
    if quick_first and not mocked_call:
        quick_payload = _quick_final_rescue(
            user_input=user_input,
            requirement_understanding=requirement_understanding,
            search_plan=search_plan,
            interpretation_payload=interpretation_payload,
            time_context=time_context,
            evidence_digest=evidence_digest,
        )
        quick_frontend = quick_payload.get("frontend_json", {}) if isinstance(quick_payload, dict) else {}
        quick_itinerary = quick_frontend.get("itinerary", []) if isinstance(quick_frontend, dict) else []
        if isinstance(quick_itinerary, list) and quick_itinerary:
            quick_markdown = str(quick_payload.get("markdown_answer", "")).strip()
            quick_frontend, quick_markdown = _rewrite_payload_language_if_needed(
                user_input=user_input,
                frontend_json=quick_frontend,
                markdown_answer=quick_markdown,
                time_context=time_context,
            )
            quick_markdown = _prepend_time_anchor_if_needed(
                quick_markdown,
                user_input=user_input,
                time_context=time_context,
            )
            return {
                "requirement_understanding": requirement_understanding,
                "search_tasks": [task.to_dict() for task in search_plan],
                "search_plan": [task.to_dict() for task in search_plan],
                "search_results": [result_item.to_dict() for result_item in tool_results],
                "search_results_interpretation": interpretation_payload.get("task_result_interpretations", []),
                "sources": _normalize_sources(interpretation_payload.get("sources", [])),
                "frontend_json": quick_frontend,
                "markdown_answer": quick_markdown,
                "response_stage": str(quick_payload.get("response_stage", "finalized_by_llm_quick")),
            }

    user_prompt = _build_final_user_prompt(
        user_input=user_input,
        requirement_understanding=requirement_understanding,
        search_plan=search_plan,
        interpretation_payload=interpretation_payload,
        evidence_digest=evidence_digest,
        conversation_history=conversation_history,
        user_preferences=user_preferences,
        time_context=time_context,
    )

    messages = CLIENT.build_messages(
        system_prompt="You are a rigorous travel final-planner model. Output JSON only.",
        developer_prompt=PROMPT_FINAL_PLANNER,
        user_prompt=user_prompt,
        conversation_history=None,
    )

    final_max_tokens = _final_max_tokens(complexity)
    enable_retry = str(os.getenv("AGENT_FINAL_ENABLE_RETRY", "0")).strip().lower() in {"1", "true", "yes", "on"}
    skip_main_raw = os.getenv("AGENT_SKIP_MAIN_FINAL_LONG")
    if skip_main_raw is None:
        # Keep robust quality defaults: do not skip main final call unless explicitly configured.
        skip_main_final = False
    else:
        skip_main_final = str(skip_main_raw).strip().lower() in {"1", "true", "yes", "on"}

    attempts: list[tuple[float, int]] = []
    if not (skip_main_final and not mocked_call):
        attempts = [(0.0, final_max_tokens)]
    if enable_retry:
        attempts.append((0.0, max(900, int(final_max_tokens * 0.82))))
    extra_body = _final_extra_body(speed_mode, complexity)
    thinking_enabled = str(extra_body.get("thinking", {}).get("type", "")).lower() == "enabled"
    model_name = _final_model(speed_mode, complexity)
    last_error: str | None = None
    mocked_call = hasattr(CLIENT.json_completion, "assert_called")

    for temperature, max_tokens in attempts:
        if is_cancelled():
            last_error = "Cancelled by user."
            break
        timeout_backup = CLIENT.timeout_sec
        try:
            CLIENT.timeout_sec = _final_timeout_sec(complexity)
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
            last_error = str(result.get("error", "LLM call failed."))
            continue

        data = result.get("json", {})
        if not isinstance(data, dict):
            last_error = "LLM output is not a JSON object."
            continue

        try:
            validated = validate_final_answer(
                data,
                requirement_understanding=requirement_understanding,
                user_input=user_input,
            )
        except Exception as exc:
            last_error = f"Final schema validation failed: {exc}"
            continue

        frontend_json = validated.frontend_json.model_dump()
        markdown_answer = str(validated.markdown_answer).strip()
        if not markdown_answer:
            language = detect_user_language(user_input, default="en")
            if language == "zh":
                markdown_answer = "我已完成方案整合，但文本渲染失败。"
            else:
                markdown_answer = "The travel plan was generated, but markdown rendering failed."

        itinerary_rows = frontend_json.get("itinerary", [])
        has_itinerary = isinstance(itinerary_rows, list) and len(itinerary_rows) > 0
        has_destination = bool(str(frontend_json.get("destination", "")).strip())
        has_days = int(frontend_json.get("duration_days", 0) or 0) > 0
        if not (has_itinerary and has_destination and has_days):
            fallback = _build_fast_payload(
                user_input=user_input,
                requirement_understanding=requirement_understanding,
                search_plan=search_plan,
                tool_results=tool_results,
                interpretation_payload=interpretation_payload,
                response_stage="llm_partial_autofill",
                llm_error="Final LLM output missing structured itinerary fields.",
                allow_llm_enrichment=False,
            )
            fallback_frontend = fallback.get("frontend_json", {})
            if isinstance(fallback_frontend, dict):
                for key, value in fallback_frontend.items():
                    current = frontend_json.get(key)
                    if current in (None, "", [], {}):
                        frontend_json[key] = value
            if not has_itinerary:
                rescue_partial = _quick_final_rescue(
                    user_input=user_input,
                    requirement_understanding=requirement_understanding,
                    search_plan=search_plan,
                    interpretation_payload=interpretation_payload,
                    time_context=time_context,
                    evidence_digest=evidence_digest,
                )
                rescue_frontend = rescue_partial.get("frontend_json", {}) if isinstance(rescue_partial, dict) else {}
                rescue_itinerary = rescue_frontend.get("itinerary", []) if isinstance(rescue_frontend, dict) else []
                if isinstance(rescue_itinerary, list) and rescue_itinerary:
                    frontend_json = rescue_frontend
                    markdown_answer = str(rescue_partial.get("markdown_answer", "")).strip() or markdown_answer
                else:
                    md_rescue_partial = _markdown_rescue_payload(
                        user_input=user_input,
                        requirement_understanding=requirement_understanding,
                        search_plan=search_plan,
                        tool_results=tool_results,
                        interpretation_payload=interpretation_payload,
                        time_context=time_context,
                    )
                    if isinstance(md_rescue_partial, dict):
                        md_frontend = md_rescue_partial.get("frontend_json", {})
                        md_itinerary = md_frontend.get("itinerary", []) if isinstance(md_frontend, dict) else []
                        if isinstance(md_itinerary, list) and md_itinerary:
                            frontend_json = md_frontend
                            markdown_answer = str(md_rescue_partial.get("markdown_answer", "")).strip() or markdown_answer
                # Keep model-authored markdown when available; only fallback if markdown is empty.
                if not markdown_answer.strip():
                    markdown_answer = str(fallback.get("markdown_answer", "")).strip() or markdown_answer

        # Ensure markdown and structured itinerary stay aligned.
        # Prefer local deterministic renderer for speed and format consistency.
        itinerary_rows_after = frontend_json.get("itinerary", [])
        has_itinerary_after = isinstance(itinerary_rows_after, list) and len(itinerary_rows_after) > 0
        if has_itinerary_after:
            need_regen_markdown = (
                not markdown_answer.strip()
                or _looks_placeholder_markdown(markdown_answer)
            )
            if need_regen_markdown and not mocked_call:
                rebuilt = _build_fast_markdown(frontend_json, user_input=user_input)
                rebuilt = _prepend_time_anchor_if_needed(
                    rebuilt,
                    user_input=user_input,
                    time_context=time_context,
                )
                markdown_answer = rebuilt

        markdown_answer = _prepend_time_anchor_if_needed(
            markdown_answer,
            user_input=user_input,
            time_context=time_context,
        )
        frontend_json, markdown_answer = _rewrite_payload_language_if_needed(
            user_input=user_input,
            frontend_json=frontend_json,
            markdown_answer=markdown_answer,
            time_context=time_context,
        )
        markdown_answer = _prepend_time_anchor_if_needed(
            markdown_answer,
            user_input=user_input,
            time_context=time_context,
        )

        return {
            "requirement_understanding": requirement_understanding,
            "search_tasks": [task.to_dict() for task in search_plan],
            "search_plan": [task.to_dict() for task in search_plan],
            "search_results": [result_item.to_dict() for result_item in tool_results],
            "search_results_interpretation": interpretation_payload.get("task_result_interpretations", []),
            "sources": _normalize_sources(interpretation_payload.get("sources", [])),
            "frontend_json": frontend_json,
            "markdown_answer": markdown_answer,
            "response_stage": "finalized_by_llm",
        }

    rescue_after_main = _quick_final_rescue(
        user_input=user_input,
        requirement_understanding=requirement_understanding,
        search_plan=search_plan,
        interpretation_payload=interpretation_payload,
        time_context=time_context,
        evidence_digest=evidence_digest,
    )
    if isinstance(rescue_after_main, dict):
        rescue_frontend = rescue_after_main.get("frontend_json", {})
        rescue_markdown = str(rescue_after_main.get("markdown_answer", "")).strip()
        if isinstance(rescue_frontend, dict):
            rescue_frontend, rescue_markdown = _rewrite_payload_language_if_needed(
                user_input=user_input,
                frontend_json=rescue_frontend,
                markdown_answer=rescue_markdown,
                time_context=time_context,
            )
            rescue_markdown = _prepend_time_anchor_if_needed(
                rescue_markdown,
                user_input=user_input,
                time_context=time_context,
            )
        return {
            "requirement_understanding": requirement_understanding,
            "search_tasks": [task.to_dict() for task in search_plan],
            "search_plan": [task.to_dict() for task in search_plan],
            "search_results": [result_item.to_dict() for result_item in tool_results],
            "search_results_interpretation": interpretation_payload.get("task_result_interpretations", []),
            "sources": _normalize_sources(interpretation_payload.get("sources", [])),
            "frontend_json": rescue_frontend,
            "markdown_answer": rescue_markdown,
            "response_stage": str(rescue_after_main.get("response_stage", "finalized_by_llm_rescue")),
            "llm_error": _truncate_text(last_error or "", 500),
        }

    enable_markdown_rescue = str(os.getenv("AGENT_ENABLE_MARKDOWN_RESCUE", "0")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if enable_markdown_rescue:
        markdown_rescue = _markdown_rescue_payload(
            user_input=user_input,
            requirement_understanding=requirement_understanding,
            search_plan=search_plan,
            tool_results=tool_results,
            interpretation_payload=interpretation_payload,
            time_context=time_context,
        )
        if isinstance(markdown_rescue, dict):
            rescue_frontend = markdown_rescue.get("frontend_json", {})
            rescue_markdown = str(markdown_rescue.get("markdown_answer", "")).strip()
            if isinstance(rescue_frontend, dict):
                rescue_frontend, rescue_markdown = _rewrite_payload_language_if_needed(
                    user_input=user_input,
                    frontend_json=rescue_frontend,
                    markdown_answer=rescue_markdown,
                    time_context=time_context,
                )
                rescue_markdown = _prepend_time_anchor_if_needed(
                    rescue_markdown,
                    user_input=user_input,
                    time_context=time_context,
                )
            return {
                "requirement_understanding": requirement_understanding,
                "search_tasks": [task.to_dict() for task in search_plan],
                "search_plan": [task.to_dict() for task in search_plan],
                "search_results": [result_item.to_dict() for result_item in tool_results],
                "search_results_interpretation": interpretation_payload.get("task_result_interpretations", []),
                "sources": _normalize_sources(interpretation_payload.get("sources", [])),
                "frontend_json": rescue_frontend,
                "markdown_answer": rescue_markdown,
                "response_stage": str(markdown_rescue.get("response_stage", "finalized_by_markdown_rescue")),
                "llm_error": _truncate_text(last_error or "", 500),
            }

    enable_json_rescue = str(os.getenv("AGENT_ENABLE_JSON_RESCUE_AFTER_MARKDOWN", "0")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if enable_json_rescue:
        rescue_payload = _quick_final_rescue(
            user_input=user_input,
            requirement_understanding=requirement_understanding,
            search_plan=search_plan,
            interpretation_payload=interpretation_payload,
            time_context=time_context,
            evidence_digest=evidence_digest,
        )
        if isinstance(rescue_payload, dict):
            rescue_frontend = rescue_payload.get("frontend_json", {})
            rescue_markdown = str(rescue_payload.get("markdown_answer", "")).strip()
            if isinstance(rescue_frontend, dict):
                rescue_frontend, rescue_markdown = _rewrite_payload_language_if_needed(
                    user_input=user_input,
                    frontend_json=rescue_frontend,
                    markdown_answer=rescue_markdown,
                    time_context=time_context,
                )
                rescue_markdown = _prepend_time_anchor_if_needed(
                    rescue_markdown,
                    user_input=user_input,
                    time_context=time_context,
                )
            return {
                "requirement_understanding": requirement_understanding,
                "search_tasks": [task.to_dict() for task in search_plan],
                "search_plan": [task.to_dict() for task in search_plan],
                "search_results": [result_item.to_dict() for result_item in tool_results],
                "search_results_interpretation": interpretation_payload.get("task_result_interpretations", []),
                "sources": _normalize_sources(interpretation_payload.get("sources", [])),
                "frontend_json": rescue_frontend,
                "markdown_answer": rescue_markdown,
                "response_stage": str(rescue_payload.get("response_stage", "finalized_by_llm_rescue")),
            }

    fallback_payload = _fallback_payload(
        user_input,
        requirement_understanding,
        search_plan,
        tool_results,
        interpretation_payload,
        time_context=time_context,
        error_message=last_error,
        response_stage="llm_final_failed",
    )
    fallback_frontend = fallback_payload.get("frontend_json", {})
    fallback_markdown = str(fallback_payload.get("markdown_answer", "")).strip()
    if isinstance(fallback_frontend, dict):
        fallback_frontend, fallback_markdown = _rewrite_payload_language_if_needed(
            user_input=user_input,
            frontend_json=fallback_frontend,
            markdown_answer=fallback_markdown,
            time_context=time_context,
        )
        fallback_markdown = _prepend_time_anchor_if_needed(
            fallback_markdown,
            user_input=user_input,
            time_context=time_context,
        )
        fallback_payload["frontend_json"] = fallback_frontend
        fallback_payload["markdown_answer"] = fallback_markdown
    return fallback_payload


def render_markdown(payload: dict[str, Any]) -> str:
    answer = str(payload.get("markdown_answer", "")).strip()
    if answer:
        return answer
    return "Travel plan generated, but markdown answer is empty."
