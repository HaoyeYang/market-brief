#!/usr/bin/env python3
"""Recover a completed market-brief result when the outer Claude relay fails.

Claude workflows persist a local journal before the outer ``claude -p`` relay
returns.  This helper only accepts a completed workflow whose start time and
exact staged input paths match the current run.  The normal deterministic
publication gates still run after recovery.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


BEGIN = "<<RANK_JSON_BEGIN>>"
END = "<<RANK_JSON_END>>"


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _workflow_args(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def find_completed_result(
    root: Path,
    *,
    data_path: str,
    history_path: str,
    started_after: float,
) -> tuple[Path, dict[str, Any]] | None:
    """Return the newest exact-match completed workflow, if one exists."""

    threshold_ms = int(started_after * 1000)
    candidates: list[tuple[int, Path, dict[str, Any]]] = []
    if not root.is_dir():
        return None

    # Claude currently nests journals as projects/<project>/<session>/workflows,
    # but keep this tolerant of one additional layout change.
    for path in root.glob("**/workflows/*.json"):
        try:
            record = _read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        if record.get("workflowName") != "market-brief" or record.get("status") != "completed":
            continue
        start_ms = record.get("startTime")
        if not isinstance(start_ms, (int, float)) or start_ms < threshold_ms:
            continue
        args = _workflow_args(record.get("args"))
        if not args:
            continue
        if args.get("dataPath") != data_path or args.get("historyPath") != history_path:
            continue
        result = record.get("result")
        if not isinstance(result, str) or BEGIN not in result or END not in result:
            continue
        if result.index(BEGIN) >= result.index(END):
            continue
        candidates.append((int(start_ms), path, record))

    if not candidates:
        return None
    _, path, record = max(candidates, key=lambda item: item[0])
    return path, record


def recover_run_json(
    run_json_path: Path,
    workflow_path: Path,
    workflow: dict[str, Any],
) -> None:
    try:
        outer = _read_json(run_json_path)
    except (OSError, json.JSONDecodeError):
        outer = {}
    if not isinstance(outer, dict):
        outer = {}

    outer["outer_relay_is_error"] = outer.get("is_error")
    outer["is_error"] = False
    outer["result"] = workflow["result"]
    outer["recovered_from_workflow_journal"] = {
        "run_id": workflow.get("runId"),
        "start_time_ms": workflow.get("startTime"),
        "duration_ms": workflow.get("durationMs"),
        "total_tokens": workflow.get("totalTokens"),
        "total_tool_calls": workflow.get("totalToolCalls"),
        "journal": str(workflow_path),
    }

    run_json_path.parent.mkdir(parents=True, exist_ok=True)
    temp = run_json_path.with_name(f".{run_json_path.name}.{os.getpid()}.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(outer, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, run_json_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--projects-root", type=Path, required=True)
    parser.add_argument("--data-path", required=True)
    parser.add_argument("--history-path", required=True)
    parser.add_argument("--started-after", type=float, required=True)
    parser.add_argument("--run-json", type=Path, required=True)
    args = parser.parse_args()

    match = find_completed_result(
        args.projects_root,
        data_path=args.data_path,
        history_path=args.history_path,
        started_after=args.started_after,
    )
    if match is None:
        return 3
    workflow_path, workflow = match
    recover_run_json(args.run_json, workflow_path, workflow)
    print(f"recovered completed workflow journal: {workflow_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
