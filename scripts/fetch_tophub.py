#!/usr/bin/env python3
"""Fetch robotics-related trending news from Tophub, YouTube, and Twitter."""

from __future__ import annotations

import calendar
import json
import re
import sys
import textwrap
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

WORKDIR = Path(__file__).resolve().parents[1]
VENDOR_DIR = WORKDIR / 'vendor'
for pkg in ("feedparser-6.0.11", "beautifulsoup4-4.12.3", "soupsieve-2.5", "sgmllib3k-1.0.0"):
    pkg_path = VENDOR_DIR / pkg
    if pkg_path.exists():
        sys.path.insert(0, str(pkg_path))

import feedparser
from bs4 import BeautifulSoup

TODAY = datetime.now(timezone.utc).astimezone().date()
DATA_PATH = WORKDIR / f"data/{TODAY:%Y/%m/%d}.json"
CONTENT_PATH = WORKDIR / f"content/{TODAY:%Y-%m-%d}.md"

KEYWORDS = ["机器人", "具身智能", "robot", "Robot", "智能体", "自动驾驶"]
YOUTUBE_QUERIES = ["robot", "自动驾驶", "具身智能"]
YOUTUBE_SEARCH_BASE = "https://www.youtube.com/results"
YOUTUBE_SP_PARAM = "CAI%253D"  # short-by upload date
TWITTER_QUERY = "robot OR \"具身智能\" OR \"自动驾驶\""
TWITTER_RSS_ENDPOINTS = [
    "https://nitter.pufe.org/search/rss",
    "https://nitter.cz/search/rss",
]
TWITTER_MAX_DAYS = 7
YOUTUBE_MAX_DAYS = 7
SOCIAL_SOURCES = {"YouTube", "Twitter"}

RELATIVE_TIME_PATTERN = re.compile(
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>second|minute|hour|day|week|month|year)s?\s+ago",
    re.IGNORECASE,
)
ABSOLUTE_DATE_PATTERN = re.compile(
    r"(?P<month>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t|tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(?P<day>\d{1,2}),\s+(?P<year>\d{4})",
    re.IGNORECASE,
)
TIME_UNIT_TO_DAYS = {
    "second": 1 / 86400,
    "minute": 1 / 1440,
    "hour": 1 / 24,
    "day": 1,
    "week": 7,
    "month": 30,
    "year": 365,
}
MONTH_NAME_TO_NUM = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

HEADERS = {"User-Agent": "Mozilla/5.0 (Robot-Daily Bot)"}
TIMEOUT = 15
TOPHUB_BASE = "https://tophub.today"


def slugify(text: str) -> str:
    text = re.sub(r"[\s_]+", "-", text.strip().lower())
    text = re.sub(r"[^a-z0-9-]", "", text)
    return text[:60] or "item"


def today_date() -> str:
    return TODAY.isoformat()


def parse_youtube_age_days(text: str) -> Optional[float]:
    if not text:
        return None
    text = text.replace('•', ' ').replace('·', ' ')
    match = RELATIVE_TIME_PATTERN.search(text)
    if match:
        try:
            value = float(match.group('num'))
        except ValueError:
            value = None
        if value is not None:
            unit = match.group('unit').lower()
            factor = TIME_UNIT_TO_DAYS.get(unit)
            if factor is not None:
                return value * factor
    date_match = ABSOLUTE_DATE_PATTERN.search(text)
    if date_match:
        month_key = date_match.group('month').lower().rstrip('.')
        month = MONTH_NAME_TO_NUM.get(month_key)
        day = int(date_match.group('day'))
        year = int(date_match.group('year'))
        if month:
            try:
                published = datetime(year, month, day, tzinfo=timezone.utc)
            except ValueError:
                published = None
            if published:
                delta = datetime.now(timezone.utc) - published
                return max(delta.total_seconds() / 86400, 0.0)
    return None


def parse_twitter_published(entry) -> Optional[datetime]:
    published_parsed = entry.get('published_parsed') or entry.get('updated_parsed')
    if published_parsed:
        try:
            timestamp = calendar.timegm(published_parsed)
        except (ValueError, OverflowError):
            timestamp = None
        if timestamp is not None:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    published = entry.get('published') or entry.get('updated')
    if published:
        try:
            dt = parsedate_to_datetime(published)
        except (TypeError, ValueError):
            dt = None
        if dt:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt
    return None


def is_within_days(age_days: Optional[float], max_days: int) -> bool:
    if age_days is None:
        return False
    return age_days <= max_days


def is_recent_datetime(dt: Optional[datetime], max_days: int) -> bool:
    if not dt:
        return False
    return datetime.now(timezone.utc) - dt <= timedelta(days=max_days)


@dataclass
class Candidate:
    title: str
    url: str
    heat: str
    source: str
    summary: str
    region: str = "Global"
    category: Optional[List[str]] = None

    def as_entry(self) -> dict:
        categories = self.category or infer_category(self.title)
        return {
            "id": f"{today_date()}-{slugify(self.source + '-' + self.title)}",
            "date": today_date(),
            "title": self.title,
            "category": categories,
            "region": self.region,
            "summary": self.summary or self.title,
            "source": self.source,
            "source_url": self.url,
            "tags": [],
            "impact": self.heat or "",
        }


def match_keywords(text: str) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in KEYWORDS)


def fetch_html(url: str) -> Optional[str]:
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="ignore")
    except Exception:
        return None


def build_full_url(href: str) -> str:
    if href.startswith("http"):
        return href
    return f"{TOPHUB_BASE}{href}"


def discover_channels() -> Tuple[Optional[str], List[str]]:
    homepage_html = fetch_html(TOPHUB_BASE)
    if not homepage_html:
        return None, []
    soup = BeautifulSoup(homepage_html, "html.parser")
    channels: Set[str] = set()
    for anchor in soup.select('div.cc-cd a[href^="/n/"]'):
        href = anchor.get("href")
        if href:
            channels.add(build_full_url(href))
    return homepage_html, sorted(channels)


def parse_homepage(html: str) -> List[Candidate]:
    soup = BeautifulSoup(html, "html.parser")
    items: List[Candidate] = []
    for anchor in soup.select("div.cc-cd-cb-l a"):
        title_span = anchor.select_one("span.t")
        if not title_span:
            continue
        title = title_span.get_text(strip=True)
        if not match_keywords(title):
            continue
        heat_span = anchor.select_one("span.e")
        heat = heat_span.get_text(strip=True) if heat_span else ""
        href = anchor.get("href") or ""
        url = build_full_url(href)
        parent = anchor.find_parent("div", class_="cc-cd")
        source_span = parent.select_one("div.cc-cd-lb span") if parent else None
        source = source_span.get_text(strip=True) if source_span else "Tophub"
        items.append(Candidate(title=title, url=url, heat=heat, source=source, summary=title))
    return items


def parse_channel_page(html: str, source_name: str) -> List[Candidate]:
    soup = BeautifulSoup(html, "html.parser")
    items: List[Candidate] = []

    for row in soup.select("table tr"):
        link = row.find("a", attrs={"itemid": True})
        if not link:
            continue
        title = link.get_text(strip=True)
        if not match_keywords(title):
            continue
        href = link.get("href") or ""
        url = build_full_url(href)
        heat = row.get_text(" ", strip=True)
        items.append(Candidate(title=title, url=url, heat=heat, source=source_name, summary=title))

    if items:
        return items

    for anchor in soup.select("div.cc-cd-cb-l a"):
        title_span = anchor.select_one("span.t")
        if not title_span:
            continue
        title = title_span.get_text(strip=True)
        if not match_keywords(title):
            continue
        href = anchor.get("href") or ""
        url = build_full_url(href)
        heat_span = anchor.select_one("span.e")
        heat = heat_span.get_text(strip=True) if heat_span else ""
        items.append(Candidate(title=title, url=url, heat=heat, source=source_name, summary=title))

    return items


def fetch_tophub_candidates() -> List[Candidate]:
    homepage_html, channel_urls = discover_channels()
    candidates: List[Candidate] = []

    if homepage_html:
        candidates.extend(parse_homepage(homepage_html))

    for url in channel_urls:
        html = fetch_html(url)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        title_tag = soup.find("title")
        source_name = title_tag.get_text(strip=True) if title_tag else url
        candidates.extend(parse_channel_page(html, source_name))

    return candidates


def fetch_youtube_candidates(limit_per_query: int = 5) -> List[Candidate]:
    results: List[Candidate] = []
    for query in YOUTUBE_QUERIES:
        encoded_query = urllib.parse.quote(query)
        url = (
            "https://r.jina.ai/"
            + f"https://www.youtube.com/results?search_query={encoded_query}&sp={YOUTUBE_SP_PARAM}"
        )
        text = fetch_html(url)
        if not text:
            continue
        lines = text.splitlines()
        count = 0
        for idx, line in enumerate(lines):
            match = re.match(r"### \[(.+?)\]\((https://www\.youtube\.com/watch\?[^\s)]+).*", line)
            if not match:
                continue
            title = match.group(1).strip()
            video_url = match.group(2)
            channel = ""
            heat = ""
            summary = ""
            for look_ahead in range(idx + 1, min(len(lines), idx + 15)):
                candidate = lines[look_ahead].strip()
                if not candidate:
                    continue
                if not channel and candidate.startswith("[") and "](" in candidate:
                    channel = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", candidate).strip()
                if ("views" in candidate.lower() or "ago" in candidate.lower()) and not heat:
                    heat = candidate
                if candidate.startswith("From the video description"):
                    summary = candidate
                    break
            summary_text = summary or f"{channel}".strip()
            age_days = parse_youtube_age_days(heat)
            if not is_within_days(age_days, YOUTUBE_MAX_DAYS):
                continue
            results.append(
                Candidate(
                    title=title,
                    url=video_url,
                    heat=heat,
                    source="YouTube",
                    summary=summary_text or title,
                    category=["social", "video"],
                )
            )
            count += 1
            if count >= limit_per_query:
                break
    return results


def fetch_twitter_candidates(limit: int = 10) -> List[Candidate]:
    query = urllib.parse.quote(TWITTER_QUERY)
    entries: List[Candidate] = []
    for endpoint in TWITTER_RSS_ENDPOINTS:
        rss_url = f"{endpoint}?f=tweets&q={query}"
        try:
            feed = feedparser.parse(rss_url)
        except Exception:
            continue
        for entry in feed.entries:
            title = entry.get("title", "").strip()
            if not title or not match_keywords(title):
                continue
            link = entry.get("link", "")
            summary = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text(" ", strip=True)
            published = entry.get("published", "")
            published_dt = parse_twitter_published(entry)
            if not is_recent_datetime(published_dt, TWITTER_MAX_DAYS):
                continue
            entries.append(
                Candidate(
                    title=title,
                    url=link,
                    heat=published,
                    source="Twitter",
                    summary=summary or title,
                    category=["social", "trending"],
                )
            )
        if entries:
            break
    return entries[:limit]


def infer_category(title: str) -> List[str]:
    title_lower = title.lower()
    categories = []
    if "无人机" in title or "drone" in title_lower:
        categories.append("defense")
    if "人形" in title or "humanoid" in title_lower or "具身" in title_lower:
        categories.append("humanoid")
    if "机器人" in title_lower or "robot" in title_lower:
        categories.append("robotics")
    if "融资" in title or "funding" in title_lower:
        categories.append("funding")
    if "自动驾驶" in title_lower:
        categories.append("autonomous-driving")
    if not categories:
        categories.append("general")
    return categories


def load_existing() -> List[dict]:
    if DATA_PATH.exists():
        return json.loads(DATA_PATH.read_text())
    return []


def move_social_sources_to_end(entries: List[dict]) -> List[dict]:
    primary = []
    social = []
    for item in entries:
        if item.get('source') in SOCIAL_SOURCES:
            social.append(item)
        else:
            primary.append(item)
    return primary + social


def merge_entries(existing: List[dict], new_items: List[dict]) -> List[dict]:
    seen_titles = {item["title"].lower(): item["id"] for item in existing}
    seen_urls = {item.get("source_url", ""): item["id"] for item in existing if item.get("source_url")}
    for item in new_items:
        title_key = item["title"].lower()
        url_key = item.get("source_url", "")
        if title_key in seen_titles or (url_key and url_key in seen_urls):
            continue
        existing.append(item)
        seen_titles[title_key] = item["id"]
        if url_key:
            seen_urls[url_key] = item["id"]
    existing.sort(key=lambda x: (x.get("date", ""), x.get("title", "")), reverse=True)
    return existing


def write_json(data: List[dict]) -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def write_markdown(data: List[dict]) -> None:
    sources = sorted({item.get("source", "") for item in data if item.get("source")})
    lines = [f"# {today_date()} 全球机器人快讯", "", "> 数据来源：" + "、".join(sources)]
    for idx, item in enumerate(data, start=1):
        impact = item.get("impact", "")
        impact_line = impact if impact else "热度指标：无"
        lines.extend(
            [
                f"## {idx}. {item['title']}",
                f"- **时间**：{item.get('date', '')}  ",
                f"- **亮点**：{item.get('summary', '')}  ",
                f"- **意义**：{impact_line}  ",
                f"- **来源**：[{item.get('source','')}]({item.get('source_url','')})",
                "",
            ]
        )
    CONTENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTENT_PATH.write_text("\n".join(lines))


def main() -> None:
    tophub_candidates = [c.as_entry() for c in fetch_tophub_candidates()]
    youtube_candidates = [c.as_entry() for c in fetch_youtube_candidates()]
    twitter_candidates = [c.as_entry() for c in fetch_twitter_candidates()]

    existing = load_existing()
    merged = merge_entries(existing, tophub_candidates + youtube_candidates + twitter_candidates)
    ordered = move_social_sources_to_end(merged)
    write_json(ordered)
    write_markdown(ordered)


if __name__ == "__main__":
    main()
