from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .text_slot_utils import detect_user_language


def _norm_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


class SourceItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = "Untitled"
    url: str
    platform: str = ""
    snippet: str = ""


class ExplicitRequirement(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    value: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ImplicitRequirement(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    description: str
    reason: str = ""


class MissingInformation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    field: str
    importance: Literal["low", "medium", "high"] = "medium"
    is_blocking: bool = False
    reason: str


class RequirementUnderstanding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user_intent: str = "travel_planning"
    interpreted_goal: str
    explicit_requirements: list[ExplicitRequirement] = Field(default_factory=list)
    implicit_requirements: list[ImplicitRequirement] = Field(default_factory=list)
    missing_information: list[MissingInformation] = Field(default_factory=list)
    should_ask_clarifying_questions: bool = False
    clarifying_questions: list[str] = Field(default_factory=list)
    default_assumptions: list[str] = Field(default_factory=list)
    can_continue_with_draft_plan: bool = True


class SearchPlanTaskSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_id: str
    information_need: str
    why_needed: str
    tool: Literal["web_search", "poi_search", "weather_search", "transport_search", "hotel_area_search"]
    queries: list[str] = Field(default_factory=list)
    freshness: Literal["low", "medium", "high"] = "medium"
    priority: Literal["low", "medium", "high"] = "medium"
    expected_evidence: list[str] = Field(default_factory=list)


class SearchPlanOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    search_tasks: list[SearchPlanTaskSchema] = Field(default_factory=list)


class KeyFindingSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    finding: str
    source: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    how_to_use: str


class ConflictSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    topic: str
    different_versions: list[str] = Field(default_factory=list)
    suggested_resolution: str = ""


class SearchTaskInterpretationOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_id: str
    key_findings: list[KeyFindingSchema] = Field(default_factory=list)
    conflicts: list[ConflictSchema] = Field(default_factory=list)
    low_confidence_items: list[str] = Field(default_factory=list)
    summary_for_final_planner: str = ""


class ItineraryDayItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    day: int
    theme: str
    morning: str
    afternoon: str
    evening: str
    food_recommendations: list[str] = Field(default_factory=list)
    transport_notes: list[str] = Field(default_factory=list)
    why_this_day_works: str


class FrontendJsonOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    destination: str
    duration_days: int
    assumptions: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    itinerary: list[ItineraryDayItem] = Field(default_factory=list)
    hotel_area_suggestions: list[Any] = Field(default_factory=list)
    budget_estimate: dict[str, Any] = Field(default_factory=dict)
    tips: list[Any] = Field(default_factory=list)
    follow_up_questions: list[Any] = Field(default_factory=list)


class FinalAnswerOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    frontend_json: FrontendJsonOutput
    markdown_answer: str


def _extract_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = _norm_text(value)
    if not text:
        return default
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return default
    try:
        return int(digits)
    except Exception:
        return default


def _to_list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
            if text:
                out.append(text)
            continue
        if isinstance(item, dict):
            for key in ("field", "value", "question", "name", "title", "description", "reason", "type"):
                text = _norm_text(item.get(key))
                if text:
                    out.append(text)
                    break
    return out


def _extract_requirement_value(requirement_understanding: dict[str, Any], targets: set[str]) -> str:
    normalized_targets = {str(item).strip().lower() for item in targets if str(item).strip()}
    expanded_targets = set(normalized_targets)
    if normalized_targets & {"destination", "city", "to_city", "dest"}:
        expanded_targets.update({"目的地", "旅游目的地", "到达地", "终点", "目的城市"})
    if normalized_targets & {"duration", "duration_days", "days"}:
        expanded_targets.update({"天数", "旅游天数", "行程天数", "时长", "旅行时长", "游玩天数"})
    if normalized_targets & {"origin", "from_city", "departure_city", "departure"}:
        expanded_targets.update({"出发地", "出发城市", "起点"})

    explicit = requirement_understanding.get("explicit_requirements", [])
    if not isinstance(explicit, list):
        return ""
    for row in explicit:
        if not isinstance(row, dict):
            continue
        req_type = _norm_text(row.get("type")).lower()
        req_type_norm = req_type.replace("_", "").replace(" ", "")
        matched = False
        for target in expanded_targets:
            target_norm = target.lower().replace("_", "").replace(" ", "")
            if not target_norm:
                continue
            if req_type == target.lower() or req_type_norm == target_norm:
                matched = True
                break
            if target_norm in req_type_norm:
                matched = True
                break
        if matched:
            value = _norm_text(row.get("value"))
            if value:
                return value
    return ""


def _normalize_day_row(row: Any, index: int, language: str) -> dict[str, Any]:
    item = row if isinstance(row, dict) else {}
    day = _extract_int(item.get("day"), default=index + 1)
    theme = _norm_text(item.get("theme")) or _norm_text(item.get("title")) or f"Day {day}"
    if language == "zh":
        fallback_slot = "自由安排"
        fallback_why = "基于动线与节奏平衡安排。"
    else:
        fallback_slot = "Free block"
        fallback_why = "Balanced by route efficiency and pacing."

    morning = _norm_text(item.get("morning")) or _norm_text(item.get("am")) or fallback_slot
    afternoon = _norm_text(item.get("afternoon")) or _norm_text(item.get("pm")) or fallback_slot
    evening = _norm_text(item.get("evening")) or _norm_text(item.get("night")) or fallback_slot
    food = _to_list_of_strings(item.get("food_recommendations")) or _to_list_of_strings(item.get("food"))
    transport = _to_list_of_strings(item.get("transport_notes")) or _to_list_of_strings(item.get("transport"))
    why = _norm_text(item.get("why_this_day_works")) or _norm_text(item.get("reason")) or fallback_why
    return {
        "day": day,
        "theme": theme,
        "morning": morning,
        "afternoon": afternoon,
        "evening": evening,
        "food_recommendations": food,
        "transport_notes": transport,
        "why_this_day_works": why,
    }


def _normalize_frontend_json(
    payload: dict[str, Any],
    requirement_understanding: dict[str, Any] | None = None,
    user_input: str = "",
) -> dict[str, Any]:
    requirement = requirement_understanding or {}
    frontend = payload.get("frontend_json")
    if not isinstance(frontend, dict):
        frontend = payload.get("travelPlan")
    if not isinstance(frontend, dict):
        frontend = payload.get("travel_plan")
    if not isinstance(frontend, dict):
        frontend = payload
    if not isinstance(frontend, dict):
        frontend = {}

    language = detect_user_language(user_input or _norm_text(payload.get("raw_user_input")), default="en")

    destination = _norm_text(frontend.get("destination"))
    if not destination:
        destination = _extract_requirement_value(requirement, {"destination", "city", "to_city"})

    duration_days = _extract_int(frontend.get("duration_days"), default=0)
    if duration_days <= 0:
        duration_days = _extract_int(frontend.get("duration"), default=0)
    if duration_days <= 0:
        req_duration = _extract_requirement_value(requirement, {"duration", "duration_days", "days"})
        duration_days = _extract_int(req_duration, default=0)

    itinerary_raw = frontend.get("itinerary")
    itinerary: list[dict[str, Any]] = []
    if isinstance(itinerary_raw, list):
        for idx, row in enumerate(itinerary_raw):
            itinerary.append(_normalize_day_row(row, idx, language=language))

    assumptions = _to_list_of_strings(frontend.get("assumptions")) or _to_list_of_strings(
        requirement.get("default_assumptions", [])
    )
    missing_information = _to_list_of_strings(frontend.get("missing_information"))
    if not missing_information:
        missing_rows = requirement.get("missing_information", [])
        if isinstance(missing_rows, list):
            for row in missing_rows:
                if isinstance(row, dict):
                    field = _norm_text(row.get("field"))
                    if field:
                        missing_information.append(field)

    hotel_area_suggestions = frontend.get("hotel_area_suggestions")
    if not isinstance(hotel_area_suggestions, list):
        hotel_area_suggestions = []

    budget_estimate = frontend.get("budget_estimate")
    if not isinstance(budget_estimate, dict):
        budget_text = _norm_text(budget_estimate)
        budget_estimate = {"estimate": budget_text} if budget_text else {}

    tips = frontend.get("tips")
    if not isinstance(tips, list):
        tips = []

    follow_up_questions = _to_list_of_strings(frontend.get("follow_up_questions"))
    if not follow_up_questions:
        follow_up_questions = _to_list_of_strings(requirement.get("clarifying_questions", []))

    return {
        "destination": destination,
        "duration_days": duration_days,
        "assumptions": assumptions,
        "missing_information": missing_information,
        "itinerary": itinerary,
        "hotel_area_suggestions": hotel_area_suggestions,
        "budget_estimate": budget_estimate,
        "tips": tips,
        "follow_up_questions": follow_up_questions,
    }


def _build_markdown_from_frontend(
    frontend_json: dict[str, Any],
    user_input: str = "",
    language_hint: str = "en",
) -> str:
    language = language_hint
    destination = _norm_text(frontend_json.get("destination"))
    duration_days = _extract_int(frontend_json.get("duration_days"), default=0)
    assumptions = _to_list_of_strings(frontend_json.get("assumptions"))
    follow_up_questions = _to_list_of_strings(frontend_json.get("follow_up_questions"))
    missing_information = _to_list_of_strings(frontend_json.get("missing_information"))
    itinerary = frontend_json.get("itinerary", [])
    hotel_areas = frontend_json.get("hotel_area_suggestions", [])
    budget = frontend_json.get("budget_estimate", {})
    tips = _to_list_of_strings(frontend_json.get("tips"))

    has_itinerary = isinstance(itinerary, list) and len(itinerary) > 0

    if language == "zh":
        lines: list[str] = []
        lines.append("### ???????")
        if user_input:
            lines.append(f"- ?????{user_input}")
        lines.append(f"- ????{destination or '???'}")
        lines.append(f"- ?????{duration_days if duration_days > 0 else '???'}")
        lines.append("")

        if assumptions:
            lines.append("### ???????????")
            for item in assumptions[:8]:
                lines.append(f"- {item}")
            lines.append("")

        if missing_information:
            lines.append("### ???????")
            for item in missing_information[:8]:
                lines.append(f"- {item}")
            lines.append("")

        if has_itinerary:
            title_days = duration_days if duration_days > 0 else len(itinerary)
            lines.append(f"### {destination or '???'}{title_days}?????")
            for day_row in itinerary:
                if not isinstance(day_row, dict):
                    continue
                day = _extract_int(day_row.get("day"), 0)
                theme = _norm_text(day_row.get("theme")) or f"Day {day if day > 0 else '?'}"
                morning = _norm_text(day_row.get("morning"))
                afternoon = _norm_text(day_row.get("afternoon"))
                evening = _norm_text(day_row.get("evening"))
                food = _to_list_of_strings(day_row.get("food_recommendations"))
                transport = _to_list_of_strings(day_row.get("transport_notes"))
                why = _norm_text(day_row.get("why_this_day_works"))
                lines.append(f"- **Day {day if day > 0 else '?'}?{theme}**")
                if morning:
                    lines.append(f"  - **??**?{morning}")
                if afternoon:
                    lines.append(f"  - **??**?{afternoon}")
                if evening:
                    lines.append(f"  - **??**?{evening}")
                if food:
                    lines.append(f"  - **??**?{'?'.join(food[:4])}")
                if transport:
                    lines.append(f"  - **??**?{'?'.join(transport[:4])}")
                if why:
                    lines.append(f"  - **????**?{why}")
            lines.append("")
        else:
            lines.append("?????????????????????????????")
            lines.append("")

        if isinstance(budget, dict) and budget:
            lines.append("### ????")
            lines.append("| ?? | ???? |")
            lines.append("|---|---|")
            for key, value in list(budget.items())[:12]:
                k = _norm_text(key)
                v = _norm_text(value)
                if k and v:
                    lines.append(f"| {k} | {v} |")
            lines.append("")

        if isinstance(hotel_areas, list) and hotel_areas:
            lines.append("### ??????")
            for item in hotel_areas[:4]:
                if not isinstance(item, dict):
                    continue
                area = _norm_text(item.get("area") or item.get("name"))
                pros = _to_list_of_strings(item.get("pros"))
                cons = _to_list_of_strings(item.get("cons"))
                fit = _to_list_of_strings(item.get("suitable_for"))
                if area:
                    lines.append(f"- **{area}**")
                if pros:
                    lines.append(f"  - **??**?{'?'.join(pros[:3])}")
                if cons:
                    lines.append(f"  - **??**?{'?'.join(cons[:3])}")
                if fit:
                    lines.append(f"  - **??**?{'?'.join(fit[:3])}")
            lines.append("")

        if tips:
            lines.append("### ????")
            for item in tips[:6]:
                lines.append(f"- {item}")
            lines.append("")

        if follow_up_questions:
            lines.append("### ????????")
            for q in follow_up_questions[:2]:
                lines.append(f"- {q}")

        return "\n".join(lines).strip()

    lines: list[str] = []
    lines.append("### I understand your request")
    if user_input:
        lines.append(f"- Original request: {user_input}")
    lines.append(f"- Destination: {destination or 'TBD'}")
    lines.append(f"- Duration: {duration_days if duration_days > 0 else 'TBD'} days")
    lines.append("")

    if assumptions:
        lines.append("### Assumptions")
        for item in assumptions[:8]:
            lines.append(f"- {item}")
        lines.append("")

    if missing_information:
        lines.append("### Still missing")
        for item in missing_information[:8]:
            lines.append(f"- {item}")
        lines.append("")

    if has_itinerary:
        title_days = duration_days if duration_days > 0 else len(itinerary)
        lines.append(f"### {destination or 'Destination'} {title_days}-day itinerary")
        for day_row in itinerary:
            if not isinstance(day_row, dict):
                continue
            day = _extract_int(day_row.get("day"), 0)
            theme = _norm_text(day_row.get("theme")) or f"Day {day if day > 0 else '?'}"
            morning = _norm_text(day_row.get("morning"))
            afternoon = _norm_text(day_row.get("afternoon"))
            evening = _norm_text(day_row.get("evening"))
            food = _to_list_of_strings(day_row.get("food_recommendations"))
            transport = _to_list_of_strings(day_row.get("transport_notes"))
            why = _norm_text(day_row.get("why_this_day_works"))
            lines.append(f"- **Day {day if day > 0 else '?'}: {theme}**")
            if morning:
                lines.append(f"  - **Morning:** {morning}")
            if afternoon:
                lines.append(f"  - **Afternoon:** {afternoon}")
            if evening:
                lines.append(f"  - **Evening:** {evening}")
            if food:
                lines.append(f"  - **Food:** {'; '.join(food[:4])}")
            if transport:
                lines.append(f"  - **Transport:** {'; '.join(transport[:4])}")
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
            k = _norm_text(key)
            v = _norm_text(value)
            if k and v:
                lines.append(f"| {k} | {v} |")
        lines.append("")

    if isinstance(hotel_areas, list) and hotel_areas:
        lines.append("### Hotel area suggestions")
        for item in hotel_areas[:4]:
            if not isinstance(item, dict):
                continue
            area = _norm_text(item.get("area") or item.get("name"))
            pros = _to_list_of_strings(item.get("pros"))
            cons = _to_list_of_strings(item.get("cons"))
            fit = _to_list_of_strings(item.get("suitable_for"))
            if area:
                lines.append(f"- **{area}**")
            if pros:
                lines.append(f"  - **Pros:** {'; '.join(pros[:3])}")
            if cons:
                lines.append(f"  - **Cons:** {'; '.join(cons[:3])}")
            if fit:
                lines.append(f"  - **Good for:** {'; '.join(fit[:3])}")
        lines.append("")

    if tips:
        lines.append("### Tips")
        for item in tips[:6]:
            lines.append(f"- {item}")
        lines.append("")

    if follow_up_questions:
        lines.append("### Next, you can tell me")
        for q in follow_up_questions[:2]:
            lines.append(f"- {q}")

    return "\n".join(lines).strip()

def _normalize_final_answer_payload(
    data: dict[str, Any],
    requirement_understanding: dict[str, Any] | None = None,
    user_input: str = "",
) -> dict[str, Any]:
    payload = dict(data)
    markdown_answer = _norm_text(payload.get("markdown_answer"))
    if not markdown_answer:
        markdown_answer = _norm_text(payload.get("answer")) or _norm_text(payload.get("response"))
    frontend_json = _normalize_frontend_json(
        payload,
        requirement_understanding=requirement_understanding,
        user_input=user_input,
    )
    if not markdown_answer:
        language = detect_user_language(user_input or _norm_text(payload.get("raw_user_input")), default="en")
        markdown_answer = _build_markdown_from_frontend(
            frontend_json,
            user_input=user_input or _norm_text(payload.get("raw_user_input")),
            language_hint=language,
        )
    else:
        itinerary_rows = frontend_json.get("itinerary", [])
        if isinstance(itinerary_rows, list) and itinerary_rows:
            markdown_lc = markdown_answer.lower()
            looks_placeholder = any(
                marker in markdown_lc
                for marker in (
                    "tbd destination",
                    "tbd pace",
                    "no recommendation yet",
                    "no transport notes yet",
                    "reliable day-by-day itinerary data is currently unavailable",
                )
            )
            if looks_placeholder:
                language = detect_user_language(user_input or _norm_text(payload.get("raw_user_input")), default="en")
                markdown_answer = _build_markdown_from_frontend(
                    frontend_json,
                    user_input=user_input or _norm_text(payload.get("raw_user_input")),
                    language_hint=language,
                )
    return {
        "frontend_json": frontend_json,
        "markdown_answer": markdown_answer,
    }


def validate_requirement_understanding(data: dict[str, Any], user_input: str) -> RequirementUnderstanding:
    payload = dict(data)
    payload["user_intent"] = _norm_text(payload.get("user_intent")) or "travel_planning"
    payload["interpreted_goal"] = _norm_text(payload.get("interpreted_goal")) or f"Understand and plan for: {user_input}"
    return RequirementUnderstanding.model_validate(payload)


def validate_search_plan(data: dict[str, Any]) -> SearchPlanOutput:
    return SearchPlanOutput.model_validate(data)


def validate_search_interpretation(
    data: dict[str, Any],
    expected_task_id: str | None = None,
) -> SearchTaskInterpretationOutput:
    model = SearchTaskInterpretationOutput.model_validate(data)
    if expected_task_id and model.task_id.strip() != expected_task_id.strip():
        model.task_id = expected_task_id.strip()
    return model


def validate_final_answer(
    data: dict[str, Any],
    requirement_understanding: dict[str, Any] | None = None,
    user_input: str = "",
) -> FinalAnswerOutput:
    try:
        return FinalAnswerOutput.model_validate(data)
    except Exception:
        normalized = _normalize_final_answer_payload(
            data,
            requirement_understanding=requirement_understanding,
            user_input=user_input,
        )
        return FinalAnswerOutput.model_validate(normalized)
