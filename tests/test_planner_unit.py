import unittest

import planner


class PlannerFallbackTests(unittest.TestCase):
    def test_extract_destination(self) -> None:
        destination = planner._extract_destination("I want a 3-day trip to Kyoto with food.")
        self.assertEqual(destination, "Kyoto")

    def test_extract_days(self) -> None:
        days = planner._extract_days("Plan a 4-day trip to Tokyo.")
        self.assertEqual(days, 4)

    def test_extract_destination_chinese(self) -> None:
        destination = planner._extract_destination("我想要去成都旅游7天")
        self.assertEqual(destination, "成都")

    def test_extract_days_chinese(self) -> None:
        days = planner._extract_days("我想要去成都旅游7天")
        self.assertEqual(days, 7)

    def test_extract_destination_chinese_with_country_prefix(self) -> None:
        destination = planner._extract_destination("我想去中国甘肃旅游有哪些好玩的")
        self.assertEqual(destination, "甘肃")

    def test_fallback_plan_structure(self) -> None:
        plan = planner._fallback_plan("I want a 2-day trip to Kyoto.")
        self.assertGreaterEqual(len(plan.steps), 6)
        self.assertTrue(any("Kyoto" in step for step in plan.steps))

    def test_create_planner_returns_local_when_llm_missing(self) -> None:
        old_llm = planner.llm
        try:
            planner.llm = None
            local_planner = planner.create_planner()
            plan = local_planner.invoke({"objective": "I want a 2-day trip to Kyoto."})
            self.assertTrue(hasattr(plan, "steps"))
            self.assertGreater(len(plan.steps), 0)
        finally:
            planner.llm = old_llm


if __name__ == "__main__":
    unittest.main()
