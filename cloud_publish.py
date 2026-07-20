#!/usr/bin/env python3
"""Publish a deliberately small, sanitized report set to private Cloud Storage."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path


REPORT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:\.[\w-]+)*\.md$")
DROP_DATA_KEYS = {
    "api_key", "authorization", "cached_from", "cookie", "credential",
    "live_fetch_error", "password", "private_key", "request_headers",
    "secret", "session_token", "source_path", "token",
}
SECRET_MARKERS = (
    b"sk-ant-", b"nvapi-", b"ghp_", b"github_pat_", b"AIza",
    b"BEGIN PRIVATE KEY", b"/Users/", b"/opt/market-brief/", b"/home/marketbrief/",
)


@dataclass(frozen=True)
class Publication:
    object_name: str
    payload: bytes
    content_type: str


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _sanitize(value):
    if isinstance(value, dict):
        return {
            key: _sanitize(item)
            for key, item in value.items()
            if key.lower() not in DROP_DATA_KEYS
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def _usage_projection(path: Path) -> dict:
    source = _read_json(path)
    projected = {
        key: source[key]
        for key in ("estimated_cost_usd", "total_cost_usd", "cost_usd", "total_tokens")
        if key in source
    }
    if isinstance(source.get("glm_route"), dict):
        projected["glm_route"] = {
            key: source["glm_route"].get(key)
            for key in ("selected", "fallback_used", "attempts")
            if key in source["glm_route"]
        }
    if isinstance(source.get("providers"), dict):
        projected["providers"] = {
            name: {
                key: provider.get(key)
                for key in ("model", "usage", "estimated_cost_usd")
                if key in provider
            }
            for name, provider in source["providers"].items()
            if isinstance(provider, dict)
        }
    if isinstance(source.get("modelUsage"), dict):
        models = source["modelUsage"]
        total_tokens = 0
        for usage in models.values():
            if isinstance(usage, dict):
                total_tokens += sum(
                    int(usage.get(key) or 0)
                    for key in ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
                )
        projected["total_tokens"] = total_tokens
        projected["providers"] = {
            "claude": {"model": "Claude workflow", "usage": {"total_tokens": total_tokens}}
        }
    return projected


def _rank_projection(path: Path) -> dict:
    source = _read_json(path)
    return _sanitize({
        "rank": source.get("rank", {}),
        "confirmed_claims": source.get("confirmed_claims", []),
        "unverified": source.get("unverified", []),
        "diagnostics": source.get("diagnostics", {}),
        "postmortem": source.get("postmortem", {}),
        "route": source.get("route", {}),
        "degraded": bool(source.get("degraded")),
        "report_chars": source.get("report_chars"),
        "profile": source.get("profile"),
    })


def _research_projection(path: Path) -> dict:
    source = _read_json(path)
    return _sanitize({
        key: source.get(key)
        for key in (
            "route", "agents", "verification", "coverage", "source_errors",
            "evidence", "limitations",
        ) if key in source
    })


def _history_projection(path: Path) -> dict:
    source = _read_json(path)
    return _sanitize({
        key: source.get(key)
        for key in (
            "date", "mode", "history_available", "comparisons", "recent_theses",
            "pending_evaluations", "quality_metrics",
        )
        if key in source
    })


def _paired_data(project: Path, base: str) -> Path | None:
    candidates = [project / "data" / f"{base}.json"]
    if base.endswith(".recovered"):
        candidates.append(project / "data" / f"{base.removesuffix('.recovered')}.json")
    return next((path for path in candidates if path.is_file()), None)


def publications_for_report(project: Path, report: Path) -> list[Publication]:
    if report.parent.resolve() != (project / "out").resolve() or not REPORT_RE.match(report.name):
        raise ValueError(f"not a published report: {report}")
    if report.name.endswith(".full.md"):
        raise ValueError("intermediate full reports are never publishable")
    base = report.name.removesuffix(".md")
    items = [Publication(f"out/{report.name}", report.read_bytes(), "text/markdown; charset=utf-8")]

    rank = project / "out" / f"{base}.rank.json"
    if rank.is_file():
        items.append(Publication(f"out/{base}.rank.json", _json_bytes(_rank_projection(rank)), "application/json"))

    usage = next((
        path for path in (
            project / "out" / f"{base}.usage.json",
            project / "out" / f"{base}.run.json",
        ) if path.is_file()
    ), None)
    if usage:
        items.append(Publication(f"out/{base}.usage.json", _json_bytes(_usage_projection(usage)), "application/json"))

    data = _paired_data(project, base)
    if data:
        items.append(Publication(f"data/{base}.json", _json_bytes(_sanitize(_read_json(data))), "application/json"))

    research = project / "out" / f"{base}.research.json"
    if research.is_file():
        items.append(Publication(
            f"out/{base}.research.json", _json_bytes(_research_projection(research)), "application/json"
        ))
    evidence = project / "out" / f"{base}.evidence.json"
    if evidence.is_file():
        items.append(Publication(
            f"out/{base}.evidence.json", _json_bytes(_sanitize(_read_json(evidence))), "application/json"
        ))
    history = project / "out" / f"{base}.history.json"
    if history.is_file():
        items.append(Publication(
            f"out/{base}.history.json", _json_bytes(_history_projection(history)), "application/json"
        ))
    metrics = project / "out" / "metrics.json"
    if metrics.is_file():
        items.append(Publication("out/metrics.json", _json_bytes(_sanitize(_read_json(metrics))), "application/json"))

    for item in items:
        if any(marker in item.payload for marker in SECRET_MARKERS):
            raise ValueError(f"private marker blocked cloud publication: {item.object_name}")
    return items


def collect_publications(project: Path, base: str | None, publish_all: bool) -> list[Publication]:
    out_dir = project / "out"
    if publish_all:
        reports = sorted(
            (path for path in out_dir.iterdir() if path.is_file() and REPORT_RE.match(path.name) and not path.name.endswith(".full.md")),
            key=lambda path: path.name,
        )
    else:
        if not base:
            raise ValueError("--base is required unless --all is used")
        reports = [out_dir / f"{base}.md"]
    if not reports or any(not path.is_file() for path in reports):
        raise FileNotFoundError("no matching published report")
    by_name: dict[str, Publication] = {}
    for report in reports:
        for item in publications_for_report(project, report):
            by_name[item.object_name] = item
    return list(by_name.values())


def upload(bucket_name: str, items: list[Publication], project_id: str | None = None) -> None:
    from google.cloud import storage  # Imported lazily so local dry-runs stay lightweight.

    client = storage.Client(project=project_id)
    bucket = client.bucket(bucket_name)
    for item in items:
        blob = bucket.blob(item.object_name)
        blob.cache_control = "no-store"
        blob.metadata = {"market-brief-publication": "curated"}
        blob.upload_from_string(item.payload, content_type=item.content_type)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--project-id")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--base")
    group.add_argument("--all", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    items = collect_publications(args.project_dir.resolve(), args.base, args.all)
    if not args.dry_run:
        upload(args.bucket, items, args.project_id)
    print(json.dumps({"bucket": args.bucket, "objects": [item.object_name for item in items]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
