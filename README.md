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
- **Data-source debug** (expander under Rankings) — per-player detail-stat source
  (Sleeper / API-Football / missing), last-name-only Sleeper matches flagged as
  higher risk (shared surnames first), and the top players missing a Sleeper join
  by GW played, to catch name-join bugs.

## How projections work

**25/26 points, PPG and positions are Fantrax's own final numbers**, read
straight from the league export (`data/fantrax_players_2025.csv`) — no
reconstruction, so the ranking table matches Fantrax exactly. `games` is derived
as `FPts / FP-G`. The 26/27 projection is a Bayesian blend of each player's real
Fantrax PPG with a position prior, scaled to 34 gameweeks × an availability rate:

```
base PPG          = Fantrax FP/G          (games = FPts / FP-G, require ≥ 15)
k                 = max(3.0, 40 / sqrt(games))
blended PPG       = (games·PPG + k·prior_PPG) / (games + k)
participation     = min(1, games/34) · min(1, starter_rate)   (floored 0.75 if games ≥ 25)
projected 26/27   = blended_PPG · 34 · participation
```

## Data sources

| Source | Used for | Notes |
| --- | --- | --- |
| **Fantrax export** (`data/fantrax_players_2025.csv`) | **canonical pool: real 25/26 points, PPG, position, club** | the league's own player export — ground truth; re-export closer to the draft to refresh |
| Sleeper `stats/clubsoccer:epl` | stat-detail columns (goals, tackles won, clean sheets, crosses, …) via name join | free, no key; same Opta feed Fantrax scores on |
| API-Football (bundled JSON) | `starter_rate` for the projection's availability term; detail-stat fallback | harvested 2025/26 PL, 537 players |
| FPL `bootstrap-static` | cost, ownership (ADP proxy), club name | never FPL points or FPL positions |
| Fantrax `fxpa/req` | live draft board (best-effort) | needs a session cookie in `st.secrets["fantrax_cookie"]` |

**Refreshing the pool:** re-export the player list from Fantrax and overwrite
`data/fantrax_players_2025.csv` (columns `Player, Team, Position, RkOv, FPts,
FP/G` are what the app reads).

**Sleeper field codes** (for the detail columns) are data-verified and differ
from Sleeper's UI glossary: `cos` is **successful dribbles** (Opta "Contests
Succeeded"), not clean sheets — clean sheets is `cs`; `drb`/`ac` are empty (real
keys `cos`/`acnc`). The crosswalk lives in `_SLEEPER_FIELD` in `draft_engine.py`.

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
