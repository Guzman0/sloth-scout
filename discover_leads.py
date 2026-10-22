"""Discover bounties on other platforms: search with SerpApi, read pages with Browserbase.

One of Sloth Scout's sources; run.py calls search_leads() and then read_leads().

How it works:
  1. search_leads(): run a few Google searches through SerpApi (SERPAPI_API_KEY)
     aimed at bounty platforms. Keep result links that look like listings, and
     drop anything already in our Superteam or Devpost data.
  2. read_leads(): open each link in a real cloud browser on Browserbase
     (BROWSERBASE_API_KEY) and have Stagehand pull out the title, sponsor,
     reward, deadline, region and task, with Gemma doing the reading.
     Leads with both a reward and a deadline count as verified and get ranked
     like bounties. The rest go in the brief's "Unverified leads" section.

Every search costs one SerpApi credit (250 a month on the free plan), so the
search list is kept short. Browserbase is capped at MAX_PAGES pages per run.
"""

import asyncio
import os
import re
from datetime import datetime
from urllib.parse import urlparse

import requests

from common import TIMEOUT_SECONDS, SourceSkipped, describe_network_error

SERPAPI_URL = "https://serpapi.com/search.json"
SEARCHES = [
    "site:dorahacks.io/hackathon bounty",
    "site:layer3.xyz/quests",
    "site:gitcoin.co grants round open",
    "web3 content bounty open submissions",
]
SEARCH_RECENCY = "qdr:m"  # Google's "past month" filter

# Links whose path contains any of these are profiles, submissions or articles, not listings.
SKIP_PATH_PARTS = ("/build/", "/builder/", "/user/", "/users/", "/profile/", "/u/", "/blog/", "/docs/", "/news/")
# Superteam and Devpost are fetched directly, so search results from them are duplicates.
# Social sites and forums show up for general searches but are posts about bounties, not listings.
SKIP_DOMAINS = ("superteam.fun", "devpost.com",
                "linkedin.com", "facebook.com", "reddit.com", "x.com", "twitter.com", "youtube.com", "medium.com")

SEARCH_STATS = {"searches": 0}  # SerpApi credits used this run, for the run log

MAX_PAGES = 10
PAGE_LOAD_TIMEOUT_MS = 30_000
BROWSER_SESSION_SECONDS = 600  # Browserbase shuts the browser down after this, even if we crash

EXTRACT_INSTRUCTION = (
    "Extract the details of this bounty, quest, grant or hackathon listing. "
    "Copy values exactly as shown on the page. Use null for anything not shown; do not guess."
)


def _looks_like_listing(link):
    parsed = urlparse(link)
    host = parsed.netloc.lower().removeprefix("www.")
    if any(host == d or host.endswith("." + d) for d in SKIP_DOMAINS):
        return False
    path = parsed.path.lower()
    if path in ("", "/"):
        return False  # a site's home page isn't a listing
    return not any(part in path + "/" for part in SKIP_PATH_PARTS)


def search_leads(known_links):
    """Run the searches and return new result links as lead dicts.

    known_links is a set of links already found by other sources.
    Raises SourceSkipped if there's no key or SerpApi can't be reached.
    """
    api_key = os.environ.get("SERPAPI_API_KEY")
    if not api_key:
        raise SourceSkipped("SERPAPI_API_KEY isn't set")

    known = {link.rstrip("/").lower() for link in known_links}
    leads, seen = [], set()
    for query in SEARCHES:
        print(f"Searching: {query}")
        SEARCH_STATS["searches"] += 1
        try:
            response = requests.get(SERPAPI_URL, timeout=60, params={
                "engine": "google", "q": query, "tbs": SEARCH_RECENCY, "num": 10, "api_key": api_key,
            })
            data = response.json()
        except requests.RequestException as error:
            raise SourceSkipped(describe_network_error(error, "serpapi.com"))
        except ValueError:
            raise SourceSkipped("SerpApi's response wasn't valid JSON")
        if data.get("error"):
            # "Google hasn't returned any results" is a normal empty search, not a failure.
            if "hasn't returned any results" in data["error"]:
                print("    0 results")
                continue
            raise SourceSkipped(f"SerpApi said: {data['error']}")

        results = data.get("organic_results", [])
        kept = 0
        for result in results:
            link = result.get("link")
            key = (link or "").rstrip("/").lower()
            if not link or key in seen or key in known or not _looks_like_listing(link):
                continue
            seen.add(key)
            kept += 1
            leads.append({
                "kind": "lead",
                "source": urlparse(link).netloc.removeprefix("www."),
                "title": result.get("title"),
                "snippet": result.get("snippet"),
                "link": link,
                "query": query,
            })
        print(f"    {len(results)} results, {kept} new listing links")
    return leads


def _parse_deadline(text):
    """Best-effort: turn a deadline as shown on a page into YYYY-MM-DD, or None."""
    if not text:
        return None
    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if match:
        return match.group(0)
    # Drop commas and "st/nd/rd/th", then try every run of three words as a date.
    words = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text.replace(",", " ")).split()
    for start in range(len(words) - 2):
        chunk = " ".join(words[start:start + 3])
        for fmt in ("%b %d %Y", "%B %d %Y", "%d %b %Y", "%d %B %Y"):
            try:
                return datetime.strptime(chunk, fmt).date().isoformat()
            except ValueError:
                continue
    return None


async def _read_pages(leads, model, browserbase_key, gemini_key):
    # Imported here so a missing install only skips this source, not the whole run.
    from typing import Optional

    from pydantic import BaseModel, Field
    from stagehand import Stagehand, browserbase

    class LeadDetails(BaseModel):
        """The fields Stagehand should pull out of each page."""
        title: Optional[str] = Field(None, description="Name of the bounty, quest, grant or hackathon")
        sponsor: Optional[str] = Field(None, description="Organisation running or paying for it")
        reward: Optional[str] = Field(None, description="Total reward or prize pool with currency, exactly as shown")
        deadline: Optional[str] = Field(None, description="Submission deadline or end date, exactly as shown")
        region: Optional[str] = Field(None, description="Who can enter, e.g. Global or a country; null if not stated")
        task: Optional[str] = Field(None, description="One or two sentences on what participants must do")

    try:
        browser = await browserbase.launch(api_key=browserbase_key, timeout=BROWSER_SESSION_SECONDS)
    except Exception as error:  # the SDK raises several different types here
        if "403" in str(error):
            raise SourceSkipped("the Browserbase browser connection (connect.*.browserbase.com) "
                                "is blocked by this machine's network settings")
        raise SourceSkipped(f"couldn't start a Browserbase browser ({error.__class__.__name__}: {error})")

    try:
        stagehand = await Stagehand.create(browser=browser, model=f"google/{model}", model_api_key=gemini_key)
        try:
            page = (await browser.context.pages())[0]
            for number, lead in enumerate(leads, start=1):
                print(f"[{number}/{len(leads)}] Reading {lead['link']}")
                try:
                    await page.goto(lead["link"], timeout=PAGE_LOAD_TIMEOUT_MS)
                    result = await stagehand.extract(EXTRACT_INSTRUCTION, LeadDetails)
                    lead["extracted"] = result.data.model_dump()
                except Exception as error:
                    lead["read_error"] = f"{error.__class__.__name__}: {str(error)[:200]}"
                    print(f"    Couldn't read it: {lead['read_error']}")
        finally:
            await stagehand.close()
    finally:
        await browser.close()  # releases the Browserbase session so it stops costing time


def read_leads(leads, model):
    """Read up to MAX_PAGES lead pages in Browserbase and fill in their details.

    model is the Gemma model name Stagehand should use, e.g. "gemma-4-31b-it".
    Changes the lead dicts in place and returns the ones that were read.
    Raises SourceSkipped if Browserbase or Stagehand can't be used.
    """
    browserbase_key = os.environ.get("BROWSERBASE_API_KEY")
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not browserbase_key:
        raise SourceSkipped("BROWSERBASE_API_KEY isn't set")
    if not gemini_key:
        raise SourceSkipped("GEMINI_API_KEY isn't set, and Stagehand uses Gemma to read pages")
    try:
        import stagehand  # noqa: F401
    except ImportError:
        raise SourceSkipped("the stagehand package isn't installed (run: pip install -r requirements.txt)")

    to_read = leads[:MAX_PAGES]
    asyncio.run(_read_pages(to_read, model.removeprefix("models/"), browserbase_key, gemini_key))

    for lead in to_read:
        details = lead.get("extracted") or {}
        for field in ("title", "sponsor", "reward", "region", "task"):
            if details.get(field):
                lead[field] = details[field]
        lead["deadline_text"] = details.get("deadline")
        lead["deadline"] = _parse_deadline(details.get("deadline"))
    return to_read


def is_verified(lead):
    """A lead is verified once the browser found both a reward and a deadline on the page."""
    details = lead.get("extracted") or {}
    return bool(details.get("reward")) and bool(details.get("deadline"))
