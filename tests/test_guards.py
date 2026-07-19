import datetime as dt
import unittest

from fetch_data import build_agent_view, futures_dating_guard


UTC = dt.timezone.utc


def quote(session):
    return {
        "status": "ok", "session_date": session, "last": 1.0,
        "chg": 0.1, "chg_pct": 1.0, "bar_complete": False,
    }


class FuturesGuardTests(unittest.TestCase):
    def test_rth_direction_is_redacted_from_agent_view(self):
        futures = {"ES": quote("2026-07-16"), "NQ": quote("2026-07-16")}
        indices = {"SP500": quote("2026-07-16")}
        fxcc = {"Gold": quote("2026-07-16")}
        result = futures_dating_guard(
            futures, indices, fxcc,
            dt.datetime(2026, 7, 16, 15, 0, tzinfo=UTC),
        )
        self.assertIn("not evaluated", result)
        view = build_agent_view({"futures": futures, "fx_commodities_crypto": fxcc})
        self.assertIsNone(view["futures"]["ES"]["chg_pct"])
        self.assertFalse(view["futures"]["ES"]["direction_usable"])
        self.assertIsNone(view["fx_commodities_crypto"]["Gold"]["chg_pct"])

    def test_preopen_confirmed_direction_survives(self):
        futures = {"ES": quote("2026-07-16")}
        indices = {"SP500": quote("2026-07-15")}
        fxcc = {"Gold": quote("2026-07-16")}
        result = futures_dating_guard(
            futures, indices, fxcc,
            dt.datetime(2026, 7, 16, 12, 40, tzinfo=UTC),
        )
        self.assertIn("confirmed", result)
        view = build_agent_view({"futures": futures, "fx_commodities_crypto": fxcc})
        self.assertEqual(view["futures"]["ES"]["chg_pct"], 1.0)
        self.assertTrue(view["futures"]["ES"]["direction_usable"])


if __name__ == "__main__":
    unittest.main()
