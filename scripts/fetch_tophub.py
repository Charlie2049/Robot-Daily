#!/usr/bin/env python3
"""Fetch robotics-related trending news from Tophub."""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Set, Tuple

WORKDIR = Path(__file__).resolve().parents[1]
VENDOR_DIR = WORKDIR / "vendor"
for pkg in ("beautifulsoup4-4.12.3", "soupsieve-2.5"):
    pkg_path = VENDOR_DIR / pkg
    if pkg_path.exists():
        sys.path.insert(0, str(pkg_path))

from bs4 import BeautifulSoup

TODAY = datetime.now(timezone.utc).astimezone().date()
DATA_PATH = WORKDIR / f"data/{TODAY:%Y/%m/%d}.json"
CONTENT_PATH = WORKDIR / f"content/{TODAY:%Y-%m-%d}.md"

KEYWORDS = ["机器人", "具身智能", "robot", "Robot", "智能体", "自动驾驶"]
HEADERS = {"User-Agent": "Mozilla/5.0 (Robot-Daily Bot)"}
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

    existing = load_existing()
    merged = merge_entries(existing, tophub_candidates)
    write_json(merged)
    write_markdown(merged)


if __name__ == "__main__":
    main()
