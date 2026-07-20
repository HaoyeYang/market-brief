import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from multi_provider_brief import (
    ProviderError, call_claude_writer, estimated_cost, extract_json, load_credentials,
    normalize_agent_packet, normalize_report_headings,
    normalize_verification_packet, run,
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

    def test_reasoning_prefix_selects_outer_not_last_nested_json(self):
        packet = extract_json('thinking...\n{"claims":[{"claim":"x"}],"gaps":[]}')
        self.assertEqual(packet["claims"][0]["claim"], "x")
        self.assertIn("gaps", packet)

    def test_single_claim_packets_are_normalized(self):
        agent = normalize_agent_packet({"claim": "x", "evidence_ids": ["ev-1"]}, "macro_rates")
        verifier = normalize_verification_packet({"claim": "x", "status": "supported"})
        self.assertEqual(agent["claims"][0]["claim"], "x")
        self.assertEqual(verifier["verified_claims"][0]["status"], "supported")

    def test_invalid_mixed_claim_status_is_downgraded_to_unclear(self):
        verifier = normalize_verification_packet({
            "verified_claims": [{"claim": "partly supported", "status": "mixed"}],
        })
        self.assertEqual(verifier["verified_claims"][0]["status"], "unclear")

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

    def test_claude_writer_uses_subscription_safe_cli_and_records_resolved_model(self):
        seen = {}

        def fake_run(command, **kwargs):
            seen["command"] = command
            seen["env"] = kwargs["env"]
            return SimpleNamespace(
                returncode=0, stderr="",
                stdout=json.dumps({
                    "is_error": False, "result": '{"ok":true}',
                    "session_id": "session-test", "permission_denials": [],
                    "total_cost_usd": 0.25,
                    "modelUsage": {
                        "claude-opus-4-8": {
                            "inputTokens": 10, "outputTokens": 20,
                            "cacheReadInputTokens": 30, "cacheCreationInputTokens": 40,
                        },
                    },
                }),
            )

        with patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "must-not-leak",
            "ANTHROPIC_AUTH_TOKEN": "must-not-leak",
            "CLAUDE_CODE_OAUTH_TOKEN": "subscription-token",
        }):
            response = call_claude_writer(
                messages=[{"role": "system", "content": "system"}, {"role": "user", "content": "user"}],
                claude_bin="/usr/bin/true", run_command=fake_run,
            )
        self.assertIn("--safe-mode", seen["command"])
        self.assertNotIn("ANTHROPIC_API_KEY", seen["env"])
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", seen["env"])
        self.assertEqual(seen["env"]["CLAUDE_CODE_OAUTH_TOKEN"], "subscription-token")
        self.assertEqual(response["model"], "claude-opus-4-8")
        self.assertEqual(response["api_equivalent_cost_usd"], 0.25)

    def test_claude_writer_failure_falls_back_to_kimi(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data.json").write_text(json.dumps({
                "date": "2026-07-19", "mode": "intraday", "run_context": {"is_session": False},
            }))
            (root / "history.json").write_text("{}")
            creds = root / "credentials.env"
            creds.write_text("MOONSHOT_API_KEY=kimi-secret\nZAI_API_KEY=glm-secret\n")
            creds.chmod(0o600)
            session = FakeSession([
                FakeResponse({"ranked_findings": [{"point": "x"}]}),
                FakeResponse(self._final_payload()),
            ])
            args = self._args(root, creds)
            args.writer = "claude"
            args.claude_bin = "/usr/bin/true"
            with patch(
                "multi_provider_brief.call_claude_writer",
                side_effect=ProviderError("subscription quota exhausted"),
            ):
                paths = run(args, session=session)
            usage = json.loads(Path(paths["usage"]).read_text())
            self.assertEqual(usage["writer_route"]["selected"], "kimi")
            self.assertTrue(usage["writer_route"]["fallback_used"])
            self.assertEqual(usage["providers"]["writer"]["provider"], "kimi")

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

    def test_audited_evidence_enables_routed_agents_and_postmortem(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data.json").write_text(json.dumps({
                "date": "2026-07-19", "mode": "intraday", "run_context": {"is_session": False},
            }))
            (root / "history.json").write_text(json.dumps({
                "pending_evaluations": [{"catalyst_id": "old-1", "horizon": 1}],
            }))
            (root / "evidence.json").write_text(json.dumps({
                "route": {"day_type": "normal", "active_agents": ["us_equities", "macro_rates", "source_verifier"]},
                "coverage": {"official": 1},
                "evidence": [{
                    "id": "ev-1", "url": "https://www.bls.gov/test", "source_tier": "official",
                    "excerpt": "official evidence", "published_at": "2026-07-19T12:00:00+00:00",
                }],
            }))
            creds = root / "credentials.env"
            creds.write_text("MOONSHOT_API_KEY=kimi-secret\nZAI_API_KEY=glm-secret\n")
            creds.chmod(0o600)
            agent_one = {"agent": "us_equities", "claims": [{"claim": "x", "evidence_ids": ["ev-1"]}]}
            agent_two = {"agent": "macro_rates", "claims": [{"claim": "y", "evidence_ids": ["ev-1"]}]}
            verifier = {
                "verified_claims": [{
                    "claim": "x", "agent": "us_equities", "status": "supported",
                    "evidence_ids": ["ev-1"], "reason": "direct",
                }],
                "postmortem_results": [{
                    "catalyst_id": "old-1", "horizon": 1, "verdict": "confirmed",
                    "reason": "met", "evidence_keys": ["ev-1"],
                }],
            }
            session = FakeSession([
                FakeResponse(agent_one), FakeResponse(agent_two), FakeResponse(verifier),
                FakeResponse(self._final_payload()),
            ])
            args = self._args(root, creds)
            args.evidence = str(root / "evidence.json")
            paths = run(args, session=session)
            self.assertEqual(len(session.calls), 4)
            research = json.loads(Path(paths["research"]).read_text())
            rank = json.loads(Path(paths["rank"]).read_text())
            self.assertEqual(len(research["agents"]), 2)
            self.assertEqual(rank["postmortem"]["results"][0]["verdict"], "confirmed")
            self.assertEqual(rank["confirmed_claims"][0]["url"], "https://www.bls.gov/test")
            self.assertFalse(rank["degraded"])

    def _args(self, root, creds):
        return SimpleNamespace(
            date="2026-07-19", mode="intraday", data=str(root / "data.json"),
            history=str(root / "history.json"), reference_report=None,
            out_dir=str(root / "out"), credentials=str(creds), dry_run=False,
            glm_max_tokens=12000, nvidia_max_tokens=16384, kimi_max_tokens=20000,
            writer="kimi", claude_bin="claude", claude_model="opus",
            claude_effort="high", claude_timeout=900,
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
