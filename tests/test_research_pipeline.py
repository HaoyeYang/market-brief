import datetime as dt
import unittest

from research_pipeline import _allowed_domain, evidence, route_for


class ResearchPipelineTests(unittest.TestCase):
    def test_only_official_and_allowlisted_media_are_evidence_domains(self):
        self.assertTrue(_allowed_domain("https://www.federalreserve.gov/newsevents/a.htm"))
        self.assertTrue(_allowed_domain("https://www.reuters.com/markets/test"))
        self.assertFalse(_allowed_domain("https://example.net/rumor"))

    def test_high_impact_calendar_expands_agents_and_searches(self):
        item = evidence(
            source="BLS", title="Consumer Price Index release", url="https://www.bls.gov/cpi/",
            retrieved_at="2026-07-19T19:00:00+00:00", event_time="2026-07-21T12:30:00+00:00",
            kind="macro_calendar", audit_status="official_calendar",
        )
        route = route_for([item], mode="preopen", is_session=True)
        self.assertEqual(route["day_type"], "high_impact")
        self.assertEqual(route["search_query_budget"], 3)
        self.assertIn("macro_rates", route["active_agents"])
        self.assertIn("ai_tech_chain", route["active_agents"])
        self.assertIn("source_verifier", route["active_agents"])
        self.assertIn("news_scout", route["active_agents"])

    def test_quiet_day_reduces_work(self):
        route = route_for([], mode="close", is_session=True)
        self.assertEqual(route["day_type"], "quiet")
        self.assertEqual(route["search_query_budget"], 1)
        self.assertNotIn("macro_rates", route["active_agents"])


if __name__ == "__main__":
    unittest.main()
