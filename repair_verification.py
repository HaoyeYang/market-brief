#!/usr/bin/env python3
"""Repair a malformed verifier packet without rerunning specialists or writer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

from multi_provider_brief import (
    DEFAULT_CREDENTIALS, ProviderError, atomic_write, call_glm_with_fallback,
    effective_config, estimated_cost, normalize_agent_packet,
    normalize_verification_packet, verifier_prompt,
)


def repair(args, session=requests) -> dict:
    research = json.loads(args.research.read_text())
    evidence = json.loads(args.evidence.read_text())
    history = json.loads(args.history.read_text())
    rank = json.loads(args.rank.read_text())
    usage = json.loads(args.run_json.read_text())
    existing = research.get("verification") or {}
    if isinstance(existing.get("verified_claims"), list) and not args.force:
        return {"status": "already_valid", "verified_claims": len(existing["verified_claims"])}

    agents = [
        normalize_agent_packet(packet, str(packet.get("agent") or "unknown"))
        for packet in research.get("agents", []) if isinstance(packet, dict)
    ]
    config = effective_config(args.credentials.expanduser())
    if not config.get("glm_key"):
        raise ProviderError("ZAI_API_KEY is required as the NVIDIA fallback")
    response, packet, route = call_glm_with_fallback(
        config=config, messages=verifier_prompt(agents, evidence, history),
        paid_max_tokens=6500, nvidia_max_tokens=6500, session=session,
    )
    verification = normalize_verification_packet(packet)
    research["agents"] = agents
    research["verification"] = verification

    evidence_by_id = {
        item.get("id"): item for item in evidence.get("evidence", [])
        if isinstance(item, dict)
    }
    confirmed = []
    for claim in verification.get("verified_claims", []):
        if claim.get("status") != "supported":
            continue
        for evidence_id in claim.get("evidence_ids", []) or [None]:
            item = evidence_by_id.get(evidence_id, {})
            confirmed.append({
                "angle": claim.get("agent"), "claim": claim.get("claim", ""),
                "url": item.get("url"),
                "ts": item.get("published_at") or item.get("event_time"),
            })
    rank["confirmed_claims"] = confirmed
    rank["postmortem"] = {"results": verification.get("postmortem_results", [])}
    rank["unverified"] = list(dict.fromkeys(
        list(rank.get("unverified", [])) + list(verification.get("unverified", []))
    ))
    metrics = {}
    for agent in agents:
        name = agent.get("agent", "unknown")
        claims = agent.get("claims", [])
        supported = sum(
            item.get("status") == "supported" and item.get("agent") == name
            for item in verification.get("verified_claims", [])
        )
        metrics[name] = {
            "scouted": len(claims), "excerpt_supported": supported,
            "source_confirmed": supported,
        }
    metrics["kimi-k3-synthesis"] = (
        (rank.get("diagnostics") or {}).get("angle_metrics", {}).get("kimi-k3-synthesis")
        or {"scouted": len((rank.get("rank") or {}).get("ranked", [])),
            "excerpt_supported": 0, "source_confirmed": 0}
    )
    rank.setdefault("diagnostics", {})["angle_metrics"] = metrics
    rank["diagnostics"]["active_angles"] = list(metrics)

    provider = route["selected"]
    cost = estimated_cost(provider, response.get("usage", {}))
    usage.setdefault("providers", {})["glm_verification_repair"] = {
        "provider": provider,
        "model": "z-ai/glm-5.2" if provider == "nvidia" else "glm-5.2",
        "usage": response.get("usage", {}), "finish_reason": response.get("finish_reason"),
        "reasoning_chars": response.get("reasoning_chars"), "estimated_cost_usd": cost,
    }
    usage["verification_repair"] = {
        "route": route, "verified_claims": len(verification.get("verified_claims", [])),
    }
    for key in ("estimated_cost_usd", "total_cost_usd"):
        usage[key] = round(float(usage.get(key) or 0) + cost, 6)

    atomic_write(args.research, json.dumps(research, ensure_ascii=False, indent=2) + "\n")
    atomic_write(args.rank, json.dumps(rank, ensure_ascii=False, indent=2) + "\n")
    atomic_write(args.run_json, json.dumps(usage, ensure_ascii=False, indent=2) + "\n")
    return {
        "status": "repaired", "provider": provider,
        "verified_claims": len(verification.get("verified_claims", [])),
        "supported_claims": sum(item.get("status") == "supported" for item in verification.get("verified_claims", [])),
        "estimated_incremental_cost_usd": cost,
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--research", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--rank", type=Path, required=True)
    parser.add_argument("--run-json", type=Path, required=True)
    parser.add_argument("--credentials", type=Path, default=DEFAULT_CREDENTIALS)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    try:
        print(json.dumps(repair(parse_args(argv)), ensure_ascii=False))
        return 0
    except (ProviderError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"verification repair failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
