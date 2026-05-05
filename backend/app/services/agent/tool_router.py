from __future__ import annotations

from .types import SearchTask


SUPPORTED_TOOLS = {
    "web_search",
    "poi_search",
    "hotel_area_search",
    "weather_search",
    "transport_search",
    # backward-compatible aliases
    "place_search",
    "hotel_search",
}


def route_tool(task: SearchTask) -> str:
    # Routing is model-driven. Code path only validates support and provides a safe fallback.
    return task.tool if task.tool in SUPPORTED_TOOLS else "web_search"
