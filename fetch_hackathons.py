"""Fetch open online Web3 hackathons from Devpost and save them to data/hackathons.json.

One of Sloth Scout's sources; run.py calls fetch_devpost(). You can also run
this file on its own to just fetch.

How it works:
  1. Ask Devpost's public hackathon listing API for open, online hackathons with
     the Blockchain theme (one request; no key needed).
  2. Skip invite-only ones, since we can't enter those.
  3. Save title, organizer, prize total, deadline, themes, link and whether it's
     online to data/hackathons.json.

The API is undocumented, so field names come from inspecting real responses.
"""

import json
import re
import sys
from datetime import datetime, timezone

import requests

from common import ROOT, TIMEOUT_SECONDS, USER_AGENT, SourceSkipped, describe_network_error

LISTINGS_URL = "https://devpost.com/api/hackathons"
PARAMS = {
    "status[]": "open",
    "challenge_type[]": "online",
    "themes[]": "Blockchain",  # without this, most results aren't Web3 at all
    "per_page": 40,  # Devpost's maximum
}

OUTPUT_FILE = ROOT / "data" / "hackathons.json"


def clean_prize(raw):
    """'$<span data-currency-value>3,000</span>' -> '$3,000'."""
    if not raw:
        return None
    text = re.sub(r"<[^>]+>", "", raw)
    return " ".join(text.split()).replace("$ ", "$").replace("₹ ", "₹") or None


def parse_end_date(period):
    """Turn Devpost's date range text into the end date as YYYY-MM-DD.

    Devpost writes ranges like "Aug 31 - Oct 23, 2026", "Oct 05 - 25, 2026"
    (same month) or "Dec 01, 2026 - Jan 15, 2027". Returns None if the text
    doesn't look like any of those.
    """
    if not period or " - " not in period:
        return None
    start, end = (part.strip() for part in period.rsplit(" - ", 1))
    if end[:1].isdigit():  # "25, 2026": borrow the month from the start
        end = f"{start.split()[0]} {end}"
    try:
        return datetime.strptime(end, "%b %d, %Y").date().isoformat()
    except ValueError:
        return None


def fetch_devpost():
    """Fetch open online Web3 hackathons, save them and return them.

    Raises SourceSkipped with a plain-English reason if Devpost can't be reached.
    """
    print(f"Fetching hackathons from {LISTINGS_URL} ...")
    try:
        response = requests.get(LISTINGS_URL, params=PARAMS, timeout=TIMEOUT_SECONDS,
                                headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        data = response.json()
    except requests.HTTPError as error:
        raise SourceSkipped(f"Devpost returned an error ({error.response.status_code})")
    except requests.RequestException as error:
        raise SourceSkipped(describe_network_error(error, "devpost.com"))
    except ValueError:
        raise SourceSkipped("Devpost's response wasn't valid JSON; the API may have changed")

    listings = data.get("hackathons")
    if not isinstance(listings, list):
        raise SourceSkipped("Devpost's response had no hackathon list; the API may have changed")

    hackathons = []
    for item in listings:
        if item.get("invite_only"):
            continue
        location = (item.get("displayed_location") or {}).get("location")
        hackathons.append({
            "kind": "hackathon",
            "source": "Devpost",
            "title": item.get("title"),
            "sponsor": item.get("organization_name"),
            "prize_total": clean_prize(item.get("prize_amount")),
            "deadline": parse_end_date(item.get("submission_period_dates")),
            "dates": item.get("submission_period_dates"),
            "themes": [t.get("name") for t in item.get("themes") or []],
            "online": location == "Online",
            "registrations": item.get("registrations_count"),
            "link": item.get("url"),
        })

    total = (data.get("meta") or {}).get("total_count")
    print(f"Got {len(listings)} open online Blockchain hackathons"
          f"{f' (Devpost says {total})' if total is not None else ''}, kept {len(hackathons)} that aren't invite-only.")

    OUTPUT_FILE.parent.mkdir(exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": LISTINGS_URL,
        "hackathons": hackathons,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return hackathons


def main():
    try:
        hackathons = fetch_devpost()
    except SourceSkipped as reason:
        sys.exit(f"Stopped: {reason}. Nothing was saved.")
    print(f"\nSaved {len(hackathons)} hackathons to {OUTPUT_FILE.relative_to(ROOT)}\n")
    for h in hackathons:
        print(f"- {h['title']}")
        print(f"    prize: {h['prize_total'] or 'not stated'}   deadline: {h['deadline'] or 'unknown'}")


if __name__ == "__main__":
    main()
