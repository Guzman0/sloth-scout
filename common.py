"""Small things shared by every Sloth Scout source."""

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
