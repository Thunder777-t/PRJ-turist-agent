from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.services.agent import intent_analyzer, itinerary_generator, task_planner
from backend.app.services.agent.types import SearchTask


def _build_requirement_payload(
    *,
    goal: str,
    explicit: list[dict],
    implicit: list[dict],
    missing: list[dict],
    assumptions: list[str],
    ask: bool,
    questions: list[str],
    can_continue: bool,
) -> dict:
    return {
        "user_intent": "travel_planning",
        "interpreted_goal": goal,
        "explicit_requirements": explicit,
        "implicit_requirements": implicit,
        "missing_information": missing,
        "default_assumptions": assumptions,
        "should_ask_clarifying_questions": ask,
        "clarifying_questions": questions,
        "can_continue_with_draft_plan": can_continue,
    }


class M11LlmDrivenAgentTests(unittest.TestCase):
    def _get_explicit_value(self, payload: dict, req_type: str) -> str | None:
        explicit = payload.get("explicit_requirements", [])
        if not isinstance(explicit, list):
            return None
        for item in explicit:
            if not isinstance(item, dict):
                continue
            if str(item.get("type", "")).strip() == req_type:
                value = str(item.get("value", "")).strip()
                return value or None
        return None

    def test_requirement_understanding_cases_are_llm_driven(self) -> None:
        case_map = {
            "我想去成都玩7天": _build_requirement_payload(
                goal="用户希望获得成都7天旅游规划",
                explicit=[
                    {"type": "destination", "value": "成都", "confidence": 0.98},
                    {"type": "duration", "value": "7天", "confidence": 0.96},
                ],
                implicit=[
                    {"type": "itinerary", "description": "需要日程安排", "reason": "7天行程通常要按天安排"},
                ],
                missing=[
                    {"field": "travel_dates", "importance": "high", "is_blocking": False, "reason": "影响天气与拥挤度"},
                ],
                assumptions=["首次到访成都", "预算中等"],
                ask=False,
                questions=["大概什么时候去成都？"],
                can_continue=True,
            ),
            "想在蓉城待一周，帮我安排一下": _build_requirement_payload(
                goal="用户希望获得成都约一周旅游规划",
                explicit=[
                    {"type": "destination", "value": "成都", "confidence": 0.95},
                    {"type": "duration", "value": "约7天", "confidence": 0.9},
                ],
                implicit=[
                    {"type": "itinerary", "description": "需要按天行程", "reason": "请求了完整安排"},
                ],
                missing=[
                    {"field": "budget", "importance": "medium", "is_blocking": False, "reason": "影响酒店和餐饮档位"},
                ],
                assumptions=["节奏适中"],
                ask=False,
                questions=["预算偏经济、中等还是舒适？"],
                can_continue=True,
            ),
            "下个月带爸妈去成都，不想太累": _build_requirement_payload(
                goal="用户计划下个月带父母去成都，需要轻松节奏与适老化安排",
                explicit=[
                    {"type": "destination", "value": "成都", "confidence": 0.97},
                    {"type": "travel_time", "value": "下个月", "confidence": 0.9},
                    {"type": "companions", "value": "父母", "confidence": 0.96},
                    {"type": "pace_preference", "value": "轻松", "confidence": 0.94},
                ],
                implicit=[
                    {"type": "senior_friendly", "description": "需要适老化安排", "reason": "同行人为父母且不想太累"},
                ],
                missing=[
                    {"field": "exact_dates", "importance": "medium", "is_blocking": False, "reason": "影响天气与预约"},
                ],
                assumptions=["减少长距离步行", "优先可休息景点"],
                ask=False,
                questions=["父母是否有行动或饮食限制？"],
                can_continue=True,
            ),
            "想去一个适合吃东西、慢慢逛的城市，7天左右，预算别太高": _build_requirement_payload(
                goal="用户希望先确定一个适合美食和慢节奏的目的地，再做约7天低预算方案",
                explicit=[
                    {"type": "duration", "value": "约7天", "confidence": 0.87},
                    {"type": "budget", "value": "不太高", "confidence": 0.84},
                    {"type": "travel_style", "value": "慢节奏逛吃", "confidence": 0.88},
                ],
                implicit=[
                    {"type": "destination_recommendation", "description": "需要先推荐目的地", "reason": "用户未指定城市"},
                ],
                missing=[
                    {"field": "destination", "importance": "high", "is_blocking": True, "reason": "缺少核心目的地"},
                ],
                assumptions=[],
                ask=True,
                questions=["你更偏好南方还是北方城市？", "是否优先国内城市？"],
                can_continue=False,
            ),
        }

        def fake_json_completion(messages, temperature=0.0, max_tokens=1800):  # noqa: ANN001,ANN201
            prompt = str(messages[-1].get("content", ""))
            for user_input, payload in case_map.items():
                if user_input in prompt:
                    return {"ok": True, "json": payload}
            return {"ok": False, "error": "unmapped test case"}

        with patch.object(intent_analyzer.CLIENT, "enabled", True), patch.object(
            intent_analyzer.CLIENT,
            "json_completion",
            side_effect=fake_json_completion,
        ) as mocked:
            out1 = intent_analyzer.analyze_intent("我想去成都玩7天", [], {})
            self.assertEqual(out1["user_intent"], "travel_planning")
            self.assertEqual(self._get_explicit_value(out1, "destination"), "成都")
            self.assertEqual(self._get_explicit_value(out1, "duration"), "7天")

            out2 = intent_analyzer.analyze_intent("想在蓉城待一周，帮我安排一下", [], {})
            self.assertEqual(self._get_explicit_value(out2, "destination"), "成都")
            self.assertIn("7", self._get_explicit_value(out2, "duration") or "")

            out3 = intent_analyzer.analyze_intent("下个月带爸妈去成都，不想太累", [], {})
            self.assertEqual(self._get_explicit_value(out3, "companions"), "父母")
            self.assertEqual(self._get_explicit_value(out3, "pace_preference"), "轻松")
            self.assertEqual(self._get_explicit_value(out3, "travel_time"), "下个月")
            implicit_types = {str(item.get("type", "")).strip() for item in out3.get("implicit_requirements", [])}
            self.assertIn("senior_friendly", implicit_types)

            out4 = intent_analyzer.analyze_intent(
                "想去一个适合吃东西、慢慢逛的城市，7天左右，预算别太高",
                [],
                {},
            )
            self.assertIsNone(self._get_explicit_value(out4, "destination"))
            self.assertTrue(bool(out4.get("should_ask_clarifying_questions")))
            missing_fields = {str(item.get("field", "")).strip() for item in out4.get("missing_information", [])}
            self.assertIn("destination", missing_fields)
            self.assertFalse(bool(out4.get("can_continue_with_draft_plan")))

            self.assertEqual(mocked.call_count, 4)

    def test_requirement_understanding_follows_llm_output_not_keyword_parse(self) -> None:
        llm_override = _build_requirement_payload(
            goal="模型给出非关键词结果",
            explicit=[
                {"type": "destination", "value": "重庆", "confidence": 0.91},
                {"type": "duration", "value": "3天", "confidence": 0.89},
            ],
            implicit=[],
            missing=[],
            assumptions=[],
            ask=False,
            questions=[],
            can_continue=True,
        )
        with patch.object(intent_analyzer.CLIENT, "enabled", True), patch.object(
            intent_analyzer.CLIENT,
            "json_completion",
            return_value={"ok": True, "json": llm_override},
        ):
            out = intent_analyzer.analyze_intent("我想去成都玩7天", [], {})
        self.assertEqual(self._get_explicit_value(out, "destination"), "重庆")
        self.assertEqual(self._get_explicit_value(out, "duration"), "3天")

    def test_search_tasks_and_final_answer_are_llm_generated(self) -> None:
        search_plan_payload = {
            "search_tasks": [
                {
                    "task_id": "t1",
                    "information_need": "测试信息需求",
                    "why_needed": "验证由LLM生成",
                    "tool": "poi_search",
                    "queries": ["测试 query 1", "测试 query 2"],
                    "freshness": "high",
                    "priority": "high",
                    "expected_evidence": ["POI名称", "开放时间"],
                }
            ]
        }

        with patch.object(task_planner.CLIENT, "enabled", True), patch.object(
            task_planner.CLIENT,
            "json_completion",
            return_value={"ok": True, "json": search_plan_payload},
        ):
            tasks = task_planner.plan_tasks(
                requirement_understanding={"user_intent": "travel_planning"},
                user_input="任意输入",
                conversation_history=[],
            )
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].tool, "poi_search")
        self.assertEqual(tasks[0].queries, ["测试 query 1", "测试 query 2"])
        self.assertEqual(tasks[0].freshness, "high")
        self.assertEqual(tasks[0].expected_evidence, ["POI名称", "开放时间"])

        final_payload = {
            "frontend_json": {
                "destination": "测试城",
                "duration_days": 5,
                "assumptions": ["假设A"],
                "missing_information": ["travel_dates"],
                "itinerary": [
                    {
                        "day": 1,
                        "theme": "轻松入城",
                        "morning": "城市漫步",
                        "afternoon": "地标参观",
                        "evening": "夜市",
                        "food_recommendations": ["本地小吃"],
                        "transport_notes": ["地铁优先"],
                        "why_this_day_works": "到达日节奏适中",
                    }
                ],
                "hotel_area_suggestions": [],
                "budget_estimate": {},
                "tips": [],
                "follow_up_questions": [],
            },
            "markdown_answer": "这是 LLM 生成的最终回答。",
        }

        with patch.object(itinerary_generator.CLIENT, "enabled", True), patch.object(
            itinerary_generator.CLIENT,
            "json_completion",
            return_value={"ok": True, "json": final_payload},
        ):
            out = itinerary_generator.generate_itinerary_payload(
                user_input="任意输入",
                requirement_understanding={"user_intent": "travel_planning"},
                search_plan=tasks,
                tool_results=[],
                interpretation_payload={"task_result_interpretations": [], "sources": []},
                user_preferences={},
                conversation_history=[],
            )
        self.assertEqual(out["frontend_json"]["destination"], "测试城")
        self.assertEqual(out["frontend_json"]["duration_days"], 5)
        self.assertEqual(out["markdown_answer"], "这是 LLM 生成的最终回答。")

    def test_static_guard_no_hardcoded_city_mapping_or_regex_parser(self) -> None:
        intent_code = Path(intent_analyzer.__file__).read_text(encoding="utf-8")
        planner_code = Path(task_planner.__file__).read_text(encoding="utf-8")
        final_code = Path(itinerary_generator.__file__).read_text(encoding="utf-8")

        banned_snippets = [
            "CITY_ALIAS_MAP",
            "CITY_KEYWORDS",
            "DESTINATION_MAP",
            "re.search(",
            "re.findall(",
            "in user_input",
            ".includes(",
        ]
        for snippet in banned_snippets:
            self.assertNotIn(snippet, intent_code)
            self.assertNotIn(snippet, planner_code)

        # Guard that these nodes are explicitly LLM-called.
        self.assertIn("json_completion(", intent_code)
        self.assertIn("json_completion(", planner_code)
        self.assertIn("json_completion(", final_code)


if __name__ == "__main__":
    unittest.main()
