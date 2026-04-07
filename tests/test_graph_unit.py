import unittest

import graph


class GraphRoutingTests(unittest.TestCase):
    def test_select_tool_weather(self) -> None:
        step = "Check weather forecast for Kyoto tomorrow."
        route = graph._select_tool(step, "Kyoto", "I want a trip to Kyoto")
        self.assertEqual(route["tool"], "check_weather")

    def test_select_tool_transport_not_confused_by_train(self) -> None:
        step = "Research train and bus transport options in Kyoto."
        route = graph._select_tool(step, "Kyoto", "I want a trip to Kyoto")
        self.assertEqual(route["tool"], "estimate_transport")

    def test_select_tool_hotel(self) -> None:
        step = "Find budget hotels and accommodations in Kyoto."
        route = graph._select_tool(step, "Kyoto", "I want a trip to Kyoto")
        self.assertEqual(route["tool"], "find_hotels")

    def test_select_tool_budget_breakdown(self) -> None:
        step = "Calculate total budget breakdown for flights, food, and attractions."
        route = graph._select_tool(step, "Kyoto", "I want a trip to Kyoto")
        self.assertEqual(route["tool"], "estimate_budget")

    def test_verification_skipped_for_non_place_step(self) -> None:
        verification = graph._run_verification_layer(
            "Estimate total budget for the trip.",
            {"estimated_total": 300},
            "Kyoto",
        )
        self.assertFalse(verification["checked"])
        self.assertEqual(verification["verified_count"], 0)
        self.assertEqual(verification["unverified_count"], 0)

    def test_fallback_plan_uses_days_and_destination(self) -> None:
        steps = graph._fallback_plan_for_graph("I want a 2-day trip to Kyoto with food focus.")
        self.assertTrue(any("Kyoto" in step for step in steps))
        self.assertTrue(any("2-day" in step for step in steps))

    def test_extract_destination_and_days_chinese(self) -> None:
        text = "我想要去成都旅游7天"
        self.assertEqual(graph._extract_destination(text), "成都")
        self.assertEqual(graph._extract_days(text), 7)

    def test_attraction_intent_uses_discovery_fallback_plan(self) -> None:
        text = "我想去中国甘肃旅游有哪些好玩的"
        steps = graph._fallback_plan_for_graph(text)
        self.assertTrue(graph._is_attraction_intent(text))
        self.assertTrue(any("Summarize 5-8 must-visit highlights" in step for step in steps))
        self.assertFalse(any("3-day itinerary" in step for step in steps))


if __name__ == "__main__":
    unittest.main()
