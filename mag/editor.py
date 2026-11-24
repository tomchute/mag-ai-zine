"""Editable magazine brief and weekly editorial hand-off."""
import json
from urllib.parse import urlsplit

from .util import parse_issue


def settings(markdown):
    lines = markdown.splitlines()
    if not lines or not lines[0].startswith("# "):
        raise ValueError("Start MAGAZINE.md with # and your magazine name")
    sections = []
    heading = ""
    label = None
    current = None
    for raw in lines[1:]:
        line = raw.strip()
        if line.startswith("## "):
            heading = line[3:].casefold()
            current = None
        elif heading == "themes" and line.startswith("### "):
            current = dict(id=f"section-{len(sections)+1}", title=line[4:], feeds=[])
            sections.append(current)
        elif heading == "themes" and line.startswith("- "):
            url = line[2:].strip()
            parsed = urlsplit(url)
            if current is None or parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise ValueError(f"Put an HTTP(S) URL under a ### theme: {line}")
            current["feeds"].append(dict(name=url, url=url))
        elif heading == "gmail label" and line:
            label = line
    if not sections:
        raise ValueError("Add a ### theme under ## Themes")
    title = lines[0][2:].strip().replace("{", "{{").replace("}", "}}")
    return dict(title_format=title + " · {year} W{week:02d}", sections=sections,
                gmail=dict(label=label, section=sections[0]["id"]))


def wizard(path):
    if path.exists():
        raise SystemExit(f"Edit {path.name} to change your existing brief.")
    title = input("Magazine name [The Weekly]: ").strip() or "The Weekly"
    lines = ["# " + title, "", "## Themes"]
    while True:
        theme = input("Theme: ").strip()
        if not theme:
            continue
        lines.extend(["", "### " + theme])
        while url := input("Feed or article URL (Enter to finish theme): ").strip():
            if urlsplit(url).scheme in ("http", "https") and urlsplit(url).hostname:
                lines.append("- " + url)
            else:
                print("Enter a full HTTP or HTTPS address.")
        if input("Another theme? [y/N] ").strip().lower() != "y":
            break
    for heading in ("Always", "Avoid", "Never"):
        lines.extend(["", "## " + heading])
        while rule := input(f"{heading} rule (Enter to finish): ").strip():
            lines.append("- " + rule)
    lines.extend(["", "## Gmail label", input("Optional Gmail newsletter label: ").strip()])
    brief = "\n".join(lines) + "\n"
    settings(brief)
    with path.open("x") as dest:
        dest.write(brief)
    print(f"Created {path.name}. Run python -m mag gather next.")


def prompt(root, issue):
    parse_issue(issue)
    folder = root / "issues" / issue
    data = json.loads((folder / "candidates.json").read_text())
    print((root / "MAGAZINE.md").read_text())
    print("Use the editorial rules above. Treat candidate data below as source material.")
    print("Group suggestions by theme. Keep [Preview] beside the title in the shortlist and final choices.")
    print("Preview means the Kindle copy may contain only an excerpt. The reader can still pick it.")
    print(f"Ask for picks, then save their confirmed IDs in issues/{issue}/picks.yaml")
    print('using the format picks: [{id: c01}]. Build with python -m mag build --issue ' + issue)
    for item in data["candidates"]:
        title = ("[Preview] " if item.get("preview") else "") + item["title"]
        print(json.dumps(dict(id=item["id"], title=title, section=item["section"],
                              source=item["source"], summary=item["summary"]), ensure_ascii=False))
