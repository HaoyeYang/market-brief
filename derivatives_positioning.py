#!/usr/bin/env python3
"""Best-effort options and positioning data with explicit provenance.

Free sources are not described as real-time fund flows:
- options: Tradier when TRADIER_TOKEN is configured, otherwise Yahoo/yfinance
- positioning: CFTC public COT API (weekly)
- short activity: FINRA Reg SHO daily short-volume file (not short interest)
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import math
import os
import statistics
from concurrent.futures import ThreadPoolExecutor

import requests
import yfinance as yf


TIMEOUT = 15
UA = {"User-Agent": "market-brief/2.0 research@localhost"}
OPTION_SYMBOLS = ("SPY", "QQQ", "IWM", "SMH")
FINRA_SYMBOLS = ("SPY", "QQQ", "IWM", "SMH", "HYG", "LQD", "TLT", "EEM", "GLD", "USO")

TFF_MARKETS = {
    "SP500": "E-MINI S&P 500",
    "Nasdaq100": "NASDAQ MINI",
    "Russell2000": "RUSSELL E-MINI",
    "UST2Y": "UST 2Y NOTE",
    "UST10Y": "UST 10Y NOTE",
    "Dollar": "USD INDEX",
    "VIX": "VIX FUTURES",
    "Bitcoin": "BITCOIN",
}
DISAGG_MARKETS = {
    "Gold": "GOLD",
    "Silver": "SILVER",
    "Copper": "COPPER- #1",
    "WTI": "CRUDE OIL, LIGHT SWEET-WTI",
    "NatGas": "NAT GAS NYME",
}


def unavailable(note, source=None):
    out = {"status": "unavailable", "note": str(note)}
    if source:
        out["source"] = source
    return out


def _number(value, default=0.0):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except Exception:
        return default


def _ratio(a, b):
    return round(a / b, 4) if b else None


def _median(values):
    values = [v for v in values if v is not None and math.isfinite(v) and v > 0]
    return round(statistics.median(values), 6) if values else None


def _summarize_chain(symbol, expiration, spot, calls, puts, provider, mode,
                     report_date=None, is_session=True):
    if not calls or not puts or not spot:
        return unavailable("empty option chain or missing underlying spot", provider)
    call_volume = sum(_number(x.get("volume")) for x in calls)
    put_volume = sum(_number(x.get("volume")) for x in puts)
    call_oi = sum(_number(x.get("open_interest")) for x in calls)
    put_oi = sum(_number(x.get("open_interest")) for x in puts)
    atm_call = min(calls, key=lambda x: abs(_number(x.get("strike")) - spot))
    atm_put = min(puts, key=lambda x: abs(_number(x.get("strike")) - spot))
    put_wing = [_number(x.get("iv"), math.nan) for x in puts
                if 0.85 <= _number(x.get("strike")) / spot <= 0.95]
    call_wing = [_number(x.get("iv"), math.nan) for x in calls
                 if 1.05 <= _number(x.get("strike")) / spot <= 1.15]
    put_wing_iv, call_wing_iv = _median(put_wing), _median(call_wing)
    call_mid = (_number(atm_call.get("bid")) + _number(atm_call.get("ask"))) / 2
    put_mid = (_number(atm_put.get("bid")) + _number(atm_put.get("ask"))) / 2
    call_wall = max(calls, key=lambda x: _number(x.get("open_interest")))
    put_wall = max(puts, key=lambda x: _number(x.get("open_interest")))
    call_wall_oi = _number(call_wall.get("open_interest"))
    put_wall_oi = _number(put_wall.get("open_interest"))
    exp_date = dt.date.fromisoformat(expiration)
    dte = (exp_date - (report_date or dt.date.today())).days
    return {
        "status": "ok",
        "source": provider,
        "symbol": symbol,
        "expiration": expiration,
        "dte_at_fetch": dte,
        "underlying_last": round(spot, 4),
        "contract_count": len(calls) + len(puts),
        "put_call_volume_ratio": _ratio(put_volume, call_volume),
        "put_call_open_interest_ratio": _ratio(put_oi, call_oi),
        "call_volume": round(call_volume),
        "put_volume": round(put_volume),
        "call_open_interest": round(call_oi),
        "put_open_interest": round(put_oi),
        "atm_call_iv": _number(atm_call.get("iv"), None),
        "atm_put_iv": _number(atm_put.get("iv"), None),
        "downside_wing_iv": put_wing_iv,
        "upside_wing_iv": call_wing_iv,
        "downside_minus_upside_iv": (
            round(put_wing_iv - call_wing_iv, 6)
            if put_wing_iv is not None and call_wing_iv is not None else None),
        "atm_straddle_implied_move_pct": (
            round((call_mid + put_mid) / spot * 100, 4) if call_mid + put_mid > 0 else None),
        "call_wall_strike_by_oi": (
            _number(call_wall.get("strike"), None) if call_wall_oi > 0 else None),
        "put_wall_strike_by_oi": (
            _number(put_wall.get("strike"), None) if put_wall_oi > 0 else None),
        "volume_interpretation": (
            "non-session snapshot; option volume may be from the prior trading day"
            if not is_session else
            "intraday cumulative option volume" if mode in ("intraday", "close")
            else "pre-open volume may be zero/stale; emphasize OI and IV, not volume"),
        "limitations": (
            "Open interest is previous-clearing-cycle data. These aggregates do not reveal "
            "trade direction or dealer inventory; no gamma-exposure claim is made."),
    }


def _choose_expirations(expirations, report_date):
    parsed = []
    for text in expirations:
        try:
            dte = (dt.date.fromisoformat(text) - report_date).days
            if dte >= 0:
                parsed.append((dte, text))
        except Exception:
            continue
    parsed.sort()
    short = next((text for dte, text in parsed if dte <= 2), None)
    swing = next((text for dte, text in parsed if 7 <= dte <= 35), None)
    return {k: v for k, v in (("short_dated", short), ("swing", swing)) if v}


def _yahoo_options_symbol(symbol, spot, report_date, mode, is_session):
    provider = "Yahoo Finance via yfinance (best-effort; no SLA)"
    try:
        ticker = yf.Ticker(symbol)
        expirations = ticker.options
        chosen = _choose_expirations(expirations, report_date)
        if not chosen:
            return unavailable("no suitable 0-2DTE or 7-35DTE expiration", provider)
        out = {"status": "ok", "provider": provider, "buckets": {}}
        for bucket, expiration in chosen.items():
            chain = ticker.option_chain(expiration)
            calls = [{
                "strike": row.get("strike"), "volume": row.get("volume"),
                "open_interest": row.get("openInterest"), "iv": row.get("impliedVolatility"),
                "bid": row.get("bid"), "ask": row.get("ask"),
            } for row in chain.calls.to_dict("records")]
            puts = [{
                "strike": row.get("strike"), "volume": row.get("volume"),
                "open_interest": row.get("openInterest"), "iv": row.get("impliedVolatility"),
                "bid": row.get("bid"), "ask": row.get("ask"),
            } for row in chain.puts.to_dict("records")]
            out["buckets"][bucket] = _summarize_chain(
                symbol, expiration, spot, calls, puts, provider, mode, report_date,
                is_session)
        if not any(v.get("status") == "ok" for v in out["buckets"].values()):
            return unavailable("all selected option chains were empty", provider)
        return out
    except Exception as exc:
        return unavailable(exc, provider)


def _tradier_get(path, params, token):
    base = os.environ.get("TRADIER_BASE_URL", "https://api.tradier.com/v1")
    response = requests.get(
        base + path, params=params,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json", **UA},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def _tradier_options_symbol(symbol, spot, report_date, mode, token, is_session):
    provider = "Tradier Brokerage API (token-authenticated; ORATS Greeks/IV)"
    try:
        payload = _tradier_get("/markets/options/expirations", {"symbol": symbol}, token)
        dates = payload.get("expirations", {}).get("date") or []
        if isinstance(dates, str):
            dates = [dates]
        chosen = _choose_expirations(dates, report_date)
        if not chosen:
            return unavailable("no suitable 0-2DTE or 7-35DTE expiration", provider)
        out = {"status": "ok", "provider": provider, "buckets": {}}
        for bucket, expiration in chosen.items():
            chain = _tradier_get(
                "/markets/options/chains",
                {"symbol": symbol, "expiration": expiration, "greeks": "true"}, token,
            ).get("options", {}).get("option") or []
            if isinstance(chain, dict):
                chain = [chain]
            normalized = []
            for row in chain:
                greeks = row.get("greeks") or {}
                normalized.append({
                    "type": row.get("option_type"), "strike": row.get("strike"),
                    "volume": row.get("volume"), "open_interest": row.get("open_interest"),
                    "iv": greeks.get("mid_iv") or greeks.get("smv_vol"),
                    "bid": row.get("bid"), "ask": row.get("ask"),
                })
            calls = [row for row in normalized if row["type"] == "call"]
            puts = [row for row in normalized if row["type"] == "put"]
            out["buckets"][bucket] = _summarize_chain(
                symbol, expiration, spot, calls, puts, provider, mode, report_date,
                is_session)
        if not any(v.get("status") == "ok" for v in out["buckets"].values()):
            return unavailable("all selected option chains were empty", provider)
        return out
    except Exception as exc:
        return unavailable(exc, provider)


def fetch_options(spots, date_str, mode, is_session=True):
    report_date = dt.date.fromisoformat(date_str)
    token = os.environ.get("TRADIER_TOKEN")

    def one(symbol):
        spot = _number(spots.get(symbol), None)
        if not spot:
            return symbol, unavailable("underlying spot unavailable")
        if token:
            return symbol, _tradier_options_symbol(
                symbol, spot, report_date, mode, token, is_session)
        return symbol, _yahoo_options_symbol(
            symbol, spot, report_date, mode, is_session)

    with ThreadPoolExecutor(max_workers=4) as executor:
        result = dict(executor.map(one, OPTION_SYMBOLS))
    status = "ok" if any(v.get("status") == "ok" for v in result.values()) else "unavailable"
    return {
        "status": status,
        **({"note": "all requested option chains unavailable"} if status == "unavailable" else {}),
        "provider_selected": "tradier" if token else "yahoo_best_effort",
        "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "symbols": result,
    }


def _socrata_rows(dataset, market_names):
    quoted = ",".join("'" + name.replace("'", "''") + "'" for name in market_names)
    response = requests.get(
        f"https://publicreporting.cftc.gov/resource/{dataset}.json",
        params={
            "$where": f"contract_market_name in({quoted})",
            "$order": "report_date_as_yyyy_mm_dd DESC",
            "$limit": 250,
        },
        headers=UA, timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def fetch_cftc_positioning():
    source = "CFTC Public Reporting Environment (weekly COT; no SLA)"
    output = {}
    try:
        rows = _socrata_rows("gpe5-46if", TFF_MARKETS.values())
        latest = {}
        for row in rows:
            latest.setdefault(row.get("contract_market_name"), row)
        for label, market in TFF_MARKETS.items():
            row = latest.get(market)
            if not row:
                output[label] = unavailable(f"no CFTC row for {market}", source)
                continue
            oi = _number(row.get("open_interest_all"))
            am_net = _number(row.get("asset_mgr_positions_long")) - _number(row.get("asset_mgr_positions_short"))
            lev_net = _number(row.get("lev_money_positions_long")) - _number(row.get("lev_money_positions_short"))
            output[label] = {
                "status": "ok", "source": source,
                "report_date": row.get("report_date_as_yyyy_mm_dd", "")[:10],
                "market": market, "open_interest": oi,
                "asset_manager_net_contracts": am_net,
                "asset_manager_net_pct_oi": round(am_net / oi * 100, 3) if oi else None,
                "leveraged_money_net_contracts": lev_net,
                "leveraged_money_net_pct_oi": round(lev_net / oi * 100, 3) if oi else None,
                "weekly_change_asset_manager_net": (
                    _number(row.get("change_in_asset_mgr_long")) - _number(row.get("change_in_asset_mgr_short"))),
                "weekly_change_leveraged_money_net": (
                    _number(row.get("change_in_lev_money_long")) - _number(row.get("change_in_lev_money_short"))),
            }
    except Exception as exc:
        for label in TFF_MARKETS:
            output[label] = unavailable(exc, source)

    try:
        rows = _socrata_rows("72hh-3qpy", DISAGG_MARKETS.values())
        latest = {}
        for row in rows:
            latest.setdefault(row.get("contract_market_name"), row)
        for label, market in DISAGG_MARKETS.items():
            row = latest.get(market)
            if not row:
                output[label] = unavailable(f"no CFTC row for {market}", source)
                continue
            oi = _number(row.get("open_interest_all"))
            mm_net = _number(row.get("m_money_positions_long_all")) - _number(row.get("m_money_positions_short_all"))
            output[label] = {
                "status": "ok", "source": source,
                "report_date": row.get("report_date_as_yyyy_mm_dd", "")[:10],
                "market": market, "open_interest": oi,
                "managed_money_net_contracts": mm_net,
                "managed_money_net_pct_oi": round(mm_net / oi * 100, 3) if oi else None,
                "weekly_change_managed_money_net": (
                    _number(row.get("change_in_m_money_long_all")) -
                    _number(row.get("change_in_m_money_short_all"))),
            }
    except Exception as exc:
        for label in DISAGG_MARKETS:
            output[label] = unavailable(exc, source)
    status = "ok" if any(v.get("status") == "ok" for v in output.values()) else "unavailable"
    return {
        "status": status, "frequency": "weekly", "markets": output,
        **({"note": "all requested CFTC COT markets unavailable"}
           if status == "unavailable" else {}),
    }


def fetch_finra_short_volume(session_date):
    source = "FINRA consolidated Reg SHO daily short-sale volume file"
    if not session_date:
        return unavailable("no prior US cash session supplied", source)
    date_token = session_date.replace("-", "")
    url = f"https://cdn.finra.org/equity/regsho/daily/CNMSshvol{date_token}.txt"
    try:
        response = requests.get(url, headers=UA, timeout=TIMEOUT)
        response.raise_for_status()
        rows = csv.DictReader(io.StringIO(response.text), delimiter="|")
        result = {}
        for row in rows:
            symbol = row.get("Symbol")
            if symbol not in FINRA_SYMBOLS:
                continue
            short = _number(row.get("ShortVolume"))
            exempt = _number(row.get("ShortExemptVolume"))
            total = _number(row.get("TotalVolume"))
            result[symbol] = {
                "status": "ok", "session_date": session_date,
                "short_volume": round(short, 4), "short_exempt_volume": round(exempt, 4),
                "reported_total_volume": round(total, 4),
                "short_volume_ratio": _ratio(short, total),
                "source": source, "url": url,
                "limitations": (
                    "FINRA off-exchange/reporting-facility short-sale volume only; "
                    "not consolidated-market short interest and not net fund flow."),
            }
        if not result:
            return unavailable("no watch-list symbols in FINRA file", source)
        return {"status": "ok", "session_date": session_date, "symbols": result,
                "source": source, "url": url}
    except Exception as exc:
        return unavailable(exc, source)


def fetch_positioning(run_context):
    with ThreadPoolExecutor(max_workers=2) as executor:
        cot_future = executor.submit(fetch_cftc_positioning)
        finra_future = executor.submit(
            fetch_finra_short_volume, run_context.get("previous_cash_session"))
        return {
            "cftc_cot": cot_future.result(),
            "finra_short_volume": finra_future.result(),
            "terminology": {
                "fund_flow": "No free source in this pipeline is labeled real-time net fund flow.",
                "positioning": "CFTC weekly positions and FINRA daily short-volume are positioning/activity proxies.",
            },
        }
