import datetime as dt
import unittest

from market_clock import assess


UTC = dt.timezone.utc


class MarketClockTests(unittest.TestCase):
    def test_regular_preopen_window(self):
        result = assess(
            "2026-07-16", "preopen",
            dt.datetime(2026, 7, 16, 12, 40, tzinfo=UTC),
        )
        self.assertTrue(result.is_session)
        self.assertTrue(result.due)
        self.assertEqual(result.expected_cash_session, "2026-07-15")

    def test_preopen_rejects_early_trigger(self):
        result = assess(
            "2026-07-16", "preopen",
            dt.datetime(2026, 7, 16, 12, 20, tzinfo=UTC),
        )
        self.assertFalse(result.due)

    def test_market_holiday_skips(self):
        result = assess(
            "2026-07-03", "preopen",
            dt.datetime(2026, 7, 3, 12, 40, tzinfo=UTC),
        )
        self.assertFalse(result.is_session)
        self.assertFalse(result.due)

    def test_early_close_window_tracks_exchange_calendar(self):
        result = assess(
            "2026-11-27", "close",
            dt.datetime(2026, 11, 27, 18, 15, tzinfo=UTC),
        )
        self.assertTrue(result.due)
        self.assertIn("12:00:00-06:00", result.session_close_ct)


if __name__ == "__main__":
    unittest.main()
