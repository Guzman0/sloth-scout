# 🦥 Sloth Scout

A slow-and-steady daily bounty briefing, built for the DEV Hacktoberfest "Slow and Steady" challenge.

**Goal:** each morning, find new Web3 bounties, rank them against my skills with Google's open-weight Gemma model, and give me a short brief.

## Status

- [x] **Step 1: fetch bounties.** `fetch_bounties.py` pulls open bounties from [Superteam Earn](https://superteam.fun/earn) and saves them to `data/bounties.json`.
- [x] **Step 2: rank with Gemma.** `rank_bounties.py` scores each bounty against `profile.md` with Gemma and writes `brief.md`.
- [ ] Step 3: write the morning brief

## Run it

```bash
pip install -r requirements.txt
python fetch_bounties.py
export GEMINI_API_KEY=your-key-here   # free key: https://aistudio.google.com/apikey
python rank_bounties.py
```

## How the fetch works

1. It makes one request to `https://superteam.fun/api/listings`, a public endpoint that needs no key.
2. It keeps only listings that are bounties, have status `OPEN`, and haven't passed their deadline.
3. For each kept bounty, it requests `/api/listings/details/<slug>` to get the description. The listings response doesn't include one. There's a 1-second pause between requests.
4. It saves each bounty to `data/bounties.json` with these fields: title, sponsor (and whether Superteam has verified it), region (`Global` or the one place it's open to), reward, token, deadline, listing type, agent access, link, skills, and a plain-text description.

The API is undocumented, so the field names come from inspecting real responses. If the listings request fails, the script stops and saves nothing. It never makes up data.

## How the ranking works

1. It reads `data/bounties.json` and `profile.md`, which lists your skills and preferences. Edit `profile.md` to change what counts as a good fit.
2. It drops bounties that aren't open to your region before asking Gemma, so they cost no API calls. The allowed regions are set in `ELIGIBLE_REGIONS` at the top of `rank_bounties.py`: `Global` and the UK.
3. It checks which Gemma models your key can use and picks the one set by `GEMMA_MODEL`. You can change the default in the file, or override it for one run with `GEMMA_MODEL=gemma-4-31b-it python rank_bounties.py`. Setting it to `auto` picks the largest. The key is read from `GEMINI_API_KEY` and is never saved or printed.
4. For each bounty, it asks Gemma for a JSON answer with these fields: `fit_score` (0-10), `reason`, `effort` (low/medium/high), `scam_risk` (low/medium/high) and `scam_reason`. If Google says it's busy, the script waits and tries again. If a reply isn't valid JSON, it asks once more and then skips that bounty.
5. It saves every result to `data/ranked.json`, best fit first, and writes `brief.md` with the top 5. A bounty with a score under 5 counts as a poor fit.
6. It adds one line per run to `data/run_log.jsonl` with the model, run time in seconds, number of Gemma calls, busy retries and JSON retries.
