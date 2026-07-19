import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from multi_provider_brief import (
    ProviderError, estimated_cost, extract_json, load_credentials,
    normalize_report_headings, run,
)


class FakeResponse:
    def __init__(self, content=None, usage=None, status_code=200, text=""):
        self.status_code = status_code
        self.text = text
        self._body = {
            "id": "request-test", "usage": usage or {},
            "choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}],
        }

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class MultiProviderBriefTests(unittest.TestCase):
    def test_credentials_reject_group_readable_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "credentials.env"
            path.write_text("MOONSHOT_API_KEY=secret\n")
            path.chmod(0o640)
            with self.assertRaises(ProviderError):
                load_credentials(path)

    def test_extracts_fenced_json(self):
        self.assertEqual(extract_json("```json\n{\"a\": 1}\n```"), {"a": 1})

    def test_normalizes_split_required_headings(self):
        report = "## 宏观\nA\n## 外汇\nB\n## 信用\nC"
        normalized = normalize_report_headings(report)
        self.assertIn("## 宏观、利率与央行", normalized)
        self.assertIn("## 外汇、商品与加密资产", normalized)
        self.assertIn("## 信用、波动率与流动性", normalized)

    def test_estimated_cost_handles_cached_input(self):
        usage = {
            "prompt_tokens": 1000, "completion_tokens": 2000,
            "prompt_tokens_details": {"cached_tokens": 100},
        }
        self.assertEqual(estimated_cost("zai-paid", usage), 0.010086)

    def test_one_call_per_provider_and_no_secret_in_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = {
                "date": "2026-07-19", "mode": "intraday",
                "data_quality": {"freshness": {"status": "ok"}},
                "run_context": {"is_session": False}, "futures": {},
                "fx_commodities_crypto": {},
            }
            (root / "data.json").write_text(json.dumps(data))
            (root / "history.json").write_text(json.dumps({"comparisons": {}}))
            creds = root / "credentials.env"
            creds.write_text("MOONSHOT_API_KEY=kimi-secret\nZAI_API_KEY=glm-secret\n")
            creds.chmod(0o600)
            research = {"thesis": "研究结论", "ranked_findings": [{"point": "x"}]}
            final = {
                "report_markdown": "# 美股与全球市场盘中影子简报\n" + ("分析内容。" * 600),
                "thesis": "总论",
                "ranked": [{
                    "point": str(i), "sources": ["deterministic:test"],
                    "source_angles": ["glm-5.2-analysis"],
                } for i in range(8)],
                "catalysts": [{
                    "name": str(i), "confirm_condition": "x", "invalidate_condition": "y",
                    "sources": ["deterministic:test"], "source_angles": ["glm-5.2-analysis"],
                } for i in range(3)],
            }
            session = FakeSession([
                FakeResponse(research, {"prompt_tokens": 10, "completion_tokens": 20}),
                FakeResponse(final, {"prompt_tokens": 30, "completion_tokens": 40}),
            ])
            args = SimpleNamespace(
                date="2026-07-19", mode="intraday", data=str(root / "data.json"),
                history=str(root / "history.json"), reference_report=None,
                out_dir=str(root / "out"), credentials=str(creds), dry_run=False,
                glm_max_tokens=12000, nvidia_max_tokens=16384, kimi_max_tokens=20000,
            )
            with patch.dict(os.environ, {}, clear=False):
                paths = run(args, session=session)
            self.assertEqual(len(session.calls), 2)
            self.assertIn("api.z.ai", session.calls[0][0])
            self.assertIn("api.moonshot.ai", session.calls[1][0])
            kimi_payload = session.calls[1][1]["json"]
            self.assertEqual(kimi_payload["reasoning_effort"], "max")
            self.assertNotIn("temperature", kimi_payload)
            combined = "".join(Path(path).read_text() for path in paths.values())
            self.assertNotIn("kimi-secret", combined)
            self.assertNotIn("glm-secret", combined)

    def test_nvidia_is_used_first_when_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = {"date": "2026-07-19", "mode": "intraday", "run_context": {"is_session": False}}
            (root / "data.json").write_text(json.dumps(data))
            (root / "history.json").write_text("{}")
            creds = root / "credentials.env"
            creds.write_text(
                "MOONSHOT_API_KEY=kimi-secret\nZAI_API_KEY=glm-secret\n"
                "NVIDIA_API_KEY=nv-secret\n"
            )
            creds.chmod(0o600)
            research = {"ranked_findings": [{"point": "x"}]}
            final = self._final_payload()
            session = FakeSession([FakeResponse(research), FakeResponse(final)])
            paths = run(self._args(root, creds), session=session)
            self.assertIn("integrate.api.nvidia.com", session.calls[0][0])
            self.assertEqual(session.calls[0][1]["json"]["model"], "z-ai/glm-5.2")
            usage = json.loads(Path(paths["usage"]).read_text())
            self.assertEqual(usage["glm_route"]["selected"], "nvidia")
            self.assertFalse(usage["glm_route"]["fallback_used"])

    def test_five_transient_nvidia_failures_then_paid_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data.json").write_text(json.dumps({
                "date": "2026-07-19", "mode": "intraday", "run_context": {"is_session": False},
            }))
            (root / "history.json").write_text("{}")
            creds = root / "credentials.env"
            creds.write_text(
                "MOONSHOT_API_KEY=kimi-secret\nZAI_API_KEY=glm-secret\n"
                "NVIDIA_API_KEY=nv-secret\n"
            )
            creds.chmod(0o600)
            failures = [FakeResponse(status_code=503, text="busy") for _ in range(5)]
            session = FakeSession(failures + [
                FakeResponse({"ranked_findings": [{"point": "x"}]}),
                FakeResponse(self._final_payload()),
            ])
            with patch("multi_provider_brief.time.sleep"):
                paths = run(self._args(root, creds), session=session)
            self.assertEqual(len(session.calls), 7)
            self.assertTrue(all("nvidia.com" in call[0] for call in session.calls[:5]))
            self.assertIn("api.z.ai", session.calls[5][0])
            usage = json.loads(Path(paths["usage"]).read_text())
            self.assertEqual(usage["glm_route"]["attempts"], 5)
            self.assertEqual(usage["glm_route"]["selected"], "zai-paid")

    def _args(self, root, creds):
        return SimpleNamespace(
            date="2026-07-19", mode="intraday", data=str(root / "data.json"),
            history=str(root / "history.json"), reference_report=None,
            out_dir=str(root / "out"), credentials=str(creds), dry_run=False,
            glm_max_tokens=12000, nvidia_max_tokens=16384, kimi_max_tokens=20000,
        )

    def _final_payload(self):
        return {
            "report_markdown": "# 美股与全球市场盘中影子简报\n" + ("分析内容。" * 600),
            "thesis": "总论",
            "ranked": [{
                "point": str(i), "sources": ["deterministic:test"],
                "source_angles": ["glm-5.2-analysis"],
            } for i in range(8)],
            "catalysts": [{
                "name": str(i), "confirm_condition": "x", "invalidate_condition": "y",
                "sources": ["deterministic:test"], "source_angles": ["glm-5.2-analysis"],
            } for i in range(3)],
        }


if __name__ == "__main__":
    unittest.main()
