#!/usr/bin/env python3
"""NYSE-aware run windows for the market brief.

All decisions are made in America/Chicago, independent of the Mac's current
system timezone.  Exit codes for the CLI are intentionally scheduler-friendly:
0 = due, 3 = not due/market closed, 2 = configuration error.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from dataclasses import asdict, dataclass

import exchange_calendars as xcals
import pandas as pd
from zoneinfo import ZoneInfo


CT = ZoneInfo("America/Chicago")
UTC = dt.timezone.utc
XNYS = xcals.get_calendar("XNYS")
VALID_MODES = ("preopen", "close", "intraday")


@dataclass(frozen=True)
class MarketWindow:
    date: str
    mode: str
    now_ct: str
    is_session: bool
    due: bool
    reason: str
    session_open_ct: str | None
    session_close_ct: str | None
    expected_cash_session: str | None
    previous_cash_session: str | None
    window_start_ct: str | None
    window_end_ct: str | None


def _to_datetime(value: str | None) -> dt.datetime:
    if not value:
        return dt.datetime.now(UTC)
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CT)
    return parsed.astimezone(UTC)


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def assess(date_str: str, mode: str, now: dt.datetime | None = None) -> MarketWindow:
    if mode not in VALID_MODES:
        raise ValueError(f"unsupported mode {mode!r}; expected one of {VALID_MODES}")

    report_date = dt.date.fromisoformat(date_str)
    session = pd.Timestamp(report_date)
    now_utc = (now or dt.datetime.now(UTC)).astimezone(UTC)
    now_ct = now_utc.astimezone(CT)
    is_session = bool(XNYS.is_session(session))

    previous = XNYS.date_to_session(session, direction="previous")
    if is_session:
        previous = XNYS.previous_session(session)
    previous_str = previous.date().isoformat()

    if not is_session:
        return MarketWindow(
            date=date_str,
            mode=mode,
            now_ct=_iso(now_ct),
            is_session=False,
            due=False,
            reason="NYSE closed for this date",
            session_open_ct=None,
            session_close_ct=None,
            expected_cash_session=None,
            previous_cash_session=previous_str,
            window_start_ct=None,
            window_end_ct=None,
        )

    open_ct = XNYS.session_open(session).to_pydatetime().astimezone(CT)
    close_ct = XNYS.session_close(session).to_pydatetime().astimezone(CT)

    if mode == "preopen":
        start = open_ct - dt.timedelta(minutes=50)   # normally 07:40 CT
        end = open_ct - dt.timedelta(minutes=5)
        expected = previous_str
    elif mode == "close":
        start = close_ct + dt.timedelta(minutes=15)
        end = close_ct + dt.timedelta(minutes=105)
        expected = date_str
    else:  # explicit manual/on-demand mode
        start = open_ct
        end = close_ct
        expected = date_str if now_ct >= open_ct else previous_str

    same_report_day = now_ct.date() == report_date
    due = same_report_day and start <= now_ct <= end
    if due:
        reason = "inside scheduled market window"
    elif not same_report_day:
        reason = "current Chicago date does not match report date"
    elif now_ct < start:
        reason = "before scheduled market window"
    else:
        reason = "after scheduled market window"

    return MarketWindow(
        date=date_str,
        mode=mode,
        now_ct=_iso(now_ct),
        is_session=True,
        due=due,
        reason=reason,
        session_open_ct=_iso(open_ct),
        session_close_ct=_iso(close_ct),
        expected_cash_session=expected,
        previous_cash_session=previous_str,
        window_start_ct=_iso(start),
        window_end_ct=_iso(end),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="Chicago report date (YYYY-MM-DD)")
    ap.add_argument("--mode", required=True, choices=VALID_MODES)
    ap.add_argument("--now", default=None, help="test override: ISO-8601 timestamp")
    ap.add_argument("--allow-outside-window", action="store_true")
    args = ap.parse_args()

    try:
        result = assess(args.date, args.mode, _to_datetime(args.now))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2

    payload = asdict(result)
    # Manual intraday snapshots are allowed on closed days for diagnostics or
    # weekend review. Scheduled preopen/close products remain session-only.
    if args.allow_outside_window and (result.is_session or result.mode == "intraday"):
        payload["due"] = True
        payload["reason"] = (
            "manual non-session snapshot" if not result.is_session
            else "manual override outside scheduled window"
        )
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["due"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
