#!/usr/bin/env python3
"""Polished read-only report dashboard intended for an SSH/IAP tunnel."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse
from zoneinfo import ZoneInfo


CT = ZoneInfo("America/Chicago")
VISIBLE_SUFFIXES = (".md", ".json", ".txt")
REPORT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:\.[\w-]+)*\.md$")
CSS = r"""
:root {
  color-scheme: light;
  --bg:#f3f6fb; --panel:#fff; --panel-2:#f8faff; --ink:#172033;
  --muted:#657087; --line:#e4e9f2; --accent:#2657e8; --accent-2:#12a9a0;
  --accent-soft:#e9efff; --good:#14866d; --warn:#b76e12; --bad:#c43d4b;
  --shadow:0 18px 55px rgba(31,45,75,.09); --radius:22px;
}
*{box-sizing:border-box} html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif}
a{color:inherit}.topbar{height:72px;display:flex;align-items:center;justify-content:space-between;padding:0 max(24px,calc((100vw - 1240px)/2));background:rgba(255,255,255,.82);border-bottom:1px solid rgba(228,233,242,.88);backdrop-filter:blur(18px);position:sticky;top:0;z-index:20}
.brand{display:flex;align-items:center;gap:12px;text-decoration:none;font-weight:760;letter-spacing:-.02em}.brand-mark{width:36px;height:36px;border-radius:11px;background:linear-gradient(145deg,#163db7,#2874ff);display:grid;place-items:center;box-shadow:0 8px 20px rgba(38,87,232,.25)}
.brand-mark span{display:flex;align-items:flex-end;gap:3px;height:18px}.brand-mark i{display:block;width:3px;border-radius:3px;background:white}.brand-mark i:nth-child(1){height:7px;opacity:.7}.brand-mark i:nth-child(2){height:13px}.brand-mark i:nth-child(3){height:18px}.brand small{display:block;font-size:10px;line-height:1;color:var(--muted);font-weight:650;letter-spacing:.13em;text-transform:uppercase;margin-top:2px}
.private-pill,.badge{display:inline-flex;align-items:center;gap:7px;border:1px solid var(--line);background:var(--panel);padding:7px 11px;border-radius:999px;color:var(--muted);font-size:12px;font-weight:650}.pulse{width:7px;height:7px;border-radius:50%;background:#14a981;box-shadow:0 0 0 5px rgba(20,169,129,.11)}
.container{width:min(1240px,calc(100% - 40px));margin:0 auto}.hero{margin-top:34px;padding:36px 38px;border-radius:28px;color:white;position:relative;overflow:hidden;background:linear-gradient(124deg,#14285b 0%,#193f9b 55%,#176f91 100%);box-shadow:0 24px 65px rgba(20,40,91,.2)}
.hero:before{content:"";position:absolute;inset:0;background-image:linear-gradient(rgba(255,255,255,.055) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.055) 1px,transparent 1px);background-size:34px 34px;mask-image:linear-gradient(90deg,black,transparent 76%)}.hero:after{content:"";position:absolute;width:360px;height:360px;border:80px solid rgba(65,229,206,.12);border-radius:50%;right:-110px;top:-185px}
.hero>*{position:relative;z-index:1}.eyebrow{font-size:11px;text-transform:uppercase;letter-spacing:.18em;font-weight:750;color:#a9d8ff}.hero h1{font-size:clamp(29px,4vw,48px);line-height:1.14;letter-spacing:-.035em;max-width:850px;margin:12px 0 14px}.hero p{max-width:790px;color:#dce8ff;font-size:16px;margin:0}.hero-actions{display:flex;gap:12px;align-items:center;margin-top:24px}.button{display:inline-flex;align-items:center;gap:8px;text-decoration:none;background:white;color:#193b90;border-radius:12px;padding:10px 15px;font-weight:750;font-size:13px;box-shadow:0 8px 24px rgba(3,16,50,.18)}.hero-meta{color:#c4d6f8;font-size:12px}
.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:18px 0 30px}.metric{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:18px 19px;box-shadow:0 8px 28px rgba(35,50,82,.045)}.metric-label{display:flex;justify-content:space-between;color:var(--muted);font-size:12px;font-weight:670}.metric-icon{width:27px;height:27px;border-radius:9px;background:var(--accent-soft);color:var(--accent);display:grid;place-items:center;font-size:12px}.metric-value{font-size:24px;font-weight:790;letter-spacing:-.03em;margin-top:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.metric-note{font-size:11px;color:var(--muted);margin-top:2px}
.dashboard-grid{display:grid;grid-template-columns:minmax(0,1.65fr) minmax(290px,.75fr);gap:22px;margin-bottom:45px}.section-title{display:flex;align-items:end;justify-content:space-between;margin:0 0 13px}.section-title h2{font-size:19px;letter-spacing:-.02em;margin:0}.section-title span{font-size:12px;color:var(--muted)}
.report-list{display:grid;gap:12px}.report-card{display:grid;grid-template-columns:105px minmax(0,1fr) auto;align-items:center;gap:18px;padding:20px;background:var(--panel);border:1px solid var(--line);border-radius:18px;text-decoration:none;transition:.18s ease;box-shadow:0 7px 25px rgba(35,50,82,.035)}.report-card:hover{transform:translateY(-2px);border-color:#b8c8fa;box-shadow:var(--shadow)}.date-block{border-right:1px solid var(--line)}.date-day{font-weight:800;font-size:17px;line-height:1.2}.date-year{font-size:11px;color:var(--muted);margin-top:4px}.report-copy{min-width:0}.report-title{font-weight:770;font-size:16px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.report-excerpt{color:var(--muted);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:4px}.report-arrow{width:34px;height:34px;border-radius:11px;background:var(--panel-2);display:grid;place-items:center;color:var(--accent);font-weight:800}
.badge{padding:4px 8px;font-size:10px;margin-right:7px}.badge.official{background:#eaf8f4;color:var(--good);border-color:#c8ece2}.badge.shadow{background:#fff6e7;color:var(--warn);border-color:#f2ddbd}.badge.failed{background:#fff0f2;color:var(--bad);border-color:#f2ccd1}.badge.recovered{background:#eef2ff;color:#4b5fc0;border-color:#d6ddff}
.side-stack{display:grid;gap:16px;align-content:start}.side-card{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:20px;box-shadow:0 7px 25px rgba(35,50,82,.035)}.side-card h3{margin:0 0 13px;font-size:15px}.system-row{display:flex;justify-content:space-between;align-items:center;padding:9px 0;border-top:1px solid var(--line);font-size:12px}.system-row:first-of-type{border:0}.system-row span{color:var(--muted)}.system-row strong{font-size:12px}.good{color:var(--good)}
.artifact details{border-top:1px solid var(--line);padding:11px 0}.artifact details:first-of-type{border:0}.artifact summary{cursor:pointer;font-size:12px;font-weight:720}.artifact-list{display:grid;gap:6px;margin-top:10px}.artifact-link{display:flex;justify-content:space-between;gap:10px;color:var(--muted);font-size:11px;text-decoration:none;padding:6px 0}.artifact-link:hover{color:var(--accent)}.artifact-link span:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.footer{color:var(--muted);font-size:11px;text-align:center;padding:8px 0 35px}

.reader-wrap{width:min(1360px,calc(100% - 40px));margin:28px auto 50px}.reader-head{background:linear-gradient(120deg,#15275a,#214da7);color:white;border-radius:23px;padding:27px 31px;margin-bottom:18px}.reader-head .back{display:inline-flex;color:#c9dafc;text-decoration:none;font-size:12px;margin-bottom:17px}.reader-head h1{font-size:clamp(25px,3.5vw,39px);line-height:1.2;letter-spacing:-.03em;margin:0 0 13px}.reader-meta{display:flex;flex-wrap:wrap;gap:8px;align-items:center;color:#c9dafc;font-size:12px}.reader-meta .badge{background:rgba(255,255,255,.1);border-color:rgba(255,255,255,.18);color:white}
.market-cockpit{display:grid;gap:16px;margin:0 0 22px}.cockpit-panel{background:var(--panel);border:1px solid var(--line);border-radius:20px;padding:21px;box-shadow:0 8px 30px rgba(35,50,82,.045);min-width:0}.cockpit-title{display:flex;align-items:start;justify-content:space-between;gap:14px;margin-bottom:14px}.cockpit-title h2{font-size:17px;line-height:1.3;margin:0;letter-spacing:-.02em}.cockpit-title p{font-size:10px;color:var(--muted);margin:3px 0 0}.source-chip{flex:0 0 auto;border:1px solid var(--line);border-radius:999px;padding:4px 8px;color:var(--muted);font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.05em}
.market-strip{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}.market-strip.cross{grid-template-columns:repeat(6,1fr)}.quote-card{padding:15px;border-radius:15px;background:var(--panel-2);border:1px solid var(--line);min-width:0}.quote-name{font-size:10px;color:var(--muted);font-weight:720;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.quote-last{font-size:20px;font-weight:800;letter-spacing:-.03em;margin-top:5px}.quote-change{display:inline-flex;align-items:center;gap:4px;margin-top:2px;font-size:11px;font-weight:760}.up{color:var(--good)}.down{color:var(--bad)}.flat{color:var(--muted)}
.cockpit-split{display:grid;grid-template-columns:minmax(0,1.65fr) minmax(280px,.75fr);gap:16px}.sector-map{display:grid;grid-template-columns:repeat(4,1fr);grid-auto-rows:82px;gap:7px}.heat{border-radius:12px;padding:11px;color:white;display:flex;flex-direction:column;justify-content:space-between;min-width:0;box-shadow:inset 0 0 0 1px rgba(255,255,255,.11)}.heat strong{font-size:14px}.heat span{font-size:11px;font-weight:750}.heat em{font-style:normal;font-size:9px;opacity:.78}.heat-pos-1{background:#277c70}.heat-pos-2{background:#17866f}.heat-pos-3{background:#08775f}.heat-pos-4{background:#00634f}.heat-neg-1{background:#98636b}.heat-neg-2{background:#a94f5c}.heat-neg-3{background:#b63d4d}.heat-neg-4{background:#962b3b}.heat-flat{background:#617086
}
.pulse-stack{display:grid;gap:9px}.pulse-card{background:var(--panel-2);border:1px solid var(--line);border-radius:14px;padding:13px}.pulse-card span{display:block;color:var(--muted);font-size:10px}.pulse-card strong{display:block;font-size:17px;margin-top:2px}.pulse-card small{display:block;color:var(--muted);font-size:9px;margin-top:2px}.quality-row{display:flex;flex-wrap:wrap;gap:7px;margin-top:12px}.quality-flag{font-size:9px;color:var(--muted);background:var(--panel-2);border:1px solid var(--line);padding:5px 8px;border-radius:999px}.quality-flag.good-flag{color:var(--good);background:#eaf8f4;border-color:#c8ece2}
.world-grid{display:grid;gap:9px}.world-row{display:grid;grid-template-columns:105px minmax(150px,1fr) 56px;gap:10px;align-items:center;font-size:10px}.world-label{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.world-track{height:14px;background:linear-gradient(90deg,transparent 49.6%,var(--line) 49.6%,var(--line) 50.4%,transparent 50.4%);position:relative}.world-bar{position:absolute;top:2px;height:10px;border-radius:5px}.world-bar.positive{left:50%;background:var(--good)}.world-bar.negative{right:50%;background:var(--bad)}.world-value{text-align:right;font-weight:760}
.macro-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:9px}.macro-card{background:var(--panel-2);border:1px solid var(--line);border-radius:14px;padding:13px}.macro-card span{display:block;color:var(--muted);font-size:9px}.macro-card strong{font-size:17px}.macro-card small{display:block;color:var(--muted);font-size:9px;margin-top:3px}
.option-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:9px}.option-card{background:var(--panel-2);border:1px solid var(--line);border-radius:15px;padding:14px}.option-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}.option-head strong{font-size:14px}.option-head span{font-size:9px;color:var(--muted)}.option-move{font-size:22px;font-weight:800;letter-spacing:-.03em}.option-move small{font-size:9px;color:var(--muted);font-weight:650}.option-facts{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-top:10px}.option-fact{border-top:1px solid var(--line);padding-top:6px}.option-fact span{display:block;color:var(--muted);font-size:8px}.option-fact strong{font-size:10px}
.position-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.activity-list{display:grid;gap:9px}.activity-row{display:grid;grid-template-columns:42px 1fr 45px;gap:8px;align-items:center;font-size:10px}.activity-track{height:8px;border-radius:5px;background:var(--line);overflow:hidden}.activity-fill{height:100%;border-radius:5px;background:linear-gradient(90deg,#536fd3,#8c68c9)}.cot-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}.cot-card{border:1px solid var(--line);border-radius:12px;padding:11px;background:var(--panel-2)}.cot-card>strong{font-size:11px}.cot-pair{display:flex;justify-content:space-between;gap:7px;margin-top:7px}.cot-pair span{font-size:8px;color:var(--muted)}.cot-pair b{display:block;font-size:10px}.guard-note{display:flex;align-items:start;gap:9px;margin-top:13px;padding:10px 12px;border-radius:12px;background:#fff7e9;border:1px solid #f1dfbc;color:#76511d;font-size:9px}.guard-note strong{flex:0 0 auto}.longform-head{display:flex;align-items:end;justify-content:space-between;max-width:1210px;margin:30px auto 12px}.longform-head h2{font-size:19px;margin:0}.longform-head span{font-size:10px;color:var(--muted)}
.reader-grid{display:grid;grid-template-columns:230px minmax(0,790px) 190px;gap:18px;justify-content:center;align-items:start}.toc,.reader-side{position:sticky;top:91px;background:var(--panel);border:1px solid var(--line);border-radius:17px;padding:17px}.toc-title{font-size:11px;text-transform:uppercase;letter-spacing:.11em;font-weight:780;color:var(--muted);margin-bottom:10px}.toc a{display:block;text-decoration:none;color:var(--muted);font-size:11px;line-height:1.45;padding:6px 8px;border-left:2px solid transparent}.toc a:hover{color:var(--accent);border-color:var(--accent);background:var(--accent-soft)}
.article{background:var(--panel);border:1px solid var(--line);border-radius:20px;padding:clamp(24px,5vw,55px);box-shadow:var(--shadow);min-width:0}.article h1{font-size:31px;line-height:1.25;letter-spacing:-.03em;margin:0 0 22px}.article h2{font-size:22px;line-height:1.35;letter-spacing:-.02em;margin:42px 0 15px;padding-top:4px;border-top:1px solid var(--line)}.article h3{font-size:17px;margin:28px 0 10px}.article p{margin:0 0 16px;color:#303b50}.article strong{color:var(--ink)}.article a{color:var(--accent);text-decoration-color:#b8c8fa;text-underline-offset:3px;word-break:break-all}.article ul,.article ol{padding-left:22px;margin:0 0 17px}.article li{margin:6px 0}.article blockquote{border-left:3px solid var(--accent);background:var(--accent-soft);padding:12px 15px;margin:18px 0;color:#3b4e83}.article code{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;background:#edf1f7;padding:2px 5px;border-radius:5px}.article pre,.json-view{overflow:auto;background:#121927;color:#d9e4f8;border-radius:14px;padding:18px;font:12px/1.65 ui-monospace,SFMono-Regular,Menlo,monospace}.article table{width:100%;border-collapse:collapse;margin:18px 0;font-size:12px}.article th{background:var(--panel-2);text-align:left}.article th,.article td{padding:10px;border:1px solid var(--line)}
.reader-side .mini-stat{padding:10px 0;border-top:1px solid var(--line)}.reader-side .mini-stat:first-of-type{border:0}.mini-stat span{display:block;color:var(--muted);font-size:10px}.mini-stat strong{font-size:13px}.raw-link{display:block;margin-top:12px;text-align:center;text-decoration:none;color:var(--accent);background:var(--accent-soft);border-radius:10px;padding:8px;font-size:11px;font-weight:700}
.empty{background:var(--panel);border:1px dashed #cbd3e1;border-radius:18px;padding:40px;text-align:center;color:var(--muted)}
@media(max-width:980px){.metrics{grid-template-columns:repeat(2,1fr)}.dashboard-grid{grid-template-columns:1fr}.reader-grid{grid-template-columns:minmax(0,800px)}.toc,.reader-side{display:none}.market-strip,.market-strip.cross{grid-template-columns:repeat(3,1fr)}.cockpit-split,.position-grid{grid-template-columns:1fr}.option-grid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:620px){.container,.reader-wrap{width:min(100% - 24px,1240px)}.topbar{padding:0 15px}.private-pill{font-size:0}.private-pill:after{content:"IAP";font-size:11px}.hero{padding:28px 23px;margin-top:18px}.metrics{grid-template-columns:1fr 1fr;gap:9px}.metric{padding:15px}.metric-value{font-size:19px}.report-card{grid-template-columns:78px minmax(0,1fr);padding:16px;gap:12px}.report-arrow{display:none}.reader-head{padding:23px}.article{padding:24px 20px}.cockpit-panel{padding:16px}.market-strip,.market-strip.cross{grid-template-columns:repeat(2,1fr)}.sector-map{grid-template-columns:repeat(2,1fr)}.option-grid,.macro-grid{grid-template-columns:1fr 1fr}.world-row{grid-template-columns:82px minmax(110px,1fr) 48px}.cot-grid{grid-template-columns:1fr}.longform-head{display:block}.longform-head span{display:block;margin-top:4px}}
@media(prefers-color-scheme:dark){:root{color-scheme:dark;--bg:#0d1320;--panel:#141c2b;--panel-2:#192335;--ink:#eef3fc;--muted:#9aa8bd;--line:#263248;--accent:#7ba2ff;--accent-2:#39c9bd;--accent-soft:#1d2b4d;--shadow:0 18px 55px rgba(0,0,0,.25)}.topbar{background:rgba(14,20,32,.86)}.article p{color:#cbd5e6}.article code{background:#202b3d}.report-card:hover{border-color:#4f68a8}.badge.official{background:#14372f;border-color:#245b4d}.badge.shadow{background:#3a2b14;border-color:#66502a}.badge.failed{background:#3c1c25;border-color:#6a3040}.quality-flag.good-flag{background:#14372f;border-color:#245b4d}.guard-note{background:#302717;border-color:#544325;color:#e5c992}}
"""


def visible_files(out_dir: Path) -> list[Path]:
    if not out_dir.is_dir():
        return []
    files = [
        path for path in out_dir.iterdir()
        if path.is_file() and path.name.endswith(VISIBLE_SUFFIXES)
        and not path.name.startswith(".")
    ]
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)


def report_files(out_dir: Path) -> list[Path]:
    return [
        path for path in visible_files(out_dir)
        if REPORT_RE.match(path.name) and not path.name.endswith(".full.md")
    ]


def resolve_visible(out_dir: Path, name: str) -> Path | None:
    if Path(name).name != name or not name.endswith(VISIBLE_SUFFIXES):
        return None
    path = (out_dir / name).resolve()
    try:
        path.relative_to(out_dir.resolve())
    except ValueError:
        return None
    return path if path.is_file() else None


def _read_json(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _artifact_base(report: Path) -> str:
    return report.name.removesuffix(".md")


def _usage_for(report: Path) -> tuple[dict, Path | None]:
    base = _artifact_base(report)
    for suffix in (".usage.json", ".run.json"):
        candidate = report.parent / f"{base}{suffix}"
        if candidate.is_file():
            return _read_json(candidate), candidate
    return {}, None


def _rank_for(report: Path) -> dict:
    return _read_json(report.parent / f"{_artifact_base(report)}.rank.json")


def _data_for(report: Path) -> tuple[dict, Path | None]:
    """Find the gated deterministic data paired with a published report."""
    base = _artifact_base(report)
    bases = [base]
    if base.endswith(".recovered"):
        bases.append(base.removesuffix(".recovered"))
    data_dir = report.parent.parent / "data"
    for candidate_base in bases:
        candidate = data_dir / f"{candidate_base}.json"
        if candidate.is_file():
            return _read_json(candidate), candidate
    return {}, None


def _plain_excerpt(text: str, limit: int = 190) -> str:
    lines = []
    for raw in text.splitlines():
        if raw.lstrip().startswith("#"):
            continue
        value = re.sub(r"^[#>*\-\d.)\s]+", "", raw).strip()
        if value and not value.startswith("http"):
            lines.append(re.sub(r"[`*_\[\]]", "", value))
        if len(" ".join(lines)) >= limit:
            break
    joined = " ".join(lines)
    return joined[:limit].rstrip() + ("…" if len(joined) > limit else "")


def _tokens(usage: dict) -> int:
    total = 0
    for provider in (usage.get("providers") or {}).values():
        if isinstance(provider, dict):
            detail = provider.get("usage") or {}
            total += int(detail.get("total_tokens") or 0)
    if total:
        return total
    return int((usage.get("usage") or {}).get("total_tokens") or usage.get("total_tokens") or 0)


def _cost(usage: dict) -> float | None:
    for key in ("estimated_cost_usd", "total_cost_usd", "cost_usd"):
        value = usage.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _route(usage: dict) -> str:
    selected = (usage.get("glm_route") or {}).get("selected")
    if selected == "nvidia":
        return "NVIDIA GLM-5.2 → Kimi K3"
    if selected == "zai-paid":
        return "Z.AI GLM-5.2 → Kimi K3"
    models = []
    for name, provider in (usage.get("providers") or {}).items():
        if isinstance(provider, dict):
            models.append(str(provider.get("model") or name))
    return " → ".join(models) or "Claude research workflow"


def report_summary(path: Path) -> dict:
    text = path.read_text(errors="replace")
    heading = next((line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("# ")), path.stem)
    usage, usage_path = _usage_for(path)
    rank = _rank_for(path)
    data, data_path = _data_for(path)
    name = path.name
    mode = "盘前" if ".preopen" in name else "收盘" if ".close" in name else "盘中" if ".intraday" in name else "日报"
    kind = "shadow" if "dual-shadow" in name or "影子" in heading else "recovered" if "recovered" in name else "official"
    date_text = name[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", name) else "—"
    modified = dt.datetime.fromtimestamp(path.stat().st_mtime, CT)
    return {
        "path": path, "name": name, "title": heading, "excerpt": _plain_excerpt(text),
        "date": date_text, "mode": mode, "kind": kind, "modified": modified,
        "chars": len(text), "usage": usage, "usage_path": usage_path,
        "rank": rank, "tokens": _tokens(usage), "cost": _cost(usage), "route": _route(usage),
        "degraded": bool(rank.get("degraded")), "data": data, "data_path": data_path,
    }


def _inline(text: str) -> str:
    value = html.escape(text, quote=False)
    value = re.sub(r"`([^`]+)`", r"<code>\1</code>", value)
    value = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        lambda m: f'<a href="{html.escape(html.unescape(m.group(2)), quote=True)}" target="_blank" rel="noopener noreferrer">{m.group(1)}</a>',
        value,
    )
    value = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", value)
    return value


def markdown_to_html(source: str) -> tuple[str, list[tuple[str, str]]]:
    """Render the report's Markdown subset after escaping all raw HTML."""
    lines = source.splitlines()
    output: list[str] = []
    toc: list[tuple[str, str]] = []
    used: dict[str, int] = {}
    index = 0
    in_code = False
    code_lines: list[str] = []

    def slug_for(title: str) -> str:
        base = re.sub(r"[^\w\u3400-\u9fff]+", "-", title.lower()).strip("-") or "section"
        used[base] = used.get(base, 0) + 1
        return base if used[base] == 1 else f"{base}-{used[base]}"

    while index < len(lines):
        line = lines[index]
        if line.startswith("```"):
            if in_code:
                output.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
                code_lines = []
                in_code = False
            else:
                in_code = True
            index += 1
            continue
        if in_code:
            code_lines.append(line)
            index += 1
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            level = len(heading.group(1))
            title = heading.group(2).strip()
            slug = slug_for(title)
            output.append(f'<h{level} id="{slug}">{_inline(title)}</h{level}>')
            if level == 2:
                toc.append((title, slug))
            index += 1
            continue
        if line.startswith("|") and index + 1 < len(lines) and re.match(r"^\s*\|?[\s:|-]+\|\s*$", lines[index + 1]):
            headers = [cell.strip() for cell in line.strip().strip("|").split("|")]
            output.append("<table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in headers) + "</tr></thead><tbody>")
            index += 2
            while index < len(lines) and lines[index].startswith("|"):
                cells = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
                output.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
                index += 1
            output.append("</tbody></table>")
            continue
        if re.match(r"^[-*]\s+", line):
            output.append("<ul>")
            while index < len(lines) and re.match(r"^[-*]\s+", lines[index]):
                item_text = re.sub(r"^[-*]\s+", "", lines[index])
                output.append(f"<li>{_inline(item_text)}</li>")
                index += 1
            output.append("</ul>")
            continue
        if re.match(r"^\d+[.)]\s+", line):
            output.append("<ol>")
            while index < len(lines) and re.match(r"^\d+[.)]\s+", lines[index]):
                item_text = re.sub(r"^\d+[.)]\s+", "", lines[index])
                output.append(f"<li>{_inline(item_text)}</li>")
                index += 1
            output.append("</ol>")
            continue
        if line.startswith("> "):
            output.append(f"<blockquote>{_inline(line[2:])}</blockquote>")
        elif re.match(r"^\s*[-*_]{3,}\s*$", line):
            output.append("<hr>")
        elif line.strip():
            output.append(f"<p>{_inline(line.strip())}</p>")
        index += 1
    if in_code:
        output.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
    return "\n".join(output), toc


def _badge(summary: dict) -> str:
    labels = {"official": "正式报告", "shadow": "影子验证", "recovered": "恢复报告"}
    return f'<span class="badge {summary["kind"]}">{labels[summary["kind"]]}</span>'


def _compact_number(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:,}"


def _format_market_number(value, decimals: int | None = None) -> str:
    if not isinstance(value, (int, float)):
        return "—"
    if decimals is None:
        decimals = 0 if abs(value) >= 1000 else 2
    return f"{value:,.{decimals}f}"


def _direction(value) -> tuple[str, str]:
    if not isinstance(value, (int, float)) or abs(value) < 0.00005:
        return "flat", "•"
    return ("up", "▲") if value > 0 else ("down", "▼")


def _quote_card(label: str, item: dict) -> str:
    last = _format_market_number(item.get("last"))
    usable = item.get("direction_usable", True) is not False
    pct = item.get("chg_pct") if usable else None
    direction, arrow = _direction(pct)
    change = f"{arrow} {abs(pct):.2f}%" if isinstance(pct, (int, float)) else "方向未评估"
    return (
        f'<div class="quote-card"><div class="quote-name">{html.escape(label)}</div>'
        f'<div class="quote-last">{last}</div><div class="quote-change {direction}">{change}</div></div>'
    )


def _heat_class(value) -> str:
    if not isinstance(value, (int, float)) or abs(value) < 0.1:
        return "heat-flat"
    level = 1 if abs(value) < 0.5 else 2 if abs(value) < 1 else 3 if abs(value) < 2 else 4
    return f"heat-{'pos' if value > 0 else 'neg'}-{level}"


def _visual_overview_html(data: dict) -> str:
    """Build a no-JavaScript visual layer from publication-gated market data."""
    if not data:
        return '<div class="empty">这份历史报告没有配套的确定性数据文件，因此只显示文字原稿。</div>'

    index_labels = {
        "SP500": "标普 500", "Nasdaq": "纳斯达克", "Dow": "道琼斯",
        "Russell2000": "罗素 2000", "VIX": "VIX 波动率",
    }
    indices = data.get("indices") or {}
    index_cards = "".join(
        _quote_card(label, indices.get(key) or {}) for key, label in index_labels.items()
        if isinstance(indices.get(key), dict)
    )

    sector_labels = {
        "XLK": "科技", "XLF": "金融", "XLE": "能源", "XLV": "医疗",
        "XLI": "工业", "XLY": "可选消费", "XLP": "必选消费", "XLU": "公用事业",
        "XLB": "材料", "XLRE": "地产", "XLC": "通信", "SMH": "半导体",
    }
    sectors = data.get("sectors") or {}
    heat_tiles = []
    for symbol, label in sector_labels.items():
        item = sectors.get(symbol) or {}
        pct = item.get("chg_pct")
        pct_text = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else "未评估"
        heat_tiles.append(
            f'<div class="heat {_heat_class(pct)}"><strong>{html.escape(label)}</strong>'
            f'<span>{pct_text}</span><em>{symbol}</em></div>'
        )

    breadth = data.get("breadth_proxy") or {}
    rates = data.get("rates") or {}
    breadth_value = breadth.get("rsp_minus_spy_chg_pct")
    breadth_text = f"{breadth_value:+.2f} pp" if isinstance(breadth_value, (int, float)) else "—"
    rate_cards = [
        ("等权 − 市值权", breadth_text, "正值代表市场广度相对改善"),
        ("美国 2 年期", f'{_format_market_number((rates.get("DGS2") or {}).get("last"))}%', f'{_format_market_number((rates.get("DGS2") or {}).get("chg_bp"), 1)} bp'),
        ("美国 10 年期", f'{_format_market_number((rates.get("DGS10") or {}).get("last"))}%', f'{_format_market_number((rates.get("DGS10") or {}).get("chg_bp"), 1)} bp'),
        ("10Y − 2Y 曲线", f'{_format_market_number((rates.get("T10Y2Y") or {}).get("last"))}%', f'{_format_market_number((rates.get("T10Y2Y") or {}).get("chg_bp"), 1)} bp'),
    ]
    pulse_cards = "".join(
        f'<div class="pulse-card"><span>{label}</span><strong>{value}</strong><small>{note}</small></div>'
        for label, value, note in rate_cards
    )

    global_labels = {
        "EuroStoxx50": "欧元区蓝筹", "FTSE100": "英国 FTSE", "DAX": "德国 DAX",
        "Nikkei225": "日经 225", "HangSeng": "恒生指数", "Shanghai": "上证指数",
        "IndiaNifty50": "印度 Nifty",
    }
    global_data = data.get("global_indices") or {}
    valid_global = [
        (key, label, (global_data.get(key) or {}).get("chg_pct"))
        for key, label in global_labels.items() if isinstance(global_data.get(key), dict)
    ]
    scale = max([abs(item[2]) for item in valid_global if isinstance(item[2], (int, float))] or [1])
    world_rows = []
    for _, label, pct in valid_global:
        if not isinstance(pct, (int, float)):
            continue
        width = min(50.0, abs(pct) / scale * 49.0)
        direction = "positive" if pct >= 0 else "negative"
        tone, _ = _direction(pct)
        world_rows.append(
            f'<div class="world-row"><span class="world-label">{html.escape(label)}</span>'
            f'<div class="world-track"><i class="world-bar {direction}" style="width:{width:.2f}%"></i></div>'
            f'<strong class="world-value {tone}">{pct:+.2f}%</strong></div>'
        )

    macro = data.get("macro") or {}
    macro_specs = [
        ("CPI", "CPI 同比", "yoy_pct", "%"),
        ("CoreCPI", "核心 CPI 同比", "yoy_pct", "%"),
        ("NonfarmPayrolls", "非农新增", "mom_diff", "K"),
        ("Unemployment", "失业率", "level", "%"),
    ]
    macro_cards = []
    for key, label, value_key, suffix in macro_specs:
        item = macro.get(key) or {}
        released = item.get("released") or {}
        prior = item.get("prior_released") or {}
        value, prior_value = released.get(value_key), prior.get(value_key)
        current_text = f"{value:.1f}{suffix}" if isinstance(value, (int, float)) else "—"
        prior_text = f"前值 {prior_value:.1f}{suffix}" if isinstance(prior_value, (int, float)) else "前值 —"
        period = str(item.get("ref_period") or "")[:7]
        macro_cards.append(
            f'<div class="macro-card"><span>{html.escape(label)} · {period}</span>'
            f'<strong>{current_text}</strong><small>{prior_text}</small></div>'
        )

    cross_specs = [
        ("美元指数", (data.get("fx_commodities_crypto") or {}).get("DXY") or {}),
        ("美元/日元", (data.get("fx_commodities_crypto") or {}).get("USDJPY") or {}),
        ("黄金", (data.get("fx_commodities_crypto") or {}).get("Gold") or {}),
        ("WTI 原油", (data.get("fx_commodities_crypto") or {}).get("WTI") or {}),
        ("比特币", (data.get("fx_commodities_crypto") or {}).get("BTC") or {}),
        ("高收益债 HYG", (data.get("factors_credit") or {}).get("HighYield") or {}),
    ]
    cross_cards = "".join(_quote_card(label, item) for label, item in cross_specs if item)

    option_symbols = ((data.get("options") or {}).get("symbols") or {})
    option_cards = []
    for symbol in ("SPY", "QQQ", "IWM", "SMH"):
        short = (((option_symbols.get(symbol) or {}).get("buckets") or {}).get("short_dated") or {})
        if not short:
            continue
        move = short.get("atm_straddle_implied_move_pct")
        pc = short.get("put_call_volume_ratio")
        skew = short.get("downside_minus_upside_iv")
        expiration = str(short.get("expiration") or "—")
        option_cards.append(f'''<div class="option-card"><div class="option-head"><strong>{symbol}</strong><span>{expiration} · {short.get("dte_at_fetch", "—")} DTE</span></div>
<div class="option-move">{_format_market_number(move, 2)}% <small>隐含波动区间</small></div><div class="option-facts">
<div class="option-fact"><span>PUT / CALL 成交量</span><strong>{_format_market_number(pc, 2)}</strong></div>
<div class="option-fact"><span>下行 − 上行 IV</span><strong>{_format_market_number(skew, 3)}</strong></div>
<div class="option-fact"><span>CALL WALL</span><strong>{_format_market_number(short.get("call_wall_strike_by_oi"), 1)}</strong></div>
<div class="option-fact"><span>PUT WALL</span><strong>{_format_market_number(short.get("put_wall_strike_by_oi"), 1)}</strong></div></div></div>''')

    positioning = data.get("positioning") or {}
    finra = ((positioning.get("finra_short_volume") or {}).get("symbols") or {})
    activity_rows = []
    for symbol in ("SPY", "QQQ", "IWM", "SMH", "HYG", "TLT"):
        ratio = (finra.get(symbol) or {}).get("short_volume_ratio")
        if not isinstance(ratio, (int, float)):
            continue
        activity_rows.append(
            f'<div class="activity-row"><strong>{symbol}</strong><div class="activity-track">'
            f'<div class="activity-fill" style="width:{max(0, min(100, ratio * 100)):.1f}%"></div></div>'
            f'<span>{ratio * 100:.1f}%</span></div>'
        )
    cftc = ((positioning.get("cftc_cot") or {}).get("markets") or {})
    cot_cards = []
    for key, label in (("SP500", "标普 500"), ("Nasdaq100", "纳指 100"), ("Russell2000", "罗素 2000"), ("UST10Y", "10 年美债")):
        item = cftc.get(key) or {}
        if not item:
            continue
        asset = item.get("asset_manager_net_pct_oi")
        leveraged = item.get("leveraged_money_net_pct_oi")
        cot_cards.append(
            f'<div class="cot-card"><strong>{html.escape(label)}</strong><div class="cot-pair">'
            f'<span>资产管理<b>{_format_market_number(asset, 1)}%</b></span>'
            f'<span>杠杆资金<b>{_format_market_number(leveraged, 1)}%</b></span></div></div>'
        )

    quality = data.get("data_quality") or {}
    freshness = quality.get("freshness") or {}
    run_context = data.get("run_context") or {}
    cache_count = len(quality.get("cache_fallbacks") or [])
    redacted_count = len(quality.get("redacted_direction_fields") or [])
    session_text = "交易时段" if run_context.get("is_session") else "非交易时段快照"
    quality_flags = (
        f'<span class="quality-flag good-flag">Publication gate: {html.escape(str(freshness.get("status") or "unknown"))}</span>'
        f'<span class="quality-flag">{session_text}</span><span class="quality-flag">缓存回退 {cache_count}</span>'
        f'<span class="quality-flag">方向字段屏蔽 {redacted_count}</span>'
    )

    return f'''<section class="market-cockpit" aria-label="市场可视化总览">
<section class="cockpit-panel"><div class="cockpit-title"><div><h2>美国市场一览</h2><p>先看方向、波动与市场宽度，再进入文字解释</p></div><span class="source-chip">gated data</span></div><div class="market-strip">{index_cards}</div><div class="quality-row">{quality_flags}</div></section>
<div class="cockpit-split"><section class="cockpit-panel"><div class="cockpit-title"><div><h2>板块热力图</h2><p>颜色代表日涨跌；等面积展示，不代表板块市值</p></div><span class="source-chip">US sectors</span></div><div class="sector-map">{"".join(heat_tiles)}</div></section>
<section class="cockpit-panel"><div class="cockpit-title"><div><h2>利率与市场广度</h2><p>利率数据的缓存状态已在质量标签披露</p></div></div><div class="pulse-stack">{pulse_cards}</div></section></div>
<div class="cockpit-split"><section class="cockpit-panel"><div class="cockpit-title"><div><h2>全球市场相对表现</h2><p>以 0% 为中轴，长度表示当日变动绝对值</p></div><span class="source-chip">Americas · EMEA · APAC</span></div><div class="world-grid">{"".join(world_rows)}</div></section>
<section class="cockpit-panel"><div class="cockpit-title"><div><h2>宏观脉搏</h2><p>最近已公布值，不等同于下一次市场预期</p></div><span class="source-chip">FRED</span></div><div class="macro-grid">{"".join(macro_cards)}</div></section></div>
<section class="cockpit-panel"><div class="cockpit-title"><div><h2>跨资产监控</h2><p>被门控的数据只显示价格，“方向未评估”不会被着色成行情结论</p></div><span class="source-chip">FX · commodities · credit</span></div><div class="market-strip cross">{cross_cards}</div></section>
<section class="cockpit-panel"><div class="cockpit-title"><div><h2>期权活动代理</h2><p>短期限近 ATM 聚合；用于观察保护需求、隐含区间和 OI 集中位置</p></div><span class="source-chip">best effort · no SLA</span></div><div class="option-grid">{"".join(option_cards)}</div><div class="guard-note"><strong>解释边界</strong><span>成交量不揭示买卖方向，未推断做市商库存或 Gamma Exposure；Open Interest 属于上一清算周期。</span></div></section>
<section class="cockpit-panel"><div class="cockpit-title"><div><h2>仓位与交易活动</h2><p>把日频活动代理与周频机构仓位分开阅读</p></div><span class="source-chip">FINRA · CFTC</span></div><div class="position-grid"><div><div class="toc-title">FINRA SHORT-VOLUME RATIO</div><div class="activity-list">{"".join(activity_rows)}</div></div><div><div class="toc-title">CFTC NET % OF OPEN INTEREST · WEEKLY</div><div class="cot-grid">{"".join(cot_cards)}</div></div></div><div class="guard-note"><strong>不是资金净流入</strong><span>FINRA 指标仅覆盖场外/报告设施的卖空成交活动；CFTC 是周频期货持仓。二者都不能当作 ETF 申赎或全市场资金流。</span></div></section>
</section>'''


def _page(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark">
<title>{html.escape(title)} · Market Brief</title><style>{CSS}</style></head><body>
<header class="topbar"><a class="brand" href="/"><span class="brand-mark"><span><i></i><i></i><i></i></span></span><span>Market Brief<small>Daily Intelligence</small></span></a><span class="private-pill"><i class="pulse"></i>只读 · 私有 IAP</span></header>
{body}<footer class="footer">Read-only research workspace · America/Chicago · Not investment advice</footer></body></html>"""


def dashboard_html(out_dir: Path) -> str:
    reports = [report_summary(path) for path in report_files(out_dir)]
    latest = reports[0] if reports else None
    artifacts = [path for path in visible_files(out_dir) if path not in {item["path"] for item in reports}]
    if latest:
        cost = f'${latest["cost"]:.3f}' if latest["cost"] is not None else "—"
        token_text = _compact_number(latest["tokens"]) if latest["tokens"] else "—"
        hero = f"""<section class="hero"><div class="eyebrow">Latest Market Intelligence · {latest['date']}</div>
<h1>{html.escape(latest['title'])}</h1><p>{html.escape(latest['excerpt'])}</p><div class="hero-actions"><a class="button" href="/report/{quote(latest['name'])}">阅读完整报告 <span>→</span></a><span class="hero-meta">{latest['mode']} · 更新于 {latest['modified'].strftime('%H:%M CT')}</span></div></section>"""
        metrics = (
            ("报告状态", "影子验证" if latest["kind"] == "shadow" else "已发布", "通过确定性 publication gate", "✓"),
            ("估算成本", cost, "以供应商最终账单为准", "$"),
            ("模型用量", token_text, "GLM research + Kimi synthesis", "T"),
            ("研究路由", latest["route"], "自动记录回退与失败", "↗"),
        )
    else:
        hero = '<section class="hero"><div class="eyebrow">Market Intelligence</div><h1>等待第一份市场报告</h1><p>定时器会在下一个有效市场窗口自动生成。</p></section>'
        metrics = (("报告状态", "等待中", "暂无已发布报告", "○"),) * 4

    metric_html = "".join(
        f'<div class="metric"><div class="metric-label"><span>{label}</span><i class="metric-icon">{icon}</i></div><div class="metric-value">{html.escape(value)}</div><div class="metric-note">{note}</div></div>'
        for label, value, note, icon in metrics
    )
    cards = []
    for item in reports[:12]:
        date_value = dt.date.fromisoformat(item["date"])
        cards.append(f"""<a class="report-card" href="/report/{quote(item['name'])}"><div class="date-block"><div class="date-day">{date_value.strftime('%m · %d')}</div><div class="date-year">{date_value.year} · {item['mode']}</div></div><div class="report-copy"><div class="report-title">{_badge(item)}{html.escape(item['title'])}</div><div class="report-excerpt">{html.escape(item['excerpt'])}</div></div><span class="report-arrow">→</span></a>""")
    report_section = "".join(cards) if cards else '<div class="empty">暂无报告</div>'

    grouped = {"研究与评分": [], "运行与用量": [], "失败与状态": []}
    for path in artifacts:
        bucket = "失败与状态" if "FAILED" in path.name or path.name.startswith("status") or path.suffix == ".txt" else "运行与用量" if "usage" in path.name or "run" in path.name or "context" in path.name else "研究与评分"
        grouped[bucket].append(path)
    artifact_html = ""
    for label, paths in grouped.items():
        if not paths:
            continue
        links = "".join(f'<a class="artifact-link" href="/file/{quote(path.name)}"><span>{html.escape(path.name)}</span><span>{path.stat().st_size / 1024:.1f} KB</span></a>' for path in paths[:15])
        artifact_html += f'<details><summary>{label} · {len(paths)}</summary><div class="artifact-list">{links}</div></details>'

    body = f"""<main class="container">{hero}<section class="metrics">{metric_html}</section><div class="dashboard-grid"><section><div class="section-title"><h2>最近报告</h2><span>{len(reports)} 份已保存</span></div><div class="report-list">{report_section}</div></section><aside class="side-stack"><div class="side-card"><h3>系统运行状态</h3><div class="system-row"><span>市场日历</span><strong class="good">NYSE aware</strong></div><div class="system-row"><span>盘前窗口</span><strong>07:40–08:25 CT</strong></div><div class="system-row"><span>收盘窗口</span><strong>15:15–16:45 CT</strong></div><div class="system-row"><span>访问方式</span><strong>IAP / SSH only</strong></div></div><div class="side-card artifact"><h3>审计与原始文件</h3>{artifact_html or '<span class="metric-note">暂无辅助文件</span>'}</div></aside></div></main>"""
    return _page("市场情报", body)


def report_html(summary: dict) -> str:
    source = summary["path"].read_text(errors="replace")
    source = re.sub(r"^#\s+[^\n]+\n?", "", source, count=1)
    article, toc = markdown_to_html(source)
    toc_html = "".join(f'<a href="#{html.escape(slug)}">{html.escape(title)}</a>' for title, slug in toc)
    cost = f'${summary["cost"]:.3f}' if summary["cost"] is not None else "—"
    tokens = _compact_number(summary["tokens"]) if summary["tokens"] else "—"
    quality = "降级标记" if summary["degraded"] else "校验通过"
    visual = _visual_overview_html(summary.get("data") or {})
    data_state = "已连接" if summary.get("data_path") else "无配套快照"
    body = f"""<main class="reader-wrap"><section class="reader-head"><a class="back" href="/">← 返回仪表盘</a><h1>{html.escape(summary['title'])}</h1><div class="reader-meta">{_badge(summary)}<span>{summary['date']}</span><span>·</span><span>{summary['mode']}</span><span>·</span><span>{summary['chars']:,} characters</span></div></section>{visual}<div class="longform-head"><div><h2>深度解读与证据链</h2><span>可视化帮助扫描，下面保留完整论证、来源、确认条件与失效条件</span></div></div><div class="reader-grid"><nav class="toc"><div class="toc-title">报告目录</div>{toc_html or '<span class="metric-note">无章节目录</span>'}</nav><article class="article">{article}</article><aside class="reader-side"><div class="toc-title">RUN SUMMARY</div><div class="mini-stat"><span>模型路由</span><strong>{html.escape(summary['route'])}</strong></div><div class="mini-stat"><span>Token</span><strong>{tokens}</strong></div><div class="mini-stat"><span>估算成本</span><strong>{cost}</strong></div><div class="mini-stat"><span>质量状态</span><strong>{quality}</strong></div><div class="mini-stat"><span>确定性快照</span><strong>{data_state}</strong></div><a class="raw-link" href="/file/{quote(summary['name'])}?raw=1">查看原始 Markdown</a></aside></div></main>"""
    return _page(summary["title"], body)


def artifact_html(path: Path) -> str:
    raw = path.read_text(errors="replace")
    if path.suffix == ".json":
        try:
            raw = json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            pass
    body = f'<main class="reader-wrap"><section class="reader-head"><a class="back" href="/">← 返回仪表盘</a><h1>{html.escape(path.name)}</h1><div class="reader-meta"><span>{path.stat().st_size:,} bytes</span><span>·</span><span>只读审计文件</span></div></section><pre class="json-view">{html.escape(raw)}</pre></main>'
    return _page(path.name, body)


def handler_for(out_dir: Path):
    class ReportHandler(BaseHTTPRequestHandler):
        server_version = "MarketBrief"
        sys_version = ""

        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/healthz":
                return self._send(b"ok\n", "text/plain; charset=utf-8")
            if parsed.path == "/":
                return self._send(dashboard_html(out_dir).encode(), "text/html; charset=utf-8")
            if parsed.path.startswith("/report/"):
                path = resolve_visible(out_dir, unquote(parsed.path.removeprefix("/report/")))
                if path is None or path.suffix != ".md" or path.name.endswith(".full.md"):
                    return self.send_error(404)
                return self._send(report_html(report_summary(path)).encode(), "text/html; charset=utf-8")
            if parsed.path.startswith("/file/"):
                path = resolve_visible(out_dir, unquote(parsed.path.removeprefix("/file/")))
                if path is None:
                    return self.send_error(404)
                if parsed.query == "raw=1":
                    content_type = "text/markdown; charset=utf-8" if path.suffix == ".md" else "text/plain; charset=utf-8"
                    return self._send(path.read_bytes(), content_type)
                if path.suffix == ".md" and not path.name.endswith(".full.md"):
                    return self._send(report_html(report_summary(path)).encode(), "text/html; charset=utf-8")
                return self._send(artifact_html(path).encode(), "text/html; charset=utf-8")
            self.send_error(404)

        def _send(self, body: bytes, content_type: str):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            return

    return ReportHandler


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=Path("out"))
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    server = ThreadingHTTPServer((args.bind, args.port), handler_for(args.out_dir.resolve()))
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
