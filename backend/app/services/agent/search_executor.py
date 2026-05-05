from __future__ import annotations

import os
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import Callable

from .tool_router import route_tool
from .runtime_flags import get_agent_speed_mode
from .tools import LiveTravelToolset
from .types import SearchTask, ToolExecutionResult


ToolCallable = Callable[..., dict]


def _speed_mode() -> str:
    return get_agent_speed_mode(default="quality")


class SearchExecutor:
    def __init__(
        self,
        toolset: LiveTravelToolset | None = None,
        max_workers: int | None = None,
    ) -> None:
        self.toolset = toolset or LiveTravelToolset()
        speed_mode = _speed_mode()
        default_workers = 6 if speed_mode == "fast" else 5
        default_query_cap = 1 if speed_mode == "fast" else 2
        configured_workers = self._safe_int(os.getenv("AGENT_SEARCH_MAX_WORKERS"), default_workers)
        self.max_workers = max(1, max_workers if isinstance(max_workers, int) else configured_workers)
        self.global_query_cap = max(1, self._safe_int(os.getenv("AGENT_MAX_QUERIES_PER_TASK"), default_query_cap))
        self.registry: dict[str, ToolCallable] = {
            "web_search": self.toolset.search_web,
            "poi_search": self.toolset.search_places,
            "hotel_area_search": self.toolset.search_hotels,
            "weather_search": self.toolset.search_weather,
            "transport_search": self.toolset.search_transport,
            # backward-compatible aliases
            "place_search": self.toolset.search_places,
            "hotel_search": self.toolset.search_hotels,
        }

    @staticmethod
    def _safe_int(raw: str | None, default: int) -> int:
        try:
            value = int(str(raw or "").strip())
        except Exception:
            return default
        return value if value > 0 else default

    def _run_tool(self, tool_name: str, query: str) -> dict:
        tool = self.registry.get(tool_name)
        if not tool:
            return {"error": f"Tool {tool_name} not found"}

        if tool_name == "weather_search":
            return tool(query, None)
        return tool(query)

    @staticmethod
    def _extract_destination_from_understanding(
        requirement_understanding: dict[str, object] | None,
    ) -> str:
        if not isinstance(requirement_understanding, dict):
            return ""

        explicit = requirement_understanding.get("explicit_requirements", [])
        if not isinstance(explicit, list):
            return ""

        type_aliases = {
            "destination",
            "travel_destination",
            "destination_city",
            "dest",
            "目的地",
            "目的城市",
            "旅游目的地",
            "到达地",
            "终点",
        }
        semantic_keys = ("destination", "dest", "目的地", "目的城市", "到达", "终点")

        for row in explicit:
            if not isinstance(row, dict):
                continue
            req_type = str(row.get("type", "")).strip().lower()
            req_type_norm = req_type.replace("_", "").replace(" ", "")
            value = str(row.get("value", "")).strip()
            if not value or len(value) < 2:
                continue
            if req_type in type_aliases or req_type_norm in type_aliases:
                return value
            if any(key in req_type for key in semantic_keys):
                return value
        return ""

    @staticmethod
    def _enrich_query(tool_name: str, query: str, destination_hint: str) -> str:
        q = str(query).strip()
        if not q or not destination_hint:
            return q

        # Only add destination context for tools that require local area hints.
        # Web/transport queries should remain LLM-planned to avoid query drift.
        context_tools = {"poi_search", "hotel_area_search", "weather_search"}
        if tool_name not in context_tools:
            return q
        if destination_hint in q:
            return q
        return f"{destination_hint} {q}"

    def _query_budget(self, task: SearchTask) -> int:
        priority = str(task.priority).strip().lower()
        if priority == "high":
            budget = 2
        elif priority == "medium":
            budget = 1
        else:
            budget = 1
        return max(1, min(budget, self.global_query_cap))

    def _prepare_task_queries(self, task: SearchTask) -> list[str]:
        budget = self._query_budget(task)
        out: list[str] = []
        seen: set[str] = set()
        for raw in task.queries or []:
            query = str(raw).strip()
            if not query:
                continue
            key = query.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(query)
            if len(out) >= budget:
                break
        return out

    @staticmethod
    def _is_cancelled(cancel_event: object | None) -> bool:
        return bool(cancel_event and getattr(cancel_event, "is_set", lambda: False)())

    @staticmethod
    def _result_from_output(
        task_id: str,
        tool_name: str,
        query: str,
        output: dict,
        round_id: int,
    ) -> ToolExecutionResult:
        success = not bool(output.get("error"))
        return ToolExecutionResult(
            task_id=task_id,
            tool=tool_name,
            query=query,
            success=success,
            output=output,
            source=str(output.get("source", "tool_call")),
            round=max(1, int(round_id)),
        )

    def execute(
        self,
        search_plan: list[SearchTask],
        requirement_understanding: dict[str, object] | None = None,
        cancel_event: object | None = None,
        round_id: int = 1,
    ) -> list[ToolExecutionResult]:
        if not search_plan:
            return []

        results_with_seq: list[tuple[int, ToolExecutionResult]] = []
        destination_hint = self._extract_destination_from_understanding(requirement_understanding)
        cache: dict[tuple[str, str], dict] = {}
        future_map: dict[Future[dict], list[tuple[int, str, str, str]]] = {}
        future_by_key: dict[tuple[str, str], Future[dict]] = {}
        sequence = 0

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            for task in sorted(search_plan, key=lambda t: t.priority_rank()):
                if self._is_cancelled(cancel_event):
                    break

                tool_name = route_tool(task)
                for q in self._prepare_task_queries(task):
                    if self._is_cancelled(cancel_event):
                        break

                    enriched_query = self._enrich_query(tool_name, q, destination_hint)
                    cache_key = (tool_name, enriched_query.lower())
                    meta = (sequence, task.task_id, tool_name, enriched_query)
                    sequence += 1

                    if cache_key in cache:
                        result = self._result_from_output(
                            task_id=task.task_id,
                            tool_name=tool_name,
                            query=enriched_query,
                            output=cache[cache_key],
                            round_id=round_id,
                        )
                        results_with_seq.append((meta[0], result))
                        continue

                    existing_future = future_by_key.get(cache_key)
                    if existing_future is not None:
                        future_map.setdefault(existing_future, []).append(meta)
                        continue

                    future = pool.submit(self._run_tool, tool_name, enriched_query)
                    future_by_key[cache_key] = future
                    future_map[future] = [meta]

            for future in as_completed(list(future_map.keys())):
                if self._is_cancelled(cancel_event):
                    for pending in future_map:
                        pending.cancel()
                    break

                metas = future_map.get(future, [])
                try:
                    output = future.result()
                except Exception as exc:
                    output = {"error": str(exc), "source": "tool_runtime_error"}

                for seq, task_id, tool_name, query in metas:
                    cache[(tool_name, query.lower())] = output
                    result = self._result_from_output(
                        task_id=task_id,
                        tool_name=tool_name,
                        query=query,
                        output=output,
                        round_id=round_id,
                    )
                    results_with_seq.append((seq, result))

        results_with_seq.sort(key=lambda pair: pair[0])
        return [item for _, item in results_with_seq]
