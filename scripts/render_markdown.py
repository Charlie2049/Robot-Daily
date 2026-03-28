#!/usr/bin/env python3
"""Render a Markdown digest from the structured JSON dataset."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CONTENT_DIR = ROOT / "content"


def load_entries(json_path: Path) -> List[Dict[str, Any]]:
    if not json_path.exists():
        raise FileNotFoundError(f"Dataset not found: {json_path}")
    with json_path.open("r", encoding="utf-8") as f:
        entries = json.load(f)
    if not isinstance(entries, list):
        raise ValueError("JSON root must be a list")
    return entries


def to_markdown(date_str: str, entries: List[Dict[str, Any]]) -> str:
    header = f"# {date_str} 全球机器人快讯\n\n"
    source_set = sorted({e.get("source", "") for e in entries if e.get("source")})
    if source_set:
        header += "> 数据来源：" + "、".join(source_set) + "\n\n"

    body_parts = []
    for idx, entry in enumerate(entries, start=1):
        title = entry.get("title", "未命名事件")
        date = entry.get("date", "未知日期")
        summary = entry.get("summary", "")
        impact = entry.get("impact")
        source = entry.get("source")
        url = entry.get("source_url")

        lines = [f"## {idx}. {title}", f"- **时间**：{date}  "]
        if summary:
            lines.append(f"- **亮点**：{summary}  ")
        if impact:
            lines.append(f"- **意义**：{impact}  ")
        if source and url:
            lines.append(f"- **来源**：[{source}]({url})")
        elif source:
            lines.append(f"- **来源**：{source}")
        body_parts.append("\n".join(lines))

    return header + "\n\n".join(body_parts) + "\n"


def infer_json_path(date_str: str) -> Path:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return DATA_DIR / f"{dt.year:04d}" / f"{dt.month:02d}" / f"{dt.day:02d}.json"


def infer_output_path(date_str: str) -> Path:
    return CONTENT_DIR / f"{date_str}.md"


def main() -> None:
    parser = argparse.ArgumentParser(description="Render Markdown digest from JSON dataset")
    parser.add_argument("date", help="Report date in YYYY-MM-DD format")
    parser.add_argument("--json", dest="json_path", type=Path, help="Optional explicit JSON path")
    parser.add_argument("--output", dest="output_path", type=Path, help="Optional output Markdown path")
    args = parser.parse_args()

    json_path = args.json_path or infer_json_path(args.date)
    output_path = args.output_path or infer_output_path(args.date)

    entries = load_entries(json_path)
    entries.sort(key=lambda e: e.get("date", ""), reverse=True)

    markdown = to_markdown(args.date, entries)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")
    print(f"Markdown written to {output_path}")


if __name__ == "__main__":  # pragma: no cover
    main()
