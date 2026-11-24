"""Small shared helpers: issue ids, URLs, HTTP, word counts."""

from __future__ import annotations

import datetime as dt
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit

# An honest client identity. If a site refuses it (403/429), we accept the refusal:
# no disguising as a browser. Paywalled or bot-protected newsletters come in by email instead.
USER_AGENT = "Mozilla/5.0 (compatible; mag-ai-zine/0.1; personal feed reader)"
TRACKING = re.compile(r"^(utm_|r$|ref$|ref_src$|source$|publication_id$|post_id$|isFreemail$|triedRedirect$)", re.I)


ISSUE_RE = re.compile(r"^(\d{4})-W(\d{2})(?:-(\d+))?$")


def issue_id(day: dt.date, extra: int = 1) -> str:
    """ISO week id, e.g. 2026-W46; a second issue in the same week is 2026-W46-2."""
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}" + (f"-{extra}" if extra > 1 else "")


def parse_issue(issue: str) -> tuple[int, int, int]:
    m = ISSUE_RE.match(issue)
    if not m:
        raise ValueError(f"bad issue id {issue!r} (expected e.g. 2026-W46 or 2026-W46-2)")
    return int(m[1]), int(m[2]), int(m[3] or 1)


def issue_monday(issue: str) -> dt.date:
    year, week, _ = parse_issue(issue)
    return dt.date.fromisocalendar(year, week, 1)


def issue_title(fmt: str, issue: str) -> str:
    year, week, extra = parse_issue(issue)
    return fmt.format(year=year, week=week) + (f" ({extra})" if extra > 1 else "")


def clean_url(url: str | None) -> str | None:
    """The fetchable URL: tracking params and fragment removed, host and path untouched."""
    if not url:
        return None
    parts = urlsplit(url.strip())
    query = "&".join(q for q in parts.query.split("&") if q and not TRACKING.match(q.split("=", 1)[0]))
    return urlunsplit((parts.scheme or "https", parts.netloc, parts.path, query, ""))


def url_key(url: str | None) -> str | None:
    """Dedupe key only — never fetch this (dropping www. or a trailing slash can 404)."""
    if not url:
        return None
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    return f"{host}{parts.path.rstrip('/')}"


_WORD = re.compile(r"\w+(?:['’]\w+)*")


def count_words(text: str) -> int:
    return len(_WORD.findall(text or ""))


def first_words(text: str, n: int = 60) -> str:
    words = (text or "").split()
    out = " ".join(words[:n])
    return out + ("…" if len(words) > n else "")


def reading_minutes(words: int, wpm: int = 230) -> int:
    return max(1, round(words / wpm))


def http_get(url: str, timeout: int = 30, retries: int = 2) -> tuple[bytes, str]:
    """GET a URL; returns (body, content-type). Retries transient network failures only;
    a refusal (403/429) or a 404 is final."""
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/rss+xml,application/atom+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-GB,en;q=0.9",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read(), resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code in (403, 404, 410, 429):
                break
        except Exception as exc:  # noqa: BLE001 — network errors come in many shapes
            last = exc
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed: {last}")
