from __future__ import annotations

import html
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

from .runtime_flags import get_agent_speed_mode, runtime_truthy


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except Exception:
        return default
    return value if value > 0 else default


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except Exception:
        return default
    return value if value >= 0 else default


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _speed_mode() -> str:
    raw = (os.getenv("AGENT_SPEED_MODE", "quality") or "quality").strip().lower()
    if raw in {"fast", "aggressive", "极速"}:
        return "fast"
    if raw in {"quality", "high_quality", "accurate", "精确", "高质量"}:
        return "quality"
    return "balanced"


def _default_timeout_sec() -> float:
    mode = get_agent_speed_mode(default="quality")
    return _env_float("AGENT_HTTP_TIMEOUT_SEC", 5.0 if mode == "fast" else 10.0)


def _default_retries() -> int:
    mode = get_agent_speed_mode(default="quality")
    return _env_int("AGENT_HTTP_RETRIES", 0 if mode == "fast" else 1)


def _query_relevance_min() -> float:
    mode = get_agent_speed_mode(default="quality")
    return _env_float("AGENT_QUERY_RELEVANCE_MIN", 0.52 if mode == "fast" else 0.60)


def _query_relevance_fallback_min() -> float:
    mode = get_agent_speed_mode(default="quality")
    return _env_float("AGENT_QUERY_RELEVANCE_FALLBACK_MIN", 0.38 if mode == "fast" else 0.48)


def _enable_page_fetch() -> bool:
    return runtime_truthy("agent_enable_page_fetch", "AGENT_ENABLE_PAGE_FETCH", False)


def _page_fetch_max_items() -> int:
    enabled = _enable_page_fetch()
    default_items = 1 if enabled else 0
    return max(0, _env_int("AGENT_PAGE_FETCH_MAX_ITEMS", default_items))


def _page_fetch_workers() -> int:
    return max(1, _env_int("AGENT_PAGE_FETCH_WORKERS", 4))


def _page_fetch_timeout_sec() -> float:
    mode = get_agent_speed_mode(default="quality")
    return _env_float("AGENT_PAGE_FETCH_TIMEOUT_SEC", 2.2 if mode == "fast" else 3.0)
CJK_GENERIC_STOPWORDS = {
    "后天",
    "今天",
    "明天",
    "最新",
    "官方",
    "推荐",
    "旅游",
    "旅行",
    "攻略",
    "安排",
    "计划",
    "方案",
}
ROUTE_TAIL_TERMS = {
    "后天",
    "明天",
    "今天",
    "大后天",
    "航班",
    "机票",
    "高铁",
    "火车",
    "旅游",
    "天气",
    "酒店",
    "住宿",
    "攻略",
    "景点",
    "价格",
    "票价",
}


def _http_get_json(
    url: str,
    params: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
    retries: int | None = None,
) -> Any:
    req_timeout = _default_timeout_sec() if timeout is None else timeout
    req_retries = _default_retries() if retries is None else retries
    last_error: Exception | None = None
    for _ in range(req_retries + 1):
        try:
            with httpx.Client(timeout=req_timeout, follow_redirects=True) as client:
                response = client.get(url, params=params, headers=headers)
                response.raise_for_status()
                return response.json()
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"GET JSON failed: {last_error}")


def _http_get_text(
    url: str,
    params: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
    retries: int | None = None,
) -> str:
    req_timeout = _default_timeout_sec() if timeout is None else timeout
    req_retries = _default_retries() if retries is None else retries
    last_error: Exception | None = None
    for _ in range(req_retries + 1):
        try:
            with httpx.Client(timeout=req_timeout, follow_redirects=True) as client:
                response = client.get(url, params=params, headers=headers)
                response.raise_for_status()
                return response.text
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"GET text failed: {last_error}")


def _strip_html(value: str) -> str:
    no_tags = re.sub(r"<[^>]+>", "", value or "")
    compact = re.sub(r"\s+", " ", no_tags).strip()
    return html.unescape(compact)


def _extract_text_from_html_document(value: str, max_chars: int = 1400) -> str:
    text = value or ""
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = _strip_html(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def _fetch_page_excerpt(url: str) -> dict[str, str]:
    try:
        with httpx.Client(timeout=_page_fetch_timeout_sec(), follow_redirects=True) as client:
            response = client.get(url, headers={"User-Agent": "TouristAgent/1.0"})
            response.raise_for_status()
            body = response.text
    except Exception:
        return {}

    if not body:
        return {}

    title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", body)
    title = _strip_html(title_match.group(1)) if title_match else ""
    excerpt = _extract_text_from_html_document(body, max_chars=1800)
    if not excerpt:
        return {}
    return {"title": title, "content_excerpt": excerpt}


def _enrich_items_with_page_excerpt(items: list[dict[str, str]]) -> list[dict[str, str]]:
    if not _enable_page_fetch():
        return items
    fetch_limit = _page_fetch_max_items()
    if not items or fetch_limit <= 0:
        return items

    out = [dict(item) for item in items]
    candidate_indexes: list[int] = []
    for idx, row in enumerate(out):
        url = str(row.get("url", "")).strip()
        if not url.startswith("http"):
            continue
        candidate_indexes.append(idx)
        if len(candidate_indexes) >= fetch_limit:
            break
    if not candidate_indexes:
        return out

    with ThreadPoolExecutor(max_workers=min(_page_fetch_workers(), len(candidate_indexes))) as pool:
        future_map = {
            pool.submit(_fetch_page_excerpt, str(out[idx].get("url", "")).strip()): idx
            for idx in candidate_indexes
        }
        for future in as_completed(future_map):
            idx = future_map[future]
            try:
                fetched = future.result()
            except Exception:
                fetched = {}
            if not isinstance(fetched, dict) or not fetched:
                continue

            content_excerpt = str(fetched.get("content_excerpt", "")).strip()
            fetched_title = str(fetched.get("title", "")).strip()
            if content_excerpt:
                out[idx]["content_excerpt"] = content_excerpt
                current_snippet = str(out[idx].get("snippet", "")).strip()
                if len(content_excerpt) > len(current_snippet):
                    out[idx]["snippet"] = content_excerpt[:220]
            if fetched_title and len(fetched_title) >= 4:
                current_title = str(out[idx].get("title", "")).strip()
                if not current_title or len(current_title) < 4:
                    out[idx]["title"] = fetched_title
    return out


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in (text or ""))


def _dedupe_preserve_order(tokens: list[str], limit: int = 16) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        key = token.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(token.strip())
        if len(out) >= limit:
            break
    return out


def _extract_route_pairs(text: str) -> list[tuple[str, str]]:
    def clean_city_candidate(raw: str) -> str:
        value = raw.strip()
        changed = True
        while changed:
            changed = False
            for suffix in sorted(ROUTE_TAIL_TERMS, key=len, reverse=True):
                if value.endswith(suffix) and len(value) > len(suffix):
                    value = value[: -len(suffix)].strip()
                    changed = True
        return value

    pairs: list[tuple[str, str]] = []
    for from_city, to_city in re.findall(r"([\u4e00-\u9fff]{2,8})到([\u4e00-\u9fff]{2,12})", text):
        from_clean = clean_city_candidate(from_city)
        to_clean = clean_city_candidate(to_city)
        if not from_clean or not to_clean:
            continue
        if len(from_clean) > 6 or len(to_clean) > 6:
            continue
        if from_clean != to_clean:
            pairs.append((from_clean, to_clean))
    return pairs


def _query_tokens(query: str) -> list[str]:
    normalized = _normalize_spaces(query).lower()
    if not normalized:
        return []

    latin_stopwords = {"travel", "trip", "plan", "guide", "itinerary", "best", "latest", "official"}

    tokens: list[str] = []

    for from_city, to_city in _extract_route_pairs(normalized):
        tokens.extend([from_city, to_city, f"{from_city}到{to_city}"])

    for days_text in re.findall(r"[0-9]+\s*天", normalized):
        tokens.append(days_text.replace(" ", ""))

    cjk_runs = re.findall(r"[\u4e00-\u9fff]{2,24}", normalized)
    for run in cjk_runs:
        cleaned = run
        cleaned = cleaned.replace("到", " ")
        for stop in sorted(CJK_GENERIC_STOPWORDS, key=len, reverse=True):
            cleaned = cleaned.replace(stop, " ")
        for segment in re.split(r"\s+", cleaned):
            seg = segment.strip()
            if len(seg) < 2:
                continue
            tokens.append(seg)
            if len(seg) >= 3:
                tokens.append(seg[:2])
            if len(seg) >= 4:
                tokens.append(seg[:3])
                tokens.append(seg[-2:])

    # Preserve non-CJK signals, e.g. "Disney", "Pudong", "PVG".
    chunks = re.split(r"[\s,，。；;、|/()（）\-]+", normalized)
    for chunk in chunks:
        token = chunk.strip()
        if not token:
            continue
        if _contains_cjk(token):
            continue
        if re.fullmatch(r"[0-9]+", token):
            continue
        token = re.sub(r"[^a-z0-9_]+", "", token)
        if len(token) <= 1:
            continue
        if token in latin_stopwords:
            continue
        tokens.append(token)

    return _dedupe_preserve_order(tokens, limit=16)


def _cjk_char_overlap_ratio(query: str, text: str) -> float:
    def normalized_cjk_runs(value: str) -> list[str]:
        lowered = value.lower()
        for stop in sorted(CJK_GENERIC_STOPWORDS, key=len, reverse=True):
            lowered = lowered.replace(stop, " ")
        lowered = lowered.replace("到", " ")
        return re.findall(r"[\u4e00-\u9fff]{2,24}", lowered)

    query_runs = normalized_cjk_runs(query)
    text_runs = normalized_cjk_runs(text)
    if not query_runs or not text_runs:
        return 0.0

    query_grams: set[str] = set()
    for run in query_runs:
        if len(run) == 2:
            query_grams.add(run)
            continue
        for idx in range(0, len(run) - 1):
            query_grams.add(run[idx : idx + 2])

    text_grams: set[str] = set()
    for run in text_runs:
        if len(run) == 2:
            text_grams.add(run)
            continue
        for idx in range(0, len(run) - 1):
            text_grams.add(run[idx : idx + 2])

    if not query_grams or not text_grams:
        return 0.0
    return len(query_grams & text_grams) / max(1, len(query_grams))


def _text_relevance_score(query: str, title: str, snippet: str) -> float:
    tokens = _query_tokens(query)
    if not tokens:
        return 1.0

    text = f"{(title or '').lower()} {(snippet or '').lower()}".strip()
    if not text:
        return 0.0

    matched_weight = 0.0
    total_weight = 0.0
    for token in tokens:
        weight = 1.0
        if len(token) >= 4 and not _contains_cjk(token):
            weight = 1.2
        if any("\u4e00" <= ch <= "\u9fff" for ch in token) and len(token) >= 2:
            weight = 1.3
        total_weight += weight
        if token in text:
            matched_weight += weight

    token_score = (matched_weight / total_weight) if total_weight > 0 else 0.0
    overlap_score = _cjk_char_overlap_ratio(query, text)

    route_boost = 0.0
    route_penalty = 0.0
    for from_city, to_city in _extract_route_pairs(query):
        from_hit = from_city.lower() in text
        to_hit = to_city.lower() in text
        if from_hit and to_hit:
            route_boost = max(route_boost, 0.24)
        elif from_hit or to_hit:
            route_penalty = max(route_penalty, 0.24)

    compact_query = re.sub(r"\s+", "", query.strip().lower())
    exact_boost = 0.12 if compact_query and compact_query in re.sub(r"\s+", "", text) else 0.0

    base_score = max(token_score, overlap_score * 0.95)
    final_score = base_score + route_boost + exact_boost - route_penalty
    return min(1.0, max(0.0, final_score))


def _canonicalize_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urllib.parse.urlparse(raw)
        query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        filtered_pairs = [
            (k, v)
            for k, v in query_pairs
            if k.lower()
            not in {
                "utm_source",
                "utm_medium",
                "utm_campaign",
                "utm_term",
                "utm_content",
                "gclid",
                "fbclid",
                "msclkid",
            }
        ]
        normalized = parsed._replace(query=urllib.parse.urlencode(filtered_pairs), fragment="")
        return urllib.parse.urlunparse(normalized)
    except Exception:
        return raw


def _domain(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.lower().strip()
    except Exception:
        return ""


def _domain_quality_penalty(url: str) -> float:
    domain = _domain(url)
    if not domain:
        return 0.0
    high_signal_domains = (
        "wikipedia.org",
        "wikivoyage.org",
        "tripadvisor.com",
        "booking.com",
        "skyscanner.",
        "trip.com",
        "ctrip.com",
        "govt.nz",
        "newzealand.com",
    )
    for item in high_signal_domains:
        if item in domain:
            return -0.06
    low_signal_domains = (
        "zhihu.com",
        "zhidao.baidu.com",
        "tieba.baidu.com",
        "baijiahao.baidu.com",
        "sohu.com",
        "163.com",
        "ixigua.com",
        "bilibili.com",
        "taobao.com",
        "fliggy.com",
        "travelarbitrage.",
    )
    for item in low_signal_domains:
        if item in domain:
            return 0.14
    return 0.0


def _merge_domain_duplicates(items: list[dict[str, str]]) -> list[dict[str, str]]:
    if not items:
        return []

    by_url: dict[str, dict[str, str]] = {}
    for item in items:
        row = dict(item)
        canonical_url = _canonicalize_url(str(row.get("url", "")))
        if not canonical_url:
            continue
        row["url"] = canonical_url
        key = canonical_url.lower()
        if key in by_url:
            if len(str(row.get("snippet", ""))) > len(str(by_url[key].get("snippet", ""))):
                by_url[key] = row
            continue
        by_url[key] = row

    merged: list[dict[str, str]] = []
    seen_domain_fingerprint: set[tuple[str, str]] = set()
    for row in by_url.values():
        title = _normalize_spaces(str(row.get("title", ""))).lower()
        snippet = _normalize_spaces(str(row.get("snippet", ""))).lower()
        domain = _domain(str(row.get("url", "")))
        fingerprint = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", f"{title} {snippet}")[:80]
        key = (domain, fingerprint)
        if key in seen_domain_fingerprint:
            continue
        seen_domain_fingerprint.add(key)
        merged.append(row)
    return merged


def _filter_items_by_query(
    items: list[dict[str, str]],
    query: str,
    min_score: float | None = None,
) -> list[dict[str, str]]:
    threshold = _query_relevance_min() if min_score is None else min_score
    if not items:
        return []

    scored: list[tuple[float, dict[str, str]]] = []
    for item in items:
        title = str(item.get("title", ""))
        snippet = str(item.get("snippet", ""))
        score = _text_relevance_score(query, title, snippet)
        score = max(0.0, score - _domain_quality_penalty(str(item.get("url", ""))))
        row = dict(item)
        row["query_relevance"] = f"{score:.3f}"
        scored.append((score, row))

    filtered = [row for score, row in scored if score >= threshold]
    if filtered:
        return _merge_domain_duplicates(filtered)

    # If all items are below threshold, keep only one fallback row when signal is not trivial.
    scored.sort(key=lambda x: x[0], reverse=True)
    top_score, top_item = scored[0]
    if top_score >= _query_relevance_fallback_min():
        return _merge_domain_duplicates([top_item])
    return []


def _to_platform(url: str) -> str:
    lowered = (url or "").lower()
    if "wikipedia.org" in lowered:
        return "wikipedia"
    if "xiaohongshu.com" in lowered:
        return "xiaohongshu"
    if "zhihu.com" in lowered:
        return "zhihu"
    if "tripadvisor" in lowered:
        return "tripadvisor"
    if "ctrip" in lowered or "trip.com" in lowered:
        return "trip"
    if "duckduckgo.com" in lowered:
        return "duckduckgo"
    if "openstreetmap.org" in lowered:
        return "openstreetmap"
    return "web"


def _decode_duckduckgo_href(href: str) -> str:
    value = html.unescape((href or "").strip())
    if not value:
        return value
    if value.startswith("//duckduckgo.com/l/?"):
        try:
            parsed = urllib.parse.urlparse(f"https:{value}")
            qs = urllib.parse.parse_qs(parsed.query)
            resolved = qs.get("uddg", [""])[0]
            if resolved:
                return resolved
        except Exception:
            return value
    return value


def _bing_rss_search(query: str, limit: int = 8) -> list[dict[str, str]]:
    xml_text = _http_get_text(
        "https://www.bing.com/search",
        params={
            "q": query,
            "format": "rss",
            "setlang": "zh-CN",
            "mkt": "zh-CN",
        },
        headers={"User-Agent": "TouristAgent/1.0"},
    )
    root = ET.fromstring(xml_text)

    items: list[dict[str, str]] = []
    seen = set()
    for node in root.findall("./channel/item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        snippet = _strip_html(node.findtext("description") or "")
        if not title or not link:
            continue
        if link in seen:
            continue
        seen.add(link)
        items.append(
            {
                "title": title,
                "url": link,
                "snippet": snippet,
                "platform": _to_platform(link),
            }
        )
        if len(items) >= limit:
            break
    return _filter_items_by_query(items, query=query)


def _duckduckgo_html_search(query: str, limit: int = 8) -> list[dict[str, str]]:
    html_text = _http_get_text(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers={"User-Agent": "Mozilla/5.0 (compatible; TouristAgent/1.0)"},
    )

    items: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    matches = list(
        re.finditer(
            r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>',
            html_text,
            re.I,
        )
    )
    for match in matches:
        raw_url = _decode_duckduckgo_href(match.group(1))
        url = _canonicalize_url(raw_url)
        if not url:
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)

        title = _strip_html(match.group(2))
        snippet = ""
        tail = html_text[match.end() : match.end() + 1800]
        snippet_match = re.search(
            r'(?:<a[^>]*class="result__snippet"[^>]*>|<div[^>]*class="result__snippet"[^>]*>)([\s\S]*?)(?:</a>|</div>)',
            tail,
            re.I,
        )
        if snippet_match:
            snippet = _strip_html(snippet_match.group(1))

        if not title:
            continue
        items.append(
            {
                "title": title,
                "url": url,
                "snippet": snippet,
                "platform": _to_platform(url),
            }
        )
        if len(items) >= limit:
            break

    return _filter_items_by_query(items, query=query)


def _relax_query(query: str) -> str:
    text = _normalize_spaces(query)
    if not text:
        return text
    for pattern in [
        r"后天",
        r"明天",
        r"今天",
        r"大后天",
        r"最新",
        r"实时",
        r"多少钱",
        r"怎么安排",
        r"应该",
    ]:
        text = re.sub(pattern, " ", text)
    text = _normalize_spaces(text)
    return text or query


def _multi_source_search(query: str, limit: int = 8) -> list[dict[str, str]]:
    # Prefer Bing RSS for Chinese travel intent, then blend DDG for recall.
    bing_items: list[dict[str, str]] = []
    try:
        bing_items = _bing_rss_search(query, limit=limit)
    except Exception:
        bing_items = []

    ddg_items: list[dict[str, str]] = []
    try:
        ddg_items = _duckduckgo_html_search(query, limit=limit)
    except Exception:
        ddg_items = []

    merged = _merge_domain_duplicates(bing_items + ddg_items)
    if merged:
        merged = _filter_items_by_query(merged, query=query)
        if merged:
            return merged[:limit]

    relaxed_query = _relax_query(query)
    if relaxed_query != query:
        fallback_items: list[dict[str, str]] = []
        try:
            fallback_items.extend(_bing_rss_search(relaxed_query, limit=limit))
        except Exception:
            pass
        try:
            fallback_items.extend(_duckduckgo_html_search(relaxed_query, limit=limit))
        except Exception:
            pass
        fallback_merged = _merge_domain_duplicates(fallback_items)
        fallback = _filter_items_by_query(fallback_merged, query=query)
        return fallback[:limit]

    return []


def _hybrid_web_search(query: str, limit: int = 8) -> list[dict[str, str]]:
    try:
        return _multi_source_search(query, limit=limit)
    except Exception:
        return []


def _nominatim_search(query: str, limit: int = 8) -> list[dict[str, Any]]:
    data = _http_get_json(
        "https://nominatim.openstreetmap.org/search",
        params={"q": query, "format": "jsonv2", "limit": limit},
        headers={"User-Agent": "TouristAgent/1.0 (educational project)"},
    )
    return data if isinstance(data, list) else []


def _osm_map_url(item: dict[str, Any]) -> str:
    lat = str(item.get("lat", "")).strip()
    lon = str(item.get("lon", "")).strip()
    if not lat or not lon:
        return ""
    return f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=12/{lat}/{lon}"


def _top_geo(location: str) -> dict[str, Any] | None:
    data = _http_get_json(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": location, "count": 1, "format": "json"},
        headers={"User-Agent": "TouristAgent/1.0"},
    )
    results = data.get("results") if isinstance(data, dict) else None
    if isinstance(results, list) and results:
        return results[0]

    # Fallback: resolve coordinates via OSM nominatim, then query Open-Meteo with that point.
    try:
        rows = _nominatim_search(location, limit=1)
        if rows:
            row = rows[0]
            lat = row.get("lat")
            lon = row.get("lon")
            if lat is not None and lon is not None:
                return {
                    "latitude": float(lat),
                    "longitude": float(lon),
                    "name": str(row.get("name") or row.get("display_name", location)).split(",")[0].strip(),
                    "admin1": "",
                    "country": "",
                }
    except Exception:
        pass
    return None


def _extract_weather_location(query: str) -> str:
    text = _normalize_spaces(query)
    if not text:
        return query

    # Remove obvious weather/time words that break geocoding.
    patterns = [
        r"天气预报",
        r"天气",
        r"未来[一二三四五六七0-9]+天",
        r"[0-9]+天",
        r"后天",
        r"明天",
        r"今天",
        r"气温",
        r"降雨",
    ]
    cleaned = text
    for pattern in patterns:
        cleaned = re.sub(pattern, " ", cleaned)
    cleaned = _normalize_spaces(cleaned)

    if not cleaned:
        return query

    # Prefer the first CJK location-like segment.
    chunks = re.split(r"[\s,，。；;、|/()（）\-]+", cleaned)
    for chunk in chunks:
        token = chunk.strip()
        if not token:
            continue
        if any("\u4e00" <= ch <= "\u9fff" for ch in token):
            return token
    return cleaned


class LiveTravelToolset:
    def search_web(self, query: str, limit: int = 8) -> dict[str, Any]:
        try:
            items = _hybrid_web_search(query, limit=limit)
            items = _enrich_items_with_page_excerpt(items)
            return {
                "query": query,
                "items": items,
                "count": len(items),
                "source": "hybrid_search",
            }
        except Exception as exc:
            return {"query": query, "error": str(exc), "items": [], "source": "hybrid_search"}

    def search_places(self, query: str, limit: int = 10) -> dict[str, Any]:
        try:
            query_text = str(query or "").strip()
            query_lc = query_text.lower()
            attraction_intent = any(
                marker in query_text
                for marker in ("景点", "必去", "游玩", "路线", "玩法", "攻略")
            ) or any(
                marker in query_lc
                for marker in ("attraction", "must-see", "must see", "itinerary", "things to do", "sightseeing")
            )
            if attraction_intent:
                web_items = _hybrid_web_search(query_text, limit=max(5, min(limit, 10)))
                if web_items:
                    pois_from_web: list[dict[str, Any]] = []
                    for item in web_items:
                        if not isinstance(item, dict):
                            continue
                        title = str(item.get("title", "")).strip()
                        url = str(item.get("url", "")).strip()
                        snippet = str(item.get("snippet", "")).strip()
                        if not title:
                            continue
                        pois_from_web.append(
                            {
                                "name": title,
                                "address": snippet,
                                "type": "web_result",
                                "lat": "",
                                "lon": "",
                                "url": url,
                                "query_relevance": str(item.get("query_relevance", "")),
                            }
                        )
                    if pois_from_web:
                        return {
                            "query": query_text,
                            "pois": pois_from_web,
                            "count": len(pois_from_web),
                            "source": "hybrid_search",
                        }

            rows = _nominatim_search(query, limit=limit)
            pois: list[dict[str, Any]] = []
            for item in rows:
                name = (item.get("name") or item.get("display_name", "").split(",")[0] or "").strip()
                if not name:
                    continue
                pois.append(
                    {
                        "name": name,
                        "address": str(item.get("display_name", "")).strip(),
                        "type": str(item.get("type", "")).strip(),
                        "lat": str(item.get("lat", "")).strip(),
                        "lon": str(item.get("lon", "")).strip(),
                        "url": _osm_map_url(item),
                    }
                )
            if not pois:
                fallback_items = _hybrid_web_search(query, limit=max(3, min(limit, 10)))
                for item in fallback_items:
                    if not isinstance(item, dict):
                        continue
                    title = str(item.get("title", "")).strip()
                    url = str(item.get("url", "")).strip()
                    if not title or not url:
                        continue
                    pois.append(
                        {
                            "name": title,
                            "address": str(item.get("snippet", "")).strip(),
                            "type": "web_result",
                            "lat": "",
                            "lon": "",
                            "url": url,
                            "query_relevance": str(item.get("query_relevance", "")),
                        }
                    )
            return {
                "query": query,
                "pois": pois,
                "count": len(pois),
                "source": "openstreetmap_nominatim" if rows else "hybrid_search_fallback",
            }
        except Exception as exc:
            return {"query": query, "error": str(exc), "pois": [], "source": "openstreetmap_nominatim"}

    def search_hotels(self, query: str, limit: int = 8) -> dict[str, Any]:
        try:
            rows = _nominatim_search(query, limit=limit)
            hotels: list[dict[str, Any]] = []
            for item in rows:
                name = (item.get("name") or item.get("display_name", "").split(",")[0] or "").strip()
                if not name:
                    continue
                hotels.append(
                    {
                        "name": name,
                        "address": str(item.get("display_name", "")).strip(),
                        "lat": str(item.get("lat", "")).strip(),
                        "lon": str(item.get("lon", "")).strip(),
                        "url": _osm_map_url(item),
                    }
                )
            if not hotels:
                fallback_items = _hybrid_web_search(query, limit=max(3, min(limit, 10)))
                for item in fallback_items:
                    if not isinstance(item, dict):
                        continue
                    title = str(item.get("title", "")).strip()
                    url = str(item.get("url", "")).strip()
                    if not title or not url:
                        continue
                    hotels.append(
                        {
                            "name": title,
                            "address": str(item.get("snippet", "")).strip(),
                            "lat": "",
                            "lon": "",
                            "url": url,
                            "query_relevance": str(item.get("query_relevance", "")),
                        }
                    )
            return {
                "query": query,
                "areas": hotels,
                "count": len(hotels),
                "source": "openstreetmap_nominatim" if rows else "hybrid_search_fallback",
            }
        except Exception as exc:
            return {"query": query, "error": str(exc), "areas": [], "source": "openstreetmap_nominatim"}

    def search_transport(self, query: str, limit: int = 8) -> dict[str, Any]:
        try:
            items = _hybrid_web_search(query, limit=limit)
            items = _enrich_items_with_page_excerpt(items)
            return {
                "query": query,
                "transport_items": items,
                "count": len(items),
                "source": "hybrid_search",
            }
        except Exception as exc:
            return {"query": query, "error": str(exc), "transport_items": [], "source": "hybrid_search"}

    def search_weather(self, location: str, date_range: str | None = None) -> dict[str, Any]:
        try:
            normalized_location = _extract_weather_location(location)
            geo = _top_geo(normalized_location)
            if not geo:
                return {
                    "location": normalized_location,
                    "date_range": date_range,
                    "forecast": [],
                    "error": "Location not found",
                    "source": "open-meteo",
                }

            weather = _http_get_json(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": geo["latitude"],
                    "longitude": geo["longitude"],
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
                    "forecast_days": 7,
                    "timezone": "auto",
                },
                headers={"User-Agent": "TouristAgent/1.0"},
            )

            daily = weather.get("daily", {}) if isinstance(weather, dict) else {}
            days: list[dict[str, Any]] = []
            times = daily.get("time", []) if isinstance(daily.get("time", []), list) else []
            for idx, day in enumerate(times):
                days.append(
                    {
                        "date": day,
                        "temp_max_c": (daily.get("temperature_2m_max", [None]) or [None])[idx],
                        "temp_min_c": (daily.get("temperature_2m_min", [None]) or [None])[idx],
                        "precip_prob_max": (daily.get("precipitation_probability_max", [None]) or [None])[idx],
                        "weather_code": (daily.get("weather_code", [None]) or [None])[idx],
                    }
                )

            display_name_parts = [
                str(geo.get("name", "")).strip(),
                str(geo.get("admin1", "")).strip(),
                str(geo.get("country", "")).strip(),
            ]
            display_name = ", ".join([p for p in display_name_parts if p]) or location

            return {
                "location": display_name,
                "date_range": date_range,
                "forecast": days,
                "source": "open-meteo",
            }
        except Exception as exc:
            return {
                "location": location,
                "date_range": date_range,
                "forecast": [],
                "error": str(exc),
                "source": "open-meteo",
            }
