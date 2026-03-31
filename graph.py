import json
import operator
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
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
    match = re.search(r"\bto\s+([A-Za-z][A-Za-z\s\-]{1,50})", user_input, re.IGNORECASE)
    if not match:
        return ""

    destination = match.group(1).strip(" .,")
    for stop_word in [" with ", " for ", " on ", " including "]:
        idx = destination.lower().find(stop_word)
        if idx != -1:
            destination = destination[:idx].strip()
    return destination


def _extract_days(text: str) -> int:
    patterns = [r"(\d+)\s*-\s*day", r"(\d+)\s*day", r"(\d+)\s*days"]
    lower = text.lower()
    for pattern in patterns:
        match = re.search(pattern, lower)
        if match:
            return max(1, int(match.group(1)))
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
        data = _http_get_json(
            "https://en.wikipedia.org/w/api.php",
            {
                "action": "query",
                "list": "search",
                "srsearch": cleaned_query,
                "format": "json",
                "srlimit": 5,
                "utf8": 1,
            },
        )
        raw_results = data.get("query", {}).get("search", [])
        attractions = [item.get("title", "") for item in raw_results if item.get("title")]
        snippets = [re.sub(r"<.*?>", "", item.get("snippet", "")) for item in raw_results]
        return {
            "query": cleaned_query,
            "attractions": attractions,
            "snippets": snippets,
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
    return [
        f"Search top attractions in {destination}.",
        f"Find accommodation options in {destination}.",
        f"Check weather in {destination}.",
        f"Estimate transport options in {destination}.",
        f"Create a {days}-day itinerary in {destination}.",
        "Estimate total travel budget and verify key attraction opening hours.",
    ]


def plan_node(state: PlanExecuteState) -> Dict[str, Any]:
    print("\n[PLANNER] Planning...")
    planner = create_planner()
    parser = PydanticOutputParser(pydantic_object=Plan)

    try:
        result = planner.invoke(
            {
                "objective": state["input"],
                "format_instructions": parser.get_format_instructions(),
            }
        )
        steps = result.steps if result and getattr(result, "steps", None) else []
        if not steps:
            fallback = _fallback_plan_for_graph(state.get("input", ""))
            print("WARNING: Planner returned empty steps. Using fallback plan.")
            return {"plan": fallback}
        return {"plan": steps}
    except Exception as e:
        print(f"WARNING: Planning failed ({e}). Using fallback plan.")
        fallback = _fallback_plan_for_graph(state.get("input", ""))
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


def finalize_node(state: PlanExecuteState) -> Dict[str, str]:
    if state.get("response"):
        return {"response": state["response"]}

    results = state.get("step_results", [])
    if not results:
        return {"response": "No itinerary could be produced."}

    lines: List[str] = ["Final itinerary draft (Plan-and-Execute):"]
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

    lines.append("\nExecution trace (JSON):")
    lines.append(json.dumps(results, ensure_ascii=True, indent=2))

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
