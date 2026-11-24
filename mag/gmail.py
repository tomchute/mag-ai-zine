"""Read newsletter emails (e.g. paid Substack posts) from a Gmail label over IMAP.

Needs GMAIL_USER, GMAIL_APP_PASSWORD and IMAP connectivity.
"""

from __future__ import annotations

import datetime as dt
import email
import email.utils
import imaplib
import re
from email import policy
from email.message import EmailMessage

from lxml import html as lhtml

from .sanitize import CONTROL
from .util import clean_url, count_words, first_words

IMAP_HOST = "imap.gmail.com"

# Substack (and similar) post URLs: https://<pub>.substack.com/p/<slug>, custom domains, open.substack.com/pub/<pub>/p/<slug>
POST_URL = re.compile(r"""https?://[^\s"'<>]+?/p/[A-Za-z0-9][A-Za-z0-9-]*""")
SKIP_URL = re.compile(r"/(comments|subscribe|app-link|redirect|action)(/|$)|/p/[^/]+/comments", re.I)


def _html_part(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("html",))
    if part is not None:
        return part.get_content()
    part = msg.get_body(preferencelist=("plain",))
    if part is not None:
        from html import escape

        return "".join(f"<p>{escape(p)}</p>" for p in part.get_content().split("\n\n") if p.strip())
    return ""


# Spacer characters newsletters pad their inbox preview with (combining grapheme joiner,
# soft hyphen, zero-width spaces/joiners, word joiner, BOM). Invisible, but each one
# would otherwise count as a "word" and fill the summary.
INVISIBLE = re.compile("[\u034f\u00ad\u200b-\u200d\u2060\ufeff]")
# Inline styles that hide an element: the preheader block, tracking stubs.
HIDDEN = re.compile(r"display\s*:\s*none|max-height\s*:\s*0|mso-hide\s*:\s*all|visibility\s*:\s*hidden", re.I)
SUMMARY_MIN_WORDS = 8  # paragraphs shorter than this are bylines, dates, buttons


def _visible_root(html: str):
    root = lhtml.document_fromstring(CONTROL.sub("", INVISIBLE.sub("", html)) or "<div></div>")
    for bad in root.xpath(".//script|.//style|.//head"):
        bad.drop_tree()
    for el in root.xpath(".//*[@style]"):
        if HIDDEN.search(el.get("style", "")) and el.getparent() is not None:
            el.drop_tree()
    return root


def email_text(html: str) -> tuple[str, str]:
    """(summary source, full visible text) for one newsletter email.

    The summary skips the hidden preview line and its padding, then takes the body's
    first real paragraphs, so it opens on the piece itself rather than on the
    title, byline and buttons above it."""
    if not html or not html.strip():
        return "", ""
    root = _visible_root(html)
    full = " ".join(root.text_content().split())
    paras = [" ".join(p.text_content().split()) for p in root.iter("p")]
    body = " ".join(p for p in paras if len(p.split()) >= SUMMARY_MIN_WORDS)
    return body or full, full


def is_preview(html: str) -> bool:
    """True when the email carries only the opening of the post: some publications email a
    preview that stops mid-thought with "..." and a "View this post on the web" link."""
    if not html or not html.strip():
        return False
    root = _visible_root(html)
    paras = [" ".join(p.text_content().split()) for p in root.iter("p")]
    body = [p for p in paras if len(p.split()) >= SUMMARY_MIN_WORDS]
    visible = " ".join(" ".join(root.itertext()).split()).lower()
    if re.search(r"continue reading this post for free|subscribe to (?:keep|continue) reading", visible):
        return True
    return bool(body) and body[-1].rstrip().endswith(("...", "…"))


def find_post_url(html: str) -> str | None:
    for m in POST_URL.finditer(html):
        url = m.group(0)
        if SKIP_URL.search(url):
            continue
        return clean_url(url)
    return None


def parse_newsletter(msg: EmailMessage) -> dict:
    """Pull candidate metadata out of one newsletter email."""
    html = _html_part(msg)
    lead, text = email_text(html)
    from_name, from_addr = email.utils.parseaddr(str(msg.get("From", "")))
    date = msg.get("Date")
    try:
        published = email.utils.parsedate_to_datetime(str(date)).astimezone(dt.timezone.utc).isoformat()
    except (TypeError, ValueError):
        published = None
    return {
        "origin": "email",
        "message_id": str(msg.get("Message-ID", "")).strip(),
        "title": str(msg.get("Subject", "")).strip(),
        "source": from_name or from_addr,
        "sender": f"{from_name} <{from_addr}>",
        "author": None,
        "url": find_post_url(html),
        "published": published,
        "summary": first_words(lead, 60),
        "words": count_words(text),
        "preview": is_preview(html),
    }


def message_html(msg: EmailMessage) -> str:
    return _html_part(msg)


class Gmail:
    def __init__(self, user: str, password: str, label: str):
        self.user, self.password, self.label = user, password, label
        self._imap: imaplib.IMAP4_SSL | None = None

    def __enter__(self) -> Gmail:
        self._imap = imaplib.IMAP4_SSL(IMAP_HOST)
        self._imap.login(self.user, self.password)
        typ, _ = self._imap.select(f'"{self.label}"', readonly=True)
        if typ != "OK":
            raise RuntimeError(f"Gmail label {self.label!r} not found — create it and a filter that applies it")
        return self

    def __exit__(self, *exc) -> None:
        if self._imap is not None:
            try:
                self._imap.logout()
            except Exception:  # noqa: BLE001
                pass

    def _fetch(self, num: bytes) -> EmailMessage:
        assert self._imap is not None
        typ, data = self._imap.fetch(num, "(BODY.PEEK[])")
        raw = next(part[1] for part in data if isinstance(part, tuple))
        return email.message_from_bytes(raw, policy=policy.default)  # type: ignore[return-value]

    def since(self, since: dt.date) -> list[EmailMessage]:
        assert self._imap is not None
        typ, data = self._imap.search(None, "SINCE", since.strftime("%d-%b-%Y"))
        return [self._fetch(n) for n in (data[0] or b"").split()]

    def total(self) -> int:
        """Messages in the label overall (for diagnosing an empty week vs an empty label)."""
        assert self._imap is not None
        typ, data = self._imap.search(None, "ALL")
        return len((data[0] or b"").split())

    def by_message_id(self, message_id: str) -> EmailMessage | None:
        assert self._imap is not None
        typ, data = self._imap.search(None, "HEADER", "Message-ID", message_id)
        nums = (data[0] or b"").split()
        return self._fetch(nums[-1]) if nums else None
