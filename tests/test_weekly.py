import contextlib
import datetime as dt
import io
import json
import tempfile
import unittest
import zipfile
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from mag import config
from mag.editor import prompt, wizard
from mag.epub import build
from mag.fetch import Fetcher
from mag.gather import gather, write_candidates
from mag.gmail import parse_newsletter


class WeeklyTest(unittest.TestCase):
    def test_weekly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brief = root / "MAGAZINE.md"
            urls = ["https://example.org/story", "https://example.org/feed"]
            with patch("builtins.input", side_effect=["Sunday", "Ideas", *urls, "", "", "", "", "", ""]):
                wizard(brief)
            cfg = config.load(brief)
            paragraph = " ".join(["The town built a new public reading room."] * 25)
            page = f"<html><title>Reading room</title><article><p>{paragraph}</p></article></html>"
            feed = b"<rss version='2.0'><channel><title>Sample</title></channel></rss>"
            now = dt.datetime.now(dt.timezone.utc)
            with patch("mag.gather.http_get", side_effect=[(page.encode(), "text/html"), (feed, "text/xml")]):
                items, errors = gather(cfg, now, set())
            self.assertFalse(errors)
            msg = EmailMessage()
            msg["Subject"] = "Newsletter"
            msg["Message-ID"] = "<fixture@example.org>"
            msg["From"] = "Editorial <editor@example.org>"
            msg.set_content(f"<p>{paragraph}</p>", subtype="html")
            self.assertFalse(parse_newsletter(msg)["preview"])
            msg.set_content(f"<p>{paragraph}</p><a>Continue reading this post for free in the Substack app</a>", subtype="html")
            preview = parse_newsletter(msg)
            from mag.gather import is_post
            self.assertTrue(is_post(preview))
            preview.update(section=cfg.sections[0].id, id="c02")
            items.append(preview)
            issue = "2026-W47"
            write_candidates(root / "issues" / issue / "candidates.json", issue, items, [], now)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                prompt(root, issue)
            self.assertIn("[Preview] Newsletter", output.getvalue())
            self.assertNotIn("[Preview] Reading room", output.getvalue())
            fetcher = Fetcher(cfg)
            with patch("mag.fetch.http_get", return_value=(page.encode(), "text/html")), patch.object(fetcher, "_gmail_conn") as gmail:
                gmail.return_value.by_message_id.return_value = msg
                articles = [fetcher.candidate(item, None) for item in items]
            dest = root / "weekly.epub"
            build(cfg, issue, articles, dest)
            with zipfile.ZipFile(dest) as book:
                self.assertEqual(book.read("mimetype"), b"application/epub+zip")
                self.assertIsNone(book.testzip())
