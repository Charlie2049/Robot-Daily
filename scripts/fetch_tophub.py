#!/usr/bin/env python3
"""Fetch robotics-related trending news from Tophub and update the dataset."""

from __future__ import annotations

import json
import re
import textwrap
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

import requests
from bs4 import BeautifulSoup

WORKDIR = Path(__file__).resolve().parents[1]
TODAY = datetime.now(timezone.utc).astimezone().date()
DATA_PATH = WORKDIR / f"data/{TODAY:%Y/%m/%d}.json"
CONTENT_PATH = WORKDIR / f"content/{TODAY:%Y-%m-%d}.md"

TOPHUB_CHANNELS = [
    "https://tophub.today",
    "https://tophub.today/n/Q1Vd5Ko85R",  # 36氪 24h
    "https://tophub.today/n/K7GdaMgdQy",  # 抖音热搜
    "https://tophub.today/n/L4MdA5ldxD",  # 小红书热榜
    "https://tophub.today/n/mproPpoq6O",  # 知乎热榜
]

KEYWORDS = ["机器人", "humanoid", "robot", "无人机", "智能体", "robotics", "仿生"]

HEADERS = {"User-Agent": "Mozilla/5.0 (Robot-Daily Bot)"}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)
TIMEOUT = 15


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


def match_keywords(title: str) -> bool:
    lowered = title.lower()
    return any(keyword in lowered for keyword in [k.lower() for k in KEYWORDS])


def fetch_html(url: str) -> Optional[str]:
    try:
        resp = SESSION.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException:
        return None
    return resp.text


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
        url = href if href.startswith("http") else f"https://tophub.today{href}"
        source_span = anchor.find_parent("div", class_="cc-cd").select_one("div.cc-cd-lb span")
        source = source_span.get_text(strip=True) if source_span else "Tophub"
        items.append(Candidate(title=title, url=url, heat=heat, source=source))
    return items


def parse_table_page(html: str, source_name: str) -> List[Candidate]:
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
        url = href if href.startswith("http") else f"https://tophub.today{href}"
        heat = row.get_text(" ", strip=True)
        items.append(Candidate(title=title, url=url, heat=heat, source=source_name))
    return items


def fetch_candidates() -> List[Candidate]:
    collected: List[Candidate] = []
    for url in TOPHUB_CHANNELS:
        html = fetch_html(url)
        if not html:
            continue
        if url == "https://tophub.today":
            collected.extend(parse_homepage(html))
        else:
            soup = BeautifulSoup(html, "lxml")
            source_name = soup.find("title").get_text(strip=True)
            collected.extend(parse_table_page(html, source_name))
    return collected


def fetch_summary(url: str) -> str:
    try:
        resp = SESSION.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        paragraphs = [p.get_text(strip=True) for p in soup.select("p") if p.get_text(strip=True)]
        text = " ".join(paragraphs)
        return textwrap.shorten(text, width=240, placeholder="…") if text else ""
    except requests.RequestException:
        return ""


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
    summary = fetch_summary(candidate.url) or candidate.title
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
    existing_titles = {item["title"].lower(): item["id"] for item in existing}
    for item in new_items:
        if item["title"].lower() in existing_titles:
            continue
        existing.append(item)
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
