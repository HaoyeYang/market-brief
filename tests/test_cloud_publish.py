import json
import tempfile
import unittest
from pathlib import Path

from cloud_publish import collect_publications, publications_for_report


class CloudPublishTests(unittest.TestCase):
    def test_only_curated_projection_is_publishable(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "out").mkdir()
            (project / "data").mkdir()
            base = "2026-07-19.intraday"
            report = project / "out" / f"{base}.md"
            report.write_text("# 私人报告\n\n已通过 publication gate。")
            (project / "out" / f"{base}.rank.json").write_text(json.dumps({
                "degraded": False, "report_chars": 20, "ranked": ["private detail"],
                "rank": {"catalysts": [{"name": "CPI", "confirm_condition": "yield rises"}]},
            }))
            (project / "out" / f"{base}.research.json").write_text(json.dumps({
                "route": {"day_type": "normal"},
                "evidence": [{"id": "ev-1", "url": "https://www.bls.gov/cpi/"}],
                "internal_prompt": "not projected",
            }))
            (project / "out" / f"{base}.history.json").write_text(json.dumps({
                "comparisons": {"1d": {"available": False}}, "private_note": "not projected",
            }))
            (project / "out" / "metrics.json").write_text(json.dumps({"run_count": 1}))
            (project / "out" / f"{base}.run.json").write_text(json.dumps({
                "total_cost_usd": 1.2, "session_id": "internal-session",
                "result": "full model relay", "modelUsage": {
                    "model": {"inputTokens": 10, "outputTokens": 20},
                },
            }))
            (project / "data" / f"{base}.json").write_text(json.dumps({
                "indices": {"SP500": {"last": 100}},
                "cached_from": "/opt/market-brief/private.json",
                "nested": {"live_fetch_error": "private endpoint detail", "status": "ok"},
            }))

            items = collect_publications(project, base, False)
            payloads = {item.object_name: item.payload.decode() for item in items}
            self.assertEqual(set(payloads), {
                f"out/{base}.md", f"out/{base}.rank.json",
                f"out/{base}.usage.json", f"data/{base}.json",
                f"out/{base}.research.json", f"out/{base}.history.json", "out/metrics.json",
            })
            combined = "".join(payloads.values())
            self.assertNotIn("internal-session", combined)
            self.assertNotIn("full model relay", combined)
            self.assertNotIn("private detail", combined)
            self.assertNotIn("cached_from", combined)
            self.assertNotIn("live_fetch_error", combined)
            self.assertNotIn("internal_prompt", combined)
            self.assertNotIn("private_note", combined)
            self.assertIn("yield rises", combined)
            self.assertIn('"total_tokens":30', combined)

    def test_intermediate_full_report_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "out").mkdir()
            (project / "data").mkdir()
            report = project / "out" / "2026-07-19.close.full.md"
            report.write_text("private")
            with self.assertRaises(ValueError):
                publications_for_report(project, report)


if __name__ == "__main__":
    unittest.main()
