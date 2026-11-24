"""Load sources.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Feed:
    name: str
    url: str


@dataclass
class Section:
    id: str
    title: str
    feeds: list[Feed] = field(default_factory=list)


@dataclass
class Config:
    title_format: str
    lookback_days: int
    max_per_feed: int
    sections: list[Section]
    gmail_label: str | None
    gmail_section: str
    gmail_route: dict[str, str]
    classics_section: Section
    cover_subtitle: str = ""

    def section(self, sid: str) -> Section:
        if sid == self.classics_section.id:
            return self.classics_section
        for s in self.sections:
            if s.id == sid:
                return s
        raise KeyError(f"unknown section {sid!r}")

    def email_section(self, sender: str) -> str:
        """Section for a labelled email: first `gmail.route` key found in the sender, else the default."""
        sender = sender.lower()
        for key, section in self.gmail_route.items():
            if key in sender:
                return section
        return self.gmail_section

    @property
    def all_sections(self) -> list[Section]:
        return [*self.sections, self.classics_section]


def load(path: Path | None = None) -> Config:
    from .editor import settings
    data = settings((path or ROOT / "MAGAZINE.md").read_text())
    sections = [
        Section(s["id"], s["title"], [Feed(f["name"], f["url"]) for f in s.get("feeds") or []])
        for s in data["sections"]
    ]
    gmail = data.get("gmail") or {}
    classics = data.get("classics") or {}
    return Config(
        title_format=data.get("title_format", "Mag AI Zine · {year} · Wk {week:02d}"),
        lookback_days=int(data.get("lookback_days", 8)),
        max_per_feed=int(data.get("max_per_feed", 6)),
        sections=sections,
        gmail_label=gmail.get("label"),
        gmail_section=gmail.get("section", sections[0].id if sections else "substack"),
        gmail_route={str(k).lower(): v for k, v in (gmail.get("route") or {}).items()},
        classics_section=Section(classics.get("id", "classics"), classics.get("title", "From the Shelf")),
        cover_subtitle=str(data.get("cover_subtitle") or ""),
    )
