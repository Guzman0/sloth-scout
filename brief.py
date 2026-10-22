"""Write brief.md: the short morning briefing run.py ends with."""

from datetime import date

from common import ROOT
from rank_bounties import format_reward

BRIEF_FILE = ROOT / "brief.md"

POOR_FIT_BELOW = 5  # fit scores under this count as poor fits
MIN_DAYS_LEFT = 3  # from profile.md: deadlines at least 3 days away
TOP_N = 5
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
        f"### {number}. {item['title']}",
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


def _section(title, ranked, skipped, today, extra_notes=()):
    good = [r for r in ranked if r["fit_score"] >= POOR_FIT_BELOW]
    too_soon = [r for r in good if (d := days_left(r, today)) is not None and d < MIN_DAYS_LEFT]
    shown = [r for r in good if r not in too_soon]
    poor = [r for r in ranked if r["fit_score"] < POOR_FIT_BELOW]

    lines = [f"## {title}", ""]
    if shown:
        lines += [f"Top {min(TOP_N, len(shown))} of {len(shown)} good fits, best first.", ""]
        for number, item in enumerate(shown[:TOP_N], start=1):
            lines += _entry(number, item)
    else:
        lines += ["No good fits today.", ""]

    notes = [f"Poor fits (score under {POOR_FIT_BELOW}): {len(poor)}."]
    if too_soon:
        titles = "; ".join(f"{r['title']} ({_deadline_text(r)})" for r in too_soon)
        notes.append(f"Good fits closing in under {MIN_DAYS_LEFT} days, left out: {titles}.")
    notes += list(extra_notes)
    if skipped:
        titles = "; ".join(s["title"] for s in skipped)
        notes.append(f"Couldn't rank {len(skipped)} (will retry next run): {titles}.")
    return lines + notes + [""]


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


def _sources_section(sources):
    lines = ["## Sources", ""]
    for name, info in sources.items():
        if info["status"] == "ok":
            lines.append(f"- ✅ **{name}:** {info['summary']} ({info['seconds']}s)")
        else:
            lines.append(f"- ⚠️ **{name}: skipped.** {info['reason']}")
    return lines + [""]


def write_brief(today, ranked, skipped, not_eligible, unverified, sources, model):
    bounties = [r for r in ranked if r["kind"] in ("bounty", "lead")]
    hackathons = [r for r in ranked if r["kind"] == "hackathon"]
    region_note = []
    if not_eligible:
        regions = ", ".join(sorted({b["region"] for b in not_eligible}))
        region_note = [f"Not open to your region ({regions}), so not ranked: {len(not_eligible)}."]

    lines = [f"# 🦥 Sloth Scout brief: {today.strftime('%A, %B %-d, %Y')}", ""]
    skipped_sources = [name for name, info in sources.items() if info["status"] != "ok"]
    if skipped_sources:
        lines += [f"> ⚠️ Skipped this run: {', '.join(skipped_sources)}. See Sources at the bottom.", ""]
    lines += _section("Bounties", bounties,
                      [s for s in skipped if s["kind"] in ("bounty", "lead")], today, region_note)
    lines += _section("Hackathons", hackathons, [s for s in skipped if s["kind"] == "hackathon"], today)
    lines += _unverified_section(unverified)
    lines += _sources_section(sources)
    lines.append(f"Ranked by {model.removeprefix('models/') if model else 'nobody (ranking skipped)'}.")
    BRIEF_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
