#!/usr/bin/env python3
"""Generate a routed GLM-5.2 research packet and a provider-neutral brief.

The research tier tries NVIDIA before paid Z.AI.  The writer can be Kimi K3 or
the official Claude Code CLI authenticated to a Claude subscription; when
Claude is selected, Kimi remains the automatic availability fallback.
Credentials are read from the process environment or a mode-0600 env file;
the file is parsed as data and is never sourced as shell code.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests


DEFAULT_CREDENTIALS = Path.home() / ".config" / "market-brief" / "credentials.env"
DEFAULT_KIMI_BASE = "https://api.moonshot.ai/v1"
DEFAULT_GLM_BASE = "https://api.z.ai/api/paas/v4"
DEFAULT_NVIDIA_BASE = "https://integrate.api.nvidia.com/v1"
PROVENANCE_URLS = (
    "https://finance.yahoo.com/",
    "https://fred.stlouisfed.org/",
    "https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm",
    "https://www.finra.org/finra-data/browse-catalog/short-sale-volume-data",
)
PRICE_PER_MILLION = {
    # https://docs.z.ai/guides/overview/pricing
    "zai-paid": {"input": 1.40, "cached_input": 0.26, "output": 4.40},
    # https://platform.kimi.ai/ (Kimi K3 list pricing)
    "kimi": {"input": 3.00, "cached_input": 0.30, "output": 15.00},
    "nvidia": {"input": 0.0, "cached_input": 0.0, "output": 0.0},
}
ALLOWED_CREDENTIAL_NAMES = {
    "MOONSHOT_API_KEY", "KIMI_API_KEY", "ZAI_API_KEY", "ZHIPUAI_API_KEY",
    "NVIDIA_API_KEY", "KIMI_BASE_URL", "ZAI_BASE_URL", "NVIDIA_BASE_URL",
}


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


def load_credentials(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE pairs without executing the credential file."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise ProviderError(
            f"credential file permissions are {mode:03o}; run chmod 600 {path}"
        )
    for number, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            raise ProviderError(f"invalid credential line {number} in {path}")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key not in ALLOWED_CREDENTIAL_NAMES:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def effective_config(path: Path) -> dict[str, str]:
    file_values = load_credentials(path)

    def first(*names: str) -> str:
        for name in names:
            value = os.environ.get(name) or file_values.get(name)
            if value:
                return value
        return ""

    return {
        "kimi_key": first("MOONSHOT_API_KEY", "KIMI_API_KEY"),
        "glm_key": first("ZAI_API_KEY", "ZHIPUAI_API_KEY"),
        "nvidia_key": first("NVIDIA_API_KEY"),
        "kimi_base": first("KIMI_BASE_URL") or DEFAULT_KIMI_BASE,
        "glm_base": first("ZAI_BASE_URL") or DEFAULT_GLM_BASE,
        "nvidia_base": first("NVIDIA_BASE_URL") or DEFAULT_NVIDIA_BASE,
    }


def _post_chat(
    *, base_url: str, api_key: str, payload: dict[str, Any],
    session=requests, timeout: int = 240, retries: int = 2,
) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    last_error = "unknown provider failure"
    for attempt in range(retries + 1):
        try:
            response = session.post(url, headers=headers, json=payload, timeout=timeout)
            if response.status_code in {429, 500, 502, 503, 504} and attempt < retries:
                time.sleep(2 ** attempt)
                continue
            if response.status_code >= 400:
                detail = response.text[:500].replace(api_key, "[REDACTED]")
                raise ProviderError(
                    f"HTTP {response.status_code} from {url}: {detail}",
                    retryable=response.status_code in {408, 409, 425, 429, 500, 502, 503, 504},
                )
            body = response.json()
            choice = body["choices"][0]
            message = choice["message"]
            content = message["content"]
            if not isinstance(content, str) or not content.strip():
                raise ProviderError(
                    f"empty model response from {url}; finish_reason={choice.get('finish_reason')}",
                )
            return {
                "content": content, "usage": body.get("usage", {}), "id": body.get("id"),
                "model": body.get("model"), "finish_reason": choice.get("finish_reason"),
                "reasoning_chars": len(message.get("reasoning_content") or ""),
            }
        except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
            last_error = str(exc)
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise ProviderError(f"request failed for {url}: {last_error}") from exc
    raise ProviderError(last_error)


def call_claude_writer(
    *, messages: list[dict[str, str]], claude_bin: str, model: str = "opus",
    effort: str = "high", timeout: int = 900, run_command=subprocess.run,
) -> dict[str, Any]:
    """Call the official Claude Code CLI without exposing filesystem tools.

    API-key variables are removed from the child environment on purpose.  This
    route is for a Claude subscription login; silently charging an Anthropic
    Console API key would violate the operator's cost expectation.  A
    ``CLAUDE_CODE_OAUTH_TOKEN`` (for an explicitly configured long-lived
    subscription login) is left intact.
    """
    resolved_bin = shutil.which(claude_bin) if claude_bin else None
    if not resolved_bin and claude_bin and Path(claude_bin).is_file():
        resolved_bin = claude_bin
    if not resolved_bin:
        raise ProviderError(
            f"Claude Code executable not found: {claude_bin or 'claude'}",
            retryable=False,
        )

    system_prompt = "\n\n".join(
        item.get("content", "") for item in messages if item.get("role") == "system"
    )
    user_prompt = "\n\n".join(
        item.get("content", "") for item in messages if item.get("role") != "system"
    )
    command = [
        resolved_bin, "-p", "--model", model, "--effort", effort,
        "--system-prompt", system_prompt, "--safe-mode", "--tools", "",
        "--permission-mode", "dontAsk", "--max-turns", "1",
        "--no-session-persistence", "--output-format", "json",
    ]
    child_env = os.environ.copy()
    child_env.pop("ANTHROPIC_API_KEY", None)
    child_env.pop("ANTHROPIC_AUTH_TOKEN", None)
    try:
        completed = run_command(
            command, input=user_prompt, capture_output=True, text=True,
            timeout=timeout, env=child_env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProviderError(f"Claude Code writer failed to start: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown Claude Code error")[:800]
        raise ProviderError(f"Claude Code writer exited {completed.returncode}: {detail}")
    try:
        envelope = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ProviderError("Claude Code writer returned a non-JSON CLI envelope") from exc
    if envelope.get("is_error") is not False or not isinstance(envelope.get("result"), str):
        detail = str(envelope.get("result") or "missing result")[:800]
        raise ProviderError(f"Claude Code writer reported an error: {detail}")
    if envelope.get("permission_denials"):
        raise ProviderError("Claude Code writer requested a denied tool permission")

    model_usage = envelope.get("modelUsage") or {}
    models = list(model_usage)
    requested = model.lower()
    needle = next(
        (family for family in ("opus", "sonnet", "haiku", "fable") if family in requested),
        requested,
    )
    resolved_model = next(
        (name for name in models if needle in name.lower()),
        models[-1] if models else model,
    )
    normalized_usage = {
        "input_tokens": sum(int(item.get("inputTokens") or 0) for item in model_usage.values()),
        "completion_tokens": sum(int(item.get("outputTokens") or 0) for item in model_usage.values()),
        "cache_read_input_tokens": sum(int(item.get("cacheReadInputTokens") or 0) for item in model_usage.values()),
        "cache_creation_input_tokens": sum(int(item.get("cacheCreationInputTokens") or 0) for item in model_usage.values()),
    }
    return {
        "content": envelope["result"], "usage": normalized_usage,
        "native_model_usage": model_usage, "id": envelope.get("session_id"),
        "model": resolved_model, "finish_reason": "stop", "reasoning_chars": 0,
        "api_equivalent_cost_usd": round(float(envelope.get("total_cost_usd") or 0), 6),
        "billing_mode": "claude.ai-subscription",
    }


def extract_json(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        # Reasoning endpoints may place a thinking trace before the requested
        # object. Nested objects are also independently valid JSON from their
        # opening brace, so choose the largest decoded object, not the last one.
        decoder = json.JSONDecoder()
        objects = []
        for match in re.finditer(r"\{", candidate):
            try:
                parsed, end = decoder.raw_decode(candidate[match.start():])
                if isinstance(parsed, dict):
                    objects.append((end, parsed))
            except json.JSONDecodeError:
                continue
        if not objects:
            raise ProviderError("model response did not contain a valid JSON object")
        value = max(objects, key=lambda item: item[0])[1]
    if not isinstance(value, dict):
        raise ProviderError("model JSON response is not an object")
    return value


def normalize_report_headings(report: str) -> str:
    """Map split model headings to the exact publication-gate headings."""
    replacements = (
        ("宏观、利率与央行", "## 宏观\n", "## 宏观、利率与央行\n"),
        ("外汇、商品与加密资产", "## 外汇\n", "## 外汇、商品与加密资产\n"),
        ("信用、波动率与流动性", "## 信用\n", "## 信用、波动率与流动性\n"),
    )
    for required, split_heading, combined_heading in replacements:
        if required not in report and split_heading in report:
            report = report.replace(split_heading, combined_heading, 1)
    return report


def estimated_cost(provider: str, usage: dict[str, Any]) -> float:
    prices = PRICE_PER_MILLION[provider]
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    cached = min(cached, prompt)
    amount = (
        (prompt - cached) * prices["input"] +
        cached * prices["cached_input"] +
        completion * prices["output"]
    ) / 1_000_000
    return round(amount, 6)


def _glm_paid_payload(messages: list[dict[str, str]], max_tokens: int) -> dict[str, Any]:
    return {
        "model": "glm-5.2", "messages": messages,
        "thinking": {"type": "enabled"}, "reasoning_effort": "max",
        "temperature": 1, "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }


def _glm_nvidia_payload(messages: list[dict[str, str]], max_tokens: int) -> dict[str, Any]:
    # NVIDIA's current GLM-5.2 API reference does not expose reasoning_effort.
    # max_tokens is an output cap, not an effort selector. Keep the documented
    # default reasoning behavior instead of sending an unverified parameter.
    return {
        "model": "z-ai/glm-5.2", "messages": messages,
        "temperature": 1, "top_p": 1, "max_tokens": max_tokens,
        "seed": 42, "stream": False,
    }


def call_glm_with_fallback(
    *, config: dict[str, str], messages: list[dict[str, str]],
    paid_max_tokens: int, nvidia_max_tokens: int,
    session=requests, sleep=None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Try NVIDIA up to five times, then use the paid Z.AI endpoint.

    Only transient transport/rate/server errors and malformed model output are
    retried five times. Authentication and validation failures fall back at
    once because repeating an invalid key or payload cannot heal the request.
    """
    sleep = sleep or time.sleep
    failures = []
    if config["nvidia_key"]:
        for attempt in range(1, 6):
            try:
                response = _post_chat(
                    base_url=config["nvidia_base"], api_key=config["nvidia_key"],
                    payload=_glm_nvidia_payload(messages, nvidia_max_tokens),
                    session=session, retries=0,
                )
                packet = extract_json(response["content"])
                route = {
                    "primary": "nvidia", "selected": "nvidia", "attempts": attempt,
                    "fallback_used": False, "failures": failures,
                    "reasoning": "provider default; NVIDIA GLM-5.2 API exposes no reasoning_effort selector",
                }
                return response, packet, route
            except ProviderError as exc:
                failures.append({
                    "attempt": attempt, "retryable": exc.retryable,
                    "error": str(exc)[:500],
                })
                if not exc.retryable:
                    break
                if attempt < 5:
                    sleep(min(2 ** (attempt - 1), 8))
    else:
        failures.append({
            "attempt": 0, "retryable": False,
            "error": "NVIDIA_API_KEY not configured",
        })

    response = _post_chat(
        base_url=config["glm_base"], api_key=config["glm_key"],
        payload=_glm_paid_payload(messages, paid_max_tokens),
        session=session, timeout=600,
    )
    try:
        packet = extract_json(response["content"])
    except ProviderError as exc:
        usage = response.get("usage", {})
        raise ProviderError(
            "paid Z.AI returned incomplete/non-JSON research output; "
            f"finish_reason={response.get('finish_reason')}, "
            f"completion_tokens={usage.get('completion_tokens')}, "
            f"reasoning_chars={response.get('reasoning_chars')}",
            retryable=response.get("finish_reason") in {"length", "network_error"},
        ) from exc
    return response, packet, {
        "primary": "nvidia", "selected": "zai-paid",
        "attempts": len([item for item in failures if item["attempt"] > 0]),
        "fallback_used": True, "failures": failures,
        "reasoning": "paid Z.AI fallback after NVIDIA unavailable or exhausted",
    }


AGENT_MISSIONS = {
    "us_equities": "分析美股指数、breadth、板块、mega-cap集中度、公司与ETF异动；区分事实与推断。",
    "macro_rates": "分析宏观实际值/预期/前值、Fed、利率曲线、美元和估值传导；缺失预期值时不得补造。",
    "ai_tech_chain": "分析AI、半导体、HBM/DRAM、云与数据中心产业链，重点覆盖给定watchlist并区分实质变化和短期叙事。",
    "global_cross_asset": "分析欧洲、亚洲、外汇、商品、加密、信用、波动率与地缘事件对美股的交叉验证。",
}


def normalize_agent_packet(packet: dict[str, Any], agent: str) -> dict[str, Any]:
    if isinstance(packet.get("claims"), list):
        packet.setdefault("agent", agent)
        return packet
    if isinstance(packet.get("claim"), str):
        return {
            "agent": agent, "summary": packet.get("why_it_matters") or packet["claim"],
            "claims": [packet], "contradictions": [],
            "gaps": ["provider returned one claim instead of the requested packet"],
        }
    return {
        "agent": agent, "summary": str(packet.get("summary") or "结构化输出缺失"),
        "claims": [], "contradictions": [], "gaps": ["malformed agent packet"],
    }


def normalize_verification_packet(packet: dict[str, Any]) -> dict[str, Any]:
    if isinstance(packet.get("verified_claims"), list):
        normalized_statuses = []
        for raw_claim in packet["verified_claims"]:
            if not isinstance(raw_claim, dict):
                continue
            claim = dict(raw_claim)
            if claim.get("status") not in {"supported", "unclear", "refuted"}:
                original = str(claim.get("status") or "missing")
                claim["status"] = "unclear"
                claim["reason"] = (
                    f"normalized unsupported verifier status '{original}' to unclear; "
                    + str(claim.get("reason") or "")
                ).strip()
            normalized_statuses.append(claim)
        packet["verified_claims"] = normalized_statuses
        packet.setdefault("postmortem_results", [])
        packet.setdefault("source_notes", [])
        packet.setdefault("unverified", [])
        return packet
    if packet.get("status") in {"supported", "unclear", "refuted"}:
        return {
            "verified_claims": [packet], "postmortem_results": [],
            "source_notes": ["provider returned one verification instead of the requested packet"],
            "unverified": [],
        }
    return {
        "verified_claims": [], "postmortem_results": [], "source_notes": [],
        "unverified": ["malformed verifier packet"],
    }


def agent_prompt(agent: str, data: dict, history: dict, evidence_packet: dict) -> list[dict[str, str]]:
    task = {
        "agent": agent, "mission": AGENT_MISSIONS[agent],
        "rules": [
            "只可引用 deterministic_data 路径或 evidence 中的 id；不允许用模型记忆补新闻或数字",
            "key facts 必须保留事件时间、发布时间与URL，解释 why_it_matters",
            "信息冲突写入 contradictions；没有实质更新明确写 none",
            "不提供直接买卖指令",
            "claims 保持 4–8 条，只保留能改变当日判断的最高价值事实",
        ],
        "output_schema": {
            "agent": agent, "summary": "string",
            "claims": [{
                "claim": "string", "why_it_matters": "string", "confidence": "high|medium|low",
                "evidence_ids": ["ev-id or deterministic:path"], "tickers": ["NVDA"],
            }],
            "contradictions": ["string"], "gaps": ["string"],
        },
        "deterministic_data": json.loads(_json_for_prompt(data, 42000)),
        "history_context": json.loads(_json_for_prompt(history, 16000)),
        "route": evidence_packet.get("route", {}),
        "evidence": json.loads(_json_for_prompt(evidence_packet.get("evidence", []), 46000)),
    }
    return [
        {"role": "system", "content": "你是受证据约束的金融研究agent。输出严格JSON对象，不输出Markdown。"},
        {"role": "user", "content": json.dumps(task, ensure_ascii=False)},
    ]


def verifier_prompt(agent_packets: list[dict], evidence_packet: dict, history: dict) -> list[dict[str, str]]:
    task = {
        "task": "逐条核查agent claims，并完成到期的1日/5日事后评分。只有证据直接支持时才标supported。",
        "output_schema": {
            "verified_claims": [{
                "claim": "string", "agent": "string", "status": "supported|unclear|refuted",
                "reason": "string", "evidence_ids": ["string"], "why_it_matters": "string",
            }],
            "postmortem_results": [{
                "catalyst_id": "string", "horizon": 1,
                "verdict": "confirmed|mixed|invalidated|not_evaluable",
                "reason": "string", "evidence_keys": ["string"],
            }],
            "source_notes": ["string"], "unverified": ["string"],
        },
        "requirements": [
            "URL或标题本身不等于支持；必须检查excerpt或官方API字段",
            "source_tier=official优先；可靠媒体可交叉验证但不得冒充官方",
            "不得评估尚未到期的catalyst；证据不足用not_evaluable",
        ],
        "agent_packets": agent_packets,
        "evidence": json.loads(_json_for_prompt(evidence_packet.get("evidence", []), 52000)),
        "pending_evaluations": history.get("pending_evaluations", []),
    }
    return [
        {"role": "system", "content": "你是来源验证器与事后评分审计员。输出严格JSON。"},
        {"role": "user", "content": json.dumps(task, ensure_ascii=False)},
    ]


def run_dynamic_agents(
    *, config: dict[str, str], data: dict, history: dict, evidence_packet: dict,
    profile: str, args, session=requests,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Run routed specialists and one verifier, sharing a circuit breaker."""
    requested = evidence_packet.get("route", {}).get("active_agents", [])
    agents = [name for name in requested if name in AGENT_MISSIONS]
    agents = agents[:(5 if profile == "deep" else 4)] or ["us_equities"]
    packets: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    glm_config = dict(config)

    def invoke(name: str, messages: list[dict[str, str]], max_tokens: int):
        nonlocal glm_config
        call_started = time.monotonic()
        print(f"market-brief agent start: {name}", file=sys.stderr, flush=True)
        response, packet, route = call_glm_with_fallback(
            config=glm_config, messages=messages, paid_max_tokens=max_tokens,
            nvidia_max_tokens=min(max_tokens, args.nvidia_max_tokens), session=session,
        )
        if route.get("fallback_used"):
            # Do not pay the latency of five more known-bad free calls for every
            # remaining agent in the same workflow.
            glm_config = {**glm_config, "nvidia_key": ""}
        calls.append({"agent": name, "response": response, "route": route})
        print(
            f"market-brief agent done: {name} provider={route.get('selected')} "
            f"elapsed={time.monotonic() - call_started:.1f}s",
            file=sys.stderr, flush=True,
        )
        return packet

    for agent in agents:
        packet = invoke(agent, agent_prompt(agent, data, history, evidence_packet), 4200)
        packets.append(normalize_agent_packet(packet, agent))
    verifier = normalize_verification_packet(
        invoke("source_verifier", verifier_prompt(packets, evidence_packet, history), 5000)
    )
    research = {
        "route": evidence_packet.get("route", {}), "agents": packets,
        "verification": verifier, "coverage": evidence_packet.get("coverage", {}),
        "source_errors": evidence_packet.get("source_errors", []),
        "evidence": evidence_packet.get("evidence", []),
        "limitations": evidence_packet.get("limitations", []),
    }
    return research, calls, agents


def writer_research_packet(research: dict) -> dict:
    """Keep the writer focused while the viewer retains the complete packet."""
    verification = research.get("verification") or {}
    verified = verification.get("verified_claims", [])[:36]
    referenced = {
        evidence_id for claim in verified if claim.get("status") == "supported"
        for evidence_id in claim.get("evidence_ids", [])
    }
    evidence_items = []
    for item in research.get("evidence", []):
        calendar = "calendar" in str(item.get("kind")) or item.get("kind") == "treasury_auction"
        if item.get("id") not in referenced and not calendar:
            continue
        evidence_items.append({
            key: (value[:600] if key == "excerpt" and isinstance(value, str) else value)
            for key, value in item.items()
            if key in {
                "id", "kind", "title", "excerpt", "source_name", "source_tier", "url",
                "event_time", "published_at", "audit_status", "angles", "tickers",
            }
        })
        if len(evidence_items) >= 28:
            break
    agents = []
    for packet in research.get("agents", []):
        agents.append({
            "agent": packet.get("agent"), "summary": packet.get("summary"),
            "claims": (packet.get("claims") or [])[:8],
            "contradictions": (packet.get("contradictions") or [])[:5],
            "gaps": (packet.get("gaps") or [])[:5],
        })
    return {
        "route": research.get("route", {}), "coverage": research.get("coverage", {}),
        "agents": agents,
        "verification": {
            "verified_claims": verified,
            "source_notes": (verification.get("source_notes") or [])[:12],
            "unverified": (verification.get("unverified") or [])[:16],
            "postmortem_results": verification.get("postmortem_results", []),
        },
        "evidence": evidence_items, "limitations": research.get("limitations", []),
    }


def _json_for_prompt(value: Any, limit: int) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(text) <= limit:
        return text
    # The deterministic snapshot is already small in normal operation. If it
    # grows unexpectedly, preserve valid JSON and make truncation explicit.
    return json.dumps({
        "truncated": True,
        "note": f"input exceeded {limit} characters",
        "preview": text[:limit],
    }, ensure_ascii=False, separators=(",", ":"))


def glm_prompt(data: dict, history: dict, reference: str) -> list[dict[str, str]]:
    system = (
        "你是美国与全球金融市场的资深研究员和反方审稿人。只可使用用户提供的确定性数据、历史上下文和参考报告；"
        "不得虚构实时新闻、价格、日期或来源。对非交易日、未完成 bar、direction_usable=false 必须明确降级。"
        "输出严格 JSON，不要 Markdown 围栏。"
    )
    task = {
        "task": "为第二个写作模型制作研究包，找出主线、跨资产确认/矛盾、风险与1-5日可检验催化剂。",
        "output_schema": {
            "market_state": "string", "thesis": "string",
            "ranked_findings": [{
                "point": "string", "evidence_keys": ["data.path"],
                "why_it_matters": "string", "confidence": "high|medium|low",
            }],
            "cross_asset_confirmations": ["string"],
            "contradictions": ["string"],
            "catalysts": [{
                "name": "string", "horizon_days": 1,
                "confirm_condition": "string", "invalidate_condition": "string",
            }],
            "data_limits": ["string"], "writer_instructions": ["string"],
        },
        "requirements": [
            "ranked_findings 至少 8 条，catalysts 至少 3 条",
            "每条数值判断必须带确定性 JSON 路径；没有证据就放入 data_limits",
            "把参考报告仅视为待审稿材料，数据冲突时以确定性数据为准",
            "不要声称已联网搜索",
        ],
        "deterministic_data": json.loads(_json_for_prompt(data, 50000)),
        "history_context": json.loads(_json_for_prompt(history, 24000)),
        "reference_report": reference[:16000],
    }
    return [{"role": "system", "content": system},
            {"role": "user", "content": json.dumps(task, ensure_ascii=False)}]


def kimi_prompt(
    data: dict, history: dict, research: dict, profile: str = "standard",
    writer_agent: str = "kimi-k3-synthesis",
) -> list[dict[str, str]]:
    system = (
        f"你是金融市场日报总编辑（{writer_agent}）。基于确定性数据、SQLite历史上下文和GLM研究包写简体中文报告。"
        "不能把未完成或禁止方向判断的bar当收盘，不能把免费期权/FINRA/CFTC代理误称实时资金流。"
        "输出严格 JSON，不要 Markdown 围栏。"
    )
    min_chars = 2000 if profile == "deep" else 1200
    mode = data.get("mode", "intraday")
    mode_label = {"preopen": "盘前", "close": "收盘", "intraday": "盘中"}.get(
        mode, "盘中"
    )
    is_session = bool(data.get("run_context", {}).get("is_session"))
    publication_context = (
        f"正式的{mode_label}市场报告"
        if is_session
        else f"非交易日或窗口外的{mode_label}影子验证报告"
    )
    task = {
        "task": f"生成一份可审计的{publication_context}。",
        "output_schema": {
            "report_markdown": "string",
            "thesis": "string",
            "ranked": [{
                "point": "string", "why_it_matters": "string", "section": "string",
                "sources": ["deterministic:data.path"],
                "source_angles": [f"glm-5.2-analysis|{writer_agent}"],
            }],
            "catalysts": [{
                "name": "string", "thesis_link": "string", "horizon_days": 1,
                "confirm_condition": "string", "invalidate_condition": "string",
                "sources": ["deterministic:data.path"],
                "source_angles": ["glm-5.2-analysis"],
            }],
            "unverified": ["string"],
        },
        "report_requirements": [
            f"标题前160字含‘{mode_label}’，正文{min_chars}至9000个中文字符；优先清晰、紧凑，不用重复证据表",
            "以下字符串必须作为原样的二级标题，不得拆分或改字：核心观点；美国市场；全球市场；宏观、利率与央行；外汇、商品与加密资产；信用、波动率与流动性；未来 1-5 个交易日；数据质量与存疑；来源",
            "核心观点用2-3句话判断risk-on/risk-off、最强最弱方向和市场真正交易的变量，并在结尾列出三条今日结论",
            "美国市场覆盖S&P 500、Nasdaq、Dow、Russell 2000、breadth、mega-cap集中度、VIX及主要板块；解释驱动而非只报涨跌",
            "必须包含‘AI、半导体、云与数据中心’三级小节，覆盖NVDA、AMD、AVGO、TSM、MU、QCOM和大型云厂商；无实质更新时明确说明",
            "必须包含重要公司/财报/指引/并购/监管/ETF异动；宏观事件仅在证据提供时列实际值、预期值、前值，不得补造缺失字段",
            "未来1-5日列事件时间、风险/机会、上行与下行情景、确认和失效条件，不提供直接买卖指令",
            "每个关键联网事实使用Markdown可点击链接，邻近标注source、event_time/published_at；区分事件发生和报道时间",
            "run_context.is_session=false 时必须明确标为影子验证，不可伪装成实时开盘或收盘；为 true 时按实际模式写正式报告",
            "包含‘昨日/5日/20日’小节；比较不可用时直接说明样本尚不足",
            "若为美国股市全日休市，盘前生成精简预览；收盘模式不应被调度器调用。若证据冲突，说明口径差异",
            "ranked 至少 8 条；catalysts 至少 3 条且条件必须可观察、可证伪",
            "来源章节列出实际使用的证据URL和时间；确定性数据来源URL不代表本次另行网页核验",
        ],
        "provenance_urls": list(PROVENANCE_URLS),
        "deterministic_data": json.loads(_json_for_prompt(data, 50000)),
        "history_context": json.loads(_json_for_prompt(history, 24000)),
        "glm_research_packet": research,
    }
    return [{"role": "system", "content": system},
            {"role": "user", "content": json.dumps(task, ensure_ascii=False)}]


def _validate_model_payload(payload: dict[str, Any], profile: str = "standard") -> None:
    report = payload.get("report_markdown")
    min_chars = 2000 if profile == "deep" else 1200
    if not isinstance(report, str) or len(report.strip()) < min_chars:
        raise ProviderError(f"Kimi report_markdown is missing or shorter than {min_chars} characters")
    ranked = payload.get("ranked")
    catalysts = payload.get("catalysts")
    if not isinstance(ranked, list) or len(ranked) < 6:
        raise ProviderError("Kimi returned fewer than 6 ranked points")
    if not isinstance(catalysts, list) or len(catalysts) < 2:
        raise ProviderError("Kimi returned fewer than 2 catalysts")


def build_rank(
    payload: dict[str, Any], data: dict, glm_packet: dict, glm_angle: str,
    profile: str = "standard", evidence_packet: dict | None = None,
) -> dict[str, Any]:
    ranked = payload["ranked"]
    catalysts = payload["catalysts"]
    evidence_packet = evidence_packet or {}
    evidence_by_id = {
        item.get("id"): item for item in evidence_packet.get("evidence", [])
        if isinstance(item, dict)
    }
    def normalized_sources(values):
        result = []
        for value in values or []:
            mapped = evidence_by_id.get(value, {}).get("url")
            result.append(mapped or value)
        return list(dict.fromkeys(result))
    for item in ranked:
        item.setdefault("sources", ["deterministic:market-data"])
        item["sources"] = normalized_sources(item["sources"])
        item.setdefault("source_angles", ["kimi-k3-synthesis"])
    for item in catalysts:
        item.setdefault("horizon_days", 5)
        item.setdefault("sources", ["deterministic:market-data"])
        item["sources"] = normalized_sources(item["sources"])
        item.setdefault("source_angles", [glm_angle])
    verification = glm_packet.get("verification", {}) if isinstance(glm_packet, dict) else {}
    verified = verification.get("verified_claims", []) if isinstance(verification, dict) else []
    confirmed_claims = []
    for claim in verified:
        if claim.get("status") != "supported":
            continue
        for evidence_id in claim.get("evidence_ids", []) or [None]:
            item = evidence_by_id.get(evidence_id, {})
            confirmed_claims.append({
                "angle": claim.get("agent"), "claim": claim.get("claim", ""),
                "url": item.get("url"), "ts": item.get("published_at") or item.get("event_time"),
            })
    agent_packets = glm_packet.get("agents", []) if isinstance(glm_packet, dict) else []
    angle_metrics = {}
    for packet in agent_packets:
        name = packet.get("agent", "unknown")
        claims = packet.get("claims", []) if isinstance(packet.get("claims"), list) else []
        supported = sum(
            claim.get("status") == "supported" and claim.get("agent") == name
            for claim in verified
        )
        angle_metrics[name] = {
            "scouted": len(claims), "excerpt_supported": supported,
            "source_confirmed": supported,
        }
    if not angle_metrics:
        angle_metrics = {
            glm_angle: {
                "scouted": len(glm_packet.get("ranked_findings", [])),
                "excerpt_supported": 0, "source_confirmed": 0,
            }
        }
    angle_metrics["kimi-k3-synthesis"] = {
        "scouted": len(ranked), "excerpt_supported": 0, "source_confirmed": 0,
    }
    route = evidence_packet.get("route") or {
        "day_type": "shadow", "flags": ["portable-provider-test"], "events": [],
        "reason": "GLM-5.2 analysis followed by Kimi K3 synthesis",
    }
    return {
        "rank": {
            "thesis": payload.get("thesis", ""),
            "ranked": ranked,
            "catalysts": catalysts,
            "dropped": [],
        },
        "confirmed_claims": confirmed_claims,
        "unverified": payload.get("unverified", []) + (verification.get("unverified", []) if isinstance(verification, dict) else []),
        "diagnostics": {
            "mode": data.get("mode"), "profile": profile, "day_type": route.get("day_type", "shadow"),
            "scout_count": len(agent_packets) or 2,
            "active_angles": list(angle_metrics), "angle_metrics": angle_metrics,
        },
        "postmortem": {"results": verification.get("postmortem_results", []) if isinstance(verification, dict) else []},
        "route": route,
        "degraded": not bool(evidence_packet.get("coverage", {}).get("official")),
        "report_chars": len(payload["report_markdown"].strip()),
    }


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(content)
    os.replace(tmp, path)


def run(args, session=requests) -> dict[str, str]:
    data = json.loads(Path(args.data).read_text())
    history = json.loads(Path(args.history).read_text())
    evidence_path = getattr(args, "evidence", None)
    evidence_packet = json.loads(Path(evidence_path).read_text()) if evidence_path else {}
    reference = Path(args.reference_report).read_text() if args.reference_report else ""
    if data.get("date") != args.date or data.get("mode") != args.mode:
        raise ProviderError("data date/mode does not match requested shadow run")

    config = effective_config(Path(args.credentials).expanduser())
    writer_preference = getattr(args, "writer", os.environ.get("MARKET_BRIEF_WRITER", "kimi"))
    if writer_preference not in {"kimi", "claude"}:
        raise ProviderError(f"invalid writer: {writer_preference}", retryable=False)
    claude_bin = getattr(args, "claude_bin", os.environ.get("CLAUDE_BIN", "claude"))
    claude_model = getattr(args, "claude_model", os.environ.get("MARKET_BRIEF_CLAUDE_MODEL", "opus"))
    claude_effort = getattr(args, "claude_effort", os.environ.get("MARKET_BRIEF_CLAUDE_EFFORT", "high"))
    missing = [name for name, value in (
        # Kimi stays configured even with Claude selected because it is the
        # availability fallback for expired OAuth sessions or subscription caps.
        ("MOONSHOT_API_KEY", config["kimi_key"]), ("ZAI_API_KEY", config["glm_key"]),
    ) if not value]
    readiness = {
        "date": args.date, "mode": args.mode, "credentials_file": str(Path(args.credentials).expanduser()),
        "nvidia_glm_configured": bool(config["nvidia_key"]),
        "paid_glm_configured": bool(config["glm_key"]),
        "kimi_configured": bool(config["kimi_key"]),
        "writer_preference": writer_preference,
        "claude_bin": claude_bin if writer_preference == "claude" else None,
        "claude_model": claude_model if writer_preference == "claude" else None,
        "nvidia_endpoint": config["nvidia_base"],
        "glm_endpoint": config["glm_base"], "kimi_endpoint": config["kimi_base"],
        "data_chars": len(json.dumps(data, ensure_ascii=False)),
        "history_chars": len(json.dumps(history, ensure_ascii=False)),
        "evidence_items": len(evidence_packet.get("evidence", [])),
        "dynamic_route": evidence_packet.get("route", {}),
    }
    if args.dry_run:
        print(json.dumps(readiness, ensure_ascii=False, indent=2))
        return {}
    if missing:
        raise ProviderError(
            "missing credentials: " + ", ".join(missing) +
            f"; add them to {Path(args.credentials).expanduser()} with mode 600"
        )

    profile = getattr(args, "profile", "standard")
    started = dt.datetime.now(dt.timezone.utc)
    if evidence_packet:
        glm_packet, glm_calls, active_agents = run_dynamic_agents(
            config=config, data=data, history=history, evidence_packet=evidence_packet,
            profile=profile, args=args, session=session,
        )
        routes = [call["route"] for call in glm_calls]
        selected = {route.get("selected") for route in routes}
        glm_route = {
            "primary": "nvidia", "selected": next(iter(selected)) if len(selected) == 1 else "mixed",
            "fallback_used": any(route.get("fallback_used") for route in routes),
            "attempts": sum(int(route.get("attempts") or 0) for route in routes),
            "agent_routes": [
                {"agent": call["agent"], "selected": call["route"].get("selected"),
                 "attempts": call["route"].get("attempts"),
                 "fallback_used": call["route"].get("fallback_used")}
                for call in glm_calls
            ],
        }
    else:
        glm_response, glm_packet, glm_route = call_glm_with_fallback(
            config=config, messages=glm_prompt(data, history, reference),
            paid_max_tokens=args.glm_max_tokens,
            nvidia_max_tokens=args.nvidia_max_tokens,
            session=session,
        )
        glm_calls = [{"agent": "glm_analysis", "response": glm_response, "route": glm_route}]
        active_agents = ["glm_analysis"]
    writer_research = writer_research_packet(glm_packet) if evidence_packet else glm_packet
    writer_response = None
    writer_route = {
        "primary": writer_preference, "selected": None,
        "fallback_used": False, "failures": [],
    }
    if writer_preference == "claude":
        writer_started = time.monotonic()
        print("market-brief agent start: claude-opus-synthesis", file=sys.stderr, flush=True)
        try:
            candidate = call_claude_writer(
                messages=kimi_prompt(
                    data, history, writer_research, profile,
                    writer_agent="claude-opus-synthesis",
                ),
                claude_bin=claude_bin,
                model=claude_model, effort=claude_effort,
                timeout=getattr(args, "claude_timeout", 900),
            )
            candidate_payload = extract_json(candidate["content"])
            if isinstance(candidate_payload.get("report_markdown"), str):
                candidate_payload["report_markdown"] = normalize_report_headings(
                    candidate_payload["report_markdown"]
                )
            _validate_model_payload(candidate_payload, profile)
            writer_response, final_payload = candidate, candidate_payload
            writer_route["selected"] = "claude-subscription"
        except ProviderError as exc:
            writer_route["fallback_used"] = True
            writer_route["failures"].append({"provider": "claude-subscription", "error": str(exc)[:800]})
            print(f"market-brief Claude writer unavailable; falling back to Kimi: {exc}", file=sys.stderr)
        finally:
            print(
                f"market-brief agent done: claude-opus-synthesis elapsed={time.monotonic() - writer_started:.1f}s",
                file=sys.stderr, flush=True,
            )

    if writer_response is None:
        writer_started = time.monotonic()
        print("market-brief agent start: kimi-k3-synthesis", file=sys.stderr, flush=True)
        writer_response = _post_chat(
            base_url=config["kimi_base"], api_key=config["kimi_key"], session=session,
            payload={
                "model": "kimi-k3", "messages": kimi_prompt(
                    data, history, writer_research, profile,
                    writer_agent="kimi-k3-synthesis",
                ),
                "max_completion_tokens": args.kimi_max_tokens,
                "response_format": {"type": "json_object"}, "reasoning_effort": "max",
            }, timeout=600,
        )
        print(
            f"market-brief agent done: kimi-k3-synthesis elapsed={time.monotonic() - writer_started:.1f}s",
            file=sys.stderr, flush=True,
        )
        final_payload = extract_json(writer_response["content"])
        if isinstance(final_payload.get("report_markdown"), str):
            final_payload["report_markdown"] = normalize_report_headings(
                final_payload["report_markdown"]
            )
        _validate_model_payload(final_payload, profile)
        writer_route["selected"] = "kimi"
    glm_angle = (
        "nvidia-glm-5.2-analysis" if glm_route["selected"] == "nvidia"
        else "zai-glm-5.2-analysis" if glm_route["selected"] == "zai-paid"
        else "mixed-glm-5.2-analysis"
    )
    rank = build_rank(final_payload, data, glm_packet, glm_angle, profile, evidence_packet)

    base = f"{args.date}.{args.mode}.dual-shadow"
    out_dir = Path(args.out_dir)
    report_path = out_dir / f"{base}.md"
    rank_path = out_dir / f"{base}.rank.json"
    research_path = out_dir / f"{base}.research.json"
    usage_path = out_dir / f"{base}.usage.json"
    glm_cost = 0.0
    glm_providers = {}
    for index, call in enumerate(glm_calls):
        provider = call["route"]["selected"]
        response = call["response"]
        call_cost = estimated_cost(provider, response["usage"])
        glm_cost += call_cost
        glm_providers[f"glm_{index + 1}_{call['agent']}"] = {
            "provider": provider,
            "model": "z-ai/glm-5.2" if provider == "nvidia" else "glm-5.2",
            "usage": response["usage"], "request_id": response["id"],
            "finish_reason": response.get("finish_reason"),
            "reasoning_chars": response.get("reasoning_chars"),
            "estimated_cost_usd": call_cost,
        }
    glm_cost = round(glm_cost, 6)
    writer_label = (
        "claude-opus-synthesis"
        if writer_route["selected"] == "claude-subscription"
        else "kimi-k3-synthesis"
    )
    if writer_route["selected"] == "kimi":
        writer_cost = estimated_cost("kimi", writer_response["usage"])
        writer_provider_record = {
            "provider": "kimi", "model": "kimi-k3", "usage": writer_response["usage"],
            "request_id": writer_response["id"],
            "finish_reason": writer_response.get("finish_reason"),
            "reasoning_chars": writer_response.get("reasoning_chars"),
            "estimated_cost_usd": writer_cost,
        }
        subscription_equivalent_cost = 0.0
    else:
        writer_cost = 0.0
        subscription_equivalent_cost = writer_response.get("api_equivalent_cost_usd", 0.0)
        writer_provider_record = {
            "provider": "claude-subscription", "model": writer_response["model"],
            "usage": writer_response["usage"],
            "native_model_usage": writer_response.get("native_model_usage", {}),
            "request_id": writer_response.get("id"),
            "finish_reason": writer_response.get("finish_reason"),
            "billing_mode": writer_response.get("billing_mode"),
            "estimated_incremental_cost_usd": 0.0,
            "api_equivalent_cost_usd": subscription_equivalent_cost,
        }
    external_api_cost = round(glm_cost + writer_cost, 6)
    usage = {
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "workflow": active_agents + ["source_verifier", writer_label] if evidence_packet else [glm_angle, writer_label],
        "glm_route": glm_route,
        "writer_route": writer_route,
        "providers": {
            **glm_providers,
            "writer": writer_provider_record,
        },
        "cost_usd": None,
        "estimated_cost_usd": external_api_cost,
        "total_cost_usd": external_api_cost,
        "subscription_api_equivalent_cost_usd": subscription_equivalent_cost,
        "pricing_asof": "2026-07-19",
        "pricing_sources": [
            "https://docs.z.ai/guides/overview/pricing",
            "https://platform.kimi.ai/",
        ],
        "cost_note": (
            "External API list-price estimate excludes taxes, promotions and failed calls. "
            "Claude subscription usage has no assumed marginal charge; its CLI-reported API-equivalent "
            "cost is recorded separately. Provider dashboards remain authoritative."
        ),
        "official_publication": False,
    }
    atomic_write(report_path, final_payload["report_markdown"].strip() + "\n")
    atomic_write(rank_path, json.dumps(rank, ensure_ascii=False, indent=2) + "\n")
    atomic_write(research_path, json.dumps(glm_packet, ensure_ascii=False, indent=2) + "\n")
    atomic_write(usage_path, json.dumps(usage, ensure_ascii=False, indent=2) + "\n")
    return {
        "report": str(report_path), "rank": str(rank_path),
        "research": str(research_path), "usage": str(usage_path),
    }


def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--mode", choices=("preopen", "close", "intraday"), default="intraday")
    ap.add_argument("--profile", choices=("standard", "deep"), default="standard")
    ap.add_argument("--data", required=True)
    ap.add_argument("--history", required=True)
    ap.add_argument("--evidence", help="Audited evidence packet from research_pipeline.py")
    ap.add_argument("--reference-report")
    ap.add_argument("--out-dir", default="out")
    ap.add_argument("--credentials", default=str(DEFAULT_CREDENTIALS))
    ap.add_argument("--glm-max-tokens", type=int, default=12000)
    ap.add_argument("--nvidia-max-tokens", type=int, default=16384)
    ap.add_argument("--kimi-max-tokens", type=int, default=20000)
    ap.add_argument(
        "--writer", choices=("kimi", "claude"),
        default=os.environ.get("MARKET_BRIEF_WRITER", "kimi"),
    )
    ap.add_argument("--claude-bin", default=os.environ.get("CLAUDE_BIN", "claude"))
    ap.add_argument("--claude-model", default=os.environ.get("MARKET_BRIEF_CLAUDE_MODEL", "opus"))
    ap.add_argument(
        "--claude-effort", choices=("low", "medium", "high", "xhigh", "max"),
        default=os.environ.get("MARKET_BRIEF_CLAUDE_EFFORT", "high"),
    )
    ap.add_argument("--claude-timeout", type=int, default=900)
    ap.add_argument("--dry-run", action="store_true")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    try:
        paths = run(parse_args(argv))
        if paths:
            print(json.dumps(paths, ensure_ascii=False, indent=2))
        return 0
    except ProviderError as exc:
        print(f"dual-provider shadow run failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
