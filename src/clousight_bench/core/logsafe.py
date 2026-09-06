"""Make an untrusted value safe to interpolate into a log record.

Logs are read by humans and parsed by log pipelines, and both treat a newline
as a record boundary. A value that reaches a logger straight from an HTTP path,
a header or a filename can therefore forge whole log entries -- one ``\\n`` and
the caller writes their own line, with their own severity, into our audit
trail. That is log injection (CWE-117); CodeQL reports it as ``py/log-injection``.

``sanitize_for_log`` is the single chokepoint for it: escape the control
characters, cap the length, and hand back something that can only ever be one
field of one line. Call it on every value that crossed a network or user
boundary *before* it reaches a ``logger.*`` call.
"""

from __future__ import annotations

#: Long enough to identify the offending value, short enough that a hostile
#: client cannot flood the log with one request.
DEFAULT_LIMIT = 200


def _escape(ch: str) -> str:
    # \xNN only reads unambiguously for one byte; U+2028 as \x2028 would parse
    # as \x20 + "28", so wider codepoints use the \uNNNN form.
    code = ord(ch)
    return f"\\x{code:02x}" if code <= 0xFF else f"\\u{code:04x}"


def sanitize_for_log(value: object, *, limit: int = DEFAULT_LIMIT) -> str:
    """Return ``value`` as a single-line, printable, length-capped string."""
    text = value if isinstance(value, str) else str(value)
    # CR/LF first and explicitly: this is what actually stops entry forgery, and
    # it is the form CodeQL's log-injection query recognises as a sanitizer.
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    # The remaining C0/C1 controls cannot split a record, but they can still
    # rewrite what a terminal shows (ANSI escapes, backspace), so escape them.
    text = "".join(ch if ch == " " or ch.isprintable() else _escape(ch) for ch in text)
    if len(text) > limit:
        text = text[:limit] + "...(truncated)"
    return text
