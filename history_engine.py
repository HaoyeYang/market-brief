#!/usr/bin/env python3
"""SQLite history, 1/5/20-session deltas, catalyst scoring and quality metrics."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

import exchange_calendars as xcals
import pandas as pd


XNYS = xcals.get_calendar("XNYS")
SCHEMA_VERSION = 1
SNAPSHOT_GROUPS = (
    "indices", "futures", "concentration", "breadth_proxy", "sectors",
    "fx_commodities_crypto", "global_indices", "global_etfs",
    "factors_credit", "rates", "macro", "options", "positioning",
)
METADATA_KEYS = {
    "status", "source", "provider", "provider_selected", "retrieved_at",
    "session_date", "prev_session_date", "report_date", "expiration",
    "bar_complete", "direction_usable", "direction_note", "limitations",
    "volume_interpretation", "note", "url", "frequency", "terminology",
    "cache_fallback", "cached_from", "cached_asof", "cache_age_hours",
    "live_fetch_error", "market", "symbol", "dte_at_fetch", "contract_count",
}


def connect(path: str) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
          key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runs (
          id INTEGER PRIMARY KEY,
          date TEXT NOT NULL,
          mode TEXT NOT NULL,
          profile TEXT,
          asof TEXT,
          created_at TEXT NOT NULL,
          report_path TEXT,
          data_path TEXT,
          rank_path TEXT,
          thesis TEXT,
          degraded INTEGER NOT NULL DEFAULT 0,
          total_cost_usd REAL,
          UNIQUE(date, mode)
        );
        CREATE TABLE IF NOT EXISTS snapshots (
          run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
          series_key TEXT NOT NULL,
          value REAL NOT NULL,
          metadata_json TEXT,
          PRIMARY KEY(run_id, series_key)
        );
        CREATE TABLE IF NOT EXISTS claims (
          id INTEGER PRIMARY KEY,
          run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
          angle TEXT,
          claim TEXT NOT NULL,
          url TEXT,
          domain TEXT,
          source_ts TEXT,
          ranked INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS agent_metrics (
          run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
          angle TEXT NOT NULL,
          scouted INTEGER NOT NULL DEFAULT 0,
          excerpt_supported INTEGER NOT NULL DEFAULT 0,
          source_confirmed INTEGER NOT NULL DEFAULT 0,
          ranked_points INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY(run_id, angle)
        );
        CREATE TABLE IF NOT EXISTS catalysts (
          id TEXT PRIMARY KEY,
          origin_run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
          name TEXT NOT NULL,
          thesis_link TEXT,
          horizon_days INTEGER NOT NULL,
          confirm_condition TEXT NOT NULL,
          invalidate_condition TEXT NOT NULL,
          due_1d TEXT NOT NULL,
          due_5d TEXT NOT NULL,
          sources_json TEXT NOT NULL,
          source_angles_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS catalyst_sources (
          catalyst_id TEXT NOT NULL REFERENCES catalysts(id) ON DELETE CASCADE,
          url TEXT NOT NULL,
          domain TEXT,
          angle TEXT,
          PRIMARY KEY(catalyst_id, url)
        );
        CREATE TABLE IF NOT EXISTS evaluations (
          catalyst_id TEXT NOT NULL REFERENCES catalysts(id) ON DELETE CASCADE,
          horizon INTEGER NOT NULL,
          evaluation_run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
          verdict TEXT NOT NULL,
          reason TEXT NOT NULL,
          evidence_json TEXT NOT NULL,
          scored_at TEXT NOT NULL,
          PRIMARY KEY(catalyst_id, horizon)
        );
        """
    )
    db.execute(
        "INSERT OR REPLACE INTO metadata(key,value) VALUES('schema_version',?)",
        (str(SCHEMA_VERSION),),
    )
    db.commit()
    return db


def _numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def flatten_snapshots(data: dict) -> dict[str, tuple[float, dict]]:
    out: dict[str, tuple[float, dict]] = {}

    def walk(value, path, inherited_meta):
        if isinstance(value, dict):
            meta = dict(inherited_meta)
            for key in ("session_date", "bar_complete", "status", "source",
                        "cached_asof", "cache_age_hours", "direction_usable"):
                if key in value:
                    meta[key] = value[key]
            for key, child in value.items():
                if key in METADATA_KEYS or key in {
                    "prev_close", "prev", "prior_level", "prior_released",
                }:
                    continue
                walk(child, path + [key], meta)
        elif isinstance(value, list):
            return
        elif _numeric(value):
            key = ".".join(path)
            out[key] = (float(value), inherited_meta)

    for group in SNAPSHOT_GROUPS:
        if group in data:
            walk(data[group], [group], {})
    return out


def _domain(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return None


def _session_offset(date_str: str, count: int) -> str:
    session = pd.Timestamp(date_str)
    if not XNYS.is_session(session):
        session = XNYS.date_to_session(session, direction="next")
        count = max(0, count - 1)
    for _ in range(count):
        session = XNYS.next_session(session)
    return session.date().isoformat()


def _prior_run(db, date_str, mode, observations_back):
    rows = db.execute(
        "SELECT * FROM runs WHERE date < ? AND mode=? ORDER BY date DESC LIMIT ?",
        (date_str, mode, observations_back),
    ).fetchall()
    return rows[-1] if len(rows) >= observations_back else None


def _importance(key: str, current: float, prior: float) -> float:
    delta = abs(current - prior)
    if any(x in key for x in ("chg_pct", "rsp_minus", "ratio", "skew", "yield", "DGS", "spread")):
        return delta * 20
    if prior:
        return abs((current / prior - 1) * 100)
    return delta


def build_context(db, data: dict, limit: int = 45) -> dict:
    date_str, mode = data["date"], data["mode"]
    current = flatten_snapshots(data)
    comparisons = {}
    for label, back in (("1d", 1), ("5d", 5), ("20d", 20)):
        prior_run = _prior_run(db, date_str, mode, back)
        if not prior_run:
            comparisons[label] = {"available": False, "observations_back": back}
            continue
        prior_rows = db.execute(
            "SELECT series_key,value FROM snapshots WHERE run_id=?",
            (prior_run["id"],),
        ).fetchall()
        prior = {row["series_key"]: row["value"] for row in prior_rows}
        changes = []
        for key, (value, meta) in current.items():
            if key not in prior:
                continue
            old = prior[key]
            absolute = value - old
            pct = ((value / old - 1) * 100) if old else None
            changes.append({
                "key": key, "current": value, "prior": old,
                "absolute_change": round(absolute, 6),
                "pct_change": round(pct, 4) if pct is not None else None,
                "current_meta": meta,
                "importance": _importance(key, value, old),
            })
        changes.sort(key=lambda item: item["importance"], reverse=True)
        # Keep the context cross-asset. Raw option/positioning fields are more
        # numerous than index/rate fields and must not crowd them out.
        queues = {}
        for item in changes:
            queues.setdefault(item["key"].split(".", 1)[0], []).append(item)
        selected = []
        group_order = [group for group in SNAPSHOT_GROUPS if group in queues]
        while len(selected) < limit and any(queues.get(group) for group in group_order):
            for group in group_order:
                if queues.get(group) and len(selected) < limit:
                    selected.append(queues[group].pop(0))
        for item in selected:
            item.pop("importance", None)
        comparisons[label] = {
            "available": True,
            "observations_back": back,
            "prior_date": prior_run["date"],
            "prior_thesis": prior_run["thesis"],
            "changes": selected,
        }

    pending = []
    rows = db.execute(
        """
        SELECT c.*, r.date AS origin_date, r.mode AS origin_mode
        FROM catalysts c JOIN runs r ON r.id=c.origin_run_id
        WHERE r.date < ? AND r.mode=? ORDER BY r.date, c.id
        """,
        (date_str, mode),
    ).fetchall()
    for row in rows:
        for horizon, due_key in ((1, "due_1d"), (5, "due_5d")):
            already = db.execute(
                "SELECT 1 FROM evaluations WHERE catalyst_id=? AND horizon=?",
                (row["id"], horizon),
            ).fetchone()
            if not already and date_str >= row[due_key]:
                pending.append({
                    "catalyst_id": row["id"], "horizon": horizon,
                    "origin_date": row["origin_date"], "origin_mode": row["origin_mode"],
                    "name": row["name"], "thesis_link": row["thesis_link"],
                    "confirm_condition": row["confirm_condition"],
                    "invalidate_condition": row["invalidate_condition"],
                    "sources": json.loads(row["sources_json"]),
                    "source_angles": json.loads(row["source_angles_json"]),
                })

    recent = db.execute(
        "SELECT date,mode,thesis FROM runs WHERE date < ? AND mode=? ORDER BY date DESC, id DESC LIMIT 5",
        (date_str, mode),
    ).fetchall()
    return {
        "date": date_str,
        "mode": mode,
        "history_available": bool(recent),
        "comparisons": comparisons,
        "recent_theses": [dict(row) for row in recent],
        "pending_evaluations": pending,
        "quality_metrics": metrics(db),
    }


def metrics(db) -> dict:
    agent_rows = db.execute(
        """
        SELECT angle, SUM(scouted) scouted, SUM(excerpt_supported) excerpt_supported,
               SUM(source_confirmed) source_confirmed, SUM(ranked_points) ranked_points
        FROM agent_metrics GROUP BY angle ORDER BY source_confirmed DESC, angle
        """
    ).fetchall()
    agents = []
    for row in agent_rows:
        item = dict(row)
        item["source_confirm_rate"] = round(
            row["source_confirmed"] / row["scouted"], 3) if row["scouted"] else None
        agents.append(item)

    source_rows = db.execute(
        """
        SELECT domain, COUNT(*) confirmed_claims, SUM(ranked) ranked_claims
        FROM claims WHERE domain IS NOT NULL GROUP BY domain
        ORDER BY ranked_claims DESC, confirmed_claims DESC LIMIT 30
        """
    ).fetchall()
    eval_rows = db.execute(
        "SELECT verdict,COUNT(*) n FROM evaluations GROUP BY verdict"
    ).fetchall()
    outcome_rows = db.execute(
        """
        SELECT e.verdict,c.source_angles_json,c.sources_json
        FROM evaluations e JOIN catalysts c ON c.id=e.catalyst_id
        """
    ).fetchall()
    by_agent, by_source = {}, {}

    def add(bucket, key, verdict):
        item = bucket.setdefault(key, {
            "confirmed": 0, "mixed": 0, "invalidated": 0, "not_evaluable": 0,
        })
        item[verdict] = item.get(verdict, 0) + 1

    for row in outcome_rows:
        for angle in set(json.loads(row["source_angles_json"]) or ["unattributed"]):
            add(by_agent, angle, row["verdict"])
        domains = {_domain(url) for url in json.loads(row["sources_json"])
                   if isinstance(url, str) and url.startswith("http")}
        for domain in domains or {"unattributed"}:
            add(by_source, domain, row["verdict"])

    def finish(bucket):
        result = []
        for key, counts in bucket.items():
            evaluable = counts["confirmed"] + counts["mixed"] + counts["invalidated"]
            counts = dict(counts)
            counts["value_score"] = (
                round((counts["confirmed"] + 0.5 * counts["mixed"]) / evaluable, 3)
                if evaluable else None
            )
            result.append({"name": key, **counts})
        return sorted(result, key=lambda x: (x["value_score"] is not None,
                                              x["value_score"] or -1), reverse=True)

    return {
        "agents": agents,
        "sources": [dict(row) for row in source_rows],
        "postmortem": {row["verdict"]: row["n"] for row in eval_rows},
        "postmortem_by_agent": finish(by_agent),
        "postmortem_by_source": finish(by_source),
        "run_count": db.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
    }


def ingest(db, data: dict, rank_payload: dict, args) -> int:
    rank = rank_payload.get("rank", {})
    run_result = json.loads(Path(args.run_json).read_text()) if args.run_json else {}
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    db.execute(
        """
        INSERT INTO runs(date,mode,profile,asof,created_at,report_path,data_path,rank_path,
                         thesis,degraded,total_cost_usd)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(date,mode) DO UPDATE SET
          profile=excluded.profile, asof=excluded.asof, created_at=excluded.created_at,
          report_path=excluded.report_path, data_path=excluded.data_path,
          rank_path=excluded.rank_path, thesis=excluded.thesis,
          degraded=excluded.degraded, total_cost_usd=excluded.total_cost_usd
        """,
        (data["date"], data["mode"], args.profile, data.get("asof"), now,
         args.report, args.data, args.rank, rank.get("thesis"),
         int(bool(rank_payload.get("degraded"))), run_result.get("total_cost_usd")),
    )
    run_id = db.execute(
        "SELECT id FROM runs WHERE date=? AND mode=?", (data["date"], data["mode"])
    ).fetchone()[0]
    for table in ("snapshots", "claims", "agent_metrics"):
        db.execute(f"DELETE FROM {table} WHERE run_id=?", (run_id,))

    for key, (value, meta) in flatten_snapshots(data).items():
        db.execute(
            "INSERT INTO snapshots(run_id,series_key,value,metadata_json) VALUES(?,?,?,?)",
            (run_id, key, value, json.dumps(meta, ensure_ascii=False)),
        )

    ranked_urls = {
        source for item in rank.get("ranked", []) for source in item.get("sources", [])
        if isinstance(source, str) and source.startswith("http")
    }
    for claim in rank_payload.get("confirmed_claims", []):
        url = claim.get("url")
        db.execute(
            "INSERT INTO claims(run_id,angle,claim,url,domain,source_ts,ranked) VALUES(?,?,?,?,?,?,?)",
            (run_id, claim.get("angle"), claim.get("claim", ""), url, _domain(url),
             claim.get("ts"), int(url in ranked_urls)),
        )

    angle_metrics = rank_payload.get("diagnostics", {}).get("angle_metrics", {})
    for angle, values in angle_metrics.items():
        ranked_points = sum(
            1 for item in rank.get("ranked", [])
            if angle in item.get("source_angles", [])
        )
        db.execute(
            """INSERT INTO agent_metrics(run_id,angle,scouted,excerpt_supported,source_confirmed,ranked_points)
               VALUES(?,?,?,?,?,?)""",
            (run_id, angle, values.get("scouted", 0), values.get("excerpt_supported", 0),
             values.get("source_confirmed", 0), ranked_points),
        )

    # New catalysts are deterministically identified, not model-assigned. An
    # upsert preserves already-scored evaluations when a same-day run is retried.
    current_catalyst_ids = []
    for catalyst in rank.get("catalysts", []):
        fingerprint = "|".join((data["date"], data["mode"], catalyst["name"], catalyst["confirm_condition"]))
        catalyst_id = hashlib.sha256(fingerprint.encode()).hexdigest()[:18]
        current_catalyst_ids.append(catalyst_id)
        sources = catalyst.get("sources", [])
        angles = catalyst.get("source_angles", [])
        db.execute(
            """
            INSERT INTO catalysts(
              id,origin_run_id,name,thesis_link,horizon_days,confirm_condition,
              invalidate_condition,due_1d,due_5d,sources_json,source_angles_json,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              origin_run_id=excluded.origin_run_id,name=excluded.name,
              thesis_link=excluded.thesis_link,horizon_days=excluded.horizon_days,
              confirm_condition=excluded.confirm_condition,
              invalidate_condition=excluded.invalidate_condition,
              due_1d=excluded.due_1d,due_5d=excluded.due_5d,
              sources_json=excluded.sources_json,
              source_angles_json=excluded.source_angles_json,
              created_at=excluded.created_at
            """,
            (catalyst_id, run_id, catalyst["name"], catalyst.get("thesis_link"),
             catalyst["horizon_days"], catalyst["confirm_condition"],
             catalyst["invalidate_condition"], _session_offset(data["date"], 1),
             _session_offset(data["date"], 5), json.dumps(sources, ensure_ascii=False),
             json.dumps(angles, ensure_ascii=False), now),
        )
        db.execute("DELETE FROM catalyst_sources WHERE catalyst_id=?", (catalyst_id,))
        for url in sources:
            if isinstance(url, str) and url.startswith("http"):
                for angle in angles or [None]:
                    db.execute(
                        "INSERT OR IGNORE INTO catalyst_sources(catalyst_id,url,domain,angle) VALUES(?,?,?,?)",
                        (catalyst_id, url, _domain(url), angle),
                    )

    stale = db.execute(
        "SELECT id FROM catalysts WHERE origin_run_id=?", (run_id,)
    ).fetchall()
    for row in stale:
        if row["id"] not in current_catalyst_ids:
            db.execute("DELETE FROM catalysts WHERE id=?", (row["id"],))

    for result in rank_payload.get("postmortem", {}).get("results", []):
        exists = db.execute("SELECT 1 FROM catalysts WHERE id=?", (result["catalyst_id"],)).fetchone()
        if not exists:
            continue
        db.execute(
            """
            INSERT OR REPLACE INTO evaluations(
              catalyst_id,horizon,evaluation_run_id,verdict,reason,evidence_json,scored_at)
            VALUES(?,?,?,?,?,?,?)
            """,
            (result["catalyst_id"], result["horizon"], run_id, result["verdict"],
             result["reason"], json.dumps(result.get("evidence_keys", []), ensure_ascii=False), now),
        )
    db.commit()
    return run_id


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="state/market_brief.sqlite3")
    sub = ap.add_subparsers(dest="command", required=True)
    context = sub.add_parser("context")
    context.add_argument("--data", required=True)
    context.add_argument("--out", required=True)
    context.add_argument("--limit", type=int, default=45)

    ingest_p = sub.add_parser("ingest")
    ingest_p.add_argument("--data", required=True)
    ingest_p.add_argument("--rank", required=True)
    ingest_p.add_argument("--report", required=True)
    ingest_p.add_argument("--run-json", default=None)
    ingest_p.add_argument("--profile", required=True)

    metrics_p = sub.add_parser("metrics")
    metrics_p.add_argument("--out", default=None)
    args = ap.parse_args()
    db = connect(args.db)

    if args.command == "context":
        data = json.loads(Path(args.data).read_text())
        Path(args.out).write_text(json.dumps(build_context(db, data, args.limit), indent=2, ensure_ascii=False))
    elif args.command == "ingest":
        data = json.loads(Path(args.data).read_text())
        rank = json.loads(Path(args.rank).read_text())
        ingest(db, data, rank, args)
    else:
        payload = metrics(db)
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        if args.out:
            Path(args.out).write_text(text)
        else:
            print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
