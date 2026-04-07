import json
import operator
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from dotenv import load_dotenv
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph

try:
    from planner import Plan, create_planner
except ImportError:
    print("ERROR: Could not import planner.py. Make sure it exists in the same folder.")
    sys.exit(1)

load_dotenv()


class PlanExecuteState(TypedDict, total=False):
    input: str
    user_preferences: Dict[str, Any]
    plan: List[str]
    step_results: Annotated[List[Dict[str, Any]], operator.add]
    response: str


WEATHER_CODE_MAP = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    80: "Rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    95: "Thunderstorm",
}

CHINESE_NUM_MAP = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

CURATED_CN_ATTRACTIONS = {
    "甘肃": [
        "敦煌莫高窟",
        "鸣沙山月牙泉",
        "嘉峪关关城",
        "张掖七彩丹霞",
        "麦积山石窟",
        "扎尕那",
        "甘南拉卜楞寺",
        "黄河石林",
    ],
    "成都": [
        "成都大熊猫繁育研究基地",
        "都江堰",
        "青城山",
        "宽窄巷子",
        "锦里古街",
        "武侯祠",
    ],
}

CURATED_CN_ATTRACTION_REASONS = {
    "甘肃": {
        "敦煌莫高窟": "世界文化遗产，壁画与彩塑艺术价值极高。",
        "鸣沙山月牙泉": "沙漠奇观与绿洲同框，日落景色非常出片。",
        "嘉峪关关城": "明长城西端核心关隘，历史感强。",
        "张掖七彩丹霞": "丹霞地貌色彩层次丰富，适合摄影。",
        "麦积山石窟": "石窟雕塑精美，兼具人文与自然景观。",
        "扎尕那": "山地村落与草甸风光结合，徒步体验好。",
        "甘南拉卜楞寺": "藏传文化氛围浓厚，转经长廊有代表性。",
        "黄河石林": "黄河峡谷与石林地貌结合，地貌独特。",
    }
}

CN_STOPWORDS = {
    "旅游",
    "旅行",
    "景点",
    "好玩",
    "有哪些",
    "推荐",
    "地方",
    "线路",
    "攻略",
    "中国",
}


def _contains_chinese(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def _normalize_destination_name(destination: str) -> str:
    text = destination.strip().replace("中国", "").strip()
    suffixes = [
        "回族自治区",
        "维吾尔自治区",
        "壮族自治区",
        "自治区",
        "特别行政区",
        "省",
        "市",
    ]
    for suffix in suffixes:
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)].strip()
            break
    return text or destination.strip()


def _extract_chinese_location(text: str) -> str:
    matches = re.findall(r"(?:中国)?([\u4e00-\u9fff]{2,12}(?:省|市|自治区|特别行政区)?)", text)
    for raw in matches:
        candidate = _normalize_destination_name(raw)
        if candidate and candidate not in CN_STOPWORDS:
            return candidate
    return ""


def _is_attraction_intent(text: str) -> bool:
    if _contains_keyword(text, ["attractions", "things to do", "must see", "sightseeing"]):
        return True
    return bool(re.search(r"(好玩|景点|推荐|必去|打卡|玩什么|哪里玩)", text))

def _parse_simple_chinese_number(text: str) -> int | None:
    if not text:
        return None
    if text in CHINESE_NUM_MAP:
        return CHINESE_NUM_MAP[text]
    if len(text) == 2 and text[0] == "十" and text[1] in CHINESE_NUM_MAP:
        return 10 + CHINESE_NUM_MAP[text[1]]
    if len(text) == 2 and text[1] == "十" and text[0] in CHINESE_NUM_MAP:
        return CHINESE_NUM_MAP[text[0]] * 10
    if len(text) == 3 and text[1] == "十" and text[0] in CHINESE_NUM_MAP and text[2] in CHINESE_NUM_MAP:
        return CHINESE_NUM_MAP[text[0]] * 10 + CHINESE_NUM_MAP[text[2]]
    return None


def _contains_keyword(text: str, keywords: List[str]) -> bool:
    lower = text.lower()
    for kw in keywords:
        if " " in kw:
            if kw in lower:
                return True
        else:
            if re.search(rf"\b{re.escape(kw)}\b", lower):
                return True
    return False


def _http_get_json(
    base_url: str,
    params: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 15,
    retries: int = 2,
) -> Any:
    query_string = urllib.parse.urlencode(params, doseq=True)
    url = f"{base_url}?{query_string}"
    req_headers = headers or {"User-Agent": "TouristAgent/1.0"}
    last_error = None
    for _ in range(retries + 1):
        try:
            request = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as e:
            last_error = e
    raise RuntimeError(f"HTTP request failed after retries: {last_error}")


def _extract_destination(user_input: str) -> str:
    cn_match = re.search(
        r"(?:我想要去|我想去|想要去|想去|去|到|前往)\s*([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9·\-\s]{0,20}?)(?=旅游|旅行|游玩|玩|待|住|[0-9一二三四五六七八九十两]|\s*$)",
        user_input,
    )
    if cn_match:
        destination = cn_match.group(1).strip(" ，。,.")
        if destination:
            return _normalize_destination_name(destination)

    location = _extract_chinese_location(user_input)
    if location:
        return location

    match = re.search(r"\bto\s+([A-Za-z][A-Za-z\s\-]{1,50})", user_input, re.IGNORECASE)
    if not match:
        return ""

    destination = match.group(1).strip(" .,")
    for stop_word in [" with ", " for ", " on ", " including "]:
        idx = destination.lower().find(stop_word)
        if idx != -1:
            destination = destination[:idx].strip()
    return _normalize_destination_name(destination)


def _extract_days(text: str) -> int:
    patterns = [r"(\d+)\s*-\s*day", r"(\d+)\s*day", r"(\d+)\s*days", r"(\d+)\s*天"]
    lower = text.lower()
    for pattern in patterns:
        match = re.search(pattern, lower)
        if match:
            return max(1, int(match.group(1)))
    cn_days = re.search(r"([一二三四五六七八九十两]{1,3})\s*天", text)
    if cn_days:
        parsed = _parse_simple_chinese_number(cn_days.group(1))
        if parsed:
            return max(1, parsed)
    return 3


def _nominatim_search(query: str, limit: int = 5, include_extra_tags: bool = False) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "q": query,
        "format": "jsonv2",
        "limit": limit,
    }
    if include_extra_tags:
        params["extratags"] = 1

    result = _http_get_json(
        "https://nominatim.openstreetmap.org/search",
        params=params,
        headers={"User-Agent": "TouristAgent/1.0 (educational project)"},
    )
    return result if isinstance(result, list) else []


@tool
def search_places(query: str) -> Dict[str, Any]:
    """Search for attractions and travel-relevant points using Wikipedia search."""
    try:
        cleaned_query = re.sub(
            r"^(search for|find|check|verify|research|plan|create)\s+",
            "",
            query.strip(),
            flags=re.IGNORECASE,
        )
        destination = _extract_destination(cleaned_query) or _extract_chinese_location(cleaned_query)
        destination_key = _normalize_destination_name(destination) if destination else ""

        if _contains_chinese(cleaned_query) or destination_key:
            attractions: List[str] = []
            snippets: List[str] = []
            seen = set()

            for item in CURATED_CN_ATTRACTIONS.get(destination_key, []):
                key = item.strip().lower()
                if key and key not in seen:
                    attractions.append(item)
                    snippets.append(f"精选推荐：{item}")
                    seen.add(key)

            cn_queries = []
            if destination_key:
                cn_queries.extend(
                    [
                        f"{destination_key} 旅游景点",
                        f"{destination_key} 必去景点",
                    ]
                )
            cn_queries.append(cleaned_query)

            for q in cn_queries:
                try:
                    osm_results = _nominatim_search(q, limit=6)
                except Exception:
                    osm_results = []
                for item in osm_results:
                    name = (item.get("name") or item.get("display_name", "").split(",")[0]).strip()
                    if not name:
                        continue
                    key = name.lower()
                    if key in seen:
                        continue
                    attractions.append(name)
                    snippets.append(item.get("display_name", ""))
                    seen.add(key)
                    if len(attractions) >= 10:
                        break
                if len(attractions) >= 10:
                    break

            return {
                "query": cleaned_query,
                "attractions": attractions[:10],
                "snippets": snippets[:10],
                "source": "Curated + OpenStreetMap Nominatim",
            }

        data = _http_get_json(
            "https://en.wikipedia.org/w/api.php",
            {
                "action": "query",
                "list": "search",
                "srsearch": cleaned_query,
                "format": "json",
                "srlimit": 8,
                "utf8": 1,
            },
        )
        raw_results = data.get("query", {}).get("search", [])
        attractions = [item.get("title", "") for item in raw_results if item.get("title")]
        snippets = [re.sub(r"<.*?>", "", item.get("snippet", "")) for item in raw_results]
        return {
            "query": cleaned_query,
            "attractions": attractions[:8],
            "snippets": snippets[:8],
            "source": "Wikipedia API",
        }
    except Exception as e:
        return {"error": f"search_places failed: {e}", "query": query}


@tool
def check_weather(location: str) -> Dict[str, Any]:
    """Get current weather from Open-Meteo by first geocoding location via Nominatim."""
    try:
        geo = _nominatim_search(location, limit=1)
        if not geo:
            return {"error": "Location not found", "location": location}

        lat = geo[0].get("lat")
        lon = geo[0].get("lon")
        display_name = geo[0].get("display_name", location)

        weather = _http_get_json(
            "https://api.open-meteo.com/v1/forecast",
            {
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,weather_code,wind_speed_10m",
                "timezone": "auto",
            },
        )

        current = weather.get("current", {})
        weather_code = int(current.get("weather_code", -1))
        return {
            "location": display_name,
            "temperature_c": current.get("temperature_2m"),
            "wind_speed_kmh": current.get("wind_speed_10m"),
            "weather": WEATHER_CODE_MAP.get(weather_code, f"Unknown({weather_code})"),
            "source": "Open-Meteo + Nominatim",
        }
    except Exception as e:
        return {"error": f"check_weather failed: {e}", "location": location}


@tool
def find_hotels(location: str) -> Dict[str, Any]:
    """Find hotel candidates using OpenStreetMap Nominatim search."""
    try:
        results = _nominatim_search(f"hotels in {location}", limit=5)
        hotels: List[Dict[str, str]] = []
        for item in results:
            hotels.append(
                {
                    "name": item.get("name") or item.get("display_name", "Unknown hotel"),
                    "address": item.get("display_name", "N/A"),
                }
            )

        return {
            "location": location,
            "hotels": hotels,
            "count": len(hotels),
            "source": "OpenStreetMap Nominatim",
        }
    except Exception as e:
        return {"error": f"find_hotels failed: {e}", "location": location}


@tool
def estimate_transport(step: str) -> Dict[str, Any]:
    """Provide practical transport guidance and rough daily transport cost estimates."""
    lower = step.lower()
    if any(k in lower for k in ["kyoto", "japan", "tokyo"]):
        estimate = {"daily_cost_usd": "8-15", "primary_modes": ["subway", "bus", "walk"]}
    else:
        estimate = {"daily_cost_usd": "10-20", "primary_modes": ["metro", "bus", "walk"]}

    return {
        "step": step,
        "transport_advice": [
            "Group nearby attractions into the same half-day block.",
            "Prioritize public transit passes when taking 3+ rides per day.",
            "Keep a 20-30 minute buffer between major activities.",
        ],
        "estimate": estimate,
        "source": "Heuristic transport estimator",
    }


@tool
def estimate_budget(context: str) -> Dict[str, Any]:
    """Estimate total budget based on inferred trip length and a mid-range travel profile."""
    days = _extract_days(context)
    daily_food = 35
    daily_transport = 12
    daily_attractions = 20
    daily_hotel = 70
    daily_total = daily_food + daily_transport + daily_attractions + daily_hotel

    return {
        "days": days,
        "currency": "USD",
        "breakdown_per_day": {
            "food": daily_food,
            "transport": daily_transport,
            "attractions": daily_attractions,
            "hotel": daily_hotel,
        },
        "estimated_total": daily_total * days,
        "source": "Rule-based budget estimator",
    }


TOOL_REGISTRY = {
    "search_places": search_places,
    "check_weather": check_weather,
    "find_hotels": find_hotels,
    "estimate_transport": estimate_transport,
    "estimate_budget": estimate_budget,
}


def _select_tool(step: str, destination: str, user_input: str) -> Dict[str, Any]:
    target = destination or _extract_destination(user_input) or step

    if _contains_keyword(step, ["weather", "temperature", "climate", "forecast"]):
        return {"tool": "check_weather", "tool_input": {"location": target}}
    is_stay_step = _contains_keyword(
        step,
        ["hotel", "hotels", "accommodation", "accommodations", "guesthouse", "guesthouses", "hostel", "hostels", "ryokan"],
    )
    is_budget_calc_step = _contains_keyword(step, ["calculate", "breakdown", "total budget", "sum", "estimated costs"])
    is_transport_step = _contains_keyword(step, ["transport", "route", "bus", "train", "metro", "taxi", "travel time"])
    has_budget_word = _contains_keyword(step, ["budget", "cost", "expense", "price"])

    if is_stay_step and not is_budget_calc_step:
        return {"tool": "find_hotels", "tool_input": {"location": target}}
    if is_budget_calc_step and _contains_keyword(
        step, ["total budget", "budget breakdown", "flights", "accommodation", "attractions", "food"]
    ):
        return {"tool": "estimate_budget", "tool_input": {"context": f"{step} | {user_input}"}}
    if is_transport_step:
        return {"tool": "estimate_transport", "tool_input": {"step": step}}
    if has_budget_word:
        return {"tool": "estimate_budget", "tool_input": {"context": f"{step} | {user_input}"}}
    return {"tool": "search_places", "tool_input": {"query": step}}


def _extract_candidate_entities(step: str, tool_output: Dict[str, Any]) -> List[str]:
    candidates: List[str] = []

    for attraction in tool_output.get("attractions", []):
        if isinstance(attraction, str) and attraction.strip():
            candidates.append(attraction.strip())

    for hotel in tool_output.get("hotels", []):
        if isinstance(hotel, dict):
            name = hotel.get("name", "").strip()
            if name:
                candidates.append(name)

    if not candidates:
        eg_match = re.search(r"e\.g\.,\s*([^)]+)", step, flags=re.IGNORECASE)
        if eg_match:
            for raw_name in eg_match.group(1).split(","):
                name = raw_name.strip(" .")
                if name:
                    candidates.append(name)

    if not candidates:
        extracted = re.findall(r"\b[A-Z][A-Za-z0-9'&\-]*(?:\s+[A-Z][A-Za-z0-9'&\-]*){0,3}\b", step)
        stopwords = {
            "Day",
            "Step",
            "Check",
            "Create",
            "Find",
            "Research",
            "Plan",
            "Compile",
            "Search",
            "Verify",
            "Calculate",
            "Estimate",
        }
        for item in extracted:
            if item not in stopwords and len(item) > 2:
                candidates.append(item)

    unique: List[str] = []
    seen = set()
    for name in candidates:
        key = name.lower()
        if key not in seen:
            unique.append(name)
            seen.add(key)
    return unique[:5]


def _verify_entity_exists(name: str, destination: str) -> Dict[str, Any]:
    query = f"{name} {destination}".strip()
    try:
        results = _nominatim_search(query, limit=1, include_extra_tags=True)
        if not results:
            return {
                "name": name,
                "exists": False,
                "matched_name": "",
                "opening_hours": None,
                "source": "OpenStreetMap Nominatim",
            }

        top = results[0]
        extra_tags = top.get("extratags", {}) if isinstance(top.get("extratags", {}), dict) else {}
        return {
            "name": name,
            "exists": True,
            "matched_name": top.get("display_name", ""),
            "opening_hours": extra_tags.get("opening_hours"),
            "source": "OpenStreetMap Nominatim",
        }
    except Exception as e:
        return {
            "name": name,
            "exists": False,
            "matched_name": "",
            "opening_hours": None,
            "source": "OpenStreetMap Nominatim",
            "error": str(e),
        }


def _run_verification_layer(step: str, tool_output: Dict[str, Any], destination: str) -> Dict[str, Any]:
    needs_check = _contains_keyword(step, ["attraction", "visit", "opening", "museum", "shrine", "temple", "hotel"])

    if not needs_check:
        return {
            "checked": False,
            "reason": "No place verification required for this step.",
            "verified_count": 0,
            "unverified_count": 0,
            "items": [],
        }

    candidates = _extract_candidate_entities(step, tool_output)

    if not candidates:
        return {
            "checked": True,
            "reason": "No candidate entities extracted for verification.",
            "verified_count": 0,
            "unverified_count": 0,
            "items": [],
        }

    verification_items = [_verify_entity_exists(name, destination) for name in candidates]
    verified_count = sum(1 for item in verification_items if item.get("exists"))
    unverified_count = len(verification_items) - verified_count

    return {
        "checked": True,
        "verified_count": verified_count,
        "unverified_count": unverified_count,
        "items": verification_items,
    }


def _score_confidence(status: str, verification: Dict[str, Any], tool_output: Dict[str, Any]) -> float:
    if status != "success":
        return 0.2

    score = 0.65
    if verification.get("checked"):
        score += 0.2 if verification.get("verified_count", 0) > 0 else -0.1
        if verification.get("unverified_count", 0) > 1:
            score -= 0.1

    if tool_output.get("error"):
        score -= 0.3

    return round(max(0.05, min(score, 0.99)), 2)


def _fallback_plan_for_graph(user_input: str) -> List[str]:
    destination = _extract_destination(user_input) or "your destination"
    days = _extract_days(user_input)

    if _is_attraction_intent(user_input):
        return [
            f"Search iconic attractions and natural landscapes in {destination}.",
            f"Search cultural and historical sites in {destination}.",
            f"Search local food streets and signature cuisine spots in {destination}.",
            f"Check weather and best visiting season in {destination}.",
            f"Verify opening hours and ticket information for top attractions in {destination}.",
            f"Summarize 5-8 must-visit highlights in {destination} with short reasons.",
        ]

    return [
        f"Search top attractions in {destination}.",
        f"Find accommodation options in {destination}.",
        f"Check weather in {destination}.",
        f"Estimate transport options in {destination}.",
        f"Create a {days}-day itinerary in {destination}.",
        "Estimate total travel budget and verify key attraction opening hours.",
    ]


def _normalize_plan_steps(steps: List[str], user_input: str) -> List[str]:
    destination = _extract_destination(user_input)
    days = _extract_days(user_input)
    normalized: List[str] = []

    for step in steps:
        line = step
        if destination:
            line = re.sub(r"\bdestination\b", destination, line, flags=re.IGNORECASE)
            line = re.sub(r"\bCity\s*X\b", destination, line, flags=re.IGNORECASE)
        line = re.sub(r"\b\d+\s*-\s*day\b|\b\d+\s*days?\b", f"{days}-day", line, flags=re.IGNORECASE)
        normalized.append(line)

    if destination and not any(destination.lower() in item.lower() for item in normalized):
        normalized = _fallback_plan_for_graph(user_input)
    return normalized


def _compact_preference_text(preferences: Dict[str, Any]) -> str:
    if not preferences:
        return ""

    parts: List[str] = []
    language = preferences.get("language")
    timezone = preferences.get("timezone")
    budget = preferences.get("budget_level")
    interests = preferences.get("interests", [])
    dietary = preferences.get("dietary", [])
    mobility = preferences.get("mobility_notes")

    if language:
        parts.append(f"language={language}")
    if timezone:
        parts.append(f"timezone={timezone}")
    if budget:
        parts.append(f"budget_level={budget}")
    if interests:
        parts.append("interests=" + ", ".join(str(i) for i in interests[:5]))
    if dietary:
        parts.append("dietary=" + ", ".join(str(i) for i in dietary[:5]))
    if mobility:
        parts.append(f"mobility_notes={mobility}")
    return "; ".join(parts)


def _build_objective_with_preferences(user_input: str, preferences: Dict[str, Any]) -> str:
    pref_text = _compact_preference_text(preferences)
    if not pref_text:
        return user_input
    return (
        f"{user_input}\n\n"
        "User preference profile (must be reflected in plan/budget/transport decisions):\n"
        f"{pref_text}"
    )


def plan_node(state: PlanExecuteState) -> Dict[str, Any]:
    print("\n[PLANNER] Planning...")
    planner = create_planner()
    parser = PydanticOutputParser(pydantic_object=Plan)

    preferences = state.get("user_preferences", {})
    user_input = state.get("input", "")
    destination = _extract_destination(user_input)
    days = _extract_days(user_input)

    # For attraction-discovery intent, deterministic local planning gives more stable quality.
    if destination and _is_attraction_intent(user_input):
        return {"plan": _fallback_plan_for_graph(user_input)}

    objective = _build_objective_with_preferences(user_input, preferences)
    if destination:
        objective += (
            "\n\nExtraction hint:\n"
            f"- destination: {destination}\n"
            f"- trip_length_days: {days}"
        )

    try:
        result = planner.invoke(
            {
                "objective": objective,
                "format_instructions": parser.get_format_instructions(),
            }
        )
        steps = result.steps if result and getattr(result, "steps", None) else []
        steps = _normalize_plan_steps(steps, user_input)
        if not steps:
            fallback = _fallback_plan_for_graph(user_input)
            print("WARNING: Planner returned empty steps. Using fallback plan.")
            return {"plan": fallback}
        return {"plan": steps}
    except Exception as e:
        print(f"WARNING: Planning failed ({e}). Using fallback plan.")
        fallback = _fallback_plan_for_graph(user_input)
        return {"plan": fallback}


def execute_step(state: PlanExecuteState) -> Dict[str, Any]:
    plan = state.get("plan", [])
    if not plan:
        return {}

    current_task = plan[0]
    destination = _extract_destination(state.get("input", ""))
    routing = _select_tool(current_task, destination, state.get("input", ""))
    tool_name = routing["tool"]
    tool_input = routing["tool_input"]

    print(f"\n[EXECUTOR] Executing step: {current_task}")
    print(f"    [ROUTER] {tool_name} <- {tool_input}")

    tool_output: Dict[str, Any]
    status = "success"
    try:
        tool_output = TOOL_REGISTRY[tool_name].invoke(tool_input)
        if isinstance(tool_output, dict) and tool_output.get("error"):
            status = "failed"
    except Exception as e:
        tool_output = {"error": f"Tool invocation failed: {e}"}
        status = "failed"

    verification = _run_verification_layer(current_task, tool_output, destination)
    confidence = _score_confidence(status, verification, tool_output)

    step_id = len(state.get("step_results", [])) + 1
    step_record: Dict[str, Any] = {
        "step_id": step_id,
        "step": current_task,
        "tool": tool_name,
        "tool_input": tool_input,
        "status": status,
        "confidence": confidence,
        "output": tool_output,
        "verification": verification,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }

    return {
        "plan": plan[1:],
        "step_results": [step_record],
    }


def should_continue(state: PlanExecuteState) -> str:
    if state.get("plan"):
        return "executor"
    return "finalizer"


def _summarize_tool_output(output: Dict[str, Any]) -> str:
    if output.get("error"):
        return output["error"]
    if "attractions" in output:
        attractions = output.get("attractions", [])
        return "Attractions: " + ", ".join(attractions[:3]) if attractions else "No attractions returned."
    if "hotels" in output:
        hotels = output.get("hotels", [])
        names = [h.get("name", "") for h in hotels if isinstance(h, dict)]
        return "Hotels: " + ", ".join(names[:3]) if names else "No hotel candidates returned."
    if "weather" in output:
        return f"Weather: {output.get('weather')} {output.get('temperature_c')}C"
    if "estimated_total" in output:
        return f"Estimated total budget: {output.get('estimated_total')} {output.get('currency', '')}"
    if "estimate" in output:
        return f"Transport estimate/day: {output.get('estimate', {}).get('daily_cost_usd', 'N/A')} USD"
    return json.dumps(output)[:200]


def _dedupe_keep_order(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        key = item.strip().lower()
        if not key or key in seen:
            continue
        out.append(item.strip())
        seen.add(key)
    return out


def _collect_recommendations(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    attractions: List[str] = []
    hotels: List[str] = []
    weather_line = ""
    budget_line = ""

    for item in results:
        output = item.get("output", {})
        for name in output.get("attractions", []):
            if isinstance(name, str):
                attractions.append(name)
        for hotel in output.get("hotels", []):
            if isinstance(hotel, dict):
                hotel_name = str(hotel.get("name", "")).strip()
                if hotel_name:
                    hotels.append(hotel_name)
        if not weather_line and output.get("weather"):
            weather_line = f"{output.get('location', '')}：{output.get('weather')}，约 {output.get('temperature_c')}°C"
        if not budget_line and output.get("estimated_total"):
            budget_line = f"预算估算：约 {output.get('estimated_total')} {output.get('currency', 'USD')}"

    return {
        "attractions": _dedupe_keep_order(attractions)[:8],
        "hotels": _dedupe_keep_order(hotels)[:5],
        "weather": weather_line,
        "budget": budget_line,
    }


def _attraction_reason(destination: str, attraction: str) -> str:
    dest_key = _normalize_destination_name(destination)
    reason_map = CURATED_CN_ATTRACTION_REASONS.get(dest_key, {})
    return reason_map.get(attraction, "")


def _write_execution_log(results: List[Dict[str, Any]]) -> str:
    logs_dir = Path("logs")
    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    file_path = logs_dir / f"execution_{ts}.json"
    with file_path.open("w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    return str(file_path)


def finalize_node(state: PlanExecuteState) -> Dict[str, str]:
    if state.get("response"):
        return {"response": state.get("response", "")}

    results = state.get("step_results", [])
    if not results:
        return {"response": "No itinerary could be produced."}

    user_input = state.get("input", "")
    destination = _extract_destination(user_input) or "目的地"
    rec = _collect_recommendations(results)

    lines: List[str] = [f"旅行建议（{destination}）"]
    pref_summary = _compact_preference_text(state.get("user_preferences", {}))
    if pref_summary:
        lines.append(f"已应用个性化偏好：{pref_summary}")

    if rec["attractions"]:
        lines.append("\n值得去的地方：")
        for idx, name in enumerate(rec["attractions"], 1):
            reason = _attraction_reason(destination, name)
            if reason:
                lines.append(f"{idx}. {name} - {reason}")
            else:
                lines.append(f"{idx}. {name}")
    else:
        lines.append("\n暂未检索到稳定景点名单，建议补充“出行月份/偏好（自然风光或人文）”后重试。")

    if rec["weather"]:
        lines.append(f"\n天气参考：{rec['weather']}")
    if rec["budget"] and not _is_attraction_intent(user_input):
        lines.append(rec["budget"])
    if rec["hotels"] and not _is_attraction_intent(user_input):
        lines.append("住宿参考：" + "、".join(rec["hotels"][:3]))

    if _is_attraction_intent(user_input):
        lines.append("\n玩法建议：")
        lines.append("1. 可先走河西走廊线：兰州/张掖/嘉峪关/敦煌，线路成熟。")
        lines.append("2. 莫高窟、鸣沙山等热门点建议提前预约门票。")
        lines.append("3. 甘肃南北温差较大，跨城行程建议预留机动时间。")
        try:
            log_path = _write_execution_log(results)
            lines.append(f"\n调试日志已保存：{log_path}")
        except Exception as e:
            lines.append(f"\n执行日志保存失败：{e}")
        return {"response": "\n".join(lines)}

    lines.append("\n执行摘要：")
    for item in results:
        lines.append(f"{item['step_id']}. {item['step']}")
        lines.append(
            f"   Tool={item['tool']} | Status={item['status']} | Confidence={item['confidence']}"
        )
        lines.append(f"   Result={_summarize_tool_output(item.get('output', {}))}")
        verification = item.get("verification", {})
        if verification.get("checked"):
            lines.append(
                "   Verification="
                f"verified {verification.get('verified_count', 0)}, "
                f"unverified {verification.get('unverified_count', 0)}"
            )

    try:
        log_path = _write_execution_log(results)
        lines.append(f"\n完整执行日志已保存：{log_path}")
    except Exception as e:
        lines.append(f"\n执行日志保存失败：{e}")

    return {"response": "\n".join(lines)}


workflow = StateGraph(PlanExecuteState)
workflow.add_node("planner", plan_node)
workflow.add_node("executor", execute_step)
workflow.add_node("finalizer", finalize_node)

workflow.set_entry_point("planner")
workflow.add_edge("planner", "executor")
workflow.add_conditional_edges(
    "executor",
    should_continue,
    {
        "executor": "executor",
        "finalizer": "finalizer",
    },
)
workflow.add_edge("finalizer", END)

app = workflow.compile()


if __name__ == "__main__":
    print("--- Starting Tourist Agent (DeepSeek Powered) ---")
    user_input = "I want a 2-day trip to Kyoto with a budget under 500 USD."

    try:
        final_state = app.invoke(
            {"input": user_input, "step_results": []},
            {"recursion_limit": 50},
        )

        print("\nMission complete.")
        print("\n--- Final Response ---")
        print(final_state.get("response", "No response generated."))

    except Exception as e:
        print(f"\nRuntime error: {e}")
