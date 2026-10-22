"""Write brief.md: the short morning briefing run.py ends with.

Layout: "New today" (good fits never shown in an earlier brief) comes first,
then a short "Still open" list (shown before, still accepting entries), then
notes, unverified leads and which sources ran.
"""

from datetime import date

from common import ROOT
from rank_bounties import format_reward

BRIEF_FILE = ROOT / "brief.md"

POOR_FIT_BELOW = 5  # fit scores under this count as poor fits
MIN_DAYS_LEFT = 3  # from profile.md: deadlines at least 3 days away
TOP_N = 5  # per kind, in "New today"
MAX_STILL_OPEN = 10
MAX_UNVERIFIED_SHOWN = 10


def days_left(item, today):
    """Whole days until the deadline, or None if we don't know the deadline."""
    deadline = item.get("deadline")
    if not deadline:
        return None
    try:
        return (date.fromisoformat(deadline[:10]) - today).days
    except ValueError:
        return None


def _deadline_text(item):
    if item.get("deadline"):
        return item["deadline"][:10]
    return item.get("deadline_text") or "not stated"


def _entry(number, item):
    lines = [
        f"#### {number}. {item['title']}",
        f"- **{'Prize total' if item['kind'] == 'hackathon' else 'Reward'}:** {format_reward(item)}"
        f"   **Deadline:** {_deadline_text(item)}",
        f"- **Fit:** {item['fit_score']}/10. {item['reason']}",
        f"- **Effort:** {item['effort']}",
        f"- **Scam risk:** {item['scam_risk']}. {item['scam_reason']}",
    ]
    if item["kind"] == "hackathon" and item.get("themes"):
        lines.append(f"- **Themes:** {', '.join(item['themes'])}")
    if item["kind"] == "lead":
        lines.append(f"- **Found on:** {item['source']} (read by browser, not an official API)")
    lines += [f"- **Link:** {item['link']}", ""]
    return lines


def plan_brief(ranked, seen, today):
    """Split good fits into new / still open / closing too soon.

    seen maps link -> memory record; a link with a first_shown date before
    today was in an earlier brief.
    """
    def shown_before(item):
        first = (seen.get(item["link"]) or {}).get("first_shown")
        return bool(first) and first < today.isoformat()

    good = [r for r in ranked if r["fit_score"] >= POOR_FIT_BELOW
            and ((d := days_left(r, today)) is None or d >= 0)]
    still_open = [r for r in good if shown_before(r)]
    fresh = [r for r in good if not shown_before(r)]
    too_soon = [r for r in fresh if (d := days_left(r, today)) is not None and d < MIN_DAYS_LEFT]
    fresh = [r for r in fresh if r not in too_soon]
    new_bounties = [r for r in fresh if r["kind"] in ("bounty", "lead")][:TOP_N]
    new_hackathons = [r for r in fresh if r["kind"] == "hackathon"][:TOP_N]
    return {
        "new_bounties": new_bounties,
        "new_hackathons": new_hackathons,
        "still_open": still_open[:MAX_STILL_OPEN],
        "too_soon": too_soon,
        "poor": [r for r in ranked if r["fit_score"] < POOR_FIT_BELOW],
    }


def shown_links(plan):
    """Links that appear in the brief, so memory can mark them as shown."""
    return {r["link"] for key in ("new_bounties", "new_hackathons", "still_open") for r in plan[key]}


def _new_section(plan):
    lines = ["## New today", ""]
    for title, key, empty in (("Bounties", "new_bounties", "No new good-fit bounties today."),
                              ("Hackathons", "new_hackathons", "No new good-fit hackathons today.")):
        lines += [f"### {title}", ""]
        if plan[key]:
            for number, item in enumerate(plan[key], start=1):
                lines += _entry(number, item)
        else:
            lines += [empty, ""]
    return lines


def _still_open_section(plan):
    lines = ["## Still open", ""]
    if not plan["still_open"]:
        return lines + ["Nothing from earlier briefs is still open.", ""]
    lines += ["From earlier briefs, still accepting entries:", ""]
    for r in plan["still_open"]:
        risk = f", scam risk {r['scam_risk']}" if r["scam_risk"] != "low" else ""
        lines.append(f"- [{r['title']}]({r['link']}): {format_reward(r)}, closes {_deadline_text(r)}, "
                     f"fit {r['fit_score']}/10{risk}")
    return lines + [""]


def _notes_section(plan, skipped, not_eligible):
    notes = [f"Poor fits (score under {POOR_FIT_BELOW}): {len(plan['poor'])}."]
    if plan["too_soon"]:
        titles = "; ".join(f"{r['title']} ({_deadline_text(r)})" for r in plan["too_soon"])
        notes.append(f"New good fits closing in under {MIN_DAYS_LEFT} days, left out: {titles}.")
    if not_eligible:
        regions = ", ".join(sorted({b["region"] for b in not_eligible}))
        notes.append(f"Not open to your region ({regions}), so not ranked: {len(not_eligible)}.")
    if skipped:
        titles = "; ".join(s["title"] for s in skipped)
        notes.append(f"Couldn't rank {len(skipped)} (will retry next run): {titles}.")
    return ["## Notes", ""] + [f"- {n}" for n in notes] + [""]


def _unverified_section(leads):
    lines = ["## Unverified leads", ""]
    if not leads:
        return lines + ["None today.", ""]
    lines += ["Found by search but missing a reward or deadline, so not ranked. Check these by hand.", ""]
    for lead in leads[:MAX_UNVERIFIED_SHOWN]:
        lines.append(f"- [{lead['title']}]({lead['link']}) ({lead['source']}): {lead['why_unverified']}")
    if len(leads) > MAX_UNVERIFIED_SHOWN:
        lines.append(f"- ...and {len(leads) - MAX_UNVERIFIED_SHOWN} more in data/leads.json")
    return lines + [""]


def _sources_section(sources, store):
    lines = ["## Sources", ""]
    for name, info in sources.items():
        if info["status"] == "ok":
            lines.append(f"- ✅ **{name}:** {info['summary']} ({info['seconds']}s)")
        else:
            lines.append(f"- ⚠️ **{name}: skipped.** {info['reason']}")
    if store.note:
        lines.append(f"- ⚠️ **Memory:** JSON files in data/, because {store.note}.")
    else:
        lines.append(f"- ✅ **Memory:** {store.name}")
    return lines + [""]


def write_brief(today, plan, skipped, not_eligible, unverified, sources, store, model):
    lines = [f"# 🦥 Sloth Scout brief: {today.strftime('%A, %B %-d, %Y')}", ""]
    skipped_sources = [name for name, info in sources.items() if info["status"] != "ok"]
    if skipped_sources:
        lines += [f"> ⚠️ Skipped this run: {', '.join(skipped_sources)}. See Sources at the bottom.", ""]
    if store.note:
        lines += [f"> 💾 Memory is in the JSON files this run, not MongoDB Atlas: {store.note}.", ""]
    lines += _new_section(plan)
    lines += _still_open_section(plan)
    lines += _notes_section(plan, skipped, not_eligible)
    lines += _unverified_section(unverified)
    lines += _sources_section(sources, store)
    lines.append(f"Ranked by {model.removeprefix('models/') if model else 'nobody (ranking skipped)'}.")
    BRIEF_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
