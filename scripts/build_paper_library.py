"""Build the public paper library from a deliberately bounded, public-only input."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from dashboard_theme import theme_head_script, theme_toggle_script
from site_navigation import decorate

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/papers/library.json"
TRACKS = {"mechanical", "long_term"}
REVIEWS = {"metadata", "abstract", "partial_text", "full_text"}
TEXT_FIELDS = ("research_question", "empirical_results", "limitations", "application_hypothesis", "validation_method")
REQUIRED = {"id", "title", "authors", "year", "field", "tracks", "review_status", "source_url", "core_math", *TEXT_FIELDS}
OPTIONAL = {"publication", "review_note"}


def check_text(value, name, limit=12000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name}: nonempty bounded text required")


def validate(payload):
    """Reject unknown fields so private analysis cannot hitchhike into the bundle."""
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "updated_at", "papers"}:
        raise ValueError("Library must contain only schema_version, updated_at and papers")
    if payload["schema_version"] != 1:
        raise ValueError("Unsupported paper library schema")
    date.fromisoformat(payload["updated_at"])
    papers = payload["papers"]
    if not isinstance(papers, list) or len(papers) > 200:
        raise ValueError("At most 200 papers per library")
    ids = set()
    for paper in papers:
        if not isinstance(paper, dict) or not REQUIRED <= set(paper) or set(paper) - REQUIRED - OPTIONAL:
            raise ValueError("Paper has missing or unexpected fields")
        for field in OPTIONAL & set(paper):
            check_text(paper[field], field, 3000)
        key = paper["id"]
        if not isinstance(key, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", key) or key in ids:
            raise ValueError("Invalid or duplicate paper id")
        ids.add(key)
        for field in ("title", "field"):
            check_text(paper[field], field, 500)
        if not isinstance(paper["authors"], list) or len(paper["authors"]) > 60:
            raise ValueError("authors must be a bounded list; empty means unconfirmed")
        for author in paper["authors"]:
            check_text(author, "author", 200)
        if type(paper["year"]) is not int or not 1900 <= paper["year"] <= date.today().year:
            raise ValueError("Invalid publication year")
        if not isinstance(paper["tracks"], list) or not paper["tracks"] or any(t not in TRACKS for t in paper["tracks"]):
            raise ValueError("Unknown research track")
        if len(set(paper["tracks"])) != len(paper["tracks"]):
            raise ValueError("Duplicate track")
        if paper["review_status"] not in REVIEWS:
            raise ValueError("Unknown original-text review status")
        check_text(paper["source_url"], "source_url", 2000)
        url = urlsplit(paper["source_url"])
        if url.scheme != "https" or not url.hostname or url.username or url.password or any(c.isspace() for c in paper["source_url"]):
            raise ValueError("An HTTPS original-source URL without credentials is required")
        for field in TEXT_FIELDS:
            paragraphs = paper[field] if isinstance(paper[field], list) else [paper[field]]
            if not 1 <= len(paragraphs) <= 30:
                raise ValueError(f"{field}: 1–30 paragraphs required")
            for paragraph in paragraphs:
                check_text(paragraph, field)
        if not isinstance(paper["core_math"], list) or not 1 <= len(paper["core_math"]) <= 12:
            raise ValueError("core_math requires 1–12 formula/intuition pairs")
        for equation in paper["core_math"]:
            if not isinstance(equation, dict) or set(equation) != {"formula", "intuition"}:
                raise ValueError("Equation requires only formula and intuition")
            check_text(equation["formula"], "formula", 3000)
            check_text(equation["intuition"], "intuition")
    return payload


def build(destination: Path, source: Path = SOURCE):
    if source.stat().st_size > 1024 * 1024:
        raise ValueError("Split the paper library before it exceeds 1 MiB")
    payload = validate(json.loads(source.read_text(encoding="utf-8-sig")))
    assets = ROOT / "scripts/dashboard_web"
    destination.mkdir(parents=True, exist_ok=True)
    template = (assets / "papers.html").read_text(encoding="utf-8")
    header = (assets / "header.html").read_text(encoding="utf-8").replace("@@ROOT@@", "../")
    for token in ("@@HOME_CURRENT@@", "@@KR_CURRENT@@", "@@US_CURRENT@@"):
        header = header.replace(token, "")
    header = header.replace('href="../papers/"', 'href="../papers/" aria-current="page"')
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    # The exact same approved payload is available as JSON and embedded for offline previews.
    (destination / "library.json").write_text(data, encoding="utf-8")
    for token, value in {"@@HEADER@@": header, "@@THEME_HEAD@@": theme_head_script(),
                         "@@THEME_TOGGLE@@": theme_toggle_script(),
                         "@@UPDATED@@": html.escape(payload["updated_at"]),
                         "@@DATA@@": data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")}.items():
        template = template.replace(token, value)
    for name in ("dashboard.css", "papers.css", "papers.js"):
        content = (assets / name).read_bytes()
        template = template.replace(f'"{name}"', f'"{name}?v={hashlib.sha256(content).hexdigest()[:12]}"')
        (destination / name).write_bytes(content)
    (destination.parent / "site-nav.css").write_bytes((assets / "site-nav.css").read_bytes())
    (destination / "index.html").write_text(decorate(template, "papers/index.html"), encoding="utf-8")
    return len(payload["papers"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=SOURCE)
    args = parser.parse_args()
    print(f"Built {build(args.destination, args.source)} public paper records")
