# Fantrax EPL Draft Assistant

Pre-draft rankings and a live snake-draft board for the **Wiregrass Futbol
Association** (Fantrax EPL league `wxgdnh5dmrbb90nb`) — a Streamlit web app that
projects 2026/27 fantasy points under Fantrax scoring and helps you draft.

- **Draft:** Live Online Standard Snake · 16 rounds · 2 min/pick
- **Roster:** 16 (11 active + 5 reserve + 1 IR)
- **Positions:** G (max 3) · D (max 8) · M (max 8) · F (max 6) · M/F & D/M flex

## Features

- **Rankings** — the consensus draft list, with a frozen **Pick** column marking
  the rows that land on your own snake picks (set league size + slot in the
  sidebar), plus tiers, positional ranks and a toggleable per-stat detail view.
- **Auto-rank** — one click turns the projection model into a draft-order (DP)
  list you can hand-edit in the sidebar; DP-ranked players float to the top of
  every table.
- **Live Draft** — snake board with on-the-clock tracking and your upcoming
  pick schedule. Mark picks manually, or auto-sync from Fantrax when a session
  cookie is configured.
- **My Team** — drafted squad, positional caps (G3/D8/M8/F6) and best available
  per position.
- **ADP / Value** — real ADP (from online drafts) vs projection rank: values vs
  reaches, with the expert consensus alongside. Δ is only computed for players
  that have a projection (players with no 25/26 sample show a blank Δ).
- **Tiers & Splits** — each position grouped into tiers by board rank, split at
  real drop-offs, with the gap to the next player at that position (⛰️ marks a
  10+ pick cliff, so you know when waiting costs you). Below it, the players the
  expert panel and the draft room disagree about most.
- **Data-source debug** (expander under Rankings) — per-player detail-stat source
  (Sleeper / API-Football / missing), last-name-only Sleeper matches flagged as
  higher risk (shared surnames first), and the top players missing a Sleeper join
  by GW played, to catch name-join bugs.

## How the draft list is built

The board is **consensus-driven — there is no points projection.**

Every source is treated as **one board, one vote**: each real mock draft you send
counts once, and each expert on the consensus panel counts once as their own
board. **Blend** is simply the average pick/rank across all of them:

```
blend = mean( every real draft pick , every expert's rank )
```

With 5 mock drafts and a 9-expert panel, a fully-covered player is the mean of
**14 boards** (`nB` shows how many). An expert who left a player *outside* their
top 150 still counts, at ~175 — dropping them would let one bullish ranking
leapfrog the field.

**Blend** is what you draft by — it's the first column and the default sort.

| Column | Meaning |
| --- | --- |
| **Blend** | **what to draft by** — average across every board (drafts + each expert) |
| **nB** | how many boards the player appears on |
| **ADP** | the real mock drafts alone |
| **nD** | how many real drafts he appeared in |
| **Cons** | the panel's own published aggregate (counts unranked as 200, so it reads harsher than Blend) |
| **Mine** | your manual override — never touches Blend, but sets where **Auto-rank** places them on your own DP board |

The list is **not gated on the Fantrax export** — anyone appearing in a draft or
on the consensus board is included, so players missing from the export snapshot
(e.g. Luka Vuskovic) still show up, flagged in the debug panel.

Last season's Fantrax points/PPG are still displayed as **context only**; they do
not affect the order.

### Tiers and disagreement

Within a position, a new **tier** starts wherever the gap to the next player is at
least 8 blend picks (or 1.5x that position's median gap). `next_gap` is the cost
of waiting one more pick at that position.

A player is flagged **⚠️ split** when the panel and the room disagree by 30+ picks
(needs 3+ drafts — one pick is noise) or when his actual picks spanned 45+ places.
`Gap` = consensus − ADP: positive means the room takes him earlier than the panel
rates him; negative means he falls past the panel's rank.

### Overriding a player

`data/my_overrides.csv`:

```csv
Name,Rank,Note
Johan Manzambi,60,Last tier-3 mid before the post-R6 thinning
```

`Rank` is where *you'd* take them. It shows in the **Mine** column and sets
where **🤖 Auto-rank** places them in your DP list — so your personal board is
durable and lives in git, not in a text area that dies with the session.

It deliberately does **not** feed Blend. Blend stays the pure pooled consensus,
which is what keeps the ⚠️ split flags meaningful: if you've moved someone 20
picks up, the gap to Blend is exactly the reach you're taking, and the app can
still tell you so. Re-run the app (or hit **Reload player DB**) to pick up edits.

### Do-not-draft list

`data/do_not_draft.csv` — players you never want, whatever the consensus says:

```csv
Name,Note
Pedro Neto,
Noni Madueke,
```

They stay **on the board** with their real Blend (worth seeing where the room
values them, and they keep feeding everyone else's ADP), marked 🚫 in the
Rankings table. They are dropped from **Auto-rank** and from the **Fantrax
export**.

This is deliberately separate from the `Exclude` column in
`data/fantrax_name_aliases.csv`: that one means *Fantrax rejects this player as
ineligible*, this one means *he's draftable, I just don't want him*. Keeping
them apart stops the exporter from reporting a personal fade as a league
eligibility error.

### Exporting to Fantrax

Fantrax's **Import rankings** dialog takes a two-column, header-less CSV
(`<First name> <Last name>`, `<Team abbreviation>` — the first format option).
Generate it from the current board:

```bash
python export_fantrax_rankings.py        # all ranked players
python export_fantrax_rankings.py 150    # top 150 only
```

Writes `fantrax_rankings_import.csv` in **your board order** — Blend, except
where `my_overrides.csv` sets a Rank, so the board you upload is the same one
Auto-rank builds. Do-not-draft players are left out. Note the import
**replaces** any existing rankings in Fantrax.

**Name fixes** live in `data/fantrax_name_aliases.csv`. Fantrax's importer matches
a player's *legal* name, not the display name in its own export — so "Savio" is
rejected and has to be sent as "Savio Moreira de Oliveira". Columns:

| Column | Meaning |
| --- | --- |
| `Name` | the name as it appears on our board |
| `FantraxName` | what to write instead (blank = leave unchanged) |
| `Exclude` | `Y` drops the player — used for anyone Fantrax reports as not eligible for the league |

If an upload reports more "not found" names, add them here and re-run.

**Missing club codes:** a player who only appears in a real draft (no
consensus-panel row) has no club anywhere in the data, and no club means the
export silently skips them. Add them to `data/off_pool_teams.csv`:

```csv
Name,Team,Position
Marcus Rashford,MUN,M
Tarik Muharemovic,LEE,D
```

`Team` is a Fantrax club code; `Position` (G/D/M/F) is optional and only
needed if the player isn't on the consensus panel either (unranked-but-drafted
players default to M otherwise). This only matters for **off-pool** players
(missing from the Fantrax export snapshot) — anyone in the export already
carries club and position from there.

## Data sources

| Source | Used for | Notes |
| --- | --- | --- |
| **Fantrax export** (`data/fantrax_players_2025.csv`) | **canonical pool: real 25/26 points, PPG, position, club** | the league's own player export — ground truth; re-export closer to the draft to refresh |
| Sleeper `stats/clubsoccer:epl` | stat-detail columns (goals, tackles won, clean sheets, crosses, …) via name join | free, no key; same Opta feed Fantrax scores on |
| API-Football (bundled JSON) | `starter_rate` for the projection's availability term; detail-stat fallback | harvested 2025/26 PL, 537 players |
| **ADP** (`data/adp.csv`) | **real average draft position** from online Fantrax drafts | built by `build_adp.py` from the draft files in `data/adp_drafts/` (currently 5 mocks: 2×10-team, 3×12-team) |
| **Expert consensus** (`data/consensus_ranks.csv`) | consensus rank from a 9-specialist panel (+ each expert's best/worst) | `200` in the sheet means "outside my top 150" and is treated as unranked |
| Fantrax `fxpa/req` | live draft board (best-effort) | needs a session cookie in `st.secrets["fantrax_cookie"]` |

(No FPL — this is a Fantrax app, so ADP comes from real Fantrax drafts, not FPL ownership.)

**Refreshing the pool:** the pool is the canonical source for every player's
name spelling, club and position, so a stale one is the root cause of most
import failures — a player Fantrax added after the snapshot has no canonical
name here. Either pull it live:

```bash
export FANTRAX_COOKIE='<paste the Cookie header>'   # or set it in secrets.toml
python refresh_pool.py --dry-run                    # preview the diff
python refresh_pool.py                              # write the pool
python build_adp.py && python export_fantrax_rankings.py
```

(Cookie: DevTools → Network → any fantrax.com request → Request Headers →
`Cookie`. The script refuses to overwrite unless a plausible pool comes back,
so an expired session can't clobber a good file.)

...or export the Players grid from Fantrax by hand and overwrite
`data/fantrax_players_2025.csv` (columns `Player, Team, Position, RkOv, FPts,
FP/G` are what the app reads).

**Club changed over the summer?** The pool snapshot still lists players at the
club they left. `data/off_pool_teams.csv` overrides the club for anyone, in the
pool or not.

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
