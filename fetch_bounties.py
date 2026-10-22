"""Fetch open Web3 bounties from Superteam Earn and save them to data/bounties.json.

One of Sloth Scout's sources; run.py calls fetch_superteam(). You can also run
this file on its own to just fetch.

How it works:
  1. Ask Superteam's public listings API for the current listings (one request).
  2. Keep only bounties that are open and whose deadline hasn't passed.
  3. For each of those, ask the details API for its description (one request
     each, with a short pause in between so we don't hammer their server).
  4. Save the results to data/bounties.json and print a short summary.

The API is undocumented, so field names come from inspecting real responses.
If the listings request fails, we stop. We never make up data.
"""

import html
import json
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
import requests

from common import ROOT, TIMEOUT_SECONDS, USER_AGENT, SourceSkipped, describe_network_error

LISTINGS_URL = "https://superteam.fun/api/listings"
DETAILS_URL = "https://superteam.fun/api/listings/details/{slug}"
LISTING_PAGE_URL = "https://superteam.fun/earn/listing/{slug}"

PAUSE_BETWEEN_REQUESTS = 1.0  # seconds; be polite to their server

OUTPUT_FILE = ROOT / "data" / "bounties.json"


class _TextExtractor(HTMLParser):
    """Turns the description HTML into plain text, one line per block."""

    BLOCK_TAGS = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}

    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "li":
            self.parts.append("\n- ")
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(raw_html):
    if not raw_html:
        return None
    parser = _TextExtractor()
    parser.feed(raw_html)
    text = html.unescape("".join(parser.parts))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip() or None


def get_json(session, url):
    """GET a URL and return parsed JSON. Raises requests exceptions on failure."""
    response = session.get(url, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def is_open_bounty(listing, now):
    if listing.get("type") != "bounty" or listing.get("status") != "OPEN":
        return False
    deadline = listing.get("deadline")
    if not deadline:
        return True
    return datetime.fromisoformat(deadline.replace("Z", "+00:00")) > now


def fetch_description(session, slug):
    """Return (description, skills, region), or Nones if the details request fails."""
    try:
        details = get_json(session, DETAILS_URL.format(slug=slug))
    except (requests.RequestException, ValueError) as error:
        print(f"  ! Couldn't get the details for '{slug}': {error}")
        return None, None, None
    return html_to_text(details.get("description")), details.get("skills"), details.get("region")


def fetch_superteam():
    """Fetch open bounties, save them to data/bounties.json and return them.

    Raises SourceSkipped with a plain-English reason if Superteam can't be reached.
    """
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    print(f"Fetching listings from {LISTINGS_URL} ...")
    try:
        listings = get_json(session, LISTINGS_URL)
    except requests.HTTPError as error:
        raise SourceSkipped(f"Superteam returned an error ({error.response.status_code})")
    except requests.RequestException as error:
        raise SourceSkipped(describe_network_error(error, "superteam.fun"))
    except ValueError:
        raise SourceSkipped("Superteam's response wasn't valid JSON; the API may have changed")

    if not isinstance(listings, list):
        raise SourceSkipped("expected a list of listings but got something else; the API may have changed")

    now = datetime.now(timezone.utc)
    open_bounties = [item for item in listings if is_open_bounty(item, now)]
    print(f"Got {len(listings)} listings, {len(open_bounties)} are open bounties. Fetching descriptions ...")

    bounties = []
    for item in open_bounties:
        time.sleep(PAUSE_BETWEEN_REQUESTS)
        slug = item["slug"]
        description, skills, region = fetch_description(session, slug)
        bounties.append({
            "kind": "bounty",
            "source": "Superteam Earn",
            "title": item.get("title"),
            "sponsor": (item.get("sponsor") or {}).get("name"),
            "sponsor_verified": (item.get("sponsor") or {}).get("isVerified"),
            "reward": item.get("rewardAmount"),
            "token": item.get("token"),
            "deadline": item.get("deadline"),
            "listing_type": item.get("type"),
            "agent_access": item.get("agentAccess"),
            "region": region,  # "Global", or the one place it's open to, e.g. "Germany"
            "link": LISTING_PAGE_URL.format(slug=slug),
            "skills": skills,
            "description": description,
        })

    OUTPUT_FILE.parent.mkdir(exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(
        {"fetched_at": now.isoformat(timespec="seconds"), "source": LISTINGS_URL, "bounties": bounties},
        indent=2, ensure_ascii=False,
    ) + "\n", encoding="utf-8")
    return bounties


def main():
    try:
        bounties = fetch_superteam()
    except SourceSkipped as reason:
        sys.exit(f"Stopped: {reason}. Nothing was saved.")

    print(f"\nSaved {len(bounties)} open bounties to {OUTPUT_FILE.relative_to(ROOT)}\n")
    for bounty in bounties:
        reward = f"{bounty['reward']:,} {bounty['token']}" if bounty["reward"] is not None else "variable"
        print(f"- {bounty['title']}")
        print(f"    reward: {reward}   deadline: {bounty['deadline'][:10] if bounty['deadline'] else 'none'}")


if __name__ == "__main__":
    main()
