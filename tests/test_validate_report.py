import unittest

from validate_report import REQUIRED_SECTIONS, validate


class ReportGateTests(unittest.TestCase):
    def make_report(self):
        body = "# 美股与全球市场盘中简报\n\n"
        body += "\n".join(f"## {name}\n内容。" for name in REQUIRED_SECTIONS)
        body += "\n[来源一](https://example.com/a) [来源二](https://example.com/b) [来源三](https://example.com/c)"
        return body + ("分析内容。" * 650)

    def test_accepts_structurally_complete_standard_report(self):
        rank = self.make_rank()
        data = {
            "mode": "intraday",
            "data_quality": {"freshness": {"status": "ok"}},
            "futures": {},
            "fx_commodities_crypto": {},
        }
        self.assertEqual(validate(self.make_report(), rank, data, "intraday", "standard"), [])

    def test_rejects_unredacted_unsafe_direction(self):
        rank = self.make_rank()
        data = {
            "mode": "intraday",
            "data_quality": {"freshness": {"status": "ok"}},
            "futures": {"ES": {"direction_usable": False, "chg": 1, "chg_pct": 2}},
            "fx_commodities_crypto": {},
        }
        errors = validate(self.make_report(), rank, data, "intraday", "standard")
        self.assertTrue(any("unsafe direction" in error for error in errors))

    def test_non_session_report_uses_lower_url_floor(self):
        report = self.make_report()
        report = report.replace(
            "[来源二](https://example.com/b) [来源三](https://example.com/c)", "")
        rank = self.make_rank()
        data = {
            "mode": "intraday", "run_context": {"is_session": False},
            "data_quality": {"freshness": {"status": "ok"}},
            "futures": {}, "fx_commodities_crypto": {},
        }
        self.assertEqual(validate(report, rank, data, "intraday", "standard"), [])

    def make_rank(self):
        return {
            "rank": {
                "ranked": [{"sources": ["deterministic:test"],
                            "source_angles": ["macro"]} for _ in range(6)],
                "catalysts": [{
                    "confirm_condition": "x", "invalidate_condition": "y",
                    "sources": ["deterministic:test"], "source_angles": ["macro"],
                } for _ in range(2)],
            },
            "diagnostics": {"angle_metrics": {"macro": {"scouted": 1}}},
            "postmortem": {"results": []},
        }


if __name__ == "__main__":
    unittest.main()
