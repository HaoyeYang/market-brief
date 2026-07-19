import datetime as dt
import unittest

from derivatives_positioning import _choose_expirations, _summarize_chain


class DerivativesTests(unittest.TestCase):
    def test_expiration_buckets_use_report_date(self):
        expirations = ["2026-07-16", "2026-07-17", "2026-07-24", "2026-08-28"]
        chosen = _choose_expirations(expirations, dt.date(2026, 7, 16))
        self.assertEqual(chosen["short_dated"], "2026-07-16")
        self.assertEqual(chosen["swing"], "2026-07-24")

    def test_chain_summary_is_bounded_and_does_not_claim_gex(self):
        calls = [
            {"strike": 100, "volume": 20, "open_interest": 200, "iv": .20, "bid": 2, "ask": 2.2},
            {"strike": 110, "volume": 10, "open_interest": 500, "iv": .18, "bid": .5, "ask": .7},
        ]
        puts = [
            {"strike": 100, "volume": 30, "open_interest": 300, "iv": .22, "bid": 2.1, "ask": 2.3},
            {"strike": 90, "volume": 15, "open_interest": 600, "iv": .28, "bid": .4, "ask": .6},
        ]
        result = _summarize_chain(
            "SPY", "2026-07-24", 100, calls, puts, "test", "close",
            dt.date(2026, 7, 16),
        )
        self.assertEqual(result["dte_at_fetch"], 8)
        self.assertEqual(result["put_call_volume_ratio"], 1.5)
        self.assertEqual(result["call_wall_strike_by_oi"], 110)
        self.assertIn("no gamma-exposure claim", result["limitations"])

    def test_non_session_volume_is_not_labeled_live(self):
        calls = [{"strike": 100, "volume": 1, "open_interest": 2, "iv": .2,
                  "bid": 1, "ask": 1.2}]
        puts = [{"strike": 100, "volume": 1, "open_interest": 2, "iv": .2,
                 "bid": 1, "ask": 1.2}]
        result = _summarize_chain(
            "SPY", "2026-07-24", 100, calls, puts, "test", "intraday",
            dt.date(2026, 7, 19), False,
        )
        self.assertIn("prior trading day", result["volume_interpretation"])


if __name__ == "__main__":
    unittest.main()
