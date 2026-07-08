# Fantrax EPL Draft Assistant

Pre-draft rankings and a live snake-draft board for the **Wiregrass Futbol
Association** (Fantrax EPL league `wxgdnh5dmrbb90nb`) — a Streamlit web app that
projects 2026/27 fantasy points under Fantrax scoring and helps you draft.

- **Draft:** Live Online Standard Snake · 16 rounds · 2 min/pick
- **Roster:** 16 (11 active + 5 reserve + 1 IR)
- **Positions:** G (max 3) · D (max 8) · M (max 8) · F (max 6) · M/F & D/M flex

## Features

- **Rankings** — every player projected for 26/27 under Fantrax scoring, with
  25/26 totals, PPG and a toggleable per-stat detail view. Name column is
  frozen on horizontal scroll.
- **Auto-rank** — one click turns the projection model into a draft-order (DP)
  list you can hand-edit in the sidebar; DP-ranked players float to the top of
  every table.
- **Live Draft** — snake board with on-the-clock tracking and your upcoming
  pick schedule. Mark picks manually, or auto-sync from Fantrax when a session
  cookie is configured.
- **My Team** — drafted squad, positional caps (G3/D8/M8/F6) and best available
  per position.
- **ADP / Value** — projection rank vs the community ADP proxy.

## How projections work

Points use **Fantrax scoring only** (`draft_engine.FANTRAX_SCORING`). Season
stats come from the bundled API-Football 2025/26 file
(`data/pl_stats_2025.json`, 537 players). The 26/27 projection is a Bayesian
blend of each player's own PPG with a position prior, scaled to 34 gameweeks and
an availability (participation) rate:

```
base PPG          = fantrax_total_pts / games          (require ≥ 15 games)
k                 = max(3.0, 40 / sqrt(games))
blended PPG       = (games·PPG + k·prior_PPG) / (games + k)
participation     = min(1, games/34) · min(1, starter_rate)   (floored 0.75 if games ≥ 25)
projected 26/27   = blended_PPG · 34 · participation
```

## Data sources

| Source | Used for | Notes |
| --- | --- | --- |
| API-Football (bundled JSON) | canonical pool, season stats, starter rate | harvested 2025/26 PL — re-harvest closer to kickoff once transfers settle |
| FPL `bootstrap-static` | cost, ownership (ADP proxy), club, clean-sheet gap-fill | never FPL points or FPL positions |
| Fantrax `fxpa/req` | live player pool + draft picks | best-effort, needs a session cookie in `st.secrets["fantrax_cookie"]` |

**Known pre-draft limitation:** clean sheets and own goals are back-filled from
FPL; aerials won, accurate crosses, clearances and dispossessed are not in the
season-aggregate source and default to 0, so GK/DEF totals are conservative
until the Fantrax pool (with Fantrax's own stat totals) is connected.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Optional — enable Fantrax auto-sync by creating `.streamlit/secrets.toml`:

```toml
fantrax_cookie = "<your Fantrax session Cookie header>"
```

Deploy to Streamlit Cloud from the `main` branch.
