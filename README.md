# 🦥 Sloth Scout

A slow-and-steady daily bounty briefing, built for the DEV Hacktoberfest "Slow and Steady" challenge.

**Goal:** each morning, find new Web3 bounties and hackathons, rank them against my skills with Google's open-weight Gemma model, and give me a short brief.

## Status

- [x] **Step 1: fetch bounties** from [Superteam Earn](https://superteam.fun/earn).
- [x] **Step 2: rank with Gemma** against `profile.md`, with a scam-risk check.
- [x] **Step 3: more sources and one command.** Devpost hackathons, search-based discovery on other platforms, and `run.py`.
- [x] **Step 4: memory and a schedule.** MongoDB Atlas remembers what you've seen, and GitHub Actions runs it every morning at 06:00 UK time.

## Run it

```bash
pip install -r requirements.txt     # needs Python 3.11+
export GEMINI_API_KEY=...           # required for ranking: https://aistudio.google.com/apikey
export SERPAPI_API_KEY=...          # optional: search other platforms (free: 250 searches/month)
export BROWSERBASE_API_KEY=...      # optional: read search results in a cloud browser
export MONGODB_URI=...              # optional: MongoDB Atlas memory (JSON files are used otherwise)
python run.py
```

`run.py` fetches every source, ranks everything and writes `brief.md`. If a key is missing or a site can't be reached, that source is skipped. The reason is printed and shown in `brief.md`, and the rest still runs. `BROWSERBASE_PROJECT_ID` isn't needed: Stagehand 4.x works out the project from the API key.

You can still run `python fetch_bounties.py` or `python fetch_hackathons.py` on their own to just fetch.

## Sources

| Source | File | Key | What it gets |
|---|---|---|---|
| Superteam Earn | `fetch_bounties.py` | none | Open bounties via the public listings API, plus one details request each for the description and region. |
| Devpost | `fetch_hackathons.py` | none | Open, online hackathons with the Blockchain or Machine Learning/AI theme, in one request. Invite-only ones are skipped. |
| SerpApi | `discover_leads.py` | `SERPAPI_API_KEY` | 4 Google searches per run aimed at DoraHacks, Layer3, Gitcoin, plus one general search. Links already found on Superteam or Devpost are dropped, as are profile, submission and blog pages. Results from a `site:` search must really be on that site (Google sometimes ignores `site:`), and results from the general search must mention a Web3, bug bounty, audit or hackathon keyword. |
| Browserbase + Stagehand | `discover_leads.py` | `BROWSERBASE_API_KEY` | Opens up to 10 search results in a cloud browser and extracts title, sponsor, reward, deadline, region and task. Stagehand calls Gemma through our own function, which cleans up Gemma's JSON before handing it back. |

All the APIs are undocumented, so field names come from inspecting real responses. Nothing is made up: a missing value stays empty.

## How the ranking works (`rank_bounties.py`)

1. Superteam bounties that aren't open to your region are dropped before ranking. The allowed regions are set in `ELIGIBLE_REGIONS`: `Global` and the UK. Devpost and other sites don't give a reliable region, so Gemma judges those from `profile.md`.
2. Search leads are ranked only once the browser found both a reward and a deadline. The rest go under **Unverified leads** in the brief, for you to check by hand.
3. Gemma (`GEMMA_MODEL`, default `gemma-4-31b-it`) gives each item a JSON answer with these fields: `fit_score` (0-10), `reason`, `effort`, `scam_risk` and `scam_reason`. If Google is busy, it waits and retries. If a reply isn't valid JSON, it asks once more.
4. **Saved rankings are reused.** If an item is unchanged and the model, `profile.md` and prompt are the same, last run's ranking is kept. So daily runs only ask Gemma about new items, and anything that couldn't be ranked last time is retried automatically.
5. The 3-day deadline rule is checked in code, not by Gemma, so saved rankings don't go stale as deadlines approach. Good fits closing sooner are listed in a note and left out of the top picks.

## Memory (`storage.py`)

MongoDB Atlas (`MONGODB_URI`) holds three collections in the `sloth_scout` database:

- `items`: every link seen, with the dates it was first and last seen and first and last shown in a brief.
- `rankings`: Gemma's latest ranking for each link.
- `runs`: the run log.

The JSON files in `data/` are written on every run as well. If Atlas can't be reached, the run carries on with those, and `brief.md` says so. The first time Atlas is used, it copies in what the JSON files already remember.

Because memory knows what was in earlier briefs, each brief opens with **New today** (good fits you haven't been shown) and follows with a short **Still open** list (shown before and still accepting entries).

## Daily schedule (`.github/workflows/daily-brief.yml`)

GitHub Actions runs `python run.py` at 06:00 UK time every day, and whenever you press **Run workflow** on the Actions tab. It then commits `brief.md` and `data/` back to `main`. The brief also appears on the run's summary page.

GitHub schedules use UTC, so the workflow is listed for both 05:00 and 06:00 UTC and only the one that's 06:00 in London goes ahead. GitHub can start scheduled runs a little late at busy times.

The keys come from repository secrets: `GEMINI_API_KEY`, `SERPAPI_API_KEY`, `BROWSERBASE_API_KEY` and `MONGODB_URI`. Add them under **Settings → Secrets and variables → Actions**. Any secret that's missing just skips that source.

## Output

- `brief.md`: New today (bounties, then hackathons), Still open, Notes, Unverified leads, and Sources (which sources and which memory were used, or why they were skipped).
- `data/` holds `bounties.json`, `hackathons.json`, `leads.json`, `ranked.json` and `seen.json`, plus `run_log.jsonl`. The log gets one line per run with each source's count and time, SerpApi searches used, and Gemma calls, reuses and retries.
