"""Discover bounties on other platforms: search with SerpApi, read pages with Browserbase.

One of Sloth Scout's sources; run.py calls search_leads() and then read_leads().

How it works:
  1. search_leads(): run a few Google searches through SerpApi (SERPAPI_API_KEY)
     aimed at bounty platforms. Keep result links that look like listings, and
     drop anything already in our Superteam or Devpost data.
  2. read_leads(): open each link in a real cloud browser on Browserbase
     (BROWSERBASE_API_KEY) and have Stagehand pull out the title, sponsor,
     reward, deadline, region and task. Stagehand calls Gemma through our own
     function (see _gemma_for_stagehand), so Gemma does the reading.
     Leads with both a reward and a deadline count as verified and get ranked
     like bounties. The rest go in the brief's "Unverified leads" section.

Every search costs one SerpApi credit (250 a month on the free plan), so the
search list is kept short. Browserbase is capped at MAX_PAGES pages per run.
"""

import asyncio
import json
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
                "linkedin.com", "facebook.com", "reddit.com", "x.com", "twitter.com", "youtube.com", "medium.com",
                "instagram.com", "tiktok.com")

# Results from a general search (no "site:") must mention one of these in their title or
# snippet, so random grant and government pages don't use up browser pages.
# ("dao" is left out: it matches everyday words and names too often.)
WEB3_KEYWORDS = ("web3", "crypto", "blockchain", "solana", "ethereum", "defi", "nft", "onchain", "on-chain",
                 "bug bounty", "audit", "hackathon")
_KEYWORD_PATTERN = re.compile(r"\b(" + "|".join(re.escape(k) for k in WEB3_KEYWORDS) + r")s?\b")

SEARCH_STATS = {"searches": 0}  # SerpApi credits used this run, for the run log

MAX_PAGES = 10
PAGE_LOAD_TIMEOUT_MS = 30_000
# Browserbase shuts the browser down after this even if we crash. We close it ourselves when done,
# so this is only a safety net. Gemma can take 2-4 minutes per page when Google is busy.
BROWSER_SESSION_SECONDS = 2400
# Stagehand sends Gemma the whole page. Very long pages hit Gemma's free-tier limit on text per
# minute (HTTP 429), so we cut the page text down; listing details are usually near the top.
MAX_PAGE_CHARS = 30_000

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


def _site_of(query):
    """'site:dorahacks.io/hackathon bounty' -> 'dorahacks.io'; None for a general search."""
    match = re.search(r"site:([^\s/]+)", query)
    return match.group(1).lower().removeprefix("www.") if match else None


def _matches_search(result, query):
    """Google sometimes ignores "site:" and returns other sites, so check results really match.

    Site searches keep only results on that site. General searches keep only results whose
    title or snippet mentions a Web3, security or hackathon keyword.
    """
    site = _site_of(query)
    if site:
        host = urlparse(result.get("link") or "").netloc.lower().removeprefix("www.")
        return host == site or host.endswith("." + site)
    text = f"{result.get('title') or ''} {result.get('snippet') or ''}".lower()
    return bool(_KEYWORD_PATTERN.search(text))


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
        kept = off_topic = 0
        for result in results:
            link = result.get("link")
            key = (link or "").rstrip("/").lower()
            if not link or key in seen or key in known or not _looks_like_listing(link):
                continue
            if not _matches_search(result, query):
                off_topic += 1
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
        print(f"    {len(results)} results, {kept} new listing links"
              + (f", {off_topic} off-topic skipped" if off_topic else ""))
    return leads


def _parse_deadline(text):
    """Best-effort: turn a deadline as shown on a page into YYYY-MM-DD, or None."""
    if not text:
        return None
    match = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)  # 2026-11-01 or 2026/11/01
    if match:
        year, month, day = map(int, match.groups())
        try:
            return datetime(year, month, day).date().isoformat()
        except ValueError:
            return None
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


def _gemma_for_stagehand(model, gemini_key):
    """Build the function Stagehand calls whenever it needs a model.

    Stagehand's built-in Google option can call Gemma, but Gemma tends to add a
    stray Markdown fence after its JSON, which Stagehand's strict parser
    rejects. So we make the Gemma call ourselves, clean the reply, and hand
    Stagehand the parsed JSON.
    """
    from stagehand import LLMMessageGenerateResult, LLMStructuredGenerateResult

    from rank_bounties import GemmaError, ask_gemma, parse_json_reply

    def message_text(message):
        content = message["content"] if isinstance(message["content"], list) else [message["content"]]
        return "\n".join(block["text"] for block in content if block.get("type") == "text")

    async def generate(params):
        data = params.model_dump(mode="json", by_alias=True, exclude_none=True)
        prompt = "\n\n".join(f"[{m['role']}]\n{message_text(m)}" for m in data["messages"])
        if len(prompt) > MAX_PAGE_CHARS:
            prompt = prompt[:MAX_PAGE_CHARS] + "\n\n[page text cut short to fit Gemma's limits]"
        response_format = data.get("response_format") or {}
        structured = response_format.get("type") == "json_schema"
        if structured:
            prompt += ("\n\nReply with ONLY a JSON object that matches this JSON schema:\n"
                       + json.dumps(response_format.get("schema")))
        for attempt in (1, 2):
            try:
                reply = await asyncio.to_thread(ask_gemma, model, prompt, gemini_key,
                                                json_mode=structured, system=data.get("system_prompt"))
            except GemmaError as error:
                raise RuntimeError(str(error))
            if not structured:
                return LLMMessageGenerateResult(role="assistant", output_format="text",
                                                content=[{"type": "text", "text": reply}])
            parsed = parse_json_reply(reply)
            if isinstance(parsed, dict):
                return LLMStructuredGenerateResult(role="assistant", output_format="json_schema",
                                                   content=[{"type": "text", "text": json.dumps(parsed)}],
                                                   structured_content=parsed)
        raise RuntimeError("Gemma's reply wasn't valid JSON twice")

    return generate


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
        stagehand = await Stagehand.create(browser=browser, model=_gemma_for_stagehand(model, gemini_key))
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
                    if "closed" in str(error).lower():
                        # The browser is gone; the remaining pages stay "not opened this run".
                        print("    The browser connection closed, so stopping here.")
                        break
        finally:
            await _close_quietly(stagehand)
    finally:
        await _close_quietly(browser)  # releases the Browserbase session so it stops costing time


async def _close_quietly(thing):
    """Close Stagehand or the browser, ignoring errors if the connection is already gone."""
    try:
        await thing.close()
    except Exception as error:
        print(f"    (Closing {thing.__class__.__name__} failed, probably already closed: {error.__class__.__name__})")


def read_leads(leads, model):
    """Read up to MAX_PAGES lead pages in Browserbase and fill in their details.

    model is the Gemma model Stagehand should read pages with, e.g. "models/gemma-4-31b-it".
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
    asyncio.run(_read_pages(to_read, model, browserbase_key, gemini_key))

    for lead in to_read:
        details = lead.get("extracted") or {}
        for field in ("title", "sponsor", "reward", "region", "task"):
            if details.get(field):
                lead[field] = details[field]
        lead["deadline_text"] = details.get("deadline")
        lead["deadline"] = _parse_deadline(details.get("deadline"))
    return to_read


# Deadline values that mean "there isn't one".
NO_DEADLINE_WORDS = ("ongoing", "rolling", "tbd", "tba", "none", "n/a", "no deadline", "open until filled")


def is_verified(lead):
    """A lead is verified once the browser found both a reward and a real deadline on the page."""
    details = lead.get("extracted") or {}
    deadline = (details.get("deadline") or "").strip().lower()
    has_deadline = bool(deadline) and not any(word in deadline for word in NO_DEADLINE_WORDS)
    return bool(details.get("reward")) and has_deadline
