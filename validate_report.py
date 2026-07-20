#!/usr/bin/env python3
"""Deterministic publication gate for a generated market brief."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


MODE_LABEL = {"preopen": "盘前", "close": "收盘", "intraday": "盘中"}
REQUIRED_SECTIONS = (
    "核心观点",
    "美国市场",
    "全球市场",
    "宏观、利率与央行",
    "外汇、商品与加密资产",
    "信用、波动率与流动性",
    "未来 1-5 个交易日",
    "数据质量与存疑",
    "来源",
)


def validate(report: str, rank_payload: dict, data: dict,
             mode: str, profile: str, history: dict | None = None) -> list[str]:
    errors: list[str] = []
    char_count = len(report.strip())
    min_chars = 4200 if profile == "deep" else 2800
    if char_count < min_chars:
        errors.append(f"report too short: {char_count} < {min_chars} chars")
    if char_count > 14000:
        errors.append(f"report too long: {char_count} > 14000 chars")

    if MODE_LABEL[mode] not in report[:160]:
        errors.append(f"title does not identify {MODE_LABEL[mode]} mode")
    for section in REQUIRED_SECTIONS:
        if section not in report:
            errors.append(f"missing required section: {section}")

    if "<<RANK_JSON_" in report:
        errors.append("machine sentinel leaked into published report")
    if data.get("mode") != mode:
        errors.append(f"data mode {data.get('mode')!r} != requested mode {mode!r}")
    freshness = data.get("data_quality", {}).get("freshness", {})
    if freshness.get("status") != "ok":
        errors.append(f"data freshness not ok: {freshness}")

    urls = set(re.findall(r"https://[^\s)>]+", report))
    non_session = data.get("run_context", {}).get("is_session") is False
    min_urls = 1 if non_session else (5 if profile == "deep" else 3)
    if len(urls) < min_urls:
        errors.append(f"too few clickable source URLs: {len(urls)} < {min_urls}")

    ranked = rank_payload.get("rank", {}).get("ranked", [])
    if len(ranked) < 6:
        errors.append(f"too few ranked points: {len(ranked)} < 6")
    for idx, item in enumerate(ranked):
        sources = item.get("sources") or []
        if not sources:
            errors.append(f"ranked[{idx}] has no sources")
        if not item.get("source_angles"):
            errors.append(f"ranked[{idx}] has no source_angles")

    catalysts = rank_payload.get("rank", {}).get("catalysts", [])
    if len(catalysts) < 2:
        errors.append(f"too few machine-testable catalysts: {len(catalysts)} < 2")
    for idx, item in enumerate(catalysts):
        for key in ("confirm_condition", "invalidate_condition", "sources", "source_angles"):
            if not item.get(key):
                errors.append(f"catalysts[{idx}] missing {key}")

    diagnostics = rank_payload.get("diagnostics", {})
    if not diagnostics.get("angle_metrics"):
        errors.append("rank block missing per-angle metrics")
    if "postmortem" not in rank_payload:
        errors.append("rank block missing postmortem results")

    if history:
        comparisons = history.get("comparisons", {})
        if any(item.get("available") for item in comparisons.values()):
            if "昨日/5日/20日" not in report and "昨日、5日、20日" not in report:
                errors.append("history is available but comparison subsection is missing")
        if history.get("pending_evaluations") and "事后评分" not in report:
            errors.append("due postmortem evaluations exist but report omits the scorecard")

    # The model-safe file must contain no numeric direction where the dating
    # guard says direction is unusable.
    for group in ("futures", "fx_commodities_crypto"):
        for name, item in data.get(group, {}).items():
            if item.get("direction_usable") is False:
                if item.get("chg") is not None or item.get("chg_pct") is not None:
                    errors.append(f"unsafe direction leaked to agent data: {group}.{name}")

    return errors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--rank", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--mode", required=True, choices=MODE_LABEL)
    ap.add_argument("--profile", required=True, choices=("standard", "deep"))
    ap.add_argument("--history", default=None)
    args = ap.parse_args()

    try:
        report = Path(args.report).read_text()
        rank = json.loads(Path(args.rank).read_text())
        data = json.loads(Path(args.data).read_text())
        history = json.loads(Path(args.history).read_text()) if args.history else None
        errors = validate(report, rank, data, args.mode, args.profile, history)
    except Exception as exc:
        errors = [f"validator exception: {exc}"]

    if errors:
        for error in errors:
            print("REPORT GATE: " + error, file=sys.stderr)
        return 1
    print("report publication gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
