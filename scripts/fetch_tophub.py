#!/usr/bin/env python3
"""Fetch robotics-related trending news from Tophub + global tech feeds."""

from __future__ import annotations

import calendar
import json
import re
import sys
import time
import random
import urllib.request
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import List, Optional, Set, Tuple
from urllib.parse import urlparse

WORKDIR = Path(__file__).resolve().parents[1]
VENDOR_DIR = WORKDIR / "vendor"
for pkg in (
    "beautifulsoup4-4.12.3",
    "soupsieve-2.5",
    "feedparser-6.0.11",
    "sgmllib3k-1.0.0",
):
    pkg_path = VENDOR_DIR / pkg
    if pkg_path.exists():
        sys.path.insert(0, str(pkg_path))

import feedparser  # type: ignore
from bs4 import BeautifulSoup  # type: ignore

feedparser.USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

LOCAL_TZ = timezone(timedelta(hours=8))
TODAY = datetime.now(timezone.utc).astimezone(LOCAL_TZ).date()
DATA_PATH = WORKDIR / f"data/{TODAY:%Y/%m/%d}.json"
CONTENT_PATH = WORKDIR / f"content/{TODAY:%Y-%m-%d}.md"

KEYWORDS = ["机器人", "具身智能", "robot", "Robot", "智能体", "自动驾驶", "autonomous"]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
PROXY_HEADERS = {"User-Agent": "curl/8.5.0", "Accept": "*/*"}
TIMEOUT = 15
TOPHUB_BASE = "https://tophub.today"
TOPHUB_MIRRORS = ["https://r.jina.ai/"]
TOPHUB_CHANNEL_PATTERN = re.compile(r"https?://tophub\.today/n/[A-Za-z0-9]+")
MARKDOWN_TITLE_PATTERN = re.compile(r"^Title:\s*(.+)$", re.MULTILINE)
MARKDOWN_HEADING_PATTERN = re.compile(r"^#{2,3}\s+(.+)$", re.MULTILINE)
MARKDOWN_ENTRY_PATTERN = re.compile(r"^\s*(\d+)\.\[([^\]]+)\]\(([^)]+)\)(.*)$")
CATEGORY_ENTRY_PATTERN = re.compile(r"\[(\d+)\s+([^\]]+)\]\(([^)]+)\)")
TOPHUB_CATEGORY_PATHS = ["/c/tech", "/c/finance", "/c/ai"]
TOPHUB_EXTRA_CHANNELS = [
    "https://tophub.today/n/DpQvNABoNE",  # 抖音 · 总榜
    "https://tophub.today/n/L4MdA5ldxD",  # 小红书 · 热榜
    "https://tophub.today/n/74KvxwokxM",  # 哔哩哔哩 · 全站日榜
    "https://tophub.today/n/KqndgxeLl9",  # 微博 · 热搜榜
    "https://tophub.today/n/mproPpoq6O",  # 知乎 · 热榜
]
TOPHUB_MAX_CHANNELS = 45
TOPHUB_MAX_ITEMS_PER_CHANNEL = 80
TOPHUB_MAX_CANDIDATES = 200
MAX_ARTICLE_AGE_DAYS = 2
URL_DATE_PATTERN = re.compile(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})")
ISO_DATE_PATTERN = re.compile(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})")
CN_DATE_PATTERN = re.compile(r"(20\d{2})年(\d{1,2})月(\d{1,2})日")
EN_DATE_PATTERN = re.compile(
    r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(\d{1,2}),\s+(20\d{2})",
    re.IGNORECASE,
)
EN_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
RSS_SOURCES = [
    ("TechCrunch Robotics", "https://techcrunch.com/tag/robotics/feed/"),
    ("The Verge", "https://www.theverge.com/rss/index.xml"),
    ("IEEE Spectrum Robotics", "https://spectrum.ieee.org/feeds/topic/robotics.rss"),
    ("MIT Technology Review", "https://www.technologyreview.com/feed/"),
    ("The Robot Report", "https://www.therobotreport.com/feed/"),
    ("Electrek Autopilot", "https://electrek.co/guides/tesla-autopilot/feed/"),
    ("CNBC Technology", "https://www.cnbc.com/id/19854910/device/rss/rss.html"),
    ("Bloomberg Technology", "https://feeds.bloomberg.com/technology/news.rss"),
]


def slugify(text: str) -> str:
    text = re.sub(r"[\s_]+", "-", text.strip().lower())
    text = re.sub(r"[^a-z0-9-]", "", text)
    return text[:60] or "item"


def translate_to_chinese(text: str) -> str:
    if not text:
        return text
    params = urllib.parse.urlencode(
        {"client": "gtx", "sl": "auto", "tl": "zh-CN", "dt": "t", "q": text}
    )
    url = f"https://translate.googleapis.com/translate_a/single?{params}"
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            payload = resp.read().decode("utf-8")
        data = json.loads(payload)
        return "".join(part[0] for part in data[0] if part and part[0]) or text
    except Exception:
        return text


def today_date() -> str:
    return TODAY.isoformat()


def is_recent_date(published: date) -> bool:
    delta = TODAY - published
    return timedelta(0) <= delta <= timedelta(days=MAX_ARTICLE_AGE_DAYS)


def parse_date_components(year: int, month: int, day: int) -> Optional[date]:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_url_date(url: str) -> Optional[date]:
    match = URL_DATE_PATTERN.search(url)
    if not match:
        return None
    return parse_date_components(int(match.group(1)), int(match.group(2)), int(match.group(3)))


def extract_date_from_text(blob: str) -> Optional[date]:
    for pattern in (ISO_DATE_PATTERN, CN_DATE_PATTERN, EN_DATE_PATTERN):
        for match in pattern.finditer(blob):
            if pattern is EN_DATE_PATTERN:
                month = EN_MONTHS.get(match.group(1).lower())
                day = int(match.group(2))
                year = int(match.group(3))
            else:
                year = int(match.group(1))
                month = int(match.group(2))
                day = int(match.group(3))
            published = parse_date_components(year, month, day)
            if published and is_recent_date(published):
                return published
    return None


def resolve_article_date(url: str) -> Optional[date]:
    url_date = parse_url_date(url)
    if url_date and is_recent_date(url_date):
        return url_date

    html = fetch_html(url)
    if not html:
        return None
    snippet = html[:120000]
    published = extract_date_from_text(snippet)
    if published:
        return published
    text = BeautifulSoup(snippet, "html.parser").get_text(" ", strip=True)
    return extract_date_from_text(text)


def parse_feed_datetime(entry) -> Optional[datetime]:
    candidate = entry.get("published_parsed") or entry.get("updated_parsed")
    if candidate:
        try:
            timestamp = calendar.timegm(candidate)
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (TypeError, ValueError, OverflowError):
            pass
    text_value = entry.get("published") or entry.get("updated")
    if text_value:
        try:
            dt = parsedate_to_datetime(text_value)
        except (TypeError, ValueError):
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    return None


@dataclass
class Candidate:
    title: str
    url: str
    heat: str
    source: str
    summary: str
    region: str = "Global"
    category: Optional[List[str]] = None
    published: Optional[date] = None

    def as_entry(self) -> dict:
        categories = self.category or infer_category(self.title)
        entry_date = (self.published or TODAY).isoformat()
        return {
            "id": f"{entry_date}-{slugify(self.source + '-' + self.title)}",
            "date": entry_date,
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


def fetch_html(url: str, retries: int = 3, headers: Optional[dict] = None) -> Optional[str]:
    request_headers = headers or HEADERS
    for attempt in range(1, retries + 1):
        request = urllib.request.Request(url, headers=request_headers)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, errors="ignore")
        except Exception:
            if attempt >= retries:
                return None
            sleep_time = 1.0 + random.random() * 3.0
            time.sleep(sleep_time)
    return None


def build_full_url(href: str) -> str:
    if href.startswith("http"):
        return href
    return f"{TOPHUB_BASE}{href}"


def normalize_channel_url(url: str) -> str:
    if url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


def infer_source_from_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc or ""
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return host or "Tophub"


def extract_markdown_payload(text: str) -> Optional[str]:
    marker = "Markdown Content:"
    idx = text.find(marker)
    if idx == -1:
        return None
    return text[idx + len(marker) :].strip()


def fetch_tophub_page(url: str) -> Tuple[Optional[str], str, Optional[str]]:
    html = fetch_html(url, retries=1)
    if html:
        return html, "html", None
    for prefix in TOPHUB_MIRRORS:
        proxied_url = f"{prefix}{url}"
        proxied_html = fetch_html(proxied_url, headers=PROXY_HEADERS)
        if not proxied_html:
            continue
        title_match = MARKDOWN_TITLE_PATTERN.search(proxied_html)
        title = title_match.group(1).strip() if title_match else None
        markdown = extract_markdown_payload(proxied_html)
        if markdown:
            return markdown, "markdown", title
        return proxied_html, "markdown", title
    return None, "html", None


def discover_channels() -> Tuple[Optional[str], List[str], str]:
    aggregated: List[str] = []
    seen: Set[str] = set()
    first_content: Optional[str] = None
    first_format = "markdown"
    for rel_path in TOPHUB_CATEGORY_PATHS:
        url = build_full_url(rel_path)
        content, fmt, _ = fetch_tophub_page(url)
        if not content:
            continue
        if first_content is None:
            first_content = content
            first_format = fmt
        if fmt == "markdown":
            channel_urls = parse_markdown_channel_urls(content)
        else:
            channel_urls = parse_html_channel_urls(content)
        for channel_url in channel_urls:
            if channel_url in seen:
                continue
            seen.add(channel_url)
            aggregated.append(channel_url)
            if len(aggregated) >= TOPHUB_MAX_CHANNELS:
                break
        if len(aggregated) >= TOPHUB_MAX_CHANNELS:
            break
    if aggregated:
        return first_content, aggregated[:TOPHUB_MAX_CHANNELS], first_format

    # fallback to首页
    homepage_content, content_format, _ = fetch_tophub_page(TOPHUB_BASE)
    if not homepage_content:
        return None, [], content_format
    if content_format == "markdown":
        channels = parse_markdown_channel_urls(homepage_content)
    else:
        channels = parse_html_channel_urls(homepage_content)
    return homepage_content, channels[:TOPHUB_MAX_CHANNELS], content_format


def parse_homepage_html(html: str) -> List[Candidate]:
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


def parse_markdown_channel_urls(markdown: str) -> List[str]:
    urls: List[str] = []
    seen: Set[str] = set()
    for match in TOPHUB_CHANNEL_PATTERN.finditer(markdown):
        normalized = normalize_channel_url(match.group(0))
        if normalized not in seen:
            seen.add(normalized)
            urls.append(normalized)
    return urls


def parse_html_channel_urls(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: List[str] = []
    seen: Set[str] = set()
    for anchor in soup.select('div.cc-cd a[href^="/n/"]'):
        href = anchor.get("href")
        if not href:
            continue
        normalized = build_full_url(href)
        if normalized not in seen:
            seen.add(normalized)
            urls.append(normalized)
    return urls


def parse_category_page_markdown(markdown: str, section_name: str) -> List[Candidate]:
    items: List[Candidate] = []
    for match in CATEGORY_ENTRY_PATTERN.finditer(markdown):
        title = match.group(2).strip()
        if not title or not match_keywords(title):
            continue
        url = match.group(3).strip()
        source = infer_source_from_url(url) or section_name
        candidate = Candidate(title=title, url=url, heat="", source=source, summary=title)
        candidate.published = TODAY
        items.append(candidate)
        if len(items) >= TOPHUB_MAX_ITEMS_PER_CHANNEL:
            break
    return items


def parse_channel_page_html(html: str, source_name: str) -> List[Candidate]:
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
        candidate = Candidate(title=title, url=url, heat=heat, source=source_name, summary=title)
        candidate.published = TODAY
        items.append(candidate)

    return items


def extract_markdown_heading(markdown: str) -> Optional[str]:
    match = MARKDOWN_HEADING_PATTERN.search(markdown)
    if match:
        return match.group(1).strip()
    return None


def parse_channel_page_markdown(markdown: str, source_name: str) -> List[Candidate]:
    items: List[Candidate] = []
    for line in markdown.splitlines():
        match = MARKDOWN_ENTRY_PATTERN.match(line.strip())
        if not match:
            continue
        title = match.group(2).strip()
        if not title or not match_keywords(title):
            continue
        url = match.group(3).strip()
        tail = match.group(4).strip()
        heat = ""
        if tail:
            heat = tail.split("[", 1)[0].strip()
        candidate = Candidate(title=title, url=url, heat=heat, source=source_name, summary=title)
        candidate.published = TODAY
        items.append(candidate)
        if len(items) >= TOPHUB_MAX_ITEMS_PER_CHANNEL:
            break
    return items


def fetch_tophub_candidates() -> List[Candidate]:
    candidates: List[Candidate] = []

    for rel_path in TOPHUB_CATEGORY_PATHS:
        category_url = build_full_url(rel_path)
        page_body, fmt, proxy_title = fetch_tophub_page(category_url)
        if not page_body:
            continue
        section_name = proxy_title or f"Tophub{rel_path}"
        if fmt == "markdown":
            candidates.extend(parse_category_page_markdown(page_body, section_name))
        else:
            candidates.extend(parse_channel_page_html(page_body, section_name))

    for channel_url in TOPHUB_EXTRA_CHANNELS:
        page_body, fmt, proxy_title = fetch_tophub_page(channel_url)
        if not page_body:
            continue
        source_name = extract_markdown_heading(page_body) if fmt == "markdown" else None
        source_name = source_name or proxy_title or channel_url
        if fmt == "markdown":
            candidates.extend(parse_channel_page_markdown(page_body, source_name))
        else:
            candidates.extend(parse_channel_page_html(page_body, source_name))

    if len(candidates) > TOPHUB_MAX_CANDIDATES:
        candidates = candidates[:TOPHUB_MAX_CANDIDATES]
    return candidates


def fetch_rss_candidates() -> List[Candidate]:
    items: List[Candidate] = []
    for source_name, feed_url in RSS_SOURCES:
        try:
            feed = feedparser.parse(feed_url)
        except Exception:
            continue
        for entry in feed.entries:
            title = (entry.get("title") or "").strip()
            if not title or not match_keywords(title):
                continue
            link = entry.get("link") or ""
            if not link:
                continue
            published_dt = parse_feed_datetime(entry)
            if not published_dt:
                continue
            published_date = published_dt.date()
            if not is_recent_date(published_date):
                continue
            summary_html = entry.get("summary", "")
            summary_text = BeautifulSoup(summary_html, "html.parser").get_text(" ", strip=True)
            translated_title = translate_to_chinese(title)
            display_title = translated_title or title
            summary_full = summary_text or title
            if translated_title and translated_title != title:
                summary_full = f"{summary_full}（原题：{title}）"
            items.append(
                Candidate(
                    title=display_title,
                    url=link,
                    heat=entry.get("published", ""),
                    source=source_name,
                    summary=summary_full,
                    category=None,
                    published=published_date,
                )
            )
    return items


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
    if "自动驾驶" in title_lower or "autonomous" in title_lower:
        categories.append("autonomous-driving")
    if not categories:
        categories.append("general")
    return categories


def load_existing() -> List[dict]:
    if DATA_PATH.exists():
        return json.loads(DATA_PATH.read_text())
    return []


def load_previous_day_entries() -> List[dict]:
    previous_date = TODAY - timedelta(days=1)
    prev_path = WORKDIR / f"data/{previous_date:%Y/%m/%d}.json"
    if prev_path.exists():
        return json.loads(prev_path.read_text())
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


def remove_previous_day_duplicates(data: List[dict]) -> List[dict]:
    previous_entries = load_previous_day_entries()
    if not previous_entries:
        return data
    prev_titles = {item.get("title", "").lower() for item in previous_entries if item.get("title")}
    prev_urls = {item.get("source_url", "") for item in previous_entries if item.get("source_url")}
    filtered: List[dict] = []
    for item in data:
        title_key = item.get("title", "").lower()
        url_key = item.get("source_url", "")
        if title_key in prev_titles or (url_key and url_key in prev_urls):
            continue
        filtered.append(item)
    return filtered


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


def filter_recent_candidates(candidates: List[Candidate]) -> List[Candidate]:
    filtered: List[Candidate] = []
    for candidate in candidates:
        published = candidate.published
        if not published:
            published = resolve_article_date(candidate.url)
        if not published:
            published = TODAY
        if not is_recent_date(published):
            continue
        candidate.published = published
        filtered.append(candidate)
    return filtered


def main() -> None:
    tophub_candidates = fetch_tophub_candidates()
    recent_tophub = filter_recent_candidates(tophub_candidates)
    rss_candidates = fetch_rss_candidates()

    entries = [candidate.as_entry() for candidate in (recent_tophub + rss_candidates)]
    existing = load_existing()
    merged = merge_entries(existing, entries)
    merged = remove_previous_day_duplicates(merged)
    write_json(merged)
    write_markdown(merged)


if __name__ == "__main__":
    main()
