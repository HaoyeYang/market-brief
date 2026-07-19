import tempfile
import unittest
from pathlib import Path

from report_viewer import (
    dashboard_html, markdown_to_html, report_files, report_html, report_summary,
    resolve_visible, visible_files,
)


class ReportViewerTests(unittest.TestCase):
    def test_lists_only_supported_visible_files_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old = out / "old.md"
            old.write_text("old")
            new = out / "new.json"
            new.write_text("{}")
            (out / ".private.md").write_text("hidden")
            (out / "ignore.html").write_text("ignored")
            self.assertEqual([p.name for p in visible_files(out)], ["new.json", "old.md"])

    def test_rejects_traversal_and_unknown_extensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "brief.md").write_text("ok")
            self.assertEqual(resolve_visible(out, "brief.md"), (out / "brief.md").resolve())
            self.assertIsNone(resolve_visible(out, "../brief.md"))
            self.assertIsNone(resolve_visible(out, "secret.env"))

    def test_report_listing_excludes_intermediate_full_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "2026-07-19.intraday.md").write_text("# 报告")
            (out / "2026-07-19.intraday.full.md").write_text("# 中间文件")
            (out / "notes.md").write_text("# notes")
            self.assertEqual(
                [path.name for path in report_files(out)],
                ["2026-07-19.intraday.md"],
            )

    def test_markdown_renderer_builds_toc_and_escapes_raw_html(self):
        rendered, toc = markdown_to_html(
            "# 标题\n## 核心观点\n<script>alert(1)</script>\n"
            "[来源](https://example.com/data)\n"
        )
        self.assertEqual(toc, [("核心观点", "核心观点")])
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertIn('rel="noopener noreferrer"', rendered)

    def test_dashboard_uses_report_usage_and_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            report = out / "2026-07-19.intraday.dual-shadow.md"
            report.write_text("# 周日盘中影子报告\n\n## 核心观点\n结构性回调。")
            (out / "2026-07-19.intraday.dual-shadow.usage.json").write_text(
                '{"estimated_cost_usd":0.36,"glm_route":{"selected":"zai-paid"},'
                '"providers":{"glm":{"usage":{"total_tokens":100}},'
                '"kimi":{"usage":{"total_tokens":200}}}}'
            )
            summary = report_summary(report)
            self.assertEqual(summary["tokens"], 300)
            self.assertEqual(summary["route"], "Z.AI GLM-5.2 → Kimi K3")
            dashboard = dashboard_html(out)
            self.assertIn("Latest Market Intelligence", dashboard)
            self.assertIn("$0.360", dashboard)
            self.assertIn("只读", dashboard)

    def test_report_connects_gated_data_and_builds_visual_cockpit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "out"
            data = root / "data"
            out.mkdir()
            data.mkdir()
            report = out / "2026-07-19.intraday.dual-shadow.md"
            report.write_text("# 可视化报告\n\n## 核心观点\n结构性回调。")
            (data / "2026-07-19.intraday.dual-shadow.json").write_text(
                '{"indices":{"SP500":{"last":7457.69,"chg_pct":-1.01}},'
                '"sectors":{"XLK":{"chg_pct":-1.09},"XLE":{"chg_pct":1.16}},'
                '"global_indices":{"Nikkei225":{"chg_pct":-4.03}},'
                '"fx_commodities_crypto":{"Gold":{"last":4600,"chg_pct":9.9,'
                '"direction_usable":false}},"data_quality":{"freshness":{"status":"ok"},'
                '"redacted_direction_fields":["Gold"]},"run_context":{"is_session":false}}'
            )
            summary = report_summary(report)
            self.assertEqual(summary["data_path"].name, "2026-07-19.intraday.dual-shadow.json")
            rendered = report_html(summary)
            self.assertIn("美国市场一览", rendered)
            self.assertIn("板块热力图", rendered)
            self.assertIn("全球市场相对表现", rendered)
            self.assertIn("方向未评估", rendered)
            self.assertIn("深度解读与证据链", rendered)

    def test_historical_report_without_data_falls_back_to_longform(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            out.mkdir()
            report = out / "2026-07-18.close.md"
            report.write_text("# 历史报告\n\n## 结论\n无快照。")
            rendered = report_html(report_summary(report))
            self.assertIn("没有配套的确定性数据文件", rendered)
            self.assertIn("无快照", rendered)


if __name__ == "__main__":
    unittest.main()
