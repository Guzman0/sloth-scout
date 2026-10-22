"""Sloth Scout: fetch every source, rank everything with Gemma, write brief.md.

Run with:  python run.py

Steps:
  1. Open memory: MongoDB Atlas if MONGODB_URI works, otherwise the JSON files.
  2. Fetch Superteam bounties and Devpost hackathons.
  3. Search for bounties on other platforms (SerpApi) and read those pages in a
     cloud browser (Browserbase + Stagehand, with Gemma doing the reading).
  4. Rank everything with Gemma, reusing saved rankings where nothing changed,
     and retrying anything that couldn't be ranked last time.
  5. Speak a short summary with ElevenLabs (site/brief.mp3), write brief.md
     ("New today" first, then "Still open") and the web page site/index.html,
     remember what was shown, and log the run.

If a source is missing its key or can't be reached, it's skipped with a clear
reason (in the terminal and in brief.md) and the rest still runs.
"""

import json
import os
import time
from datetime import date, datetime, timezone

from audio import build_script, make_audio, remove_old_audio
from brief import BRIEF_FILE, plan_brief, shown_links, write_brief
from common import DATA_DIR, ROOT, SourceSkipped, redact
from discover_leads import SEARCH_STATS, is_verified, read_leads, search_leads
from fetch_bounties import fetch_superteam
from fetch_hackathons import fetch_devpost
from rank_bounties import (GEMMA_MODEL, STATS, GemmaError, is_eligible, pick_gemma, rank_items,
                           ranking_fingerprint)
from storage import open_store
from webpage import INDEX_FILE, write_site

PROFILE_FILE = ROOT / "profile.md"
LEADS_FILE = DATA_DIR / "leads.json"


def run_source(sources, name, func, *args, summary=lambda result: f"{len(result)} found"):
    """Run one source, time it, and record whether it worked. Returns [] if it was skipped."""
    print(f"\n=== {name} ===")
    started = time.monotonic()
    try:
        result = func(*args)
    except Exception as error:
        # SourceSkipped carries a plain-English reason. Anything else is a crash we didn't expect;
        # record it the same way so one broken source never stops the whole run.
        reason = str(error) if isinstance(error, SourceSkipped) else f"it crashed ({error.__class__.__name__}: {error})"
        reason = redact(reason)  # reasons end up in the public brief and web page
        seconds = round(time.monotonic() - started, 1)
        sources[name] = {"status": "skipped", "reason": f"{reason[0].upper()}{reason[1:]}.", "seconds": seconds}
        print(f"⚠️  Skipping {name}: {reason}. Carrying on with the other sources.")
        return []
    seconds = round(time.monotonic() - started, 1)
    sources[name] = {"status": "ok", "count": len(result), "summary": summary(result), "seconds": seconds}
    print(f"{name}: {sources[name]['summary']} in {seconds}s")
    return result


def why_unverified(lead):
    if "extracted" not in lead and "read_error" not in lead:
        return "not opened in the browser this run"
    if lead.get("read_error"):
        return "the browser couldn't read the page"
    details = lead.get("extracted") or {}
    if details.get("reward") and details.get("deadline"):
        return f"no fixed deadline (page says \"{details['deadline']}\")"
    missing = [f for f in ("reward", "deadline") if not details.get(f)]
    return f"no {' or '.join(missing)} found on the page"


def main():
    started = time.monotonic()
    started_at = datetime.now(timezone.utc)
    today = date.today()
    sources = {}

    if not PROFILE_FILE.exists():
        raise SystemExit("Stopped: profile.md not found. It describes your skills, so Gemma has something to compare against.")
    profile = PROFILE_FILE.read_text(encoding="utf-8")

    # 1. Memory.
    store = open_store()
    if store.note:
        print(f"⚠️  Memory: using the JSON files in data/ because {store.note}.")
    else:
        print(f"Memory: {store.name}")

    # 2. Direct sources.
    bounties = run_source(sources, "Superteam Earn", fetch_superteam,
                          summary=lambda r: f"{len(r)} open bounties")
    hackathons = run_source(sources, "Devpost", fetch_devpost,
                            summary=lambda r: f"{len(r)} open online Blockchain or AI/ML hackathons")

    # Gemma is needed for ranking and for reading lead pages, so pick it once.
    api_key = os.environ.get("GEMINI_API_KEY")
    model, ranking_problem = None, None
    if not api_key:
        ranking_problem = "GEMINI_API_KEY isn't set"
    else:
        try:
            model = pick_gemma(api_key, GEMMA_MODEL)
            print(f"Using {model}")
        except GemmaError as error:
            ranking_problem = str(error)

    # 3. Discovery: search, then read pages in a cloud browser.
    seen = store.seen_items()
    # Skip links we already have: today's bounties and hackathons, and anything in memory,
    # including lead pages the browser read on an earlier run.
    known_links = {item["link"] for item in bounties + hackathons} | set(seen)
    leads = run_source(sources, "SerpApi search", search_leads, known_links,
                       summary=lambda r: f"{len(r)} new listing links from {SEARCH_STATS['searches']} searches")
    read = []
    if leads:
        if model:
            read = run_source(sources, "Browserbase reading", read_leads, leads, model,
                              summary=lambda r: f"{len(r)} pages read, {sum(map(is_verified, r))} with reward and deadline")
        else:
            sources["Browserbase reading"] = {"status": "skipped", "seconds": 0,
                                              "reason": f"Stagehand reads pages with Gemma, and Gemma isn't available ({ranking_problem})."}
    verified = [lead for lead in read if is_verified(lead)]
    unverified = [{**lead, "why_unverified": why_unverified(lead)} for lead in leads if lead not in verified]

    # 4. Rank.
    not_eligible = [b for b in bounties if not is_eligible(b)]
    for b in not_eligible:
        print(f"Not eligible (open to {b['region']} only): {b['title']}")
    to_rank = [b for b in bounties if is_eligible(b)] + hackathons + verified

    print(f"\n=== Ranking {len(to_rank)} items with Gemma ===")
    ranking_started = time.monotonic()
    fingerprint = ranking_fingerprint(model, profile) if model else None
    if model:
        ranked, skipped = rank_items(to_rank, profile, model, api_key, store.previous_rankings(fingerprint))
        print(f"Reused {STATS['reused']} saved rankings; asked Gemma about {len(to_rank) - STATS['reused']}.")
    else:
        print(f"⚠️  Skipping ranking: {ranking_problem}.")
        ranked, skipped = [], [{**item, "skip_reason": ranking_problem} for item in to_rank]
        sources["Gemma ranking"] = {"status": "skipped", "reason": f"{ranking_problem}.", "seconds": 0}
    ranking_seconds = round(time.monotonic() - ranking_started, 1)

    # 5. Audio, brief, web page, memory, log.
    plan = plan_brief(ranked, seen, today)
    script = build_script(plan, today)
    audio_ok = run_source(sources, "ElevenLabs audio", lambda: [make_audio(script)],
                          summary=lambda r: f"site/brief.mp3, {len(script)} characters")
    if not audio_ok:
        remove_old_audio()  # never leave yesterday's audio on the page
    write_brief(today, plan, skipped, not_eligible, unverified, sources, store, model)
    write_site(today, plan, skipped, not_eligible, unverified, sources, store, model,
               script=script if audio_ok else None)
    if model:
        store.save_rankings(fingerprint, model, ranked, skipped, not_eligible)
    # Leads the browser actually read are remembered so they aren't read again tomorrow.
    # Ones it couldn't open stay out of memory and get another try next run.
    read_ok = [lead for lead in read if "extracted" in lead]
    store.record_items(bounties + hackathons + read_ok, shown_links(plan), today)
    DATA_DIR.mkdir(exist_ok=True)
    LEADS_FILE.write_text(json.dumps({
        "found_at": started_at.isoformat(timespec="seconds"),
        "verified": verified,
        "unverified": unverified,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    seconds = round(time.monotonic() - started, 1)
    store.log_run({
        "started_at": started_at.isoformat(timespec="seconds"),
        "model": model,
        "seconds": seconds,
        "memory": store.name if not store.note else f"JSON files ({store.note})",
        "where": "GitHub Actions" if os.environ.get("GITHUB_ACTIONS") else "local",
        "sources": {name: {k: v for k, v in info.items() if k != "summary"} for name, info in sources.items()},
        "serpapi_searches": SEARCH_STATS["searches"],
        "ranking": {"seconds": ranking_seconds, "items": len(to_rank), "ranked": len(ranked),
                    "skipped": len(skipped), "not_eligible": len(not_eligible), **STATS},
        "brief": {"new_bounties": len(plan["new_bounties"]), "new_hackathons": len(plan["new_hackathons"]),
                  "still_open": len(plan["still_open"])},
    })

    print(f"\nDone in {seconds}s. Ranked {len(ranked)}, couldn't rank {len(skipped)}, "
          f"{len(unverified)} unverified leads.")
    print(f"Brief: {len(plan['new_bounties'])} new bounties, {len(plan['new_hackathons'])} new hackathons, "
          f"{len(plan['still_open'])} still open.")
    print(f"Gemma: {STATS['gemma_calls']} calls, {STATS['reused']} reused, "
          f"{STATS['busy_retries']} busy retries, {STATS['json_retries']} JSON retries.")
    skipped_sources = [name for name, info in sources.items() if info["status"] != "ok"]
    if skipped_sources:
        print(f"⚠️  Skipped: {', '.join(skipped_sources)} (reasons in {BRIEF_FILE.name}).")
    print(f"Wrote {BRIEF_FILE.relative_to(ROOT)} and {INDEX_FILE.relative_to(ROOT)}; memory saved to {store.name if not store.note else 'the JSON files'}.")


if __name__ == "__main__":
    main()
