"""Assemble articles into a Kindle-friendly EPUB 3 with a generated cover."""

from __future__ import annotations

import datetime as dt
import io
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

from ebooklib import epub
from PIL import Image, ImageDraw, ImageFont

from .config import Config
from .fetch import Article
from .util import issue_monday, issue_title, reading_minutes

CSS = """
body { font-family: serif; line-height: 1.45; margin: 0 4%; }
h1 { font-size: 1.6em; line-height: 1.2; margin: 1.2em 0 0.3em; }
h2 { font-size: 1.25em; margin: 1.2em 0 0.4em; }
h3, h4, h5, h6 { font-size: 1.05em; margin: 1em 0 0.3em; }
p { margin: 0 0 0.75em; text-indent: 0; }
blockquote { margin: 0.8em 1.5em; font-style: italic; }
pre, code { font-family: monospace; font-size: 0.9em; white-space: pre-wrap; }
hr { border: 0; border-top: 1px solid #999; margin: 1.5em 20%; }
.kicker { font-variant: small-caps; letter-spacing: 0.08em; color: #555; margin-bottom: 0; }
.byline { color: #444; font-size: 0.9em; margin-bottom: 1.2em; }
.blurb { font-style: italic; border-left: 3px solid #999; padding-left: 0.8em; margin: 0 0 1.5em; }
.source { font-size: 0.85em; color: #555; margin-top: 2em; border-top: 1px solid #ccc; padding-top: 0.6em; }
.toc-section { font-variant: small-caps; letter-spacing: 0.08em; margin: 1.5em 0 0.4em; }
.toc-item { margin: 0 0 0.6em; }
.toc-meta { color: #555; font-size: 0.85em; }
.masthead { text-align: center; font-size: 2.2em; letter-spacing: 0.15em; margin: 1.5em 0 0.2em; }
.issue-line { text-align: center; color: #555; margin-bottom: 2em; }
"""

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif{bold}.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-{boldname}.ttf",
]


def _font(size: int, bold: bool = False):
    for pattern in FONT_CANDIDATES:
        path = pattern.format(bold="-Bold" if bold else "", boldname="Bold" if bold else "Regular")
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _clamp(draw: ImageDraw.ImageDraw, text: str, font, width: int, max_lines: int) -> list[str]:
    """Wrap to at most max_lines, ending the last line with an ellipsis if the text was cut."""
    lines = _wrap(draw, text, font, width)
    if len(lines) <= max_lines:
        return lines
    kept = lines[:max_lines]
    words = kept[-1].split()
    while words and draw.textlength(" ".join(words) + "…", font=font) > width:
        words.pop()
    kept[-1] = " ".join(words).rstrip(".,;:–—-") + "…"
    return kept


def make_cover(title: str, date_line: str, headlines: list[str], subtitle: str = "") -> bytes:
    W, H, M = 1600, 2560, 130
    paper, ink, accent, grey = (246, 241, 231), (28, 26, 23), (178, 74, 46), (90, 86, 80)
    img = Image.new("RGB", (W, H), paper)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 34], fill=accent)
    y = 230
    mast = _font(230, bold=True)
    d.text((W // 2, y), "MAG", font=mast, fill=ink, anchor="mt")
    y += 250
    d.text((W // 2, y), "ZINE", font=mast, fill=accent, anchor="mt")
    y += 280
    if subtitle:
        d.text((W // 2, y), subtitle, font=_font(70), fill=ink, anchor="mt")
        y += 130
    else:
        y += 20
    d.line([M, y, W - M, y], fill=ink, width=5)
    y += 40
    small = _font(54)
    d.text((W // 2, y), date_line, font=small, fill=ink, anchor="mt")
    y += 100
    d.line([M, y, W - M, y], fill=ink, width=2)
    y += 110

    head, line_h, gap = _font(66, bold=True), 84, 54
    more_font, more_h = _font(58), 80
    bottom = H - 230  # keep clear of the footer
    for i, text in enumerate(headlines):
        lines = _clamp(d, text, head, W - 2 * M, 2)
        left_after = len(headlines) - i - 1
        need = len(lines) * line_h + (gap + more_h if left_after else 0)
        if y + need > bottom:
            break
        for ln in lines:
            d.text((M, y), ln, font=head, fill=ink)
            y += line_h
        y += gap
    else:
        i = len(headlines)
    rest = len(headlines) - i
    if rest:
        d.text((M, y), f"…and {rest} other piece{'s' if rest != 1 else ''}", font=more_font, fill=accent)
    d.text((W // 2, H - 150), title, font=_font(46), fill=grey, anchor="mt")
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _page(title: str, body: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">'
        f'<head><title>{escape(title)}</title><link rel="stylesheet" type="text/css" href="style.css"/></head>'
        f"<body>{body}</body></html>"
    )


def _date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value).strftime("%-d %b %Y")
    except ValueError:
        return value


def _domain(url: str | None) -> str:
    return urlsplit(url).netloc.removeprefix("www.") if url else ""


def _note_html(note: str) -> str:
    paras = [p.strip() for p in note.strip().split("\n\n") if p.strip()]
    return "".join(f"<p>{escape(' '.join(p.split()))}</p>" for p in paras)


def build(cfg: Config, issue: str, articles: list[Article], out: Path, note: str | None = None) -> Path:
    if not articles:
        raise ValueError("no articles to build")
    title = issue_title(cfg.title_format, issue)
    monday = issue_monday(issue)
    date_line = f"Week of {monday.strftime('%-d %B %Y')}"

    # Group by section, in config order.
    order = [s.id for s in cfg.all_sections]
    articles = sorted(articles, key=lambda a: order.index(a.section) if a.section in order else len(order))

    book = epub.EpubBook()
    book.set_identifier(f"mag-ai-zine-{issue}")
    book.set_title(title)
    book.set_language("en")
    book.add_author("Mag AI Zine")
    book.add_metadata("DC", "publisher", "Mag AI Zine")
    book.add_metadata("DC", "date", monday.isoformat())
    book.add_metadata("DC", "description", "; ".join(a.title for a in articles))
    book.set_cover("cover.png", make_cover(title, date_line, [a.title for a in articles], cfg.cover_subtitle), create_page=True)
    book.get_item_with_id("cover").is_linear = True  # epubcheck: a non-linear cover must be linked from somewhere

    css = epub.EpubItem(uid="style", file_name="style.css", media_type="text/css", content=CSS)
    book.add_item(css)

    chapters: list[tuple[Article, epub.EpubHtml]] = []
    for i, art in enumerate(articles, 1):
        section = cfg.section(art.section).title if art.section in order else art.section
        meta = " · ".join(x for x in [art.author, art.source, _date(art.published)] if x)
        body = f'<p class="kicker">{escape(section)}</p><h1>{escape(art.title)}</h1>'
        if meta:
            body += f'<p class="byline">{escape(meta)} · {reading_minutes(art.words)} min</p>'
        if art.blurb:
            body += f'<p class="blurb">{escape(art.blurb)}</p>'
        body += art.body
        if art.url:
            body += f'<p class="source">Read it at its source: <a href="{escape(art.url)}">{escape(_domain(art.url))}</a></p>'
        ch = epub.EpubHtml(title=art.title, file_name=f"a{i:02d}.xhtml", lang="en")
        ch.content = _page(art.title, body)
        ch.add_item(css)
        book.add_item(ch)
        chapters.append((art, ch))

    # "In this issue" page (doubles as the editor's note).
    contents = f'<p class="masthead">MAG AI ZINE</p><p class="issue-line">{escape(date_line)}</p>'
    if note:
        contents += _note_html(note)
    current = None
    for art, ch in chapters:
        if art.section != current:
            current = art.section
            label = cfg.section(current).title if current in order else current
            contents += f'<p class="toc-section">{escape(label)}</p>'
        meta = " · ".join(x for x in [art.author or art.source, f"{reading_minutes(art.words)} min"] if x)
        contents += f'<p class="toc-item"><a href="{ch.file_name}">{escape(art.title)}</a><br/><span class="toc-meta">{escape(meta)}</span></p>'
    intro = epub.EpubHtml(title="In this issue", file_name="contents.xhtml", lang="en")
    intro.content = _page("In this issue", contents)
    intro.add_item(css)
    book.add_item(intro)

    toc: list = [epub.Link("contents.xhtml", "In this issue", "contents")]
    for sid in order + sorted({a.section for a in articles} - set(order)):
        group = [ch for art, ch in chapters if art.section == sid]
        if group:
            label = cfg.section(sid).title if sid in order else sid
            toc.append((epub.Section(label, href=group[0].file_name), group))
    book.toc = toc
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["cover", intro, *[ch for _, ch in chapters]]
    book.guide = [{"type": "toc", "title": "In this issue", "href": "contents.xhtml"}]

    out.parent.mkdir(parents=True, exist_ok=True)
    epub.write_epub(str(out), book, {"epub3_pages": False})
    return out
