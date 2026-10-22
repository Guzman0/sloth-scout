"""Rank bounties, hackathons and leads against my profile with Google's Gemma model.

run.py calls rank_items(). How it works:
  1. Bounties not open to my region are dropped before ranking (see is_eligible).
  2. Rankings saved by the last run are reused for items that haven't changed,
     as long as the model, profile.md and the prompt are the same. Everything
     else (new items, and ones that couldn't be ranked last time) goes to Gemma.
  3. For each item, Gemma answers in JSON with a fit score, reason, effort and
     scam risk. If the reply isn't valid JSON, it's asked once more; if it's
     still bad, the item is skipped and retried next run.

The API key is read from the GEMINI_API_KEY environment variable. It is never
written in this file or printed.
"""

import hashlib
import json
import os
import re
import time
from datetime import date

import requests

# Which Gemma model to use, or "auto" for the largest one your key can use.
# Override for one run with:  GEMMA_MODEL=gemma-4-26b-a4b-it python run.py
# 31b is the default: on 2026-10-21 the smaller 26b ran about 3.5x faster with
# near-identical fit scores, but missed "connect your wallet" scam red flags.
GEMMA_MODEL = os.environ.get("GEMMA_MODEL", "gemma-4-31b-it")

# Superteam's "region" field is "Global" or the one place a bounty is open to.
# Matched without caring about upper/lower case.
ELIGIBLE_REGIONS = {"global", "united kingdom", "uk"}

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
TIMEOUT_SECONDS = 180  # Gemma thinks before answering, so replies can be slow
PAUSE_BETWEEN_CALLS = 2  # seconds; stay well under the free-tier rate limit
BUSY_RETRY_WAITS = [10, 30, 60]  # seconds to wait after "busy" errors (429/5xx)
MAX_TEXT_CHARS = 4000  # long descriptions cost tokens without adding much

LEVELS = ("low", "medium", "high")

# Counted during a run and saved to the run log.
STATS = {"gemma_calls": 0, "busy_retries": 0, "json_retries": 0, "reused": 0}

PROMPT_TEMPLATE = """You are helping a freelancer decide which Web3 opportunities to work on.

Today's date is {today}.

## The freelancer's profile
{profile}

## The {kind}
{details}

## Your task
Judge this {kind} for this freelancer:
- fit_score: integer 0-10. How well it matches their strong skills, language,
  location and reward preferences (10 = perfect). Score low if it needs skills
  listed under "Not for me", content in a language they don't write in, or
  isn't open to where they live. Ignore how soon the deadline is; that is
  checked separately.
- reason: one short line explaining the score.
- effort: "low", "medium" or "high". How much work a good entry takes.
- scam_risk: "low", "medium" or "high". Red flags include an unknown or
  unverified sponsor, unclear payout, an unrealistic deadline, or requests
  for payment, private keys or wallet connections.
- scam_reason: one short line explaining the scam risk.

Write the reasons to the freelancer directly, using "you" and "your".

Reply with ONLY a JSON object, no other text:
{{"fit_score": 0, "reason": "", "effort": "", "scam_risk": "", "scam_reason": ""}}
"""


class GemmaError(Exception):
    """The Gemini API failed in a way that retrying won't fix."""


def api_request(method, url, api_key, **kwargs):
    """Call the Gemini API, waiting and retrying if Google says it's busy."""
    headers = {"x-goog-api-key": api_key}  # key goes in a header, never the URL
    for wait in BUSY_RETRY_WAITS + [None]:
        try:
            response = requests.request(method, url, headers=headers, timeout=TIMEOUT_SECONDS, **kwargs)
        except requests.Timeout:
            problem = f"no answer within {TIMEOUT_SECONDS}s"
        except requests.RequestException as error:
            raise GemmaError(f"couldn't reach the Gemini API ({error.__class__.__name__})")
        else:
            if response.ok:
                return response.json()
            if response.status_code not in (429, 500, 502, 503, 504):
                raise GemmaError(f"Gemini API error {response.status_code}: {_error_message(response)}")
            problem = f"busy ({response.status_code})"
        if wait is None:
            raise GemmaError(f"Gemini API still {problem} after {len(BUSY_RETRY_WAITS)} retries")
        STATS["busy_retries"] += 1
        print(f"    Gemini API {problem}; waiting {wait}s and trying again ...")
        time.sleep(wait)


def _error_message(response):
    try:
        return response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return response.reason


def pick_gemma(api_key, wanted=GEMMA_MODEL):
    """List the Gemma models this key can use and return the one to use.

    wanted is a model name like "gemma-4-26b-a4b-it", or "auto" for the largest.
    """
    data = api_request("GET", f"{API_BASE}/models?pageSize=1000", api_key)
    candidates = []
    for model in data.get("models", []):
        name = model["name"]  # e.g. "models/gemma-4-31b-it"
        if "gemma" not in name or "generateContent" not in model.get("supportedGenerationMethods", []):
            continue
        # The first "<number>b" in the name is the total parameter count in billions.
        size = re.search(r"(\d+(?:\.\d+)?)b", name.split("/")[-1])
        candidates.append((float(size.group(1)) if size else 0.0, name))
    if not candidates:
        raise GemmaError("this API key has no Gemma models available")
    print("Gemma models available to your key:")
    for size, name in sorted(candidates, reverse=True):
        print(f"  - {name} ({size:g}B)")
    if wanted == "auto":
        return max(candidates)[1]
    wanted_name = wanted if wanted.startswith("models/") else f"models/{wanted}"
    if wanted_name not in {name for _, name in candidates}:
        raise GemmaError(f"GEMMA_MODEL is '{wanted}', but that isn't one of the models listed above")
    return wanted_name


def is_eligible(item):
    """True if the item is open globally or to my region. Unknown region counts as eligible."""
    region = item.get("region")
    if item["kind"] != "bounty" or not region:
        return True  # only Superteam gives a reliable region; Gemma judges the rest
    return region.strip().lower() in ELIGIBLE_REGIONS


def format_reward(item):
    if item["kind"] == "bounty":
        if item.get("reward") is None:
            return "not stated"
        return f"{item['reward']:,} {item.get('token') or ''}".strip()
    if item["kind"] == "hackathon":
        return item.get("prize_total") or "not stated"
    return item.get("reward") or "not stated"


def _details(item):
    """The facts about one item, laid out for the prompt."""
    if item["kind"] == "bounty":
        skills = ", ".join(
            f"{s.get('skills')} ({', '.join(s.get('subskills') or [])})" for s in item.get("skills") or []
        ) or "not listed"
        lines = [
            f"Title: {item.get('title')}",
            f"Sponsor: {item.get('sponsor')} (verified by Superteam: {'yes' if item.get('sponsor_verified') else 'no'})",
            f"Open to: {item.get('region') or 'not stated'}",
            f"Reward: {format_reward(item)}",
            f"Deadline: {(item.get('deadline') or 'none')[:10]}",
            f"Skills listed: {skills}",
            "Description:",
            (item.get("description") or "No description available.")[:MAX_TEXT_CHARS],
        ]
    elif item["kind"] == "hackathon":
        lines = [
            f"Title: {item.get('title')}",
            f"Organizer: {item.get('sponsor') or 'not stated'}",
            f"Prize total: {format_reward(item)}",
            f"Submission period: {item.get('dates') or 'not stated'}",
            f"Themes: {', '.join(item.get('themes') or []) or 'not listed'}",
            f"Online: {'yes' if item.get('online') else 'no'}",
            "Note: this is a Devpost hackathon listing; no description is available.",
        ]
    else:
        lines = [
            f"Title: {item.get('title')}",
            f"Found on: {item.get('source')}",
            f"Sponsor: {item.get('sponsor') or 'not stated'}",
            f"Reward: {format_reward(item)}",
            f"Deadline: {item.get('deadline_text') or 'not stated'}",
            f"Open to: {item.get('region') or 'not stated'}",
            f"Task: {(item.get('task') or 'not stated')[:MAX_TEXT_CHARS]}",
        ]
    return "\n".join(lines)


def build_prompt(item, profile, today):
    return PROMPT_TEMPLATE.format(today=today.isoformat(), profile=profile.strip(),
                                  kind=item["kind"], details=_details(item))


def ask_gemma(model, prompt, api_key):
    """Send one prompt and return the text of Gemma's final answer."""
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }
    STATS["gemma_calls"] += 1
    data = api_request("POST", f"{API_BASE}/{model}:generateContent", api_key, json=body)
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError):
        return ""
    # Gemma sends its thinking first and the answer last, so keep only the last part.
    answers = [p.get("text", "") for p in parts if not p.get("thought")]
    return answers[-1] if answers else ""


def parse_ranking(text):
    """Return the ranking dict if the reply is valid JSON with the right fields, else None."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if not isinstance(result, dict):
        return None
    score = result.get("fit_score")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 10:
        return None
    effort = str(result.get("effort", "")).lower()
    scam_risk = str(result.get("scam_risk", "")).lower()
    if effort not in LEVELS or scam_risk not in LEVELS:
        return None
    return {
        "fit_score": int(round(score)),
        "reason": str(result.get("reason", "")).strip(),
        "effort": effort,
        "scam_risk": scam_risk,
        "scam_reason": str(result.get("scam_reason", "")).strip(),
    }


def rank_one(model, item, profile, today, api_key):
    prompt = build_prompt(item, profile, today)
    for attempt in (1, 2):
        ranking = parse_ranking(ask_gemma(model, prompt, api_key))
        if ranking:
            return ranking
        if attempt == 1:
            STATS["json_retries"] += 1
            print("    Reply wasn't valid JSON; asking once more ...")
    return None


def ranking_fingerprint(model, profile):
    """Changes whenever the model, profile.md or the prompt changes, so old rankings aren't reused."""
    return hashlib.sha256(f"{model}\n{profile}\n{PROMPT_TEMPLATE}".encode()).hexdigest()[:16]


RANKING_FIELDS = ("fit_score", "reason", "effort", "scam_risk", "scam_reason")


def rank_items(items, profile, model, api_key, previous):
    """Rank items with Gemma, reusing rankings from `previous` where possible.

    previous maps link -> item (with ranking) from the last run, already limited
    to the same fingerprint. A saved ranking is reused only if everything Gemma
    was shown about the item is unchanged. Returns (ranked, skipped) lists.
    """
    today = date.today()
    ranked, skipped = [], []
    for number, item in enumerate(items, start=1):
        old = previous.get(item["link"])
        if old and _details(old) == _details(item):
            STATS["reused"] += 1
            ranked.append({**item, **{k: old[k] for k in RANKING_FIELDS}})
            continue
        print(f"[{number}/{len(items)}] {item['kind']}: {item['title']}")
        try:
            ranking = rank_one(model, item, profile, today, api_key)
        except GemmaError as error:
            skipped.append({**item, "skip_reason": str(error)})
            print(f"    Skipped: {error}")
        else:
            if ranking:
                ranked.append({**item, **ranking})
                print(f"    fit {ranking['fit_score']}/10, effort {ranking['effort']}, scam risk {ranking['scam_risk']}")
            else:
                skipped.append({**item, "skip_reason": "Gemma's reply wasn't valid JSON twice"})
                print("    Skipped: no valid answer after 2 tries.")
        time.sleep(PAUSE_BETWEEN_CALLS)
    ranked.sort(key=lambda r: r["fit_score"], reverse=True)
    return ranked, skipped
