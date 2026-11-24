"""Turn arbitrary article/newsletter HTML into small, valid XHTML for an EPUB.

Images, scripts, embeds, share/subscribe chrome and layout tables are removed;
layout containers become paragraphs or are unwrapped; only a short allowlist of
text-level tags and `href` survive.
"""

from __future__ import annotations

import re

from lxml import etree
from lxml import html as lhtml

KEEP = {
    "p", "h2", "h3", "h4", "h5", "h6", "em", "strong", "i", "b", "u", "s", "del",
    "blockquote", "ul", "ol", "li", "a", "pre", "code", "hr", "br", "sup", "sub",
    "cite", "q", "small", "abbr",
}
DROP = {
    "script", "style", "noscript", "iframe", "form", "button", "svg", "img", "picture",
    "video", "audio", "object", "embed", "input", "select", "textarea", "nav", "footer",
    "header", "aside", "figure", "head", "title", "meta", "link", "source", "canvas",
}
BLOCK_CONTAINERS = {
    "div", "section", "article", "main", "center", "table", "tbody", "thead", "tfoot",
    "tr", "td", "th", "dl", "dd", "dt", "details", "summary", "body", "html", "font", "span",
}
BLOCK_CHILDREN = {"p", "h2", "h3", "h4", "h5", "h6", "blockquote", "ul", "ol", "pre", "hr"}
NO_BLOCK_INSIDE = {"p", "li", "a", "em", "strong", "i", "b", "h2", "h3", "h4", "h5", "h6", "q", "cite"}
INLINE = {"em", "strong", "i", "b", "u", "s", "del", "a", "code", "br", "sup", "sub", "cite", "q", "small", "abbr"}

CHROME = re.compile(
    r"(^|[\s_-])(subscri\w*|share|social|button|btn|footer|paywall|related|comments?|"
    r"newsletter-signup|cta|promo|advert\w*|sponsor\w*|unsubscribe|preferences)($|[\s_-])",
    re.I,
)
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _is_chrome(el) -> bool:
    marker = f"{el.get('class', '')} {el.get('id', '')} {el.get('role', '')}"
    return bool(CHROME.search(marker))


def _has_content(el) -> bool:
    return bool((el.text_content() or "").strip()) or el.find(".//hr") is not None


def _clean(el) -> None:
    for child in list(el):
        if not isinstance(child.tag, str):  # comments, processing instructions
            child.drop_tree()
            continue
        tag = child.tag.lower()
        if tag in DROP or _is_chrome(child):
            child.drop_tree()
            continue
        _clean(child)
        if tag == "h1":
            child.tag = tag = "h2"
        if tag in KEEP:
            href = child.get("href") if tag == "a" else None
            for attr in list(child.attrib):
                del child.attrib[attr]
            if href and re.match(r"(?i)^(https?:|mailto:)", href):
                child.set("href", href)
            continue
        # Unknown / layout tag: become a paragraph if it holds only inline text,
        # otherwise unwrap so its block children move up a level.
        has_block = any(isinstance(c.tag, str) and c.tag in BLOCK_CHILDREN for c in child)
        parent_tag = el.tag.lower() if isinstance(el.tag, str) else ""
        if tag in BLOCK_CONTAINERS and not has_block and parent_tag not in NO_BLOCK_INSIDE and tag not in ("span", "font"):
            if _has_content(child):
                child.attrib.clear()
                child.tag = "p"
            else:
                child.drop_tree()
        else:
            child.drop_tag()


BLOCKS = {"p", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "blockquote", "pre", "hr"}
PHRASING_ONLY = {"p", "h2", "h3", "h4", "h5", "h6", "pre"} | (INLINE - {"br"})


def _fix_nesting(root) -> None:
    """A heading or paragraph inside a <p> or <a> is invalid XHTML: unwrap the outer element."""
    changed = True
    while changed:
        changed = False
        for el in root.iter():
            if el is root or not isinstance(el.tag, str) or el.tag not in PHRASING_ONLY:
                continue
            if any(isinstance(d.tag, str) and d.tag in BLOCKS for d in el.iterdescendants()):
                el.drop_tag()
                changed = True
                break


def _wrap_loose_inline(root) -> None:
    """At the root, gather stray text and inline elements into <p>s."""
    lead = (root.text or "").lstrip()
    root.text = None
    new_children = []
    para = None

    def flush():
        nonlocal para
        if para is not None and (para.text_content() or "").strip():
            new_children.append(para)
        para = None

    def ensure_para():
        nonlocal para
        if para is None:
            para = lhtml.Element("p")
        return para

    if lead.strip():
        ensure_para().text = lead
    for child in list(root):
        tail = child.tail
        child.tail = None
        if child.tag in INLINE:
            ensure_para().append(child)
        else:
            flush()
            new_children.append(child)
        if tail and tail.strip():
            p = ensure_para()
            if len(p):
                p[-1].tail = (p[-1].tail or "") + tail
            else:
                p.text = (p.text or "") + tail
    flush()
    for child in list(root):
        root.remove(child)
    for child in new_children:
        root.append(child)


def _drop_empty(root) -> None:
    # Post-order (children first) so a parent emptied by its children is dropped too.
    for el in reversed(list(root.iter())):
        if el is root or not isinstance(el.tag, str) or el.tag in ("br", "hr"):
            continue
        if not (el.text_content() or "").strip() and el.find(".//hr") is None:
            el.drop_tree()


def clean_html(raw: str) -> str:
    """Return a sanitized XHTML fragment (no wrapper element)."""
    raw = CONTROL.sub("", raw or "")
    if not raw.strip():
        return ""
    root = lhtml.fragment_fromstring(raw, create_parent="div")
    _clean(root)
    _fix_nesting(root)
    _wrap_loose_inline(root)
    _drop_empty(root)
    # Collapse runs of <br> at block edges and consecutive <hr>s.
    parts = [etree.tostring(c, method="xml", encoding="unicode", with_tail=False) for c in root]
    out = "\n".join(parts)
    out = re.sub(r"(<hr/>\s*){2,}", "<hr/>\n", out)
    return out


def html_to_text(raw: str) -> str:
    if not raw or not raw.strip():
        return ""
    root = lhtml.fragment_fromstring(CONTROL.sub("", raw), create_parent="div")
    for bad in root.xpath(".//script|.//style|.//head"):
        bad.drop_tree()
    return " ".join(root.text_content().split())


def slice_blocks(fragment: str, start: str | None, end: str | None) -> str:
    """Keep top-level blocks from the one containing `start` to the one containing `end`."""
    if not start and not end:
        return fragment
    root = lhtml.fragment_fromstring(f"<div>{fragment}</div>")
    norm = lambda s: " ".join(s.split()).lower()  # noqa: E731
    blocks = list(root)
    lo, hi = 0, len(blocks) - 1
    if start:
        hits = [i for i, b in enumerate(blocks) if norm(start) in norm(b.text_content())]
        if not hits:
            raise ValueError(f"start marker not found: {start!r}")
        lo = hits[0]
    if end:
        hits = [i for i, b in enumerate(blocks) if i >= lo and norm(end) in norm(b.text_content())]
        if not hits:
            raise ValueError(f"end marker not found: {end!r}")
        hi = hits[0]
    return "\n".join(etree.tostring(b, method="xml", encoding="unicode", with_tail=False) for b in blocks[lo : hi + 1])


def text_to_html(text: str) -> str:
    """Plain text (blank-line separated paragraphs) to <p>s."""
    from html import escape

    paras = re.split(r"\n\s*\n", text.replace("\r\n", "\n"))
    return "\n".join(f"<p>{escape(' '.join(p.split()))}</p>" for p in paras if p.strip())
