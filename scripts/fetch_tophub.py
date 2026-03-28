#!/usr/bin/env python3
"""Fetch robotics-related trending news from Tophub, YouTube, and Twitter."""

from __future__ import annotations

import json
import re
import textwrap
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

import feedparser
import requests
from bs4 import BeautifulSoup

WORKDIR = Path(__file__).resolve().parents[1]
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

HEADERS = {"User-Agent": "Mozilla/5.0 (Robot-Daily Bot)"}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)
TIMEOUT = 15
TOPHUB_BASE = "https://tophub.today"


def slugify(text: str) -> str:
    text = re.sub(r"[\s_]+", "-", text.strip().lower())
    text = re.sub(r"[^a-z0-9-]", "", text)
    return text[:60] or "item"


def today_date() -> str:
    return TODAY.isoformat()


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
    try:
        resp = SESSION.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException:
        return None
    return resp.text


def build_full_url(href: str) -> str:
    if href.startswith("http"):
        return href
    return f"{TOPHUB_BASE}{href}"


def discover_channels() -> Tuple[Optional[str], List[str]]:
    homepage_html = fetch_html(TOPHUB_BASE)
    if not homepage_html:
        return None, []
    soup = BeautifulSoup(homepage_html, "lxml")
    channels: Set[str] = set()
    for anchor in soup.select('div.cc-cd a[href^="/n/"]'):
        href = anchor.get("href")
        if href:
            channels.add(build_full_url(href))
    return homepage_html, sorted(channels)


def parse_homepage(html: str) -> List[Candidate]:
    soup = BeautifulSoup(html, "lxml")
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
    soup = BeautifulSoup(html, "lxml")
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
        soup = BeautifulSoup(html, "lxml")
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
            summary = BeautifulSoup(entry.get("summary", ""), "lxml").get_text(" ", strip=True)
            published = entry.get("published", "")
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
    write_json(merged)
    write_markdown(merged)


if __name__ == "__main__":
    main()
