#!/usr/bin/env python3
"""Fetch robotics-related trending news from Tophub and update the dataset."""

from __future__ import annotations

import json
import re
import textwrap
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

import requests
from bs4 import BeautifulSoup

WORKDIR = Path(__file__).resolve().parents[1]
TODAY = datetime.now(timezone.utc).astimezone().date()
DATA_PATH = WORKDIR / f"data/{TODAY:%Y/%m/%d}.json"
CONTENT_PATH = WORKDIR / f"content/{TODAY:%Y-%m-%d}.md"

KEYWORDS = ["机器人", "humanoid", "robot", "无人机", "智能体", "robotics", "仿生"]

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
        items.append(Candidate(title=title, url=url, heat=heat, source=source))
    return items


def parse_channel_page(html: str, source_name: str) -> List[Candidate]:
    soup = BeautifulSoup(html, "lxml")
    items: List[Candidate] = []

    # Primary: table-based榜单
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
        items.append(Candidate(title=title, url=url, heat=heat, source=source_name))

    if items:
        return items

    # Fallback: 使用 homepage 的结构解析
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
        items.append(Candidate(title=title, url=url, heat=heat, source=source_name))

    return items


def fetch_candidates() -> List[Candidate]:
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


def infer_category(title: str) -> List[str]:
    title_lower = title.lower()
    categories = []
    if "无人机" in title or "drone" in title_lower:
        categories.append("defense")
    if "人形" in title or "humanoid" in title_lower:
        categories.append("humanoid")
    if "机器人" in title_lower or "robot" in title_lower:
        categories.append("robotics")
    if "融资" in title or "funding" in title_lower:
        categories.append("funding")
    if not categories:
        categories.append("general")
    return categories


def build_entry(candidate: Candidate) -> dict:
    summary = candidate.title
    impact = candidate.heat or "热度来自 TopHub"
    return {
        "id": f"{today_date()}-{slugify(candidate.title)}",
        "date": today_date(),
        "title": candidate.title,
        "category": infer_category(candidate.title),
        "region": "Global",
        "summary": summary,
        "source": candidate.source or "Tophub",
        "source_url": candidate.url,
        "tags": [],
        "impact": impact,
    }


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
    lines = [f"# {today_date()} 全球机器人快讯", "", "> 数据来源：Tophub 聚合"]
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
    candidates = fetch_candidates()
    new_entries = [build_entry(c) for c in candidates]
    existing = load_existing()
    merged = merge_entries(existing, new_entries)
    write_json(merged)
    write_markdown(merged)


if __name__ == "__main__":
    main()
