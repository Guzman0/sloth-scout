"""Small things shared by every Sloth Scout source."""

import re
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"

USER_AGENT = "SlothScout/0.1 (personal daily bounty briefing; https://github.com/guzman0/sloth-scout)"
TIMEOUT_SECONDS = 20


class SourceSkipped(Exception):
    """A source couldn't run (missing key, blocked domain, site down).

    run.py catches this, prints the reason, notes it in brief.md and carries on
    with the other sources.
    """


def describe_network_error(error, host):
    """Turn a requests/websocket error into a plain-English reason."""
    text = str(error)
    if "403" in text and ("proxy" in text.lower() or "tunnel" in text.lower()):
        return f"{host} is blocked by this machine's network settings"
    if "timed out" in text.lower() or "timeout" in text.lower():
        return f"{host} didn't answer in time"
    return f"couldn't reach {host} ({error.__class__.__name__})"


def redact(text):
    """Strip anything that looks like a secret from a message before it's shown publicly.

    Error messages can quote URLs or headers. brief.md and the web page are public, so
    query strings, credentials in URLs and long token-like strings are removed.
    """
    text = re.sub(r"(\w+://)[^/\s:@]+:[^/\s@]+@", r"\1***@", str(text))  # user:password@ in URLs
    text = re.sub(r"\?[^\s)'\"]*", "?…", text)  # URL query strings (signed URLs, api_key=...)
    return re.sub(r"\b[A-Za-z0-9_\-]{28,}\b", "…", text)  # long token-like strings
