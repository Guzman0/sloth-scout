"""Turn the brief into a short spoken summary with ElevenLabs: site/brief.mp3.

run.py calls build_script() and then make_audio(). How it works:
  1. build_script() writes a friendly script from the brief's plan: the top
     new picks with their rewards and deadlines, any scam warnings, and how
     many earlier picks are still open. It stops adding sentences before
     MAX_SCRIPT_CHARS so it stays inside the free monthly quota.
  2. make_audio() sends the script to ElevenLabs (ELEVENLABS_API_KEY) and
     saves site/brief.mp3, replacing yesterday's file.

If the key is missing or the quota has run out, make_audio() raises
SourceSkipped; run.py then removes the old mp3 (so the page never plays
yesterday's news) and says why in the brief.
"""

import os
import re
from datetime import date

import requests

from common import ROOT, SourceSkipped, describe_network_error

TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
# "Alice - Clear, Engaging Educator": a friendly British premade voice, available on every plan.
VOICE_ID = "Xb7hH8MSUJpSbSDYk0k2"
# Flash costs half a credit per character (measured: 30 characters cost 15 credits).
MODEL_ID = "eleven_flash_v2_5"
OUTPUT_FORMAT = "mp3_22050_32"  # 32 kbps mono: clear for speech, ~240 KB a minute

MAX_SCRIPT_CHARS = 900
PICKS_SPOKEN = 3
STILL_OPEN_SPOKEN = 2  # on quiet days (no new picks), the best still-open picks are named instead

SITE_DIR = ROOT / "site"
AUDIO_FILE = SITE_DIR / "brief.mp3"
SCRIPT_FILE = SITE_DIR / "brief.txt"


def _speakable(text):
    """Drop emoji and symbols the voice would stumble over; keep letters, numbers and punctuation."""
    text = re.sub(r"[^\w\s.,:;!?'&$%/()-]", " ", text or "")
    return " ".join(text.split())


def _spoken_date(iso):
    d = date.fromisoformat(iso[:10])
    return f"{d.strftime('%B')} {d.day}"


def _spoken_reward(item):
    if item["kind"] == "bounty":
        return f"{item['reward']:,} {item.get('token') or ''}".strip() if item.get("reward") is not None else None
    if item["kind"] == "hackathon":
        return item.get("prize_total")
    return item.get("reward")


def _scam_warning(item, first):
    """A spoken warning for medium/high scam risk: full the first time, short after that."""
    if item.get("scam_risk") not in ("medium", "high"):
        return ""
    wallet = "wallet" in (item.get("scam_reason") or "").lower()
    if first:
        why = " and it asks you to connect a wallet" if wallet else ""
        return f" Heads up: scam risk is {item['scam_risk']}{why}, so check the sponsor before you start."
    return f" Also {item['scam_risk']} scam risk{', with a wallet connection' if wallet else ''}."


def _pick_sentence(label, item, first_warning):
    parts = [f"{label}: {_speakable(item['title'])}"]
    reward = _spoken_reward(item)
    if reward:
        parts.append(f"paying {_speakable(reward)}")
    if item.get("deadline"):
        parts.append(f"due {_spoken_date(item['deadline'])}")
    return ", ".join(parts) + f". Fit {item['fit_score']} out of 10." + _scam_warning(item, first_warning)


def build_script(plan, today):
    """A short spoken version of the brief, always under MAX_SCRIPT_CHARS."""
    new = plan["new_bounties"] + plan["new_hackathons"]
    sentences = [f"Good morning! Here's your Sloth Scout brief for {today.strftime('%A')}, "
                 f"{today.strftime('%B')} {today.day}."]
    if new:
        sentences.append(f"There {'is' if len(new) == 1 else 'are'} {len(new)} new "
                         f"pick{'' if len(new) == 1 else 's'} today.")
    else:
        sentences.append("No new picks today.")

    picks = (plan["new_bounties"][:PICKS_SPOKEN]
             + plan["new_hackathons"][:max(1, PICKS_SPOKEN - len(plan["new_bounties"]))])
    labels = iter(["Top pick", "Next", "Also worth a look", "And"])
    warned = False
    for item in picks:
        sentences.append(_pick_sentence(next(labels, "And"), item, first_warning=not warned))
        warned = warned or item.get("scam_risk") in ("medium", "high")

    still_open = plan["still_open"]  # already sorted best fit first
    if still_open and not new:
        # Quiet day: name the best picks that are still open, so the audio is still useful.
        named = still_open[:STILL_OPEN_SPOKEN]
        n = len(still_open)
        sentences.append(f"{n} pick{'' if n == 1 else 's'} from earlier briefs {'is' if n == 1 else 'are'} still open.")
        labels = iter(["Still the best bet", "Also still open"])
        for item in named:
            sentences.append(_pick_sentence(next(labels), item, first_warning=not warned))
            warned = warned or item.get("scam_risk") in ("medium", "high")
    elif still_open:
        n = len(still_open)
        sentences.append(f"{n} pick{'' if n == 1 else 's'} from earlier briefs {'is' if n == 1 else 'are'} still open.")
    closing = "Full details are on the page. Slow and steady!"

    # Add sentences in order until the next one would go over the limit.
    script = ""
    for sentence in sentences:
        if len(script) + len(sentence) + len(closing) + 2 > MAX_SCRIPT_CHARS:
            break
        script += sentence + " "
    return (script + closing).strip()


def make_audio(script):
    """Turn the script into site/brief.mp3. Returns the script; raises SourceSkipped on failure."""
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise SourceSkipped("ELEVENLABS_API_KEY isn't set")
    try:
        response = requests.post(
            TTS_URL.format(voice_id=VOICE_ID), params={"output_format": OUTPUT_FORMAT}, timeout=120,
            headers={"xi-api-key": api_key}, json={"text": script, "model_id": MODEL_ID},
        )
    except requests.RequestException as error:
        raise SourceSkipped(describe_network_error(error, "api.elevenlabs.io"))
    if not response.ok:
        try:
            detail = response.json().get("detail") or {}
        except ValueError:
            detail = {}
        status = detail.get("status", "") if isinstance(detail, dict) else ""
        if status == "quota_exceeded" or "quota" in str(detail).lower():
            raise SourceSkipped("the ElevenLabs monthly quota has run out")
        message = detail.get("message", "") if isinstance(detail, dict) else str(detail)
        raise SourceSkipped(f"ElevenLabs returned an error ({response.status_code}{': ' + message[:120] if message else ''})")
    if not response.headers.get("content-type", "").startswith("audio/"):
        raise SourceSkipped("ElevenLabs didn't send back audio")

    SITE_DIR.mkdir(exist_ok=True)
    AUDIO_FILE.write_bytes(response.content)  # replaces yesterday's file
    SCRIPT_FILE.write_text(script + "\n", encoding="utf-8")
    print(f"Saved {AUDIO_FILE.relative_to(ROOT)} ({len(response.content) // 1024} KB, "
          f"{len(script)} characters, ElevenLabs charged {response.headers.get('character-cost', '?')} credits)")
    return script


def remove_old_audio():
    """Delete yesterday's audio so the page never plays an out-of-date brief."""
    for path in (AUDIO_FILE, SCRIPT_FILE):
        path.unlink(missing_ok=True)
