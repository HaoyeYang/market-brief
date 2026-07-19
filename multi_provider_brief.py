#!/usr/bin/env python3
"""Generate a local NVIDIA/Z.AI GLM-5.2 -> Kimi K3 market brief.

This runner deliberately stays outside the official Claude publication path.
It reuses the deterministic, agent-safe data snapshot and SQLite history, tries
NVIDIA before paid Z.AI, and writes auditable shadow artifacts under ``out/``.
Credentials are read from the process environment or a mode-0600 env file; the
file is parsed as data and is never sourced as shell code.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import stat
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


def extract_json(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        # Reasoning endpoints may place a thinking trace before the requested
        # object. Scan every opening brace and keep the last valid JSON object.
        decoder = json.JSONDecoder()
        objects = []
        for match in re.finditer(r"\{", candidate):
            try:
                parsed, _ = decoder.raw_decode(candidate[match.start():])
                if isinstance(parsed, dict):
                    objects.append(parsed)
            except json.JSONDecodeError:
                continue
        if not objects:
            raise ProviderError("model response did not contain a valid JSON object")
        value = objects[-1]
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
) -> list[dict[str, str]]:
    system = (
        "你是金融市场日报总编辑。基于确定性数据、SQLite历史上下文和GLM研究包写简体中文报告。"
        "不能把未完成或禁止方向判断的bar当收盘，不能把免费期权/FINRA/CFTC代理误称实时资金流。"
        "输出严格 JSON，不要 Markdown 围栏。"
    )
    min_chars = 4200 if profile == "deep" else 2800
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
                "source_angles": ["glm-5.2-analysis|kimi-k3-synthesis"],
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
            f"标题前160字含‘{mode_label}’，正文{min_chars}至12000个中文字符",
            "以下字符串必须作为原样的二级标题，不得拆分或改字：核心观点；美国市场；全球市场；宏观、利率与央行；外汇、商品与加密资产；信用、波动率与流动性；未来 1-5 个交易日；数据质量与存疑；来源",
            "run_context.is_session=false 时必须明确标为影子验证，不可伪装成实时开盘或收盘；为 true 时按实际模式写正式报告",
            "包含‘昨日/5日/20日’小节；比较不可用时直接说明样本尚不足",
            "ranked 至少 8 条；catalysts 至少 3 条且条件必须可观察、可证伪",
            "来源章节至少列出提供的4个数据出处URL；URL是数据出处，不代表本次另行网页核验",
        ],
        "provenance_urls": list(PROVENANCE_URLS),
        "deterministic_data": json.loads(_json_for_prompt(data, 50000)),
        "history_context": json.loads(_json_for_prompt(history, 24000)),
        "glm_research_packet": research,
    }
    return [{"role": "system", "content": system},
            {"role": "user", "content": json.dumps(task, ensure_ascii=False)}]


def _validate_model_payload(payload: dict[str, Any]) -> None:
    report = payload.get("report_markdown")
    if not isinstance(report, str) or len(report.strip()) < 2800:
        raise ProviderError("Kimi report_markdown is missing or shorter than 2800 characters")
    ranked = payload.get("ranked")
    catalysts = payload.get("catalysts")
    if not isinstance(ranked, list) or len(ranked) < 6:
        raise ProviderError("Kimi returned fewer than 6 ranked points")
    if not isinstance(catalysts, list) or len(catalysts) < 2:
        raise ProviderError("Kimi returned fewer than 2 catalysts")


def build_rank(
    payload: dict[str, Any], data: dict, glm_packet: dict, glm_angle: str,
    profile: str = "standard",
) -> dict[str, Any]:
    ranked = payload["ranked"]
    catalysts = payload["catalysts"]
    for item in ranked:
        item.setdefault("sources", ["deterministic:market-data"])
        item.setdefault("source_angles", ["kimi-k3-synthesis"])
    for item in catalysts:
        item.setdefault("horizon_days", 5)
        item.setdefault("sources", ["deterministic:market-data"])
        item.setdefault("source_angles", [glm_angle])
    return {
        "rank": {
            "thesis": payload.get("thesis", ""),
            "ranked": ranked,
            "catalysts": catalysts,
            "dropped": [],
        },
        "confirmed_claims": [],
        "unverified": payload.get("unverified", []),
        "diagnostics": {
            "mode": data.get("mode"), "profile": profile, "day_type": "shadow",
            "scout_count": 2,
            "active_angles": [glm_angle, "kimi-k3-synthesis"],
            "angle_metrics": {
                glm_angle: {
                    "scouted": len(glm_packet.get("ranked_findings", [])),
                    "excerpt_supported": 0, "source_confirmed": 0,
                },
                "kimi-k3-synthesis": {
                    "scouted": len(ranked), "excerpt_supported": 0, "source_confirmed": 0,
                },
            },
        },
        "postmortem": {"results": []},
        "route": {
            "day_type": "shadow", "flags": ["portable-provider-test"], "events": [],
            "reason": "GLM-5.2 analysis followed by Kimi K3 synthesis",
        },
        "degraded": True,
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
    reference = Path(args.reference_report).read_text() if args.reference_report else ""
    if data.get("date") != args.date or data.get("mode") != args.mode:
        raise ProviderError("data date/mode does not match requested shadow run")

    config = effective_config(Path(args.credentials).expanduser())
    missing = [name for name, value in (
        ("MOONSHOT_API_KEY", config["kimi_key"]), ("ZAI_API_KEY", config["glm_key"]),
    ) if not value]
    readiness = {
        "date": args.date, "mode": args.mode, "credentials_file": str(Path(args.credentials).expanduser()),
        "nvidia_glm_configured": bool(config["nvidia_key"]),
        "paid_glm_configured": bool(config["glm_key"]),
        "kimi_configured": bool(config["kimi_key"]),
        "nvidia_endpoint": config["nvidia_base"],
        "glm_endpoint": config["glm_base"], "kimi_endpoint": config["kimi_base"],
        "data_chars": len(json.dumps(data, ensure_ascii=False)),
        "history_chars": len(json.dumps(history, ensure_ascii=False)),
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
    glm_response, glm_packet, glm_route = call_glm_with_fallback(
        config=config, messages=glm_prompt(data, history, reference),
        paid_max_tokens=args.glm_max_tokens,
        nvidia_max_tokens=args.nvidia_max_tokens,
        session=session,
    )
    kimi_response = _post_chat(
        base_url=config["kimi_base"], api_key=config["kimi_key"], session=session,
        payload={
            "model": "kimi-k3", "messages": kimi_prompt(data, history, glm_packet, profile),
            "max_completion_tokens": args.kimi_max_tokens,
            "response_format": {"type": "json_object"}, "reasoning_effort": "max",
        }, timeout=600,
    )
    final_payload = extract_json(kimi_response["content"])
    if isinstance(final_payload.get("report_markdown"), str):
        final_payload["report_markdown"] = normalize_report_headings(
            final_payload["report_markdown"]
        )
    _validate_model_payload(final_payload)
    glm_angle = (
        "nvidia-glm-5.2-analysis" if glm_route["selected"] == "nvidia"
        else "zai-glm-5.2-analysis"
    )
    rank = build_rank(final_payload, data, glm_packet, glm_angle, profile)

    base = f"{args.date}.{args.mode}.dual-shadow"
    out_dir = Path(args.out_dir)
    report_path = out_dir / f"{base}.md"
    rank_path = out_dir / f"{base}.rank.json"
    research_path = out_dir / f"{base}.glm-research.json"
    usage_path = out_dir / f"{base}.usage.json"
    glm_provider = glm_route["selected"]
    glm_cost = estimated_cost(glm_provider, glm_response["usage"])
    kimi_cost = estimated_cost("kimi", kimi_response["usage"])
    usage = {
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "workflow": [glm_angle, "kimi-k3-synthesis"],
        "glm_route": glm_route,
        "providers": {
            "glm": {
                "provider": glm_provider,
                "model": "z-ai/glm-5.2" if glm_provider == "nvidia" else "glm-5.2",
                "usage": glm_response["usage"], "request_id": glm_response["id"],
                "finish_reason": glm_response.get("finish_reason"),
                "reasoning_chars": glm_response.get("reasoning_chars"),
                "estimated_cost_usd": glm_cost,
            },
            "kimi": {
                "model": "kimi-k3", "usage": kimi_response["usage"],
                "request_id": kimi_response["id"],
                "finish_reason": kimi_response.get("finish_reason"),
                "reasoning_chars": kimi_response.get("reasoning_chars"),
                "estimated_cost_usd": kimi_cost,
            },
        },
        "cost_usd": None,
        "estimated_cost_usd": round(glm_cost + kimi_cost, 6),
        "total_cost_usd": round(glm_cost + kimi_cost, 6),
        "pricing_asof": "2026-07-19",
        "pricing_sources": [
            "https://docs.z.ai/guides/overview/pricing",
            "https://platform.kimi.ai/",
        ],
        "cost_note": "List-price estimate excluding taxes, promotions and failed calls; dashboards are authoritative.",
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
    ap.add_argument("--reference-report")
    ap.add_argument("--out-dir", default="out")
    ap.add_argument("--credentials", default=str(DEFAULT_CREDENTIALS))
    ap.add_argument("--glm-max-tokens", type=int, default=12000)
    ap.add_argument("--nvidia-max-tokens", type=int, default=16384)
    ap.add_argument("--kimi-max-tokens", type=int, default=20000)
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
