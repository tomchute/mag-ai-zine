"""Email the EPUB to a Send-to-Kindle address via Gmail SMTP (GitHub Actions only)."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from pathlib import Path

SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 465
MAX_BYTES = 45 * 1024 * 1024  # Send to Kindle email limit is 50 MB total


def build_message(epub: Path, title: str, sender: str, to: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = title
    msg.set_content(f"{title}\n\nSent by the mag-ai-zine build.")
    msg.add_attachment(epub.read_bytes(), maintype="application", subtype="epub+zip", filename=epub.name)
    return msg


def send(epub: Path, title: str, *, user: str, password: str, to: str) -> None:
    if epub.stat().st_size > MAX_BYTES:
        raise ValueError(f"{epub} is {epub.stat().st_size} bytes — too large for Send to Kindle email")
    msg = build_message(epub, title, user, to)
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=60) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)
