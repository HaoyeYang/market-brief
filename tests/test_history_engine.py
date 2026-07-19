import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from history_engine import (_session_offset, build_context, connect,
                            flatten_snapshots, ingest, metrics)


def data(date, value):
    return {
        "date": date, "mode": "close", "asof": date + "T21:00:00+00:00",
        "indices": {"SP500": {"status": "ok", "last": value,
                                "session_date": date, "bar_complete": True}},
        "rates": {}, "options": {}, "positioning": {},
    }


def payload(with_catalyst=False, evaluation=None):
    catalysts = []
    if with_catalyst:
        catalysts = [{
            "name": "breadth follow-through", "thesis_link": "breadth strengthens",
            "horizon_days": 5, "confirm_condition": "SP500 remains above 100",
            "invalidate_condition": "SP500 closes below 90",
            "sources": ["https://example.com/claim"], "source_angles": ["us_equities"],
        }]
    return {
        "rank": {"thesis": "test thesis", "ranked": [{
            "sources": ["https://example.com/claim"], "source_angles": ["us_equities"],
        }], "catalysts": catalysts},
        "confirmed_claims": [{"angle": "us_equities", "claim": "test",
                              "url": "https://example.com/claim", "ts": "now"}],
        "diagnostics": {"angle_metrics": {
            "us_equities": {"scouted": 2, "excerpt_supported": 1, "source_confirmed": 1},
        }},
        "postmortem": {"results": [evaluation] if evaluation else []},
        "degraded": False,
    }


class HistoryEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = connect(str(Path(self.tmp.name) / "history.sqlite3"))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def args(self, date):
        rank_path = Path(self.tmp.name) / (date + ".rank.json")
        return SimpleNamespace(profile="standard", report=date + ".md",
                               data=date + ".json", rank=str(rank_path), run_json=None)

    def test_flatten_skips_metadata_numbers(self):
        flat = flatten_snapshots({
            "options": {"symbols": {"SPY": {"status": "ok", "cache_age_hours": 3,
                                                "call_open_interest": 42}}},
        })
        self.assertIn("options.symbols.SPY.call_open_interest", flat)
        self.assertNotIn("options.symbols.SPY.cache_age_hours", flat)

    def test_context_and_postmortem_attribution(self):
        first = data("2026-07-13", 100)
        ingest(self.db, first, payload(with_catalyst=True), self.args(first["date"]))

        next_context = build_context(self.db, data("2026-07-14", 102))
        self.assertEqual(next_context["comparisons"]["1d"]["prior_date"], "2026-07-13")
        pending = next_context["pending_evaluations"]
        self.assertEqual([(x["horizon"], x["origin_date"]) for x in pending], [(1, "2026-07-13")])

        evaluation = {
            "catalyst_id": pending[0]["catalyst_id"], "horizon": 1,
            "verdict": "confirmed", "reason": "condition met",
            "evidence_keys": ["indices.SP500.last"],
        }
        ingest(self.db, data("2026-07-14", 102), payload(evaluation=evaluation),
               self.args("2026-07-14"))
        score = metrics(self.db)
        self.assertEqual(score["postmortem"]["confirmed"], 1)
        self.assertEqual(score["postmortem_by_agent"][0]["name"], "us_equities")
        self.assertEqual(score["postmortem_by_source"][0]["name"], "example.com")

    def test_five_observation_comparison(self):
        dates = ["2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16", "2026-07-17"]
        for idx, date in enumerate(dates):
            ingest(self.db, data(date, 100 + idx), payload(), self.args(date))
        context = build_context(self.db, data("2026-07-20", 110))
        self.assertEqual(context["comparisons"]["5d"]["prior_date"], "2026-07-13")
        change = context["comparisons"]["5d"]["changes"][0]
        self.assertEqual(change["absolute_change"], 10)

    def test_weekend_catalyst_one_day_due_is_next_session(self):
        self.assertEqual(_session_offset("2026-07-19", 1), "2026-07-20")


if __name__ == "__main__":
    unittest.main()
