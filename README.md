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
reconstruction, so the ranking table matches Fantrax exactly.

The **26/27 projection is bottom-up / per-stat**. For each player it takes every
Opta per-90 rate from last season (goals, SoT, KP, tackles won, interceptions,
clean sheets, …), regresses each rate toward its position mean — *volatile*
stats (goals, penalties, cards, GK saves) shrink hard, *stable* volume stats
(tackles, interceptions, aerials, passing actions) barely move — scales to
expected minutes, and scores the result with Fantrax's rules:

```
rate_s        = stat_s / (minutes / 90)                        # per-90, last season
blended_s     = (m90·rate_s + k_s·prior_rate_s) / (m90 + k_s)  # per-stat regression
expected_90s  = 34 · participation,  participation = min(1, games/34)·min(1, starter_rate)  (floor 0.75 if games≥25)
projected     = Σ_s  (blended_s · expected_90s) · fantrax_points_per_stat[s, position]
```

`k_s` is the per-stat shrinkage in `draft_engine._SHRINK`. The bottom-up scoring
was validated to reproduce Fantrax's own points (correlation ≈ 0.99 across all
positions — run `validate.py`). Players without Sleeper stats fall back to a
PPG-based projection (`proj_ppg`); each player's method is shown in the app's
debug panel.

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
