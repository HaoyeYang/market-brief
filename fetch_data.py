#!/usr/bin/env python3
"""
fetch_data.py — deterministic market-data snapshot for the daily brief.

Zero LLM tokens. Every number the brief quotes originates here, never from a
web-search + model guess. If a source fails we write null + a status note and
NEVER let a downstream agent backfill a searched value into this block.

Usage:
    python3 fetch_data.py --out data/2026-07-15.json
    python3 fetch_data.py                 # defaults to data/<today>.json
    python3 fetch_data.py --with-fred-meta # adds FRED last_updated (needs key)

Idempotent: re-running for the same date overwrites the file.

Exit code: 0 on success (file always written). 1 if a LOAD-BEARING source is
unavailable (any index, any rate, or the breadth proxy) so run.sh's `set -e`
aborts instead of writing a brief from nothing. A single missing sector,
future, or macro series is a warning, not a failure.

m/m and y/y are arithmetic on fetched observations — still deterministic, still
zero tokens, no model in the loop. Consensus/forecast figures are NEVER fetched
here (FRED has none); nor are pre/post-market or CME FedWatch. Those are left to
the scouts as cited claims. FRED_API_KEY only adds each series' raw
`last_updated` timestamp, is opt-in via --with-fred-meta, fetched concurrently,
and never fatal.
"""

import argparse
import copy
import csv
import datetime as dt
import glob
import io
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import requests

from derivatives_positioning import fetch_options, fetch_positioning

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:                       # pragma: no cover
    ET = None

try:
    import yfinance as yf
    import pandas as pd
except Exception as e:                   # pragma: no cover
    print(f"market libs import failed: {e}", file=sys.stderr)
    yf = None
    pd = None

TIMEOUT = 12
UA = {"User-Agent": "market-brief/1.0"}

# ---- what we pull -----------------------------------------------------------

INDICES = {
    "SP500": "^GSPC", "Nasdaq": "^IXIC", "Dow": "^DJI",
    "Russell2000": "^RUT", "VIX": "^VIX",
}
# Equity index futures — the live pre-open read (bar_complete:false at 07:40 CT).
FUTURES = {"ES": "ES=F", "NQ": "NQ=F", "YM": "YM=F", "RTY": "RTY=F"}
# Concentration proxy: equal-weight vs cap-weight S&P.
CONCENTRATION = {"RSP": "RSP", "SPY": "SPY"}
SECTORS = {
    "XLK": "XLK", "XLF": "XLF", "XLE": "XLE", "XLV": "XLV", "XLI": "XLI",
    "XLY": "XLY", "XLP": "XLP", "XLU": "XLU", "XLB": "XLB", "XLRE": "XLRE",
    "XLC": "XLC", "SMH": "SMH",
}
FX_COMMO_CRYPTO = {
    "DXY": "DX-Y.NYB", "Gold": "GC=F", "WTI": "CL=F", "Brent": "BZ=F",
    "Copper": "HG=F", "Silver": "SI=F", "NatGas": "NG=F",
    "EURUSD": "EURUSD=X", "USDJPY": "JPY=X", "GBPUSD": "GBPUSD=X",
    "USDCNY": "CNY=X", "BTC": "BTC-USD",
}

# Local-market indices provide a live view of sessions that do not overlap the
# US day.  Their close clocks are explicit so an Asian close is not judged by
# the New York 16:00 rule.
GLOBAL_INDICES = {
    "EuroStoxx50": "^STOXX50E", "FTSE100": "^FTSE", "DAX": "^GDAXI",
    "CAC40": "^FCHI", "Nikkei225": "^N225", "HangSeng": "^HSI",
    "Shanghai": "000001.SS", "IndiaNifty50": "^NSEI",
}
GLOBAL_INDEX_META = {
    "^STOXX50E": ("Europe/Berlin", dt.time(17, 30)),
    "^FTSE": ("Europe/London", dt.time(16, 30)),
    "^GDAXI": ("Europe/Berlin", dt.time(17, 30)),
    "^FCHI": ("Europe/Paris", dt.time(17, 30)),
    "^N225": ("Asia/Tokyo", dt.time(15, 30)),
    "^HSI": ("Asia/Hong_Kong", dt.time(16, 0)),
    "000001.SS": ("Asia/Shanghai", dt.time(15, 0)),
    "^NSEI": ("Asia/Kolkata", dt.time(15, 30)),
}

# US-listed cross-asset proxies.  These are deliberately deterministic; the
# agents explain the moves but never invent the levels.
GLOBAL_ETFS = {
    "DevelopedExUS": "EFA", "EmergingMarkets": "EEM", "Europe": "VGK",
    "Japan": "EWJ", "ChinaLargeCap": "FXI", "India": "INDA",
}
FACTORS_CREDIT = {
    "Growth": "VUG", "Value": "VTV", "Momentum": "MTUM", "Quality": "QUAL",
    "MinVol": "USMV", "HighYield": "HYG", "InvestmentGrade": "LQD",
    "EmergingDebt": "EMB", "LongTreasury": "TLT", "DollarCash": "BIL",
    "Nasdaq100ETF": "QQQ", "Russell2000ETF": "IWM",
}

# Rates: DGS2 + DGS10 fetched; T10Y2Y computed from aligned dates (see below).
RATES_FRED = {"DGS2": "DGS2", "DGS10": "DGS10"}

# Macro: (series_id, transform). Latest obs = actual, one before = prior.
# We emit the RELEASED statistic the brief needs, plus the raw level.
# Consensus is NOT here (FRED has none) — scout supplies it as a cited claim.
# ISM PMI deliberately absent: no free FRED series is the same thing.
MACRO_FRED = {
    "CPI":             ("CPIAUCSL",     "mom_pct+yoy_pct"),
    "CoreCPI":         ("CPILFESL",     "mom_pct+yoy_pct"),
    "PCE":             ("PCEPI",        "mom_pct+yoy_pct"),
    "CorePCE":         ("PCEPILFE",     "mom_pct+yoy_pct"),
    "AvgHourlyEarn":   ("CES0500000003","mom_pct+yoy_pct"),
    "PPI":             ("PPIFIS",       "mom_pct+yoy_pct"),
    "IndustrialProd":  ("INDPRO",       "mom_pct+yoy_pct"),
    "RetailSales":     ("RSAFS",        "mom_pct+yoy_pct"),
    "NonfarmPayrolls": ("PAYEMS",       "mom_diff"),
    "Unemployment":    ("UNRATE",       "level"),
    "InitialClaims":   ("ICSA",         "level"),
}

# ---- quote helpers ----------------------------------------------------------

def unavailable(note):
    return {"status": "unavailable", "note": note}

def asset_class_for(ticker):
    if ticker.endswith("-USD"):
        return "crypto"
    if ticker.endswith("=F"):
        return "futures"
    if ticker.endswith("=X"):
        return "fx"
    return "cash"

def _bar_complete(session_date_str, asset_class, now_utc,
                  market_tz=None, close_time=None):
    """Is this daily bar a settled session, or still forming?"""
    try:
        sd = dt.date.fromisoformat(session_date_str)
    except Exception:
        return None
    if asset_class == "crypto":                 # 24/7, UTC-keyed daily bar
        return sd < now_utc.date()
    if market_tz and close_time:
        try:
            tz = ZoneInfo(market_tz)
            local_now = now_utc.astimezone(tz)
            close_local = dt.datetime.combine(sd, close_time, tzinfo=tz)
            return local_now >= close_local
        except Exception:
            return None
    if ET is None:
        return None
    now_et = now_utc.astimezone(ET)
    if asset_class in ("futures", "fx"):       # near-continuous; today's is live
        return sd < now_et.date()
    close_et = dt.datetime.combine(sd, dt.time(16, 0), tzinfo=ET)  # cash close
    return now_et >= close_et

def download_quotes(tickers):
    if yf is None:
        return None
    try:
        return yf.download(tickers, period="10d", interval="1d",
                           group_by="ticker", auto_adjust=False,
                           progress=False, threads=True)
    except Exception as e:
        print(f"yf.download failed: {e}", file=sys.stderr)
        return None

def extract_quote(data, ticker, now_utc, market_meta=None):
    """Pull last/prev close + bar dates + completion from a batched download."""
    if data is None or pd is None:
        return unavailable(f"{ticker}: no market data")
    try:
        if isinstance(data.columns, pd.MultiIndex):
            sub = data[ticker]
        else:
            sub = data
        closes = sub["Close"].dropna()
        if len(closes) < 2:
            return unavailable(f"insufficient history for {ticker}")
        last = float(closes.iloc[-1])
        prev = float(closes.iloc[-2])
        sd = closes.index[-1].date().isoformat()
        market_meta = market_meta or (None, None)
        return {
            "last": round(last, 4),
            "prev_close": round(prev, 4),
            "chg": round(last - prev, 4),
            "chg_pct": round((last - prev) / prev * 100, 4),
            "session_date": sd,
            "prev_session_date": closes.index[-2].date().isoformat(),
            "bar_complete": _bar_complete(
                sd, asset_class_for(ticker), now_utc,
                market_tz=market_meta[0], close_time=market_meta[1]),
            "status": "ok",
        }
    except Exception as e:
        return unavailable(f"{ticker}: {e}")

# ---- FRED helpers -----------------------------------------------------------

def _fred_csv(series_id, cosd=None):
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    if cosd:
        url += f"&cosd={cosd}"
    r = requests.get(url, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    rows = list(csv.reader(io.StringIO(r.text)))
    date_col, val_col = 0, 1  # observation_date/DATE , <series>
    pts = []
    for row in rows[1:]:
        if len(row) <= val_col:
            continue
        d, v = row[date_col].strip(), row[val_col].strip()
        if v in ("", "."):
            continue
        try:
            pts.append((d, float(v)))
        except ValueError:
            continue
    return pts

def compute_spread(dgs2_pts, dgs10_pts):
    """T10Y2Y from aligned DGS10 - DGS2 (same observation date on both legs)."""
    if not dgs2_pts or not dgs10_pts:
        return unavailable("T10Y2Y: missing DGS2 or DGS10 leg")
    m2, m10 = dict(dgs2_pts), dict(dgs10_pts)
    common = sorted(set(m2) & set(m10))
    if len(common) < 2:
        return unavailable("T10Y2Y: <2 aligned observations")
    d1, d0 = common[-1], common[-2]
    s1, s0 = m10[d1] - m2[d1], m10[d0] - m2[d0]
    return {
        "last": round(s1, 4), "last_date": d1,
        "prev": round(s0, 4), "prev_date": d0,
        "chg_bp": round((s1 - s0) * 100, 1),
        "computed_from": "DGS10 - DGS2 (aligned dates)",
        "status": "ok",
    }

def build_rates(cosd):
    """DGS2/DGS10 latest+prior+chg_bp, plus T10Y2Y computed from aligned legs."""
    out = {}
    pts_by = {}
    def fetch_rate(name):
        try:
            pts = _fred_csv(name, cosd=cosd)
            if len(pts) < 2:
                return name, pts, unavailable(f"{name}: <2 observations")
            (pd_, pv), (ld, lv) = pts[-2], pts[-1]
            return name, pts, {
                "last": lv, "last_date": ld,
                "prev": pv, "prev_date": pd_,
                "chg_bp": round((lv - pv) * 100, 1),
                "status": "ok",
            }
        except Exception as e:
            return name, [], unavailable(f"{name}: {e}")

    with ThreadPoolExecutor(max_workers=2) as ex:
        for name, pts, entry in ex.map(fetch_rate, ("DGS2", "DGS10")):
            pts_by[name] = pts
            out[name] = entry
    out["T10Y2Y"] = compute_spread(pts_by.get("DGS2"), pts_by.get("DGS10"))
    return out


def apply_local_cache(group_name, current, now_utc, max_age_days):
    """Fill unavailable FRED entries from a recent local successful snapshot.

    This is an explicit, annotated fallback — never a silent freshness claim.
    Market quotes do not use this fallback.
    """
    paths = sorted(
        glob.glob(os.path.join("data", "*.raw.json")) +
        glob.glob(os.path.join("data", "*.json")),
        reverse=True,
    )
    notes = []
    for path in paths:
        try:
            with open(path) as f:
                payload = json.load(f)
            asof_text = payload.get("asof")
            if not asof_text:
                continue
            asof = dt.datetime.fromisoformat(asof_text.replace("Z", "+00:00"))
            age = now_utc - asof.astimezone(dt.timezone.utc)
            if age < dt.timedelta(0) or age > dt.timedelta(days=max_age_days):
                continue
            cached_group = payload.get(group_name, {})
            for key, entry in list(current.items()):
                if entry.get("status") != "unavailable":
                    continue
                cached = cached_group.get(key, {})
                if cached.get("status") != "ok":
                    continue
                replacement = copy.deepcopy(cached)
                replacement["cache_fallback"] = True
                replacement["cached_from"] = path
                replacement["cached_asof"] = asof_text
                replacement["cache_age_hours"] = round(age.total_seconds() / 3600, 2)
                replacement["live_fetch_error"] = entry.get("note")
                current[key] = replacement
                notes.append(f"{group_name}.{key} from {path} ({replacement['cache_age_hours']}h old)")
            if all(v.get("status") != "unavailable" for v in current.values()):
                break
        except Exception:
            continue
    return notes

def fetch_fred_last_updated(series_id, api_key):
    """Raw last_updated timestamp — only when a key is present. Never fatal."""
    if not api_key:
        return None
    try:
        url = ("https://api.stlouisfed.org/fred/series"
               f"?series_id={series_id}&api_key={api_key}&file_type=json")
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        return r.json().get("seriess", [{}])[0].get("last_updated")
    except Exception:
        return None

def fetch_all_fred_meta(series_ids, api_key):
    """Concurrent last_updated lookups. Empty dict if no key. Never fatal."""
    if not api_key:
        return {}
    out = {}
    try:
        with ThreadPoolExecutor(max_workers=6) as ex:
            for sid, ts in ex.map(
                    lambda s: (s, fetch_fred_last_updated(s, api_key)),
                    series_ids):
                out[sid] = ts
    except Exception as e:
        print(f"fred meta lookup failed: {e}", file=sys.stderr)
    return out

def _nz(x):
    # normalize -0.0 -> 0.0 so the brief never prints "-0.0%"
    return 0.0 if x == 0 else x

def _mom_pct(vals, i):
    if i - 1 < 0:
        return None
    return _nz(round((vals[i] / vals[i - 1] - 1) * 100, 1))

def _yoy_pct(vals, i):
    if i - 12 < 0:               # needs the same month one year earlier
        return None
    return _nz(round((vals[i] / vals[i - 12] - 1) * 100, 1))

def fetch_fred_macro(series_id, transform, cosd, last_updated):
    """Emit the released statistic (m/m & y/y, mom_diff, or level) + level."""
    try:
        pts = _fred_csv(series_id, cosd=cosd)
        if len(pts) < 2:
            return unavailable(f"{series_id}: <2 observations")
        dates = [d for d, _ in pts]
        vals = [v for _, v in pts]
        n = len(vals)
        entry = {
            "series_id": series_id,
            "transform": transform,
            "ref_period": dates[-1],
            "prior_ref_period": dates[-2],
            "level": round(vals[-1], 4),
            "prior_level": round(vals[-2], 4),
            "consensus": None,  # NEVER fetched here; scout provides + cites
            "fred_last_updated": last_updated,
            "status": "ok",
        }
        notes = []
        if transform == "level":
            entry["released"] = {"level": vals[-1]}
            entry["prior_released"] = {"level": vals[-2]}
        elif transform == "mom_diff":
            entry["released"] = {"mom_diff": round(vals[-1] - vals[-2])}
            entry["prior_released"] = ({"mom_diff": round(vals[-2] - vals[-3])}
                                       if n >= 3 else None)
        else:  # mom_pct+yoy_pct
            yoy_l = _yoy_pct(vals, n - 1)
            entry["released"] = {"mom_pct": _mom_pct(vals, n - 1),
                                 "yoy_pct": yoy_l}
            entry["prior_released"] = {"mom_pct": _mom_pct(vals, n - 2),
                                       "yoy_pct": _yoy_pct(vals, n - 2)}
            if yoy_l is None:
                notes.append("insufficient history for yoy (<13 obs)")
        if notes:
            entry["note"] = "; ".join(notes)
        return entry
    except Exception as e:
        return unavailable(f"{series_id}: {e}")

# ---- futures dating guard ---------------------------------------------------

def _tag_meaning(entries, meaning):
    for e in entries:
        if isinstance(e, dict) and e.get("status") == "ok":
            e["chg_pct_meaning"] = meaning

def futures_dating_guard(futures, indices, fxcc, now_utc):
    """Deterministic pre-open test that Yahoo dates =F bars on the SETTLEMENT
    convention (17:00 ET -> 16:00 ET next day, labeled by the settle date).

    Premise: at a pre-open run the cash S&P bar is necessarily the prior
    session. A live futures bar on the settlement convention must therefore be
    strictly dated AFTER it. If it isn't, we cannot tell a start-convention live
    bar from a stale feed, so we refuse to publish an overnight move of unknown
    meaning. Futures is load-bearing -> the caller exits 1, no brief is written.

    Only meaningful pre-open (before cash open), so it is gated to runs before
    09:30 ET; during regular hours or post-close the cash bar is today's, the
    premise fails, and futures are marked "not evaluated" and pass through.
    Mutates entries in place; returns the verdict string.
    """
    commodities = [fxcc.get(n) for n in
                   ("Gold", "WTI", "Brent", "Copper", "Silver", "NatGas")]
    now_et = now_utc.astimezone(ET) if ET else None
    if now_et is None:
        return "not evaluated (no tz data)"
    if now_et.time() >= dt.time(9, 30):        # cash open — pre-open premise no longer holds
        _tag_meaning(list(futures.values()) + commodities,
                     "not evaluated (outside pre-open window)")
        return "not evaluated (at/after 09:30 ET cash open — pre-open test N/A)"
    es = futures.get("ES", {})
    cash = indices.get("SP500", {})
    if es.get("status") != "ok" or cash.get("status") != "ok":
        return "not evaluated (ES or SP500 leg missing)"
    es_sd, cash_sd = es["session_date"], cash["session_date"]
    if es_sd > cash_sd:                      # settlement convention confirmed
        _tag_meaning(list(futures.values()) + commodities,
                     "overnight vs prior settle")
        return f"confirmed: ES {es_sd} > cash {cash_sd} (settlement convention)"
    note = ("futures bar dating ambiguous vs cash session; refusing to publish "
            "an overnight move of unknown meaning")
    for k in list(futures.keys()):
        futures[k] = unavailable(note)
    return f"ambiguous: ES {es_sd} <= cash {cash_sd} — futures marked unavailable"

# ---- main -------------------------------------------------------------------

def build(date_str, with_meta, mode="intraday", run_context=None):
    now_utc = dt.datetime.now(dt.timezone.utc)
    asof = now_utc.isoformat(timespec="seconds")
    api_key = os.environ.get("FRED_API_KEY")
    today = now_utc.date()
    macro_cosd = (today - dt.timedelta(days=5 * 366)).isoformat()  # ~5y
    rates_cosd = (today - dt.timedelta(days=400)).isoformat()      # ~1y

    all_map = {}
    for m in (INDICES, FUTURES, CONCENTRATION, SECTORS, FX_COMMO_CRYPTO,
              GLOBAL_INDICES, GLOBAL_ETFS, FACTORS_CREDIT):
        all_map.update(m)
    data = download_quotes(sorted(set(all_map.values())))

    def group(mapping):
        return {k: extract_quote(data, t, now_utc, GLOBAL_INDEX_META.get(t))
                for k, t in mapping.items()}

    indices = group(INDICES)
    futures = group(FUTURES)
    concentration = group(CONCENTRATION)
    sectors = group(SECTORS)
    fxcc = group(FX_COMMO_CRYPTO)
    global_indices = group(GLOBAL_INDICES)
    global_etfs = group(GLOBAL_ETFS)
    factors_credit = group(FACTORS_CREDIT)

    def last(entry):
        return entry.get("last") if entry.get("status") == "ok" else None

    option_spots = {
        "SPY": last(concentration.get("SPY", {})),
        "QQQ": last(factors_credit.get("Nasdaq100ETF", {})),
        "IWM": last(factors_credit.get("Russell2000ETF", {})),
        "SMH": last(sectors.get("SMH", {})),
    }
    # These optional sources are independent of FRED. Start them now so their
    # network latency overlaps the deterministic rates/macro work below.
    derivative_pool = ThreadPoolExecutor(max_workers=2)
    options_future = derivative_pool.submit(
        fetch_options, option_spots, date_str, mode,
        (run_context or {}).get("is_session", True))
    positioning_future = derivative_pool.submit(fetch_positioning, run_context or {})

    futures_dating = futures_dating_guard(futures, indices, fxcc, now_utc)

    # Equal-weight vs cap-weight daily relative performance (breadth proxy).
    rsp, spy = concentration["RSP"], concentration["SPY"]
    if rsp.get("status") == "ok" and spy.get("status") == "ok":
        spread = round(rsp["chg_pct"] - spy["chg_pct"], 4)
        if abs(spread) < 0.10:                       # deadband
            reading = "no clear tilt"
        elif spread >= 0.10:
            reading = "equal-weight leading (broadening)"
        else:
            reading = "cap-weight leading (narrowing / mega-cap driven)"
        rel = {"rsp_minus_spy_chg_pct": spread, "reading": reading,
               "status": "ok"}
    else:
        rel = unavailable("RSP or SPY leg missing")

    rates = build_rates(rates_cosd)
    cache_fallbacks = apply_local_cache("rates", rates, now_utc, max_age_days=4)

    meta_map = (fetch_all_fred_meta([sid for sid, _ in MACRO_FRED.values()],
                                    api_key)
                if with_meta else {})
    def fetch_macro_item(item):
        k, (sid, tf) = item
        return k, fetch_fred_macro(sid, tf, macro_cosd, meta_map.get(sid))

    with ThreadPoolExecutor(max_workers=6) as ex:
        macro = dict(ex.map(fetch_macro_item, MACRO_FRED.items()))
    cache_fallbacks.extend(
        apply_local_cache("macro", macro, now_utc, max_age_days=45))

    try:
        options = options_future.result()
    except Exception as exc:  # optional source: visible failure, never fatal
        options = unavailable(f"options collector failed: {exc}")
    try:
        positioning = positioning_future.result()
    except Exception as exc:
        positioning = unavailable(f"positioning collector failed: {exc}")
    derivative_pool.shutdown(wait=True)

    expected_cash_session = (run_context or {}).get("expected_cash_session")
    cash_session_actual = indices.get("SP500", {}).get("session_date")
    load_bearing_quotes = {**indices, **concentration}
    session_mismatches = [
        name for name, entry in load_bearing_quotes.items()
        if expected_cash_session and entry.get("status") == "ok"
        and entry.get("session_date") != expected_cash_session
    ]
    completion_mismatches = []
    if mode in ("preopen", "close"):
        completion_mismatches = [
            name for name, entry in load_bearing_quotes.items()
            if entry.get("status") == "ok" and entry.get("bar_complete") is not True
        ]
    freshness = {
        "expected_us_cash_session": expected_cash_session,
        "actual_us_cash_session": cash_session_actual,
        "us_cash_session_matches": (
            None if not expected_cash_session else cash_session_actual == expected_cash_session),
        "load_bearing_session_mismatches": session_mismatches,
        "load_bearing_completion_mismatches": completion_mismatches,
        "mode": mode,
        "status": "ok",
    }
    if session_mismatches or completion_mismatches:
        freshness["status"] = "stale"
        freshness["note"] = (
            f"session mismatches={session_mismatches}; "
            f"incomplete bars={completion_mismatches}; expected={expected_cash_session}")

    return {
        "date": date_str,
        "mode": mode,
        "asof": asof,
        "run_context": run_context or {},
        "data_quality": {"freshness": freshness, "cache_fallbacks": cache_fallbacks},
        "futures_dating": futures_dating,
        "coverage": {
            "session": ("latest available daily bar; it may still be live. Always "
                        "inspect session_date and bar_complete per quote"),
            "futures": ("equity index futures (ES/NQ/YM/RTY) — intraday; "
                        "bar_complete:false means a live session, the pre-open read"),
            "pre_market": "not in this file — scout's cited claim",
            "post_market": "not in this file — scout's cited claim",
            "macro": ("most recent FRED observation at fetch time (see asof, and "
                      "ref_period per series). FRED republishes on its own schedule "
                      "and may not have picked up an 08:30 ET print by 08:40 ET. Do "
                      "not assume it has. Cross-check ref_period against what the "
                      "macro scout reports."),
            "consensus": "not in this file — scout's cited claim",
            "fedwatch": "not in this file — fed scout's cited claim",
            "options": ("SPY/QQQ/IWM/SMH option-chain aggregates. Tradier is used "
                        "when TRADIER_TOKEN is set; otherwise Yahoo is best-effort. "
                        "OI is delayed by the clearing cycle; volume is cumulative."),
            "positioning": ("CFTC COT is weekly and lagged; FINRA Reg SHO is a daily "
                            "short-sale activity proxy. Neither is real-time net fund flow."),
        },
        "source_notes": {
            "market": "Yahoo Finance via yfinance (last vs prior daily close)",
            "rates_macro": "FRED keyless CSV (fredgraph.csv); stats computed here",
            "consensus": "NOT in this file — scouts supply as cited claims",
            "options": ("Tradier Brokerage API with ORATS Greeks/IV when token-authenticated; "
                        "otherwise Yahoo Finance via yfinance, best-effort/no SLA"),
            "positioning": ("CFTC public weekly COT plus FINRA consolidated daily Reg SHO "
                            "short-sale volume; explicitly not labeled fund flow"),
        },
        "indices": indices,
        "futures": futures,
        "concentration": concentration,
        "breadth_proxy": rel,
        "sectors": sectors,
        "fx_commodities_crypto": fxcc,
        "global_indices": global_indices,
        "global_etfs": global_etfs,
        "factors_credit": factors_credit,
        "rates": rates,
        "macro": macro,
        "options": options,
        "positioning": positioning,
    }


def build_agent_view(payload):
    """Remove direction fields the downstream model is not allowed to use.

    Keeping the full values in *.raw.json preserves auditability.  The model
    receives this redacted view, making the futures/commodity guard enforceable
    by data shape instead of prompt obedience.
    """
    view = copy.deepcopy(payload)
    blocked = []
    for group_name in ("futures", "fx_commodities_crypto"):
        for name, entry in view.get(group_name, {}).items():
            if not isinstance(entry, dict) or entry.get("status") != "ok":
                continue
            meaning = entry.get("chg_pct_meaning", "")
            if meaning.startswith("not evaluated"):
                blocked.append(f"{group_name}.{name}")
                entry["chg"] = None
                entry["chg_pct"] = None
                entry["direction_usable"] = False
                entry["direction_note"] = meaning
            else:
                entry["direction_usable"] = True
    view.setdefault("data_quality", {})["redacted_direction_fields"] = blocked
    return view

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--raw-out", default=None,
                    help="optional full-fidelity audit JSON; --out receives agent-safe view")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default today)")
    ap.add_argument("--mode", choices=("preopen", "close", "intraday"),
                    default="intraday")
    ap.add_argument("--context", default=None,
                    help="market_clock.py JSON used for expected-session freshness")
    ap.add_argument("--with-fred-meta", action="store_true",
                    help="add FRED last_updated timestamps (needs FRED_API_KEY)")
    args = ap.parse_args()

    date_str = args.date or dt.date.today().isoformat()
    out = args.out or os.path.join("data", f"{date_str}.json")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    run_context = {}
    if args.context:
        try:
            with open(args.context) as f:
                run_context = json.load(f)
        except Exception as e:
            print(f"invalid market context {args.context}: {e}", file=sys.stderr)
            return 2

    payload = build(date_str, args.with_fred_meta, args.mode, run_context)
    agent_payload = build_agent_view(payload)
    if args.raw_out:
        os.makedirs(os.path.dirname(args.raw_out) or ".", exist_ok=True)
        with open(args.raw_out, "w") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
    with open(out, "w") as f:                       # staging path supplied by run.sh
        json.dump(agent_payload, f, indent=2, ensure_ascii=False)
    print(f"wrote {out}")

    # Load-bearing sources: any failure here aborts the pipeline.
    fatal = []
    fatal_groups = ["indices", "rates"]
    if args.mode == "preopen":
        fatal_groups.append("futures")
    for grp in fatal_groups:
        for k, v in payload[grp].items():
            if isinstance(v, dict) and v.get("status") == "unavailable":
                fatal.append(f"{grp}.{k}: {v.get('note')}")
    if payload["breadth_proxy"].get("status") == "unavailable":
        fatal.append("breadth_proxy: " + payload["breadth_proxy"]["note"])
    freshness = payload["data_quality"]["freshness"]
    if freshness.get("status") == "stale":
        fatal.append("data_quality.freshness: " + freshness.get("note", "stale"))

    # Everything else: a warning, but the run still succeeds.
    warn = []

    def collect_unavailable(value, path):
        if not isinstance(value, dict):
            return
        if value.get("status") == "unavailable":
            warn.append(f"{path}: {value.get('note', 'unavailable')}")
            return
        for key, child in value.items():
            if isinstance(child, dict):
                collect_unavailable(child, f"{path}.{key}")

    for grp in ("futures", "concentration", "sectors", "fx_commodities_crypto",
                "global_indices", "global_etfs", "factors_credit", "macro",
                "options", "positioning"):
        if grp in fatal_groups:
            continue
        collect_unavailable(payload[grp], grp)

    if warn:
        print(f"WARN ({len(warn)}):", file=sys.stderr)
        for w in warn:
            print("  - " + w, file=sys.stderr)
    if fatal:
        print(f"FATAL — load-bearing source unavailable ({len(fatal)}):",
              file=sys.stderr)
        for b in fatal:
            print("  - " + b, file=sys.stderr)
        sys.exit(1)
    print("load-bearing sources ok")

if __name__ == "__main__":
    raise SystemExit(main() or 0)
