"""mag-ai-zine CLI.

  python -m mag gather [--issue 2026-W46]
  python -m mag build  [--issue 2026-W46] [--auto N] [--out out/]
  python -m mag send   [--issue 2026-W46] --epub out/....epub [--dry-run]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

import yaml

from . import config
from .util import issue_id, issue_title, parse_issue

ROOT = config.ROOT
ISSUES = ROOT / "issues"
HISTORY = ROOT / "history.json"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def summary(md: str) -> None:
    """Append to the GitHub Actions job summary when running in CI."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as fh:
            fh.write(md + "\n")


def load_history() -> dict:
    return json.loads(HISTORY.read_text()) if HISTORY.exists() else {"sent": []}


def sent_urls() -> set[str]:
    return {e["url"] for e in load_history()["sent"] if e.get("url")}


def resolve_picks(doc: dict, candidates: list[dict]) -> list[dict]:
    """Turn picks.yaml entries into concrete jobs; unknown ids are an error (a typo in the picks)."""
    by_id = {c["id"]: c for c in candidates}
    jobs = []
    for pick in doc.get("picks") or []:
        if "id" in pick:
            if pick["id"] not in by_id:
                raise SystemExit(f"picks.yaml: unknown candidate id {pick['id']!r}")
            jobs.append({"kind": "candidate", "cand": by_id[pick["id"]], "note": pick.get("note")})
        elif "url" in pick and "title" in pick:
            jobs.append({"kind": "classic", "pick": pick})
        else:
            raise SystemExit(f"picks.yaml: entry needs an `id`, or a `url` + `title`: {pick}")
    return jobs


def auto_picks(candidates: list[dict], per_section: int) -> dict:
    seen: dict[str, int] = {}
    picks = []
    for c in candidates:
        if seen.get(c["section"], 0) < per_section:
            seen[c["section"]] = seen.get(c["section"], 0) + 1
            picks.append({"id": c["id"]})
    return {"note": "Automatic dry-run selection.", "picks": picks}


def next_issue(day: dt.date) -> str:
    """This week's issue, or the next bonus issue (2026-W46-2, ...) if this week's was already sent."""
    n = 1
    while (ISSUES / issue_id(day, n) / "sent.json").exists():
        n += 1
    return issue_id(day, n)


def cmd_gather(args) -> None:
    cfg = config.load()
    if args.lookback:
        cfg.lookback_days = args.lookback
    now = dt.datetime.now(dt.timezone.utc)
    issue = args.issue or next_issue(now.date())
    parse_issue(issue)
    folder = ISSUES / issue
    if not args.out and (folder / "picks.yaml").exists() and not args.force:
        # Re-gathering renumbers candidates, which would point the existing picks at the wrong articles.
        log(f"{issue} already has picks.yaml — not re-gathering (use --force to replace the candidates)")
        summary(f"Skipped: **{issue}** already has picks. Re-run with *force* to replace its candidates.")
        return
    log(f"gathering {issue}")
    from .gather import gather, write_candidates

    items, errors = gather(cfg, now, sent_urls(), log=log)
    out = Path(args.out) if args.out else ISSUES / issue / "candidates.json"

    write_candidates(out, issue, items, errors, now)
    by_section: dict[str, int] = {}
    for c in items:
        by_section[c["section"]] = by_section.get(c["section"], 0) + 1
    summary(f"### Gathered {len(items)} candidates for {issue}\n" + "\n".join(f"- {k}: {v}" for k, v in by_section.items()))
    if errors:
        summary("\n**Source errors**\n" + "\n".join(f"- {e}" for e in errors))


def cmd_build(args) -> None:
    from .epub import build
    from .fetch import Fetcher

    cfg = config.load()
    issue = args.issue or issue_id(dt.date.today())
    folder = ISSUES / issue
    cand_path = Path(args.candidates) if args.candidates else folder / "candidates.json"
    candidates = json.loads(cand_path.read_text())["candidates"] if cand_path.exists() else []
    if args.auto:
        doc = auto_picks(candidates, args.auto)
        extra = folder / "picks.yaml"
        if extra.exists():  # include any classics from a real picks file in the dry run
            doc["picks"] += [p for p in (yaml.safe_load(extra.read_text()) or {}).get("picks", []) if "url" in p]
    else:
        doc = yaml.safe_load((folder / "picks.yaml").read_text()) or {}
    jobs = resolve_picks(doc, candidates)
    log(f"building {issue}: {len(jobs)} picks")

    fetcher = Fetcher(cfg, log=log)
    articles, failures = [], []
    try:
        for job in jobs:
            label = job["cand"]["title"] if job["kind"] == "candidate" else job["pick"]["title"]
            try:
                art = fetcher.candidate(job["cand"], job.get("note")) if job["kind"] == "candidate" else fetcher.classic(job["pick"])
                articles.append(art)
                log(f"  ok   {art.words:>6} words  {label}")
            except Exception as exc:  # noqa: BLE001
                failures.append({"title": label, "error": str(exc)})
                log(f"  FAIL {label}: {exc}")
    finally:
        fetcher.close()

    if not articles:
        raise SystemExit("no articles could be fetched — nothing to build")
    out_dir = Path(args.out)
    epub_path = out_dir / f"Mag-AI-Zine-{issue}.epub"
    build(cfg, issue, articles, epub_path, note=doc.get("note"))
    report = {
        "issue": issue,
        "title": issue_title(cfg.title_format, issue),
        "epub": str(epub_path),
        "articles": [{"title": a.title, "section": a.section, "url": a.url, "words": a.words} for a in articles],
        "failures": failures,
    }
    (out_dir / "build-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    log(f"wrote {epub_path} ({epub_path.stat().st_size // 1024} KB)")
    summary(
        f"### {report['title']}\n"
        + "\n".join(f"- {a['title']} ({a['words']} words)" for a in report["articles"])
        + ("\n\n**Skipped**\n" + "\n".join(f"- {f['title']}: {f['error']}" for f in failures) if failures else "")
    )


def cmd_send(args) -> None:
    from .send import send

    cfg = config.load()
    issue = args.issue or issue_id(dt.date.today())
    epub_path = Path(args.epub)
    report_path = epub_path.parent / "build-report.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else {"articles": []}
    title = issue_title(cfg.title_format, issue)
    if args.dry_run:
        log(f"dry run: would send {epub_path} as {title!r}")
        summary("Dry run — not sent. EPUB is in the workflow artifacts.")
        return
    user, password, to = (os.environ.get(k) for k in ("GMAIL_USER", "GMAIL_APP_PASSWORD", "KINDLE_EMAIL"))
    if not (user and password and to):
        raise SystemExit("send needs GMAIL_USER, GMAIL_APP_PASSWORD and KINDLE_EMAIL")
    send(epub_path, title, user=user, password=password, to=to)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    folder = ISSUES / issue
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "sent.json").write_text(json.dumps({"sent": now, **report}, indent=2, ensure_ascii=False) + "\n")
    hist = load_history()
    known = {e.get("url") for e in hist["sent"]}
    for a in report["articles"]:
        if a.get("url") not in known:
            hist["sent"].append({"issue": issue, "title": a["title"], "section": a["section"], "url": a.get("url")})
    HISTORY.write_text(json.dumps(hist, indent=2, ensure_ascii=False) + "\n")
    log(f"sent {title} to Kindle")
    summary(f"Sent **{title}** to Kindle.")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="mag")
    sub = p.add_subparsers(dest="cmd", required=True)
    from .editor import wizard, prompt
    sub.add_parser("init").set_defaults(func=lambda args: wizard(ROOT / "MAGAZINE.md"))
    weekly = sub.add_parser("prompt")
    weekly.add_argument("--issue")
    weekly.set_defaults(func=lambda args: prompt(ROOT, args.issue or next_issue(dt.date.today())))
    g = sub.add_parser("gather")
    g.add_argument("--issue")
    g.add_argument("--out")
    g.add_argument("--force", action="store_true", help="replace candidates even if picks.yaml exists")
    g.add_argument("--lookback", type=int, help="days to look back (default: lookback_days in sources.yaml)")
    g.set_defaults(func=cmd_gather)
    b = sub.add_parser("build")
    b.add_argument("--issue")
    b.add_argument("--candidates")
    b.add_argument("--auto", type=int, help="dry run: take the first N candidates per section instead of picks.yaml")
    b.add_argument("--out", default="out")
    b.set_defaults(func=cmd_build)
    s = sub.add_parser("send")
    s.add_argument("--issue")
    s.add_argument("--epub", required=True)
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_send)
    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
