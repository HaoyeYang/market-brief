#!/usr/bin/env python3
"""Build a bounded, auditable research packet for the market-brief agents.

Official calendars and releases are first-class evidence. GDELT is used only
to discover articles; an item is publishable evidence only after its source
page can be fetched and a useful excerpt can be extracted. The packet contains
no credentials and is safe to project into the private viewer.
"""

from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import hashlib
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from zoneinfo import ZoneInfo


CT = ZoneInfo("America/Chicago")
UTC = dt.timezone.utc
OFFICIAL_DOMAINS = {
    "bls.gov", "bea.gov", "federalreserve.gov", "treasury.gov", "sec.gov",
    "cftc.gov", "finra.org", "cmegroup.com", "nyse.com", "nasdaq.com",
}
TRUSTED_MEDIA = {
    "reuters.com", "bloomberg.com", "ft.com", "wsj.com", "cnbc.com",
    "marketwatch.com", "investing.com", "tradingview.com",
}
WATCHLIST = (
    "NVDA", "AMD", "AVGO", "TSM", "MU", "QCOM", "MSFT", "AMZN",
    "GOOGL", "META", "AAPL", "ORCL",
)
SEC_FORMS = {"8-K", "10-Q", "10-K", "6-K", "20-F", "40-F"}
THEME_TERMS = {
    "inflation": ("cpi", "ppi", "pce", "inflation", "consumer price", "producer price"),
    "employment": ("payroll", "employment", "unemployment", "jobless", "labor market"),
    "fomc": ("fomc", "federal funds", "monetary policy", "powell", "federal reserve"),
    "treasury": ("treasury auction", "refunding", "auction", "debt management"),
    "tech_earnings": ("earnings", "guidance", "ai", "semiconductor", "hbm", "data center", "cloud"),
    "geopolitics": ("tariff", "sanction", "war", "ceasefire", "geopolit", "trade policy"),
}
MEDIA_TERMS = (
    "market", "stock", "equity", "bond", "treasury", "yield", "federal reserve", "fed ",
    "economy", "economic", "inflation", "payroll", "jobs", "unemployment", "gdp", "pmi",
    "oil", "crude", "gold", "commodity", "dollar", "currency", "bitcoin", "crypto",
    "earnings", "revenue", "guidance", "merger", "acquisition", "ipo", "etf", "bank",
    "tariff", "sanction", "trade war", "geopolit", "iran", "china", "ai ",
    "artificial intelligence", "chip", "semiconductor", "cloud", "data center",
)


class ResearchError(RuntimeError):
    pass


def _domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return host


def _allowed_domain(url: str) -> bool:
    host = _domain(url)
    return any(host == domain or host.endswith("." + domain) for domain in OFFICIAL_DOMAINS | TRUSTED_MEDIA)


def _tier(url: str) -> str:
    host = _domain(url)
    if any(host == d or host.endswith("." + d) for d in OFFICIAL_DOMAINS):
        return "official"
    if any(host == d or host.endswith("." + d) for d in TRUSTED_MEDIA):
        return "reliable_media"
    return "other"


def _parse_time(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        pass
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).isoformat(timespec="seconds")
    except ValueError:
        return None


def _evidence_id(source: str, url: str, title: str) -> str:
    digest = hashlib.sha256(f"{source}\0{url}\0{title}".encode()).hexdigest()[:12]
    return f"ev-{digest}"


def evidence(
    *, source: str, title: str, url: str, retrieved_at: str,
    published_at: str | None = None, event_time: str | None = None,
    excerpt: str = "", kind: str = "news", audit_status: str = "source_fetched",
    tickers: list[str] | None = None,
) -> dict[str, Any]:
    text = re.sub(r"\s+", " ", excerpt).strip()[:900]
    combined = f"{title} {text}".lower()
    angles = [key for key, terms in THEME_TERMS.items() if any(term in combined for term in terms)]
    return {
        "id": _evidence_id(source, url, title), "kind": kind,
        "title": title.strip()[:300], "excerpt": text,
        "source_name": source, "source_domain": _domain(url),
        "source_tier": _tier(url), "url": url,
        "event_time": event_time, "published_at": published_at,
        "retrieved_at": retrieved_at, "audit_status": audit_status,
        "angles": angles, "tickers": sorted(set(tickers or [])),
    }


class Collector:
    def __init__(self, session=requests, now: dt.datetime | None = None):
        self.session = session
        self.now = (now or dt.datetime.now(UTC)).astimezone(UTC)
        self.retrieved_at = self.now.isoformat(timespec="seconds")
        # BLS/SEC expect an identifiable crawler User-Agent. Deployments should
        # set MARKET_BRIEF_CONTACT to an operator email; the repository keeps a
        # non-personal placeholder so private identity never enters Git.
        contact = os.getenv("MARKET_BRIEF_CONTACT", "market-brief@example.com")
        self.headers = {"User-Agent": f"market-brief/1.0 contact {contact}", "Accept": "*/*"}
        self.errors: list[dict[str, str]] = []

    def get(self, url: str, *, timeout: int = 20, max_bytes: int = 2_000_000) -> requests.Response:
        response = self.session.get(url, headers=self.headers, timeout=timeout)
        response.raise_for_status()
        content_length = int(response.headers.get("content-length") or 0)
        if content_length > max_bytes or len(response.content) > max_bytes:
            raise ResearchError(f"response too large: {url}")
        return response

    def guard(self, source: str, fn):
        try:
            return fn()
        except Exception as exc:  # One source must not erase the whole brief.
            self.errors.append({"source": source, "error": str(exc)[:400]})
            return []

    def rss(
        self, source: str, url: str, *, since: dt.datetime,
        kind: str = "official_release", audit_status: str = "source_fetched",
    ) -> list[dict]:
        root = ET.fromstring(self.get(url).content)
        result = []
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            description = BeautifulSoup(item.findtext("description") or "", "html.parser").get_text(" ", strip=True)
            published = _parse_time(item.findtext("pubDate"))
            if not title or not link or not _allowed_domain(link):
                continue
            if published and dt.datetime.fromisoformat(published) < since:
                continue
            combined = f"{title} {description}".lower()
            if kind == "media_news" and not (
                any(term in combined for term in MEDIA_TERMS)
                or any(re.search(rf"\b{ticker.lower()}\b", combined) for ticker in WATCHLIST)
            ):
                continue
            result.append(evidence(
                source=source, title=title, url=link, retrieved_at=self.retrieved_at,
                published_at=published, excerpt=description, kind=kind,
                audit_status=audit_status,
            ))
        return result[:20]

    def bls_calendar(self, *, until: dt.datetime) -> list[dict]:
        text = self.get("https://www.bls.gov/schedule/news_release/bls.ics").text
        events, block = [], {}
        for raw in text.replace("\r\n ", "").splitlines():
            if raw == "BEGIN:VEVENT":
                block = {}
            elif raw == "END:VEVENT":
                start = block.get("DTSTART", "")
                match = re.match(r"(\d{8})T?(\d{6})?", start)
                if not match:
                    continue
                day = dt.datetime.strptime(match.group(1), "%Y%m%d")
                clock = dt.datetime.strptime(match.group(2) or "083000", "%H%M%S").time()
                when = dt.datetime.combine(day.date(), clock, CT).astimezone(UTC)
                if self.now - dt.timedelta(days=1) <= when <= until:
                    title = block.get("SUMMARY", "BLS release").replace("\\,", ",")
                    url = block.get("URL", "https://www.bls.gov/schedule/news_release/")
                    events.append(evidence(
                        source="BLS", title=title, url=url, retrieved_at=self.retrieved_at,
                        event_time=when.isoformat(timespec="seconds"), kind="macro_calendar",
                        audit_status="official_calendar",
                    ))
            elif ":" in raw:
                key, value = raw.split(":", 1)
                block[key.split(";", 1)[0]] = value
        return events

    def bea_calendar(self, *, until: dt.datetime) -> list[dict]:
        url = "https://apps.bea.gov/API/signup/release_dates.json"
        body = self.get(url).json()
        result = []
        for release_name, detail in body.items():
            dates = detail.get("release_dates", []) if isinstance(detail, dict) else detail
            if not isinstance(dates, list):
                continue
            for value in dates:
                parsed = _parse_time(str(value))
                if not parsed:
                    try:
                        parsed_dt = dt.datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone(dt.timedelta(hours=-4)))
                        parsed = parsed_dt.astimezone(UTC).isoformat(timespec="seconds")
                    except ValueError:
                        continue
                when = dt.datetime.fromisoformat(parsed)
                if self.now - dt.timedelta(days=1) <= when <= until:
                    result.append(evidence(
                        source="BEA", title=release_name, url="https://www.bea.gov/news/schedule",
                        retrieved_at=self.retrieved_at, event_time=parsed, kind="macro_calendar",
                        audit_status="official_calendar",
                    ))
        return result

    def fed_calendar(self, *, until: dt.datetime) -> list[dict]:
        result = []
        cursor = self.now.astimezone(CT).date().replace(day=1)
        months = {cursor, (cursor + dt.timedelta(days=35)).replace(day=1)}
        for month in months:
            url = f"https://www.federalreserve.gov/newsevents/{month.year}-{month.strftime('%B').lower()}.htm"
            soup = BeautifulSoup(self.get(url).text, "html.parser")
            for panel in soup.select(".panel.panel-default"):
                text = re.sub(r"\s+", " ", panel.get_text(" ", strip=True))
                date_match = re.search(rf"\b({month.strftime('%B')})\s+(\d{{1,2}})\b", text)
                if not date_match:
                    continue
                day = int(date_match.group(2))
                time_match = re.search(r"(\d{1,2}:\d{2})\s*(a\.m\.|p\.m\.|AM|PM)", text, re.I)
                clock = dt.datetime.strptime(
                    (time_match.group(1) + time_match.group(2).replace(".", "")) if time_match else "12:00PM",
                    "%I:%M%p",
                ).time()
                when = dt.datetime.combine(dt.date(month.year, month.month, day), clock, dt.timezone(dt.timedelta(hours=-4))).astimezone(UTC)
                if self.now - dt.timedelta(days=1) <= when <= until:
                    anchor = panel.find("a", href=True)
                    href = anchor["href"] if anchor else url
                    if href.startswith("/"):
                        href = "https://www.federalreserve.gov" + href
                    result.append(evidence(
                        source="Federal Reserve", title=text[:240], url=href,
                        retrieved_at=self.retrieved_at, event_time=when.isoformat(timespec="seconds"),
                        excerpt=text, kind="central_bank_calendar", audit_status="official_calendar",
                    ))
        return result

    def treasury_auctions(self, *, until: dt.datetime) -> list[dict]:
        start = self.now.date().isoformat()
        end = until.date().isoformat()
        url = (
            "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/"
            f"accounting/od/auctions_query?filter=auction_date:gte:{start},auction_date:lte:{end}"
            "&sort=auction_date&page[size]=50"
        )
        result = []
        for row in self.get(url).json().get("data", []):
            title = f"U.S. Treasury {row.get('security_term')} {row.get('security_type')} auction"
            when = f"{row.get('auction_date')}T16:00:00+00:00"
            facts = [
                f"announcement {row.get('announcemt_date')}", f"issue {row.get('issue_date')}",
                f"offering {row.get('offering_amt')}", f"bid-to-cover {row.get('bid_to_cover_ratio')}",
                f"high yield {row.get('high_yield')}",
            ]
            result.append(evidence(
                source="U.S. Treasury Fiscal Data", title=title,
                url="https://fiscaldata.treasury.gov/datasets/treasury-securities-auctions-data/",
                retrieved_at=self.retrieved_at, event_time=when, excerpt="; ".join(facts),
                kind="treasury_auction", audit_status="official_api",
            ))
        return result

    def sec_filings(self, *, since: dt.datetime) -> list[dict]:
        tickers_url = "https://www.sec.gov/files/company_tickers.json"
        companies = self.get(tickers_url).json()
        mapping = {
            str(item.get("ticker", "")).upper(): int(item["cik_str"])
            for item in companies.values() if isinstance(item, dict) and item.get("cik_str")
        }
        result = []
        since_day = since.date().isoformat()
        for ticker in WATCHLIST:
            cik = mapping.get(ticker)
            if not cik:
                continue
            url = f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
            recent = self.get(url).json().get("filings", {}).get("recent", {})
            count = min(len(recent.get("form", [])), 80)
            for index in range(count):
                form = recent.get("form", [None] * count)[index]
                filed = recent.get("filingDate", [None] * count)[index]
                if form not in SEC_FORMS or not filed or filed < since_day:
                    continue
                accession = recent.get("accessionNumber", [""] * count)[index]
                primary = recent.get("primaryDocument", [""] * count)[index]
                filing_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{primary}"
                accepted = _parse_time(recent.get("acceptanceDateTime", [None] * count)[index])
                result.append(evidence(
                    source="SEC EDGAR", title=f"{ticker} filed {form}", url=filing_url,
                    retrieved_at=self.retrieved_at, published_at=accepted,
                    event_time=accepted or f"{filed}T00:00:00+00:00", kind="sec_filing",
                    audit_status="official_api", tickers=[ticker],
                ))
            time.sleep(0.12)
        return result

    def gdelt(self, query: str, *, since: dt.datetime, max_records: int = 12) -> list[dict]:
        params = {
            "query": query, "mode": "ArtList", "maxrecords": str(max_records),
            "timespan": "2d", "sort": "DateDesc", "format": "json",
        }
        response = self.session.get(
            "https://api.gdeltproject.org/api/v2/doc/doc", params=params,
            headers=self.headers, timeout=30,
        )
        response.raise_for_status()
        articles = response.json().get("articles", [])
        result = []
        for article in articles:
            url = article.get("url", "")
            if not _allowed_domain(url):
                continue
            published = _parse_time(article.get("seendate"))
            if published and dt.datetime.fromisoformat(published) < since:
                continue
            try:
                page = self.get(url, timeout=18, max_bytes=2_500_000)
                soup = BeautifulSoup(page.text, "html.parser")
                title = (soup.find("meta", property="og:title") or {}).get("content") or article.get("title") or ""
                pub_meta = (
                    soup.find("meta", property="article:published_time")
                    or soup.find("meta", attrs={"name": "date"})
                    or soup.find("meta", attrs={"name": "pub_date"})
                )
                published = _parse_time((pub_meta or {}).get("content")) or published
                excerpt_parts = [p.get_text(" ", strip=True) for p in soup.select("article p, main p")[:5]]
                excerpt = " ".join(part for part in excerpt_parts if len(part) > 35)
                if not title or len(excerpt) < 80:
                    continue
                tickers = [ticker for ticker in WATCHLIST if re.search(rf"\b{ticker}\b", f"{title} {excerpt}", re.I)]
                result.append(evidence(
                    source=article.get("domain") or _domain(url), title=title, url=url,
                    retrieved_at=self.retrieved_at, published_at=published, excerpt=excerpt,
                    kind="media_news", audit_status="source_fetched", tickers=tickers,
                ))
            except Exception as exc:
                self.errors.append({"source": _domain(url) or "news-page", "error": str(exc)[:300]})
        return result


def route_for(items: list[dict], *, mode: str, is_session: bool) -> dict[str, Any]:
    flags = sorted({angle for item in items for angle in item.get("angles", [])})
    calendar_events = [item for item in items if "calendar" in item.get("kind", "") or item.get("kind") == "treasury_auction"]
    important = {"inflation", "employment", "fomc", "tech_earnings"} & set(flags)
    if important or len(calendar_events) >= 5:
        day_type, budget = "high_impact", 3
    elif items or calendar_events:
        day_type, budget = "normal", 2
    else:
        day_type, budget = "quiet", 1
    agents = ["news_scout", "us_equities", "source_verifier"]
    if day_type != "quiet" or {"inflation", "employment", "fomc", "treasury"} & set(flags):
        agents += ["macro_rates", "calendar_router"]
    if day_type != "quiet" or "tech_earnings" in flags:
        agents.append("ai_tech_chain")
    if day_type == "high_impact" or "geopolitics" in flags:
        agents.append("global_cross_asset")
    return {
        "day_type": day_type, "flags": flags, "active_agents": list(dict.fromkeys(agents)),
        "search_query_budget": budget, "mode": mode, "is_session": is_session,
        "reason": f"{len(calendar_events)} calendar/auction events and {len(items)} audited evidence items",
    }


def collect_packet(data: dict, *, session=requests, now: dt.datetime | None = None) -> dict[str, Any]:
    collector = Collector(session=session, now=now)
    context = data.get("run_context") or {}
    since = collector.now - dt.timedelta(hours=72 if collector.now.weekday() in {0, 6} else 36)
    until = collector.now + dt.timedelta(days=8)
    items: list[dict] = []
    sources = (
        ("BLS calendar", lambda: collector.bls_calendar(until=until)),
        ("BEA calendar", lambda: collector.bea_calendar(until=until)),
        ("Fed calendar", lambda: collector.fed_calendar(until=until)),
        ("Treasury auctions", lambda: collector.treasury_auctions(until=until)),
        ("Fed releases", lambda: collector.rss("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml", since=since)),
        ("Fed speeches", lambda: collector.rss("Federal Reserve", "https://www.federalreserve.gov/feeds/speeches.xml", since=since)),
        ("BLS releases", lambda: collector.rss("BLS", "https://www.bls.gov/feed/bls_latest.rss", since=since)),
        ("BEA releases", lambda: collector.rss("BEA", "https://apps.bea.gov/rss/rss.xml", since=since)),
        ("WSJ Markets", lambda: collector.rss(
            "Wall Street Journal", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
            since=since, kind="media_news", audit_status="publisher_feed",
        )),
        ("Financial Times", lambda: collector.rss(
            "Financial Times", "https://www.ft.com/rss/home",
            since=since, kind="media_news", audit_status="publisher_feed",
        )),
        ("MarketWatch", lambda: collector.rss(
            "MarketWatch", "https://feeds.marketwatch.com/marketwatch/topstories/",
            since=since, kind="media_news", audit_status="publisher_feed",
        )),
        ("SEC EDGAR", lambda: collector.sec_filings(since=since)),
    )
    for source, fn in sources:
        items.extend(collector.guard(source, fn))

    preliminary = route_for(items, mode=data.get("mode", "intraday"), is_session=bool(context.get("is_session")))
    queries = [
        '("US stocks" OR "Wall Street" OR "Treasury yields") sourcecountry:US',
        '(AI OR semiconductor OR HBM OR "data center") (NVDA OR AMD OR AVGO OR TSM OR MU OR QCOM)',
        '(Federal Reserve OR inflation OR payrolls OR tariff OR sanctions) markets',
    ][:preliminary["search_query_budget"]]
    for index, query in enumerate(queries):
        if index:
            time.sleep(5.2)  # GDELT DOC's public endpoint permits one request / 5s.
        items.extend(collector.guard("GDELT discovery", lambda q=query: collector.gdelt(q, since=since)))

    deduped = {item["id"]: item for item in items}
    items = sorted(
        deduped.values(), key=lambda item: item.get("event_time") or item.get("published_at") or "",
        reverse=True,
    )
    route = route_for(items, mode=data.get("mode", "intraday"), is_session=bool(context.get("is_session")))
    return {
        "schema_version": 1, "generated_at": collector.retrieved_at,
        "window": {"start": since.isoformat(timespec="seconds"), "end": until.isoformat(timespec="seconds")},
        "route": route, "evidence": items, "source_errors": collector.errors,
        "coverage": {
            "official": sum(item["source_tier"] == "official" for item in items),
            "reliable_media": sum(item["source_tier"] == "reliable_media" for item in items),
            "source_fetched": sum(item["audit_status"] in {"source_fetched", "publisher_feed"} for item in items),
            "calendar_events": sum("calendar" in item["kind"] or item["kind"] == "treasury_auction" for item in items),
        },
        "limitations": [
            "GDELT is discovery-only; only successfully fetched source pages are included.",
            "FactSet/Refinitiv/Bloomberg terminal data are not claimed without licensed credentials.",
            "Consensus and CME FedWatch values appear only when an audited source supplies them.",
        ],
    }


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.data.read_text())
        atomic_json(args.out, collect_packet(data))
        return 0
    except Exception as exc:
        print(f"research pipeline failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
