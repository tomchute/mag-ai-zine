"""Resolve picks into full article bodies (sanitized XHTML)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import feedparser
import trafilatura

from .config import Config
from .gather import entry_html
from .sanitize import clean_html, html_to_text, slice_blocks, text_to_html
from .util import count_words, http_get, url_key

SHORT_EMAIL = 600  # words; a newsletter email shorter than this may be only a preview

FULL_ENOUGH = 400  # words; feed content shorter than this is probably a teaser
PAYWALL_HINT = re.compile(r"(keep reading with a \d+-day free trial|this post is for paid subscribers|subscribe to keep reading)", re.I)


@dataclass
class Article:
    section: str
    title: str
    author: str | None
    source: str | None
    url: str | None
    published: str | None
    body: str  # sanitized XHTML fragment
    blurb: str | None = None

    @property
    def words(self) -> int:
        return count_words(html_to_text(self.body))


def extract_web(html: str, log=None) -> str:
    """Best of trafilatura's recall-oriented HTML mode and its default text mode."""
    rich = _extract_rich(html)
    plain = trafilatura.extract(html, include_comments=False, include_tables=False) or ""
    rich_words, plain_words = count_words(html_to_text(rich)), count_words(plain)
    if log:
        log(f"    extract: html mode {rich_words} words, text mode {plain_words} words")
    # The text mode loses formatting, so only prefer it when it found clearly more.
    if plain_words > 1.25 * rich_words:
        return text_to_html(plain.replace("\n", "\n\n"))
    return rich


def _extract_rich(html: str) -> str:
    out = trafilatura.extract(
        html,
        output_format="html",
        include_comments=False,
        include_tables=False,
        include_images=False,
        include_links=True,
        include_formatting=True,
        favor_recall=True,
    )
    return clean_html(out or "")


def _first_heading_matches(body: str, title: str) -> bool:
    m = re.match(r"\s*<h2>(.*?)</h2>", body, re.S)
    return bool(m) and " ".join(html_to_text(m.group(1)).split()).lower() == " ".join(title.split()).lower()


def strip_duplicate_title(body: str, title: str) -> str:
    if _first_heading_matches(body, title):
        return re.sub(r"^\s*<h2>.*?</h2>\s*", "", body, count=1, flags=re.S)
    return body


class Fetcher:
    def __init__(self, cfg: Config, log=print):
        self.cfg = cfg
        self.log = log
        self._feeds: dict[str, feedparser.FeedParserDict] = {}
        self._gmail = None

    # -- sources ---------------------------------------------------------
    def _feed_for(self, cand: dict):
        section = self.cfg.section(cand["section"])
        for feed in section.feeds:
            if feed.name == cand.get("source"):
                if feed.url not in self._feeds:
                    raw, _ = http_get(feed.url)
                    self._feeds[feed.url] = feedparser.parse(raw)
                return self._feeds[feed.url]
        return None

    def _gmail_conn(self):
        if self._gmail is None:
            from .gmail import Gmail

            user, password = os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASSWORD")
            if not (user and password and self.cfg.gmail_label):
                raise RuntimeError("email pick needs GMAIL_USER, GMAIL_APP_PASSWORD and gmail.label")
            self._gmail = Gmail(user, password, self.cfg.gmail_label).__enter__()
        return self._gmail

    def close(self) -> None:
        if self._gmail is not None:
            self._gmail.__exit__(None, None, None)

    # -- resolution ------------------------------------------------------
    def from_rss(self, cand: dict) -> str:
        feed_body = ""
        feed = None if cand["origin"] == "page" else self._feed_for(cand)
        if feed is not None:
            for entry in feed.entries:
                if url_key(entry.get("link")) == url_key(cand["url"]):
                    feed_body = clean_html(entry_html(entry))
                    break
        text = html_to_text(feed_body)
        feed_words = count_words(text)
        if feed_words >= FULL_ENOUGH and not PAYWALL_HINT.search(text):
            self.log(f"    feed has full text ({feed_words} words)")
            return feed_body
        try:
            raw, ctype = http_get(cand["url"])
            web_body = extract_web(raw.decode("utf-8", "replace"), log=self.log)
            self.log(f"    feed {feed_words} words; web {count_words(html_to_text(web_body))} words from {len(raw) // 1024} KB {ctype.split(';')[0]}")
        except Exception as exc:  # noqa: BLE001
            self.log(f"    feed {feed_words} words; web fetch failed: {exc}")
            web_body = ""
        return max((feed_body, web_body), key=lambda b: count_words(html_to_text(b)))

    def from_email(self, cand: dict) -> str:
        from .gmail import is_preview, message_html

        msg = self._gmail_conn().by_message_id(cand["message_id"])
        if msg is None:
            raise RuntimeError(f"email {cand['message_id']} no longer in label")
        html = message_html(msg)
        extracted = extract_web(html)
        whole = clean_html(html)
        # trafilatura is good at dropping newsletter chrome but can over-trim; fall back if it lost most of the text.
        body = extracted if count_words(html_to_text(extracted)) >= 0.6 * count_words(html_to_text(whole)) else whole
        email_words = count_words(html_to_text(body))
        if (is_preview(html) or email_words < SHORT_EMAIL) and cand.get("url"):
            web = self.from_web_post(cand["url"])
            web_words = count_words(html_to_text(web))
            self.log(f"    email {email_words} words{' (preview)' if is_preview(html) else ''}; web {web_words} words")
            if web_words > email_words:
                return web
        return body

    def from_web_post(self, url: str) -> str:
        """The public post page, for emails that carry only a preview. A refusal (e.g. 403)
        is respected: we log it and keep the email text rather than retrying another way."""
        best = ""
        for attempt in substack_urls(url):
            try:
                raw, _ = http_get(attempt)
            except Exception as exc:  # noqa: BLE001
                self.log(f"    web post: {exc}")
                if "403" in str(exc) or "429" in str(exc):
                    break  # the site said no; don't keep knocking
                continue
            text = raw.decode("utf-8", "replace")
            if "/api/v1/posts/" in attempt:
                try:
                    body = clean_html(json.loads(text).get("body_html") or "")
                except ValueError:
                    body = ""
            else:
                body = extract_web(text)
            if count_words(html_to_text(body)) > count_words(html_to_text(best)):
                best = body
            if count_words(html_to_text(best)) >= FULL_ENOUGH and not PAYWALL_HINT.search(html_to_text(best)):
                break
        return best

    def candidate(self, cand: dict, blurb: str | None) -> Article:
        body = self.from_email(cand) if cand["origin"] == "email" else self.from_rss(cand)
        body = strip_duplicate_title(body, cand["title"])
        if count_words(html_to_text(body)) < 80:
            raise RuntimeError("extracted text is too short (paywall or extraction failure)")
        return Article(
            section=cand["section"],
            title=cand["title"],
            author=cand.get("author"),
            source=cand.get("source"),
            url=cand.get("url"),
            published=cand.get("published"),
            body=body,
            blurb=blurb,
        )

    def classic(self, pick: dict) -> Article:
        raw, ctype = http_get(pick["url"])
        if "text/plain" in ctype or pick["url"].endswith(".txt"):
            body = text_to_html(strip_gutenberg(raw.decode("utf-8", "replace")))
        else:
            body = extract_web(raw.decode("utf-8", "replace"))
        body = slice_blocks(body, pick.get("start"), pick.get("end"))
        body = strip_duplicate_title(body, pick["title"])
        return Article(
            section=pick.get("section", self.cfg.classics_section.id),
            title=pick["title"],
            author=pick.get("author"),
            source=pick.get("source"),
            url=pick["url"],
            published=pick.get("year"),
            body=body,
            blurb=pick.get("note"),
        )


def strip_gutenberg(text: str) -> str:
    start = re.search(r"\*\*\* ?START OF (THE|THIS) PROJECT GUTENBERG.*?\*\*\*", text, re.I)
    end = re.search(r"\*\*\* ?END OF (THE|THIS) PROJECT GUTENBERG", text, re.I)
    return text[start.end() if start else 0 : end.start() if end else len(text)]


def substack_urls(url: str) -> list[str]:
    """Where a Substack post's public text can be read: the post page, then Substack's own
    post API (same host), which returns the post's HTML for free posts."""
    parts = urlsplit(url)
    path = parts.path
    host = parts.netloc
    m = re.match(r"^/pub/([^/]+)(/p/[^/]+)", path) if host == "open.substack.com" else None
    if m:
        host, path = f"{m.group(1)}.substack.com", m.group(2)
    slug = re.match(r"^/p/([^/?#]+)", path)
    urls = [f"https://{host}{path}"]
    if slug:
        urls.append(f"https://{host}/api/v1/posts/{slug.group(1)}")
    return urls
