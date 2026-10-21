"""Fetch open Web3 bounties from Superteam Earn and save them to data/bounties.json.

Step 1 of Sloth Scout: fetching only. No AI, no database.

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
from pathlib import Path

import requests

LISTINGS_URL = "https://superteam.fun/api/listings"
DETAILS_URL = "https://superteam.fun/api/listings/details/{slug}"
LISTING_PAGE_URL = "https://superteam.fun/earn/listing/{slug}"

USER_AGENT = "SlothScout/0.1 (personal daily bounty briefing; https://github.com/guzman0/sloth-scout)"
TIMEOUT_SECONDS = 20
PAUSE_BETWEEN_REQUESTS = 1.0  # seconds; be polite to their server

OUTPUT_FILE = Path(__file__).parent / "data" / "bounties.json"


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
    """Return the plain-text description, or None if the details request fails."""
    try:
        details = get_json(session, DETAILS_URL.format(slug=slug))
    except (requests.RequestException, ValueError) as error:
        print(f"  ! Couldn't get the description for '{slug}': {error}")
        return None, None
    return html_to_text(details.get("description")), details.get("skills")


def main():
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    print(f"Fetching listings from {LISTINGS_URL} ...")
    try:
        listings = get_json(session, LISTINGS_URL)
    except requests.Timeout:
        sys.exit(f"Stopped: Superteam didn't answer within {TIMEOUT_SECONDS} seconds. Try again later.")
    except requests.HTTPError as error:
        sys.exit(f"Stopped: Superteam returned an error ({error.response.status_code}). Nothing was saved.")
    except requests.RequestException as error:
        sys.exit(f"Stopped: couldn't reach Superteam ({error}). Check your internet connection.")
    except ValueError:
        sys.exit("Stopped: Superteam's response wasn't valid JSON. The API may have changed. Nothing was saved.")

    if not isinstance(listings, list):
        sys.exit("Stopped: expected a list of listings but got something else. The API may have changed.")

    now = datetime.now(timezone.utc)
    open_bounties = [item for item in listings if is_open_bounty(item, now)]
    print(f"Got {len(listings)} listings, {len(open_bounties)} are open bounties. Fetching descriptions ...")

    bounties = []
    for item in open_bounties:
        time.sleep(PAUSE_BETWEEN_REQUESTS)
        slug = item["slug"]
        description, skills = fetch_description(session, slug)
        bounties.append({
            "title": item.get("title"),
            "sponsor": (item.get("sponsor") or {}).get("name"),
            "reward": item.get("rewardAmount"),
            "token": item.get("token"),
            "deadline": item.get("deadline"),
            "listing_type": item.get("type"),
            "agent_access": item.get("agentAccess"),
            "link": LISTING_PAGE_URL.format(slug=slug),
            "skills": skills,
            "description": description,
        })

    OUTPUT_FILE.parent.mkdir(exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(
        {"fetched_at": now.isoformat(timespec="seconds"), "source": LISTINGS_URL, "bounties": bounties},
        indent=2, ensure_ascii=False,
    ) + "\n", encoding="utf-8")

    print(f"\nSaved {len(bounties)} open bounties to {OUTPUT_FILE.relative_to(Path(__file__).parent)}\n")
    for bounty in bounties:
        reward = f"{bounty['reward']:,} {bounty['token']}" if bounty["reward"] is not None else "variable"
        print(f"- {bounty['title']}")
        print(f"    reward: {reward}   deadline: {bounty['deadline'][:10] if bounty['deadline'] else 'none'}")


if __name__ == "__main__":
    main()
