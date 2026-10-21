"""Rank the fetched bounties against my profile with Google's Gemma model.

Step 2 of Sloth Scout. Run fetch_bounties.py first.

How it works:
  1. Read data/bounties.json (the bounties) and profile.md (my skills).
  2. Ask the Gemini API which Gemma models my key can use, and pick the largest.
  3. For each bounty, ask Gemma for a fit score, reason, effort and scam risk,
     answered as JSON. If the reply isn't valid JSON, ask once more; if it's
     still bad, skip that bounty and say so.
  4. Save everything to data/ranked.json (best fit first) and write brief.md.

The API key is read from the GEMINI_API_KEY environment variable. It is never
written in this file or printed.
"""

import json
import os
import re
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import requests

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
TIMEOUT_SECONDS = 180  # Gemma thinks before answering, so replies can be slow
PAUSE_BETWEEN_BOUNTIES = 2  # seconds; stay well under the free-tier rate limit
BUSY_RETRY_WAITS = [10, 30, 60]  # seconds to wait after "busy" errors (429/5xx)
MAX_DESCRIPTION_CHARS = 4000  # long descriptions cost tokens without adding much

POOR_FIT_BELOW = 5  # fit scores under this count as "poor fit" in the brief
TOP_N = 5

ROOT = Path(__file__).parent
BOUNTIES_FILE = ROOT / "data" / "bounties.json"
PROFILE_FILE = ROOT / "profile.md"
RANKED_FILE = ROOT / "data" / "ranked.json"
BRIEF_FILE = ROOT / "brief.md"

LEVELS = ("low", "medium", "high")

PROMPT_TEMPLATE = """You are helping a freelancer decide which Web3 bounties to work on.

Today's date is {today}.

## The freelancer's profile
{profile}

## The bounty
Title: {title}
Sponsor: {sponsor} (verified by Superteam: {verified})
Reward: {reward} {token}
Deadline: {deadline} ({days_left})
Skills listed: {skills}
Description:
{description}

## Your task
Judge this bounty for this freelancer:
- fit_score: integer 0-10. How well it matches their strong skills and
  preferences (10 = perfect). Score low if it needs skills listed under
  "Not for me", or if the deadline is less than 3 days away.
- reason: one short line explaining the score.
- effort: "low", "medium" or "high". How much work a good submission takes.
- scam_risk: "low", "medium" or "high". Red flags include an unknown or
  unverified sponsor, unclear payout, an unrealistic deadline, or requests
  for payment, private keys or wallet connections.
- scam_reason: one short line explaining the scam risk.

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
        print(f"    Gemini API {problem}; waiting {wait}s and trying again ...")
        time.sleep(wait)


def _error_message(response):
    try:
        return response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return response.reason


def pick_largest_gemma(api_key):
    """List the Gemma models this key can use and return the biggest one's name."""
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
    return max(candidates)[1]


def build_prompt(bounty, profile, today):
    deadline = bounty.get("deadline")
    if deadline:
        days = (datetime.fromisoformat(deadline.replace("Z", "+00:00")).date() - today).days
        days_left = f"{days} days from today"
    else:
        days_left = "no deadline given"
    skills = ", ".join(
        f"{s.get('skills')} ({', '.join(s.get('subskills') or [])})" for s in bounty.get("skills") or []
    ) or "not listed"
    description = (bounty.get("description") or "No description available.")[:MAX_DESCRIPTION_CHARS]
    return PROMPT_TEMPLATE.format(
        today=today.isoformat(),
        profile=profile.strip(),
        title=bounty.get("title"),
        sponsor=bounty.get("sponsor"),
        verified="yes" if bounty.get("sponsor_verified") else "no",
        reward=bounty.get("reward") if bounty.get("reward") is not None else "not stated",
        token=bounty.get("token") or "",
        deadline=deadline[:10] if deadline else "none",
        days_left=days_left,
        skills=skills,
        description=description,
    )


def ask_gemma(model, prompt, api_key):
    """Send one prompt and return the text of Gemma's final answer."""
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }
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


def rank_one(model, bounty, profile, today, api_key):
    prompt = build_prompt(bounty, profile, today)
    for attempt in (1, 2):
        ranking = parse_ranking(ask_gemma(model, prompt, api_key))
        if ranking:
            return ranking
        if attempt == 1:
            print("    Reply wasn't valid JSON; asking once more ...")
    return None


def format_reward(bounty):
    if bounty.get("reward") is None:
        return "not stated"
    return f"{bounty['reward']:,} {bounty.get('token') or ''}".strip()


def write_brief(ranked, skipped, today):
    poor_fits = [b for b in ranked if b["fit_score"] < POOR_FIT_BELOW]
    lines = [f"# 🦥 Sloth Scout brief: {today.strftime('%A, %B %-d, %Y')}", ""]
    lines.append(f"Top {min(TOP_N, len(ranked))} of {len(ranked)} ranked bounties, best fit first.")
    lines.append("")
    for number, b in enumerate(ranked[:TOP_N], start=1):
        deadline = b["deadline"][:10] if b.get("deadline") else "none"
        lines += [
            f"## {number}. {b['title']}",
            f"- **Reward:** {format_reward(b)}   **Deadline:** {deadline}",
            f"- **Fit:** {b['fit_score']}/10. {b['reason']}",
            f"- **Effort:** {b['effort']}",
            f"- **Scam risk:** {b['scam_risk']}. {b['scam_reason']}",
            f"- **Link:** {b['link']}",
            "",
        ]
    lines.append(f"Skipped as poor fits (score under {POOR_FIT_BELOW}): {len(poor_fits)}.")
    if skipped:
        titles = "; ".join(b["title"] for b in skipped)
        lines.append(f"Couldn't rank {len(skipped)} (Gemma's reply wasn't valid JSON or the API failed): {titles}.")
    BRIEF_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        sys.exit("Stopped: GEMINI_API_KEY isn't set. Get a key at https://aistudio.google.com/apikey "
                 "and run:  export GEMINI_API_KEY=your-key-here")
    if not BOUNTIES_FILE.exists():
        sys.exit(f"Stopped: {BOUNTIES_FILE.relative_to(ROOT)} not found. Run fetch_bounties.py first.")
    if not PROFILE_FILE.exists():
        sys.exit("Stopped: profile.md not found. It describes your skills, so Gemma has something to compare against.")

    bounties = json.loads(BOUNTIES_FILE.read_text(encoding="utf-8"))["bounties"]
    profile = PROFILE_FILE.read_text(encoding="utf-8")
    today = date.today()

    try:
        model = pick_largest_gemma(api_key)
    except GemmaError as error:
        sys.exit(f"Stopped: {error}")
    print(f"Using {model}\n")

    ranked, skipped = [], []
    for number, bounty in enumerate(bounties, start=1):
        print(f"[{number}/{len(bounties)}] {bounty['title']}")
        try:
            ranking = rank_one(model, bounty, profile, today, api_key)
        except GemmaError as error:
            print(f"    Skipped: {error}")
            ranking = None
        if ranking:
            ranked.append({**bounty, **ranking})
            print(f"    fit {ranking['fit_score']}/10, effort {ranking['effort']}, scam risk {ranking['scam_risk']}")
        else:
            skipped.append(bounty)
            print("    Skipped: no valid answer after 2 tries.")
        if number < len(bounties):
            time.sleep(PAUSE_BETWEEN_BOUNTIES)

    ranked.sort(key=lambda b: b["fit_score"], reverse=True)
    RANKED_FILE.write_text(json.dumps({
        "ranked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": model,
        "ranked": ranked,
        "skipped": [{"title": b["title"], "link": b["link"]} for b in skipped],
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_brief(ranked, skipped, today)

    print(f"\nRanked {len(ranked)}, skipped {len(skipped)}. "
          f"Saved {RANKED_FILE.relative_to(ROOT)} and {BRIEF_FILE.relative_to(ROOT)}.")


if __name__ == "__main__":
    main()
