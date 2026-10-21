# 🦥 Sloth Scout

A slow-and-steady daily bounty briefing, built for the DEV Hacktoberfest "Slow and Steady" challenge.

**Goal:** each morning, find new Web3 bounties, rank them against my skills with Google's open-weight Gemma model, and give me a short brief.

## Status

- [x] **Step 1: fetch bounties.** `fetch_bounties.py` pulls open bounties from [Superteam Earn](https://superteam.fun/earn) and saves them to `data/bounties.json`.
- [ ] Step 2: rank bounties against my skills with Gemma
- [ ] Step 3: write the morning brief

## Run it

```bash
pip install -r requirements.txt
python fetch_bounties.py
```

## How the fetch works

1. It makes one request to `https://superteam.fun/api/listings`, a public endpoint that needs no key.
2. It keeps only listings that are bounties, have status `OPEN`, and haven't passed their deadline.
3. For each kept bounty, it requests `/api/listings/details/<slug>` to get the description. The listings response doesn't include one. There's a 1-second pause between requests.
4. It saves each bounty to `data/bounties.json` with these fields: title, sponsor, reward, token, deadline, listing type, agent access, link, skills, and a plain-text description.

The API is undocumented, so the field names come from inspecting real responses. If the listings request fails, the script stops and saves nothing. It never makes up data.
