#!/usr/bin/env python3
"""Generate a fully synthetic demo report so the viewer can be explored offline.

Every number, headline, and source in the generated fixture is invented. Nothing
here is copied from a real market session, a real news article, or a real run of
this project. The output is deliberately dated in the far future and stamped
SYNTHETIC so it can never be mistaken for a published brief.

    python scripts/generate_demo.py
    python report_viewer.py --out-dir out --bind 127.0.0.1 --port 8080

The generated files land in out/ and data/, which .gitignore excludes. This
script is committed; its output is not.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

DEMO_DATE = "2099-01-02"
DEMO_MODE = "close"
BASE = f"{DEMO_DATE}.{DEMO_MODE}.demo"

BANNER = (
    "> **SYNTHETIC DEMO — NOT A REAL MARKET BRIEF.**\n"
    "> 本文件由 `scripts/generate_demo.py` 生成，所有数字、标题与来源均为虚构，"
    "日期为不存在的未来交易日。请勿用于任何投资判断。\n"
    "> All figures, headlines and sources below are invented. Do not use for any"
    " investment decision.\n"
)


def _quote(last: float, chg_pct: float, **extra) -> dict:
    return {"last": last, "chg_pct": chg_pct, **extra}


def build_data() -> dict:
    """A synthetic snapshot shaped like fetch_data.py's gated agent view."""
    return {
        "date": DEMO_DATE,
        "mode": DEMO_MODE,
        "synthetic": True,
        "disclaimer": "SYNTHETIC FIXTURE. Invented values. Not market data.",
        "run_context": {
            "is_session": False,
            "session_note": "synthetic fixture; no exchange session exists on this date",
        },
        "indices": {
            "SP500": _quote(4321.00, 0.62),
            "Nasdaq": _quote(15432.10, 0.94),
            "Dow": _quote(34210.50, 0.21),
            "Russell2000": _quote(1987.60, -0.35),
            "VIX": _quote(14.20, -3.10),
        },
        "breadth_proxy": {"rsp_minus_spy_chg_pct": -0.28},
        "sectors": {
            "XLK": _quote(198.40, 1.24), "XLF": _quote(41.20, 0.18),
            "XLE": _quote(88.10, -1.42), "XLV": _quote(139.90, 0.34),
            "XLI": _quote(112.30, 0.07), "XLY": _quote(180.20, 0.66),
            "XLP": _quote(74.10, -0.22), "XLU": _quote(66.80, -0.91),
            "XLB": _quote(85.40, -0.16), "XLRE": _quote(39.70, 0.44),
            "XLC": _quote(78.30, 1.02), "SMH": _quote(212.60, 2.11),
        },
        "rates": {
            "DGS2": {"last": 4.12, "chg_bp": -3.5},
            "DGS10": {"last": 4.28, "chg_bp": 1.5},
            "T10Y2Y": {"last": 0.16, "chg_bp": 5.0},
        },
        "global_indices": {
            "EuroStoxx50": _quote(4510.0, 0.41), "FTSE100": _quote(7820.0, -0.12),
            "DAX": _quote(16120.0, 0.55), "Nikkei225": _quote(33150.0, 1.18),
            "HangSeng": _quote(17980.0, -0.74), "Shanghai": _quote(3021.0, -0.28),
            "IndiaNifty50": _quote(21440.0, 0.36),
        },
        "fx_commodities_crypto": {
            "DXY": _quote(103.40, -0.22), "USDJPY": _quote(148.20, 0.31),
            "Gold": _quote(2045.00, 0.58), "WTI": _quote(72.40, -1.85),
            # A gated field: direction was not verifiable, so it is not colored.
            "BTC": {"last": 43200.00, "chg_pct": None, "direction_usable": False},
        },
        "factors_credit": {"HighYield": _quote(77.30, 0.14)},
        "macro": {
            "CPI": {"ref_period": "2098-11", "released": {"yoy_pct": 2.4},
                    "prior_released": {"yoy_pct": 2.6}},
            "CoreCPI": {"ref_period": "2098-11", "released": {"yoy_pct": 3.1},
                        "prior_released": {"yoy_pct": 3.3}},
            "NonfarmPayrolls": {"ref_period": "2098-12", "released": {"mom_diff": 175.0},
                                "prior_released": {"mom_diff": 199.0}},
            "Unemployment": {"ref_period": "2098-12", "released": {"level": 3.9},
                             "prior_released": {"level": 3.8}},
        },
        "options": {
            "note": "Synthetic. Volume never reveals trade direction; OI is prior-cycle.",
            "symbols": {
                symbol: {"buckets": {"short_dated": {
                    "expiration": "2099-01-04", "dte_at_fetch": 2,
                    "atm_straddle_implied_move_pct": move,
                    "put_call_volume_ratio": pc,
                    "downside_minus_upside_iv": skew,
                    "call_wall_strike_by_oi": call_wall,
                    "put_wall_strike_by_oi": put_wall,
                }}}
                for symbol, move, pc, skew, call_wall, put_wall in (
                    ("SPY", 0.78, 1.14, 0.031, 435.0, 425.0),
                    ("QQQ", 0.96, 0.98, 0.024, 380.0, 370.0),
                    ("IWM", 1.12, 1.31, 0.042, 200.0, 192.0),
                    ("SMH", 1.54, 0.87, 0.019, 220.0, 205.0),
                )
            },
        },
        "positioning": {
            "finra_short_volume": {
                "note": "Reported-facility short volume only. Not net fund flow.",
                "symbols": {
                    "SPY": {"short_volume_ratio": 0.482},
                    "QQQ": {"short_volume_ratio": 0.451},
                    "IWM": {"short_volume_ratio": 0.396},
                    "SMH": {"short_volume_ratio": 0.512},
                    "HYG": {"short_volume_ratio": 0.338},
                    "TLT": {"short_volume_ratio": 0.427},
                },
            },
            "cftc_cot": {
                "note": "Weekly futures positioning, reported with a lag.",
                "markets": {
                    "SP500": {"asset_manager_net_pct_oi": 18.4, "leveraged_money_net_pct_oi": -12.1},
                    "Nasdaq100": {"asset_manager_net_pct_oi": 9.7, "leveraged_money_net_pct_oi": -6.3},
                    "Russell2000": {"asset_manager_net_pct_oi": -4.2, "leveraged_money_net_pct_oi": 3.8},
                    "UST10Y": {"asset_manager_net_pct_oi": 21.6, "leveraged_money_net_pct_oi": -19.4},
                },
            },
        },
        "data_quality": {
            "freshness": {"status": "synthetic"},
            "cache_fallbacks": ["demo_fixture"],
            "redacted_direction_fields": ["fx_commodities_crypto.BTC.chg_pct"],
        },
    }


def build_report() -> str:
    return f"""# {DEMO_DATE} 收盘简报（合成演示 / Synthetic Demo）

{BANNER}
## 核心状态 / Headline state

合成样例：科技与半导体领涨，小盘落后，波动率回落。以上全部为虚构数值，仅用于
展示 viewer 的版式与数据面板。

*Synthetic example: technology and semiconductors lead, small caps lag, implied
volatility eases. All values are invented and exist only to demonstrate layout.*

## 美国市场 / US markets

- 合成标普 500 收 4321.00（+0.62%），合成纳斯达克 +0.94%。
- 等权减市值权为 −0.28pp，表示这一虚构样例中广度弱于指数。
- 合成 VIX 回落至 14.20。

## 全球市场 / Global markets

虚构的日经 +1.18% 领涨亚洲，恒生 −0.74% 落后；欧洲窄幅分化。

## 宏观、利率与央行 / Macro, rates, central banks

虚构的 2 年期下行 3.5bp、10 年期上行 1.5bp，曲线走陡 5bp。此处不存在真实的
央行事件。

## FX、商品与加密 / FX, commodities, crypto

比特币一栏刻意展示 publication gate：该样例的方向字段被屏蔽，因此显示为
「方向未评估」而不是被着色成行情结论。

*The Bitcoin tile deliberately demonstrates the publication gate: its direction
field is redacted, so the viewer renders "direction not assessed" instead of
coloring it as a market conclusion.*

## 期权与仓位 / Options and positioning

合成的短期限隐含波幅与 OI 墙用于演示面板。**成交量不能识别买卖方向**，本项目
不会据此推断做市商库存或 Gamma Exposure；FINRA short volume 不是资金净流入；
CFTC 是周频持仓且有发布滞后。

*Synthetic short-dated implied moves and OI walls populate the panel. Volume
cannot identify trade direction; this project never infers dealer inventory or
gamma exposure from it. FINRA short volume is not net fund flow, and CFTC data
is weekly and lagged.*

## 未来 1–5 日情景 / 1–5 day scenarios

情景一（虚构）：若合成半导体动能延续，宽基指数跟随。
情景二（虚构）：若合成小盘继续落后，广度背离扩大。
这些不是预测，也不是投资建议。

## 数据质量 / Data quality

- freshness status: `synthetic`
- 缓存回退：1（`demo_fixture`）
- 方向字段屏蔽：1

## 来源 / Sources

- 无。本演示不引用任何真实来源。
- None. This demo cites no real source.

---

*Not investment advice. Synthetic fixture generated by `scripts/generate_demo.py`.*
"""


def build_rank() -> dict:
    return {
        "date": DEMO_DATE,
        "mode": DEMO_MODE,
        "synthetic": True,
        "degraded": ["synthetic_demo_fixture"],
        "route": "demo",
        "ranked_points": [
            {"rank": 1, "topic": "Synthetic semiconductor leadership",
             "why": "Demonstrates the ranked-point panel.", "agent": "demo-scout"},
            {"rank": 2, "topic": "Synthetic breadth divergence",
             "why": "Demonstrates a second ranked entry.", "agent": "demo-scout"},
        ],
        "open_questions": ["This fixture contains no real open questions."],
    }


def build_usage() -> dict:
    return {
        "synthetic": True,
        "note": "No model was called to produce this fixture.",
        "glm_route": "demo",
        "total_tokens": 0,
        "providers": [],
        "usage": {"input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0.0},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=Path("out"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    args.data_dir.mkdir(parents=True, exist_ok=True)

    written = []
    report = args.out_dir / f"{BASE}.md"
    report.write_text(build_report(), encoding="utf-8")
    written.append(report)

    for path, payload in (
        (args.data_dir / f"{BASE}.json", build_data()),
        (args.out_dir / f"{BASE}.rank.json", build_rank()),
        (args.out_dir / f"{BASE}.usage.json", build_usage()),
    ):
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        written.append(path)

    for path in written:
        print(f"wrote {path}")
    print("\nSYNTHETIC fixture only. Start the viewer with:")
    print("  python report_viewer.py --out-dir out --bind 127.0.0.1 --port 8080")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
