from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


JSONDict = dict[str, Any]


@dataclass
class TravelSlots:
    destination: str | None = None
    duration_days: int | None = None
    origin: str | None = None
    travel_time: str | None = None
    budget: str | None = None
    companions: str | None = None
    interests: list[str] = field(default_factory=list)
    transport_mode: str | None = None
    accommodation_preference: str | None = None
    food_preference: str | None = None
    pace_preference: str | None = None
    language: str | None = None

    def known_info(self) -> JSONDict:
        data: JSONDict = {
            "destination": self.destination,
            "duration_days": self.duration_days,
            "origin": self.origin,
            "travel_time": self.travel_time,
            "budget": self.budget,
            "companions": self.companions,
            "interests": self.interests,
            "transport_mode": self.transport_mode,
            "accommodation_preference": self.accommodation_preference,
            "food_preference": self.food_preference,
            "pace_preference": self.pace_preference,
            "language": self.language,
        }
        return {k: v for k, v in data.items() if v not in (None, "", [])}


@dataclass
class SearchTask:
    task_id: str
    information_need: str
    why_needed: str
    tool: str
    queries: list[str] = field(default_factory=list)
    freshness: str = "medium"
    priority: str = "medium"
    expected_evidence: list[str] = field(default_factory=list)

    def priority_rank(self) -> int:
        key = self.priority.strip().lower()
        if key == "high":
            return 1
        if key == "medium":
            return 2
        if key == "low":
            return 3
        return 4

    def to_dict(self) -> JSONDict:
        return {
            "task_id": self.task_id,
            "information_need": self.information_need,
            "why_needed": self.why_needed,
            "tool": self.tool,
            "queries": list(self.queries),
            "freshness": self.freshness,
            "priority": self.priority,
            "expected_evidence": list(self.expected_evidence),
        }


@dataclass
class ToolExecutionResult:
    task_id: str
    tool: str
    query: str
    success: bool
    output: JSONDict
    source: str
    round: int = 1

    def to_dict(self) -> JSONDict:
        return {
            "task_id": self.task_id,
            "tool": self.tool,
            "query": self.query,
            "success": self.success,
            "output": self.output,
            "source": self.source,
            "round": self.round,
        }
