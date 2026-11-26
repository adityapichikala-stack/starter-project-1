"""Reads a raw email with Python's email module (works with LF or CRLF line endings)."""
import email
import email.message
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from datetime import date
from email.utils import parseaddr, parsedate_to_datetime


@dataclass(frozen=True)
class ParsedEmail:
    sender_name: str
    sender_address: str
    subject: str
    sent_date: date | None  # from the Date: header; None if missing/unreadable
    body: str

    @property
    def searchable_text(self) -> str:
        """Everything the LLM saw, used by the grounding check."""
        return f"{self.sender_name}\n{self.subject}\n{self.body}"


class _TextOnly(HTMLParser):
    BLOCK = {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "table", "ul", "ol"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _TextOnly()
    p.feed(html)
    lines = [" ".join(line.split()) for line in "".join(p.parts).splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _decode(part: email.message.Message) -> str:
    payload = part.get_payload(decode=True)
    if isinstance(payload, bytes):
        return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    return part.get_payload() or ""


def _body(msg: email.message.Message) -> str:
    """Prefer text/plain; fall back to text/html converted to plain text."""
    parts = [p for p in msg.walk() if not p.is_multipart() and not p.get_filename()]
    for p in parts:
        if p.get_content_type() == "text/plain":
            return _decode(p)
    for p in parts:
        if p.get_content_type() == "text/html":
            return html_to_text(_decode(p))
    return ""


def parse_email(raw: str) -> ParsedEmail:
    msg = email.message_from_string(raw.strip())
    if not msg.keys():  # no headers at all: treat everything as the body
        body = raw.strip()
        if re.search(r"<(html|body|p|div|br)\b", body, re.I):
            body = html_to_text(body)
        return ParsedEmail("", "", "", None, body)

    sent: date | None = None
    if msg.get("Date"):
        try:
            sent = parsedate_to_datetime(msg["Date"]).date()
        except (TypeError, ValueError):
            sent = None

    name, addr = parseaddr(msg.get("From", ""))
    return ParsedEmail(
        sender_name=name,
        sender_address=addr,
        subject=msg.get("Subject", ""),
        sent_date=sent,
        body=_body(msg).strip(),
    )
