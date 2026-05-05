from __future__ import annotations

import json
import os
import re
from typing import Any

from ..deepseek_client import DeepseekClient
from .llm_schemas import validate_search_interpretation
from .runtime_flags import get_agent_speed_mode, runtime_truthy
from .types import SearchTask, ToolExecutionResult


CLIENT = DeepseekClient()

PROMPT_SEARCH_RESULT_ANALYZER = """
你是一个旅游信息分析 Agent。

你会收到某个搜索任务，以及该任务下的搜索结果。

你的任务是：
1. 阅读搜索结果。
2. 提取对用户最终旅行方案有帮助的信息。
3. 去除重复、广告化、低价值内容。
4. 标记信息可信度。
5. 如果不同搜索结果互相冲突，请指出。
6. 总结这些信息应该如何被用于最终行程。

不要直接生成最终旅游方案。
只输出严格 JSON。

输出 JSON schema：
{
  "task_id": string,
  "key_findings": [
    {
      "finding": string,
      "source": string,
      "confidence": number,
      "how_to_use": string
    }
  ],
  "conflicts": [
    {
      "topic": string,
      "different_versions": string[],
      "suggested_resolution": string
    }
  ],
  "low_confidence_items": string[],
  "summary_for_final_planner": string
}
""".strip()

PROMPT_BATCH_SEARCH_RESULT_ANALYZER = """
你是一个旅游信息分析 Agent。

你会一次收到多个搜索任务及对应搜索结果。你的任务是：
1. 逐任务提取高价值信息；
2. 去重、去广告化、过滤低价值内容；
3. 标记可信度并指出冲突；
4. 给出“如何用于最终行程”的总结。

不要直接生成最终旅游方案。
只输出严格 JSON。

输出 JSON schema：
{
  "task_result_interpretations": [
    {
      "task_id": string,
      "key_findings": [
        {
          "finding": string,
          "source": string,
          "confidence": number,
          "how_to_use": string
        }
      ],
      "conflicts": [
        {
          "topic": string,
          "different_versions": string[],
          "suggested_resolution": string
        }
      ],
      "low_confidence_items": string[],
      "summary_for_final_planner": string
    }
  ]
}
""".strip()


def _relevance_threshold() -> float:
    raw = str(os.getenv("AGENT_MIN_RELEVANCE_SCORE", "0.70")).strip()
    try:
        value = float(raw)
    except Exception:
        return 0.70
    return min(0.95, max(0.3, value))


def _use_llm_result_analysis() -> bool:
    raw = os.getenv("AGENT_USE_LLM_RESULT_ANALYSIS")
    if raw is not None:
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    return False


def _use_batch_llm_analysis() -> bool:
    raw = os.getenv("AGENT_USE_BATCH_RESULT_ANALYSIS")
    if raw is not None:
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    return False


def _normalize_source(
    title: str,
    url: str,
    platform: str = "",
    snippet: str = "",
) -> dict[str, str]:
    return {
        "title": title.strip() or "Untitled",
        "url": url.strip(),
        "platform": platform.strip(),
        "snippet": snippet.strip(),
    }


def _append_sources_from_items(
    bucket: list[dict[str, str]],
    seen_urls: set[str],
    items: list[dict[str, Any]],
    title_key: str = "title",
    url_key: str = "url",
    platform_key: str = "platform",
    snippet_key: str = "snippet",
    limit: int = 24,
) -> None:
    for item in items:
        if not isinstance(item, dict):
            continue
        url = str(item.get(url_key, "")).strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        bucket.append(
            _normalize_source(
                title=str(item.get(title_key, "")).strip() or url,
                url=url,
                platform=str(item.get(platform_key, "")).strip(),
                snippet=str(item.get(snippet_key, "")).strip(),
            )
        )
        if len(bucket) >= limit:
            return


def _sanitize_url(url: str) -> str:
    return url.strip().rstrip(").,，。；;）】】>」")


def _extract_urls(text: str) -> list[str]:
    urls = re.findall(r"https?://[^\s]+", text or "")
    return [_sanitize_url(url) for url in urls if _sanitize_url(url)]


def _is_low_value_source(source: dict[str, str]) -> bool:
    title = str(source.get("title", "")).strip()
    url = str(source.get("url", "")).strip().lower()
    if not title or not url:
        return True
    # Generic Q&A/topic landing pages are usually noisy for itinerary evidence.
    if re.fullmatch(r"[\u4e00-\u9fff]{2,12}\s*-\s*知乎", title):
        return True
    if "zhihu.com/topic/" in url or "zhihu.com/question/" in url:
        return True
    if "zhihu.com/" in url and ("tardis/zm/art/" not in url):
        return True
    if "zhidao.baidu.com" in url or "tieba.baidu.com" in url:
        return True
    if "agents.baidu.com" in url:
        return True
    if "taobao.com" in url or "fliggy.com" in url:
        return True
    if "travelarbitrage." in url:
        return True
    if "bk.taobao.com" in url:
        return True
    if "百度知道" in title or "有问题，就会有答案" in title:
        return True
    return False


def _source_quality_score(source: dict[str, str]) -> float:
    url = str(source.get("url", "")).strip().lower()
    title = str(source.get("title", "")).strip().lower()
    score = 0.0

    high_trust_patterns = (
        "newzealand.com",
        "doc.govt.nz",
        "immigration.govt.nz",
        "metservice.com",
        "open-meteo.com",
        "skyscanner.",
        "trip.com",
        "ctrip.com",
        "booking.com",
        "agoda.com",
        "tripadvisor.",
        "lonelyplanet.",
        "wikivoyage.org",
        "wikipedia.org",
    )
    medium_patterns = (
        "qunar.com",
        "zuzuche.com",
        "hertz.",
        "avis.",
    )
    low_patterns = (
        "smzdm.com",
        "sohu.com",
        "163.com",
        "bilibili.com",
        "zhihu.com",
        "xinhuanet.com",
        "toutiao.com",
    )

    for p in high_trust_patterns:
        if p in url:
            score += 2.0
    for p in medium_patterns:
        if p in url:
            score += 1.0
    for p in low_patterns:
        if p in url:
            score -= 1.5

    if "official" in title or "官网" in title or "旅游局" in title:
        score += 0.7
    if "攻略" in title and ("淘宝" in title or "百科" in title):
        score -= 0.8
    return score


def _rank_and_dedupe_sources(sources: list[dict[str, str]], limit: int = 16) -> list[dict[str, str]]:
    seen_urls: set[str] = set()
    unique_rows: list[dict[str, str]] = []
    for row in sources:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url", "")).strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        unique_rows.append(row)

    unique_rows.sort(key=_source_quality_score, reverse=True)
    return unique_rows[:limit]


def _task_result_source_candidates(task_rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()

    for row in task_rows:
        if not isinstance(row, dict):
            continue
        if not bool(row.get("success")):
            continue
        tool = str(row.get("tool", "")).strip()
        output = row.get("output", {})
        if not isinstance(output, dict):
            continue

        raw_items: list[dict[str, Any]] = []
        if tool == "web_search":
            raw_items = [item for item in output.get("items", []) if isinstance(item, dict)]
        elif tool == "transport_search":
            raw_items = [item for item in output.get("transport_items", []) if isinstance(item, dict)]
        elif tool in {"poi_search", "place_search"}:
            raw_items = [
                {
                    "title": item.get("name", "POI"),
                    "url": item.get("url", ""),
                    "platform": "openstreetmap",
                    "snippet": item.get("address", ""),
                }
                for item in output.get("pois", [])
                if isinstance(item, dict)
            ]
        elif tool in {"hotel_area_search", "hotel_search"}:
            raw_items = [
                {
                    "title": item.get("name", "Hotel"),
                    "url": item.get("url", ""),
                    "platform": "openstreetmap",
                    "snippet": item.get("address", ""),
                }
                for item in output.get("areas", [])
                if isinstance(item, dict)
            ]

        for item in raw_items:
            url = str(item.get("url", "")).strip()
            if not url or url in seen:
                continue
            seen.add(url)
            candidates.append(
                _normalize_source(
                    title=str(item.get("title", "")).strip() or url,
                    url=url,
                    platform=str(item.get("platform", "")).strip(),
                    snippet=str(item.get("snippet", "")).strip(),
                )
            )
    return candidates


def _choose_source_by_finding(
    finding_source: str,
    candidates: list[dict[str, str]],
) -> dict[str, str] | None:
    source_text = (finding_source or "").strip()
    if not source_text:
        return None

    urls = _extract_urls(source_text)
    if urls:
        url = urls[0].strip()
        for row in candidates:
            candidate_url = str(row.get("url", "")).strip()
            if candidate_url == url or candidate_url.startswith(url) or url.startswith(candidate_url):
                return row
        return _normalize_source(title=url, url=url)

    source_lc = source_text.lower()
    best: dict[str, str] | None = None
    best_score = 0
    for row in candidates:
        title_lc = row.get("title", "").lower()
        url_lc = row.get("url", "").lower()
        score = 0
        if source_lc and source_lc in title_lc:
            score += 3
        if source_lc and source_lc in url_lc:
            score += 2
        if title_lc and title_lc in source_lc:
            score += 2
        if url_lc and url_lc in source_lc:
            score += 1
        if score > best_score:
            best = row
            best_score = score
    return best if best_score > 0 else None


def _collect_sources_from_analyses(
    analyses: list[dict[str, Any]],
    grouped_results: dict[str, list[dict[str, Any]]],
    user_input: str,
) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    stopwords = {
        "旅游",
        "攻略",
        "推荐",
        "最新",
        "官方",
        "信息",
        "安排",
        "计划",
        "后天",
        "出发",
        "应该",
        "怎么",
    }

    def tokenize(text: str) -> list[str]:
        chunks = re.split(r"[\s,，。；;、|/()（）\-]+", (text or "").strip().lower())
        tokens: list[str] = []
        seen: set[str] = set()
        for chunk in chunks:
            token = chunk.strip()
            if not token:
                continue
            if token in stopwords:
                continue
            if len(token) <= 1:
                continue
            if re.fullmatch(r"[0-9]+", token):
                continue
            if token in seen:
                continue
            seen.add(token)
            tokens.append(token)
        return tokens[:12]

    global_tokens = tokenize(user_input)

    for analysis in analyses:
        if not isinstance(analysis, dict):
            continue
        task_id = str(analysis.get("task_id", "")).strip()
        task_rows = grouped_results.get(task_id, [])
        candidates = _task_result_source_candidates(task_rows)
        query_tokens: list[str] = []
        for row in task_rows:
            query_tokens.extend(tokenize(str((row or {}).get("query", "")).strip()))
        scoped_tokens = list(dict.fromkeys(query_tokens + global_tokens))
        findings = analysis.get("key_findings", [])
        if not isinstance(findings, list):
            continue

        for finding in findings:
            if not isinstance(finding, dict):
                continue
            confidence = float(finding.get("confidence", 0.0) or 0.0)
            if confidence < 0.7:
                continue
            source_hint = str(finding.get("source", "")).strip()
            explicit_urls = _extract_urls(source_hint)

            # Canonical tool-source shortcut for weather evidence.
            if source_hint.lower() in {"open-meteo", "open meteo", "open_meteo"}:
                selected = _normalize_source(
                    title="Open-Meteo Forecast API",
                    url="https://open-meteo.com/",
                    platform="open-meteo",
                    snippet="Forecast source used by weather tool",
                )
            else:
                selected = _choose_source_by_finding(source_hint, candidates)
            if not selected:
                continue
            searchable = (
                f"{selected.get('title', '')} {selected.get('snippet', '')} {selected.get('url', '')}"
            ).lower()
            allow_direct_high_conf_source = (
                (bool(explicit_urls) and confidence >= 0.85)
                or source_hint.lower() in {"open-meteo", "open meteo", "open_meteo"}
            )
            if scoped_tokens and not allow_direct_high_conf_source:
                hit_count = sum(1 for token in scoped_tokens if token in searchable)
                min_hits = 1 if len(scoped_tokens) < 4 else 2
                if hit_count < min_hits:
                    continue
            url = selected.get("url", "").strip()
            if not url or url in seen_urls:
                continue
            if _is_low_value_source(selected):
                continue
            seen_urls.add(url)
            sources.append(selected)
            if len(sources) >= 30:
                return sources

    return sources


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _extract_trip_days_hint(user_input: str) -> int:
    match = re.search(r"(\d+)\s*天", str(user_input or ""))
    if not match:
        return 0
    try:
        value = int(match.group(1))
    except Exception:
        return 0
    return value if value > 0 else 0


def _trip_complexity(days: int, speed_mode: str) -> str:
    if speed_mode == "fast":
        return "fast"
    if days >= 8:
        return "long"
    if days >= 5:
        return "medium"
    return "short"


def _analysis_timeout_sec(complexity: str) -> float:
    defaults = {
        "fast": 5.0,
        "short": 8.0,
        "medium": 12.0,
        "long": 16.0,
    }
    return _safe_float(
        os.getenv("AGENT_BATCH_ANALYSIS_TIMEOUT_SEC"),
        defaults.get(complexity, 8.0),
    )


def _analysis_extra_body(speed_mode: str, complexity: str) -> dict[str, Any]:
    if speed_mode == "fast":
        return {"thinking": {"type": "disabled"}}
    enable_thinking = runtime_truthy("agent_enable_thinking", "AGENT_ENABLE_THINKING", False)
    if enable_thinking and complexity == "long":
        return {"thinking": {"type": "enabled"}}
    return {"thinking": {"type": "disabled"}}


def _analysis_model(speed_mode: str, complexity: str) -> str | None:
    if speed_mode == "fast":
        return (os.getenv("AGENT_RESULT_ANALYZER_MODEL_FAST") or "").strip() or None
    if complexity == "long":
        configured = (os.getenv("AGENT_RESULT_ANALYZER_MODEL_LONG") or "").strip()
        return configured or "deepseek-v4-flash"
    return (os.getenv("AGENT_RESULT_ANALYZER_MODEL") or "").strip() or None


def _filter_result_output_by_relevance(output: dict[str, Any]) -> dict[str, Any]:
    threshold = _relevance_threshold()
    filtered = dict(output)

    for key in ("items", "transport_items", "pois", "areas"):
        rows = filtered.get(key, [])
        if not isinstance(rows, list):
            continue
        kept: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            score = _safe_float(row.get("query_relevance"), -1.0)
            if score < 0:
                # Keep non-scored rows (e.g. native POI/weather rows) for recall.
                kept.append(row)
                continue
            if score >= threshold:
                kept.append(row)
        filtered[key] = kept
    return filtered


def _filter_task_results(task_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in task_results:
        if not isinstance(row, dict):
            continue
        copied = dict(row)
        output = copied.get("output", {})
        if isinstance(output, dict):
            copied["output"] = _filter_result_output_by_relevance(output)
        out.append(copied)
    return out


def _truncate_text(value: Any, max_len: int = 180) -> str:
    text = str(value or "").strip()
    if len(text) <= max_len:
        return text
    return f"{text[: max_len - 3]}..."


def _compact_output_for_prompt(tool: str, output: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(output, dict):
        return {}

    if tool == "web_search":
        items = output.get("items", []) if isinstance(output.get("items", []), list) else []
        return {
            "source": output.get("source", ""),
            "count": len(items),
            "items": [
                {
                    "title": _truncate_text(item.get("title", ""), 120),
                    "url": str(item.get("url", "")).strip(),
                    "snippet": _truncate_text(item.get("snippet", ""), 220),
                    "query_relevance": item.get("query_relevance", ""),
                }
                for item in items[:4]
                if isinstance(item, dict)
            ],
            "error": _truncate_text(output.get("error", ""), 160),
        }

    if tool == "transport_search":
        items = (
            output.get("transport_items", [])
            if isinstance(output.get("transport_items", []), list)
            else []
        )
        return {
            "source": output.get("source", ""),
            "count": len(items),
            "transport_items": [
                {
                    "title": _truncate_text(item.get("title", ""), 120),
                    "url": str(item.get("url", "")).strip(),
                    "snippet": _truncate_text(item.get("snippet", ""), 220),
                    "query_relevance": item.get("query_relevance", ""),
                }
                for item in items[:4]
                if isinstance(item, dict)
            ],
            "error": _truncate_text(output.get("error", ""), 160),
        }

    if tool in {"poi_search", "place_search"}:
        pois = output.get("pois", []) if isinstance(output.get("pois", []), list) else []
        return {
            "source": output.get("source", ""),
            "count": len(pois),
            "pois": [
                {
                    "name": _truncate_text(item.get("name", ""), 90),
                    "address": _truncate_text(item.get("address", ""), 160),
                    "url": str(item.get("url", "")).strip(),
                    "query_relevance": item.get("query_relevance", ""),
                }
                for item in pois[:5]
                if isinstance(item, dict)
            ],
            "error": _truncate_text(output.get("error", ""), 160),
        }

    if tool in {"hotel_area_search", "hotel_search"}:
        areas = output.get("areas", []) if isinstance(output.get("areas", []), list) else []
        return {
            "source": output.get("source", ""),
            "count": len(areas),
            "areas": [
                {
                    "name": _truncate_text(item.get("name", ""), 90),
                    "address": _truncate_text(item.get("address", ""), 160),
                    "url": str(item.get("url", "")).strip(),
                    "query_relevance": item.get("query_relevance", ""),
                }
                for item in areas[:5]
                if isinstance(item, dict)
            ],
            "error": _truncate_text(output.get("error", ""), 160),
        }

    if tool == "weather_search":
        forecast = output.get("forecast", []) if isinstance(output.get("forecast", []), list) else []
        return {
            "source": output.get("source", ""),
            "location": _truncate_text(output.get("location", ""), 90),
            "forecast": [
                {
                    "date": str(item.get("date", "")).strip(),
                    "temp_max_c": item.get("temp_max_c"),
                    "temp_min_c": item.get("temp_min_c"),
                    "precip_prob_max": item.get("precip_prob_max"),
                }
                for item in forecast[:5]
                if isinstance(item, dict)
            ],
            "error": _truncate_text(output.get("error", ""), 160),
        }

    return {
        "source": output.get("source", ""),
        "error": _truncate_text(output.get("error", ""), 160),
    }


def _build_batch_payload(
    search_plan: list[SearchTask],
    grouped_results: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task in search_plan:
        task_rows = _filter_task_results(grouped_results.get(task.task_id, []))
        compact_rows = []
        for row in task_rows:
            if not isinstance(row, dict):
                continue
            compact_rows.append(
                {
                    "task_id": str(row.get("task_id", "")).strip(),
                    "tool": str(row.get("tool", "")).strip(),
                    "query": _truncate_text(row.get("query", ""), 120),
                    "success": bool(row.get("success")),
                    "output": _compact_output_for_prompt(
                        str(row.get("tool", "")).strip(),
                        row.get("output", {}) if isinstance(row.get("output", {}), dict) else {},
                    ),
                }
            )
        rows.append({"task": task.to_dict(), "results": compact_rows})
    return rows


def _collect_sources_from_results_fallback(
    grouped_results: dict[str, list[dict[str, Any]]],
) -> list[dict[str, str]]:
    threshold = _relevance_threshold()
    sources: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for task_rows in grouped_results.values():
        ranked: list[tuple[float, dict[str, str]]] = []

        for row in task_rows:
            if not isinstance(row, dict):
                continue
            if not bool(row.get("success")):
                continue
            tool = str(row.get("tool", "")).strip()
            output = row.get("output", {})
            if not isinstance(output, dict):
                continue

            if tool == "weather_search":
                ranked.append(
                    (
                        0.95,
                        _normalize_source(
                            title="Open-Meteo Forecast API",
                            url="https://open-meteo.com/",
                            platform="open-meteo",
                            snippet="Forecast source used by weather tool",
                        ),
                    )
                )

            if tool == "web_search":
                raw_items = [item for item in output.get("items", []) if isinstance(item, dict)]
            elif tool == "transport_search":
                raw_items = [item for item in output.get("transport_items", []) if isinstance(item, dict)]
            elif tool in {"poi_search", "place_search"}:
                raw_items = [
                    {
                        "title": item.get("name", "POI"),
                        "url": item.get("url", ""),
                        "platform": "openstreetmap",
                        "snippet": item.get("address", ""),
                        "query_relevance": item.get("query_relevance"),
                    }
                    for item in output.get("pois", [])
                    if isinstance(item, dict)
                ]
            elif tool in {"hotel_area_search", "hotel_search"}:
                raw_items = [
                    {
                        "title": item.get("name", "Hotel"),
                        "url": item.get("url", ""),
                        "platform": "openstreetmap",
                        "snippet": item.get("address", ""),
                        "query_relevance": item.get("query_relevance"),
                    }
                    for item in output.get("areas", [])
                    if isinstance(item, dict)
                ]
            else:
                raw_items = []

            for item in raw_items:
                url = str(item.get("url", "")).strip()
                title = str(item.get("title", "")).strip()
                if not url or not title:
                    continue
                relevance = _safe_float(item.get("query_relevance"), 0.0)
                # Prefer strong matches, but allow one medium-confidence source per task.
                if relevance < threshold:
                    continue
                ranked.append(
                    (
                        relevance,
                        _normalize_source(
                            title=title,
                            url=url,
                            platform=str(item.get("platform", "")).strip(),
                            snippet=str(item.get("snippet", "")).strip(),
                        ),
                    )
                )

        ranked.sort(key=lambda row: row[0], reverse=True)
        picked = 0
        for _, source in ranked:
            url = str(source.get("url", "")).strip()
            if not url or url in seen_urls:
                continue
            if _is_low_value_source(source):
                continue
            seen_urls.add(url)
            sources.append(source)
            picked += 1
            if picked >= 2:
                break
        if len(sources) >= 20:
            break

    return sources


def _group_results_by_task(tool_results: list[ToolExecutionResult]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in tool_results:
        grouped.setdefault(result.task_id, []).append(result.to_dict())
    return grouped


def _fallback_task_analysis(task: SearchTask, task_results: list[dict[str, Any]]) -> dict[str, Any]:
    low_conf: list[str] = []
    for row in task_results:
        if row.get("success"):
            continue
        query = str(row.get("query", "")).strip()
        err = str((row.get("output") or {}).get("error", "")).strip()
        if query and err:
            low_conf.append(f"{query}: {err}")
        elif query:
            low_conf.append(query)

    return {
        "task_id": task.task_id,
        "key_findings": [],
        "conflicts": [],
        "low_confidence_items": low_conf[:10],
        "summary_for_final_planner": (
            f"Use this task's successful results as supporting evidence for: {task.information_need}. "
            "Treat unavailable or failed queries as uncertain."
        ),
    }


def _fast_task_analysis(task: SearchTask, task_results: list[dict[str, Any]], language: str = "en") -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    low_conf: list[str] = []
    threshold = _relevance_threshold()

    def append_finding(text: str, source: str, conf: float, how: str) -> None:
        if not text.strip():
            return
        findings.append(
            {
                "finding": text.strip(),
                "source": source.strip(),
                "confidence": round(max(0.0, min(1.0, conf)), 3),
                "how_to_use": how.strip() or ("用于最终行程整合" if language == "zh" else "Use in final itinerary synthesis."),
            }
        )

    for row in task_results:
        if not isinstance(row, dict):
            continue
        if not bool(row.get("success")):
            query = str(row.get("query", "")).strip()
            err = str((row.get("output") or {}).get("error", "")).strip()
            if query and err:
                low_conf.append(f"{query}: {err}")
            elif query:
                low_conf.append(query)
            continue

        tool = str(row.get("tool", "")).strip()
        output = row.get("output", {})
        if not isinstance(output, dict):
            continue

        if tool in {"web_search", "transport_search"}:
            key = "items" if tool == "web_search" else "transport_items"
            items = output.get(key, [])
            if isinstance(items, list):
                for item in items[:2]:
                    if not isinstance(item, dict):
                        continue
                    title = str(item.get("title", "")).strip()
                    snippet = str(item.get("snippet", "")).strip()
                    source = str(item.get("url", "")).strip() or str(item.get("platform", "")).strip()
                    rel = _safe_float(item.get("query_relevance"), 0.72)
                    if rel < threshold:
                        continue
                    text = title if not snippet else f"{title}：{snippet[:80]}"
                    append_finding(
                        text,
                        source,
                        rel if rel > 0 else 0.72,
                        "用于筛选候选地点与时间安排" if language == "zh" else "Use for candidate selection and timing design.",
                    )
        elif tool in {"poi_search", "place_search"}:
            pois = output.get("pois", [])
            if isinstance(pois, list):
                for item in pois[:3]:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name", "")).strip()
                    address = str(item.get("address", "")).strip()
                    source = str(item.get("url", "")).strip()
                    rel = _safe_float(item.get("query_relevance"), 0.75)
                    if rel < threshold and "query_relevance" in item:
                        continue
                    append_finding(
                        f"{name}（{address[:60]}）" if address else name,
                        source,
                        rel,
                        "用于确定景点候选和动线" if language == "zh" else "Use for attraction ranking and routing.",
                    )
        elif tool in {"hotel_area_search", "hotel_search"}:
            areas = output.get("areas", [])
            if isinstance(areas, list):
                for item in areas[:3]:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name", "")).strip()
                    address = str(item.get("address", "")).strip()
                    source = str(item.get("url", "")).strip()
                    rel = _safe_float(item.get("query_relevance"), 0.74)
                    if rel < threshold and "query_relevance" in item:
                        continue
                    append_finding(
                        f"{name}（{address[:60]}）" if address else name,
                        source,
                        rel,
                        "用于住宿区域建议" if language == "zh" else "Use for hotel-area recommendations.",
                    )
        elif tool == "weather_search":
            location = str(output.get("location", "")).strip()
            forecast = output.get("forecast", [])
            if isinstance(forecast, list) and forecast:
                first = forecast[0] if isinstance(forecast[0], dict) else {}
                date = str(first.get("date", "")).strip()
                tmax = str(first.get("temp_max_c", "")).strip()
                tmin = str(first.get("temp_min_c", "")).strip()
                rain = str(first.get("precip_prob_max", "")).strip()
                if language == "zh":
                    text = f"{location} {date} 温度{tmin}-{tmax}℃ 降雨概率{rain}%".strip()
                    how = "用于排布室内/室外活动"
                else:
                    text = f"{location} {date} temperature {tmin}-{tmax}°C, precipitation probability {rain}%".strip()
                    how = "Use for indoor/outdoor activity sequencing."
                append_finding(text, "https://open-meteo.com/", 0.85, how)

    if not findings:
        return _fallback_task_analysis(task, task_results)

    return {
        "task_id": task.task_id,
        "key_findings": findings[:8],
        "conflicts": conflicts,
        "low_confidence_items": low_conf[:10],
        "summary_for_final_planner": (
            (f"Task {task.task_id} 提供了 {len(findings[:8])} 条可用证据，可用于 {task.information_need}。")
            if language == "zh"
            else f"Task {task.task_id} provides {len(findings[:8])} usable evidence items for {task.information_need}."
        ),
    }


def _analyze_single_task(
    task: SearchTask,
    task_results: list[dict[str, Any]],
) -> dict[str, Any]:
    filtered_results = _filter_task_results(task_results)
    if not filtered_results:
        return _fallback_task_analysis(task, task_results)
    if not _use_llm_result_analysis():
        return _fast_task_analysis(task, filtered_results)
    if not CLIENT.enabled:
        return _fallback_task_analysis(task, filtered_results)

    messages = CLIENT.build_messages(
        system_prompt="你是严谨的旅游信息分析模型，只输出 JSON。",
        developer_prompt=PROMPT_SEARCH_RESULT_ANALYZER,
        user_prompt=(
            "搜索任务：\n"
            f"{json.dumps(task.to_dict(), ensure_ascii=False)}\n\n"
            "搜索结果：\n"
            f"{json.dumps(filtered_results, ensure_ascii=False)}\n\n"
            "请仅输出 JSON。"
        ),
        conversation_history=None,
    )
    speed_mode = get_agent_speed_mode(default="quality")
    extra_body = _analysis_extra_body(speed_mode, "short")
    thinking_enabled = str(extra_body.get("thinking", {}).get("type", "")).lower() == "enabled"
    result = CLIENT.json_completion(
        messages=messages,
        temperature=0.1,
        max_tokens=1200,
        reasoning_effort=os.getenv("AGENT_REASONING_EFFORT", "medium") if thinking_enabled else None,
        extra_body=extra_body,
        model=_analysis_model(speed_mode, "short"),
        allow_model_fallback=False,
    )
    if not result.get("ok"):
        return _fallback_task_analysis(task, task_results)

    raw = result.get("json", {})
    if not isinstance(raw, dict):
        return _fallback_task_analysis(task, task_results)

    try:
        validated = validate_search_interpretation(raw, expected_task_id=task.task_id)
        return validated.model_dump()
    except Exception:
        return _fallback_task_analysis(task, task_results)


def _batch_analyze_tasks(
    search_plan: list[SearchTask],
    grouped_results: dict[str, list[dict[str, Any]]],
    speed_mode: str,
    complexity: str,
) -> list[dict[str, Any]]:
    if not search_plan:
        return []
    if not CLIENT.enabled:
        return []

    payload = {"tasks_with_results": _build_batch_payload(search_plan, grouped_results)}
    messages = CLIENT.build_messages(
        system_prompt="你是严谨的旅游信息分析模型，只输出 JSON。",
        developer_prompt=PROMPT_BATCH_SEARCH_RESULT_ANALYZER,
        user_prompt=(
            "搜索任务及结果（按 task 分组）：\n"
            f"{json.dumps(payload, ensure_ascii=False)}\n\n"
            "请仅输出 JSON。"
        ),
        conversation_history=None,
    )

    timeout_backup = CLIENT.timeout_sec
    extra_body = _analysis_extra_body(speed_mode, complexity)
    thinking_enabled = str(extra_body.get("thinking", {}).get("type", "")).lower() == "enabled"
    try:
        CLIENT.timeout_sec = _analysis_timeout_sec(complexity)
        result = CLIENT.json_completion(
            messages=messages,
            temperature=0.1,
            max_tokens=max(1400, int(os.getenv("AGENT_BATCH_ANALYSIS_MAX_TOKENS", "2200"))),
            reasoning_effort=os.getenv("AGENT_REASONING_EFFORT", "medium") if thinking_enabled else None,
            extra_body=extra_body,
            model=_analysis_model(speed_mode, complexity),
            allow_model_fallback=False,
        )
    finally:
        CLIENT.timeout_sec = timeout_backup
    if not result.get("ok"):
        return []
    raw = result.get("json", {})
    if not isinstance(raw, dict):
        return []

    rows = raw.get("task_result_interpretations")
    if not isinstance(rows, list):
        # Some models may directly return a single task object.
        if any(key in raw for key in ("task_id", "key_findings", "summary_for_final_planner")):
            rows = [raw]
        else:
            rows = []
    if not isinstance(rows, list):
        return []

    by_task: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        task_id = str(row.get("task_id", "")).strip()
        if not task_id:
            continue
        try:
            validated = validate_search_interpretation(row, expected_task_id=task_id).model_dump()
            by_task[task_id] = validated
        except Exception:
            continue

    analyses: list[dict[str, Any]] = []
    for task in search_plan:
        if task.task_id in by_task:
            analyses.append(by_task[task.task_id])
        else:
            analyses.append(_fallback_task_analysis(task, grouped_results.get(task.task_id, [])))
    return analyses


def interpret_search_results(
    user_input: str,
    requirement_understanding: dict[str, object],
    search_plan: list[SearchTask],
    tool_results: list[ToolExecutionResult],
    conversation_history: list[dict[str, str]] | None = None,
    cancel_event: object | None = None,
) -> dict[str, Any]:
    _ = user_input
    _ = requirement_understanding
    _ = conversation_history

    def is_cancelled() -> bool:
        return bool(cancel_event and getattr(cancel_event, "is_set", lambda: False)())

    grouped_results = _group_results_by_task(tool_results)
    response_language = "zh" if re.search(r"[\u4e00-\u9fff]", str(user_input or "")) else "en"

    if is_cancelled():
        return {
            "task_result_interpretations": [],
            "search_results_by_task": grouped_results,
            "sources": [],
        }

    trip_days = _extract_trip_days_hint(user_input)
    short_trip = 0 < trip_days <= 4
    speed_mode = get_agent_speed_mode(default="quality")
    complexity = _trip_complexity(trip_days, speed_mode)

    analyses: list[dict[str, Any]] = []
    if (not short_trip) and _use_batch_llm_analysis() and _use_llm_result_analysis() and CLIENT.enabled:
        analyses = _batch_analyze_tasks(
            search_plan=search_plan,
            grouped_results=grouped_results,
            speed_mode=speed_mode,
            complexity=complexity,
        )

    if not analyses:
        # Fast fallback: keep reliability and speed by local heuristic analysis.
        analyses = [
            _fast_task_analysis(
                task,
                _filter_task_results(grouped_results.get(task.task_id, [])),
                language=response_language,
            )
            for task in search_plan
        ]

    sources = _collect_sources_from_analyses(analyses, grouped_results, user_input=user_input)
    fallback_sources = _collect_sources_from_results_fallback(grouped_results)
    if not sources:
        sources = fallback_sources
    elif len(sources) < 6:
        seen_urls = {str(item.get("url", "")).strip() for item in sources if isinstance(item, dict)}
        for item in fallback_sources:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", "")).strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            sources.append(item)
            if len(sources) >= 12:
                break

    sources = _rank_and_dedupe_sources(sources, limit=16)

    return {
        "task_result_interpretations": analyses,
        "search_results_by_task": grouped_results,
        "sources": sources,
    }
