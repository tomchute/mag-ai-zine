"""Collect this week's candidates from RSS feeds and the Gmail label.

Output is metadata only (title, link, ~60-word summary, word count): full text
is fetched again at build time and never committed.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

import feedparser

from .config import Config
from .sanitize import html_to_text
from .util import clean_url, count_words, first_words, http_get, url_key


def _entry_datetime(entry) -> dt.datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        t = entry.get(key)
        if t:
            return dt.datetime(*t[:6], tzinfo=dt.timezone.utc)
    return None


def entry_html(entry) -> str:
    content = entry.get("content") or []
    if content:
        return max((c.get("value", "") for c in content), key=len)
    return entry.get("summary", "") or ""


def parse_feed(raw: bytes, section: str, source: str, since: dt.datetime, limit: int) -> list[dict]:
    feed = feedparser.parse(raw)
    out = []
    for entry in feed.entries:
        when = _entry_datetime(entry)
        if when is not None and when < since:
            continue
        link = clean_url(entry.get("link"))
        if not link:
            continue
        text = html_to_text(entry_html(entry))
        out.append(
            {
                "origin": "rss",
                "section": section,
                "source": source,
                "title": " ".join((entry.get("title") or "Untitled").split()),
                "author": entry.get("author"),
                "url": link,
                "published": when.isoformat() if when else None,
                "summary": first_words(html_to_text(entry.get("summary", "")) or text, 60),
                "words": count_words(text),
            }
        )
        if len(out) >= limit:
            break
    return out


def is_post(item: dict) -> bool:
    """Newsletter posts link to their /p/<slug> page; welcome emails, receipts and
    subscription notices don't."""
    return bool(item.get("url") or (item.get("preview") and item.get("message_id")))


def merge(rss: list[dict], emails: list[dict], sent_urls: set[str]) -> list[dict]:
    """Dedupe by URL (email copy wins: it carries paid full text), drop already-sent, assign ids."""
    sent_keys = {url_key(u) for u in sent_urls}
    by_key: dict[str, dict] = {}
    title_key: dict[tuple[str, str], str] = {}
    for item in rss + emails:  # later (email) overwrites earlier (rss)
        key = url_key(item.get("url")) or f"email:{item.get('message_id')}"
        if key in sent_keys:
            continue
        # Same post can carry different URLs in RSS vs email (open.substack.com/pub/...): also match on title.
        tkey = (item["section"], " ".join(item["title"].lower().split()))
        key = title_key.setdefault(tkey, key)
        prev = by_key.get(key)
        if prev and item["origin"] == "email":
            item = {**item, "summary": item["summary"] or prev["summary"], "author": prev.get("author"), "source": prev["source"]}
        by_key[key] = item
    # Sections keep config order (first appearance); newest first within each.
    grouped: dict[str, list[dict]] = {}
    for item in by_key.values():
        grouped.setdefault(item["section"], []).append(item)
    result = []
    for sec in grouped.values():
        result.extend(sorted(sec, key=lambda c: c.get("published") or "", reverse=True))
    for i, item in enumerate(result, 1):
        item["id"] = f"c{i:02d}"
    return result


def gather(cfg: Config, now: dt.datetime, sent_urls: set[str], log=print) -> tuple[list[dict], list[str]]:
    since = now - dt.timedelta(days=cfg.lookback_days)
    rss: list[dict] = []
    errors: list[str] = []
    for section in cfg.sections:
        for feed in section.feeds:
            try:
                raw, media_type = http_get(feed.url)
                if "html" in media_type.lower() or raw.lstrip().lower().startswith((b"<!doctype html", b"<html")):
                    from lxml import html
                    from .gmail import is_preview
                    from .fetch import extract_web
                    tree = html.fromstring(raw)
                    body = html_to_text(extract_web(raw.decode("utf-8", "replace")))
                    got = [dict(origin="page", section=section.id, source=feed.name,
                                title=tree.xpath("string(//title)") or feed.name, url=feed.url,
                                summary=first_words(body, 60), words=count_words(body),
                                preview=is_preview(raw.decode("utf-8", "replace")), published=None)]
                else:
                    got = parse_feed(raw, section.id, feed.name, since, cfg.max_per_feed)
                log(f"  {feed.name}: {len(got)}")
                rss.extend(got)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{feed.name} ({feed.url}): {exc}")
                log(f"  ! {feed.name}: {exc}")

    emails: list[dict] = []
    user, password = os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASSWORD")
    if cfg.gmail_label and user and password:
        from .gmail import Gmail, parse_newsletter

        try:
            with Gmail(user, password, cfg.gmail_label) as gm:
                skipped = 0
                for msg in gm.since(since.date()):
                    item = parse_newsletter(msg)
                    if not is_post(item):
                        skipped += 1
                        continue
                    item["section"] = cfg.email_section(item["sender"])
                    emails.append(item)
                total = gm.total()
            log(
                f"  Gmail '{cfg.gmail_label}': {len(emails)} posts in the last {cfg.lookback_days} days"
                f" ({skipped} non-post emails skipped; {total} in the label overall)"
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Gmail: {exc}")
            log(f"  ! Gmail: {exc}")
    else:
        log("  Gmail: skipped (no GMAIL_USER / GMAIL_APP_PASSWORD or no label configured)")

    return merge(rss, emails, sent_urls), errors


def write_candidates(path: Path, issue: str, items: list[dict], errors: list[str], now: dt.datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"issue": issue, "gathered": now.isoformat(timespec="seconds"), "errors": errors, "candidates": items}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {len(items)} candidates to {path}", file=sys.stderr)
