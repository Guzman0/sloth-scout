"""Build site/index.html: the public page for today's brief.

run.py calls write_site() after the brief is planned. The page is plain HTML
with inline CSS: no scripts, no trackers, nothing to build. Render serves the
site/ folder as it is.

Everything shown comes from public listings. Text is HTML-escaped and only
http(s) links become clickable, because titles and links come from other
websites. No keys, profile details or run logs go on the page.
"""

import html
from datetime import datetime, timezone
from urllib.parse import urlparse

from audio import AUDIO_FILE, SITE_DIR
from brief import MIN_DAYS_LEFT, _deadline_text
from rank_bounties import format_reward

REPO_URL = "https://github.com/Guzman0/sloth-scout"
INDEX_FILE = SITE_DIR / "index.html"

STYLE = """
:root {
  --bg: #f6f3ea; --card: #fffdf7; --text: #2b2a26; --muted: #6b665c; --line: #e3dccb;
  --moss: #3f6b4a; --moss-soft: #e4eedf; --bark: #7a5a3a; --warn: #a1561b; --warn-soft: #fbeadb;
  --bad: #a3312b; --bad-soft: #f8e1df;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #1b1d19; --card: #24271f; --text: #ecebe4; --muted: #a8a596; --line: #3a3d33;
    --moss: #9cc79f; --moss-soft: #2c3a2c; --bark: #d0ab83; --warn: #f0b27a; --warn-soft: #3d2f22;
    --bad: #f19a92; --bad-soft: #3d2523;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 17px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 720px; margin: 0 auto; padding: 24px 16px 48px; }
a { color: var(--moss); overflow-wrap: anywhere; }
.card, .player, li { overflow-wrap: anywhere; }
header { text-align: center; padding: 8px 0 16px; }
.sloth { font-size: 56px; line-height: 1; display: inline-block; transform: rotate(-8deg); }
h1 { margin: 8px 0 0; font-size: 28px; letter-spacing: -0.01em; }
.tagline { margin: 4px 0 0; color: var(--muted); font-style: italic; }
.date { margin: 12px 0 0; font-weight: 600; color: var(--bark); }
.branch { border: 0; height: 2px; background: var(--bark); opacity: .35; margin: 28px 0 8px; border-radius: 2px; }
h2 { font-size: 21px; margin: 24px 0 12px; }
h2 .leaf { margin-right: 6px; }
h3 { font-size: 17px; margin: 18px 0 8px; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; }
.player { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 14px 16px; }
.player audio { width: 100%; margin-top: 6px; }
.player details { margin-top: 8px; color: var(--muted); font-size: 15px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 14px 16px; margin: 0 0 12px; }
.card h4 { margin: 0 0 6px; font-size: 18px; line-height: 1.35; }
.meta { display: flex; flex-wrap: wrap; gap: 6px 14px; color: var(--muted); font-size: 15px; margin-bottom: 8px; }
.meta strong { color: var(--text); }
.reason { margin: 6px 0; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.chip { font-size: 13px; padding: 2px 9px; border-radius: 999px; background: var(--moss-soft); color: var(--moss); }
.chip.medium { background: var(--warn-soft); color: var(--warn); }
.chip.high { background: var(--bad-soft); color: var(--bad); }
.scam { font-size: 15px; color: var(--muted); margin: 6px 0 0; }
.empty { color: var(--muted); font-style: italic; }
ul.compact { padding-left: 20px; margin: 0; }
ul.compact li { margin: 6px 0; }
.small { font-size: 15px; color: var(--muted); }
.ok::before { content: "✅ "; } .skip::before { content: "⚠️ "; }
footer { margin-top: 36px; text-align: center; color: var(--muted); font-size: 14px; }
"""


def _e(text):
    return html.escape(str(text or ""), quote=True)


def _link(url, text):
    """A link only for http(s) URLs; anything else is shown as plain text."""
    if urlparse(str(url or "")).scheme in ("http", "https"):
        return f'<a href="{_e(url)}" rel="noopener nofollow">{_e(text)}</a>'
    return _e(text)


def _card(item):
    risk = item.get("scam_risk", "low")
    chips = [f'<span class="chip">Fit {item["fit_score"]}/10</span>',
             f'<span class="chip">Effort {_e(item.get("effort"))}</span>',
             f'<span class="chip {_e(risk)}">Scam risk {_e(risk)}</span>']
    chips += [f'<span class="chip">{_e(t)}</span>' for t in (item.get("themes") or [])[:3]]
    source = item.get("source") or ""
    return f"""
<article class="card">
  <h4>{_link(item.get("link"), item.get("title"))}</h4>
  <div class="meta">
    <span><strong>{_e(format_reward(item))}</strong></span>
    <span>Due <strong>{_e(_deadline_text(item))}</strong></span>
    {f'<span>{_e(item.get("sponsor"))}</span>' if item.get("sponsor") else ''}
    {f'<span>via {_e(source)}</span>' if source else ''}
  </div>
  <p class="reason">{_e(item.get("reason"))}</p>
  <p class="scam">Scam check: {_e(item.get("scam_reason"))}</p>
  <div class="chips">{''.join(chips)}</div>
</article>"""


def _cards(items, empty):
    return "".join(_card(i) for i in items) if items else f'<p class="empty">{_e(empty)}</p>'


def write_site(today, plan, skipped, not_eligible, unverified, sources, store, model, script=None):
    if script and AUDIO_FILE.exists():
        version = today.isoformat()  # makes browsers fetch the new file each day
        player = f"""
<section class="player" aria-label="Listen to today's brief">
  <strong>🎧 Listen to today's brief</strong>
  <audio controls preload="none" src="brief.mp3?v={version}"></audio>
  <details><summary>Read the transcript</summary><p>{_e(script)}</p></details>
</section>"""
    else:
        reason = (sources.get("ElevenLabs audio") or {}).get("reason", "")
        player = f'<section class="player"><p class="empty">No audio today. {_e(reason)}</p></section>'

    still_open = "".join(
        f'<li>{_link(r.get("link"), r.get("title"))}: {_e(format_reward(r))}, closes {_e(_deadline_text(r))}, '
        f'fit {r["fit_score"]}/10{", scam risk " + _e(r["scam_risk"]) if r.get("scam_risk") != "low" else ""}</li>'
        for r in plan["still_open"])

    notes = [f"Poor fits (score under 5): {len(plan['poor'])}."]
    if plan["too_soon"]:
        notes.append(f"New good fits closing in under {MIN_DAYS_LEFT} days: "
                     + "; ".join(f"{r['title']} ({_deadline_text(r)})" for r in plan["too_soon"]) + ".")
    if not_eligible:
        notes.append(f"Not open to the UK, so not ranked: {len(not_eligible)}.")
    if skipped:
        notes.append(f"Couldn't rank {len(skipped)} today; they'll be retried tomorrow.")

    leads = "".join(
        f'<li>{_link(l.get("link"), l.get("title"))} <span class="small">({_e(l.get("source"))}: '
        f'{_e(l.get("why_unverified"))})</span></li>' for l in unverified[:10])

    source_rows = [
        f'<li class="ok">{_e(name)}: {_e(info.get("summary"))}</li>' if info["status"] == "ok"
        else f'<li class="skip">{_e(name)}: skipped. {_e(info.get("reason"))}</li>'
        for name, info in sources.items()]
    source_rows.append(f'<li class="skip">Memory: JSON files, because {_e(store.note)}.</li>' if store.note
                       else f'<li class="ok">Memory: {_e(store.name)}</li>')

    updated = datetime.now(timezone.utc).strftime("%H:%M UTC")
    new_count = len(plan["new_bounties"]) + len(plan["new_hackathons"])
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sloth Scout · {_e(today.strftime('%B %-d, %Y'))}</title>
<meta name="description" content="Today's Web3 bounties and hackathons, ranked by Gemma: {new_count} new picks.">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🦥</text></svg>">
<style>{STYLE}</style>
</head>
<body>
<main>
<header>
  <div class="sloth" aria-hidden="true">🦥</div>
  <h1>Sloth Scout</h1>
  <p class="tagline">Slow and steady wins the bounty.</p>
  <p class="date">{_e(today.strftime('%A, %B %-d, %Y'))}</p>
</header>
{player}

<hr class="branch">
<h2><span class="leaf" aria-hidden="true">🌱</span>New today</h2>
<h3>Bounties</h3>
{_cards(plan["new_bounties"], "No new good-fit bounties today.")}
<h3>Hackathons</h3>
{_cards(plan["new_hackathons"], "No new good-fit hackathons today.")}

<hr class="branch">
<h2><span class="leaf" aria-hidden="true">🍃</span>Still open</h2>
{f'<ul class="compact">{still_open}</ul>' if still_open else '<p class="empty">Nothing from earlier briefs is still open.</p>'}

<hr class="branch">
<h2><span class="leaf" aria-hidden="true">📝</span>Notes</h2>
<ul class="compact small">{''.join(f'<li>{_e(n)}</li>' for n in notes)}</ul>

<hr class="branch">
<h2><span class="leaf" aria-hidden="true">🔎</span>Unverified leads</h2>
<p class="small">Found by search but missing a reward or deadline, so not ranked. Check these by hand.</p>
{f'<ul class="compact">{leads}</ul>' if leads else '<p class="empty">None today.</p>'}

<hr class="branch">
<h2><span class="leaf" aria-hidden="true">🌿</span>Sources</h2>
<ul class="compact small">{''.join(source_rows)}</ul>

<footer>
  <p>Ranked by {_e(model.removeprefix('models/') if model else 'nobody today')} · updated {updated}</p>
  <p>Built slowly and steadily · <a href="{REPO_URL}">Source on GitHub</a></p>
</footer>
</main>
</body>
</html>
"""
    SITE_DIR.mkdir(exist_ok=True)
    INDEX_FILE.write_text(page, encoding="utf-8")
