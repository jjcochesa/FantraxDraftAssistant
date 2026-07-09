"""
Draft engine for the Fantrax EPL Draft Assistant.

Data flow
---------
The **canonical source is Fantrax's own player export**
(``data/fantrax_players_2025.csv`` — the league's exported pool): real 25/26
Fantrax points (``FPts``), points-per-game (``FP/G``), position (G/D/M/F) and
club. These are ground truth, so the app never reconstructs points from raw
stats any more.

Enrichment is joined by name onto that pool:
  • Sleeper ``stats/clubsoccer:epl`` — real Opta per-stat counts (goals,
    tackles won, clean sheets, …) for the detail columns.
  • API-Football (bundled JSON) — ``starter_rate`` (starts/appearances) for the
    projection's availability term, and a detail-stat fallback.
  • FPL ``bootstrap-static`` — cost and ownership% (an ADP proxy until Fantrax
    community drafts start). Never FPL points or FPL positions.

The 26/27 projection is a Bayesian blend of each player's real FP/G with a
position prior, scaled to 34 GWs × an availability rate.

The Fantrax API (``fantrax.com/fxpa/req``) remains a best-effort, optional
source for the live draft board (needs a session cookie). Everything degrades
gracefully — the core runs fully offline from the two bundled data files.
"""

import csv
import json
import time
import unicodedata
from pathlib import Path
from typing import Optional

import requests

FPL_API      = "https://fantasy.premierleague.com/api"
FANTRAX_REQ  = "https://www.fantrax.com/fxpa/req"

# Fantrax roster positions, in board display order.
POSITION_ORDER = ["G", "D", "M", "F"]

SLEEPER_API   = "https://api.sleeper.app/v1"
SLEEPER_SPORT = "clubsoccer:epl"

# Fantrax stat → Sleeper season-stat JSON key. Sleeper is the same Opta feed
# Fantrax scores on and carries every stat Fantrax uses, so matched players are
# scored entirely from Sleeper (unmatched fall back to API-Football).
#
# These keys are DATA-VERIFIED against the live endpoint (dumped every numeric
# field's player-count + max across all ~537 rows). Two traps to remember:
#   • `cos` = successful dribbles (Opta "Contests Succeeded"), NOT clean sheets.
#     Clean sheets is `cs`. Sleeper's UI glossary abbreviations differ from its
#     JSON keys — do not trust the glossary, only the raw keys.
#   • `drb` and `ac` are empty in the data; the real keys are `cos` and `acnc`.
_SLEEPER_FIELD: dict[str, str] = {
    "goals":               "g",
    "assists":             "at",
    "shots_on_target":     "sot",
    "key_passes":          "kp",
    "successful_dribbles": "cos",    # NOT drb (empty)
    "accurate_crosses":    "acnc",   # NOT ac (empty)
    "aerials_won":         "aer",
    "clearances":          "clr",
    "saves":               "sv",
    "clean_sheets":        "cs",     # NOT cos (that's dribbles)
    "high_claims":         "hcs",
    "smothers":            "sm",
    "tackles_won":         "tkw",    # real tackles-won; no ×proxy needed
    "interceptions":       "int",
    "blocked_shots":       "bs",
    "goals_against":       "ga",
    "own_goals":           "og",
    "penalties_missed":    "pkm",
    "penalties_saved":     "pks",
    "penalty_drawn":       "pkd",
    "yellow_card":         "yc",
    "red_card":            "rc",
    "second_yellow":       "yc2",    # folded into red_card (a red in Fantrax)
    "minutes":             "min",
}

# Detail stats surfaced in the UI (internal key → source key handled per source).
# Points come from Fantrax directly now; these are for the stat-detail columns.
DETAIL_STATS = [
    "goals", "assists", "shots_on_target", "key_passes", "successful_dribbles",
    "accurate_crosses", "tackles_won", "interceptions", "blocked_shots",
    "aerials_won", "clearances", "clean_sheets", "saves",
    "yellow_card", "red_card",
]

# Fantrax export team code → display name (2026/27 pool, incl. promoted sides).
_EPL_TEAM: dict[str, str] = {
    "ARS": "Arsenal",     "AVL": "Aston Villa", "BHA": "Brighton",
    "BOU": "Bournemouth", "BRF": "Brentford",   "CHE": "Chelsea",
    "COV": "Coventry",    "CRY": "Crystal Palace", "EVE": "Everton",
    "FUL": "Fulham",      "HUL": "Hull",        "IPS": "Ipswich",
    "LEE": "Leeds",       "LIV": "Liverpool",   "MCI": "Man City",
    "MUN": "Man Utd",     "NEW": "Newcastle",   "NOT": "Nott'm Forest",
    "SUN": "Sunderland",  "TOT": "Spurs",
}

# ---------------------------------------------------------------------------
# Fantrax scoring rules — REFERENCE ONLY.
#
# Points are taken directly from the Fantrax export now; this table documents
# the league's scoring (GK vs D/M/F groups; clean sheets GK 8 / D 6 / M 1 / F 0)
# and is no longer used to compute points.
# ---------------------------------------------------------------------------
FANTRAX_SCORING: dict[str, dict | float] = {
    # Attacking
    "goals":               {"G": 10,  "D": 9,   "M": 9,   "F": 9},
    "assists":             {"G": 7,   "D": 6,   "M": 6,   "F": 6},
    "shots_on_target":      2.0,
    "key_passes":           2.0,
    "successful_dribbles":  1.0,   # CoS — Contests Succeeded
    "accurate_crosses":     1.0,   # ACNC
    "penalty_drawn":        {"G": 0,   "D": 2,   "M": 2,   "F": 2},   # PKD
    # Defensive
    "clean_sheets":        {"G": 8,   "D": 6,   "M": 1,   "F": 0},
    "tackles_won":         {"G": 1,   "D": 2,   "M": 2,   "F": 2},    # TKW
    "interceptions":       {"G": 1,   "D": 1.5, "M": 1.5, "F": 1.5},
    "blocked_shots":       {"G": 0,   "D": 1.5, "M": 1.5, "F": 1.5},  # BS
    "aerials_won":         {"G": 1,   "D": 0.5, "M": 0.5, "F": 0.5},  # AER
    "clearances":          {"G": 0.25,"D": 0,   "M": 0,   "F": 0},    # CLR (GK only in practice)
    # Goalkeeping
    "saves":                2.0,
    "penalties_saved":      8.0,   # PKS
    "high_claims":         {"G": 1,   "D": 0,   "M": 0,   "F": 0},    # HCS
    "smothers":            {"G": 1,   "D": 0,   "M": 0,   "F": 0},    # SM
    "goals_against":       {"G": -2,  "D": 0,   "M": 0,   "F": 0},    # GA
    # Negative
    "yellow_card":         -2.0,
    "red_card":            -7.0,
    "own_goals":           -5.0,
    "penalties_missed":    -4.0,   # PKM
    "dispossessed":        -0.5,   # DIS
}

# API-Football "position" string → Fantrax position code.
_APIF_POS: dict[str, str] = {
    "goalkeeper": "G",
    "defender":   "D",
    "midfielder": "M",
    "attacker":   "F",
}

_http = requests.Session()
_http.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; FantraxDraftAssistant/1.0)",
})


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _norm_name(name: str) -> str:
    """Accent-strip + lowercase for cross-source name matching.

    Turkish dotless-ı (U+0131) has no NFKD decomposition — replaced explicitly.
    """
    name = (name or "").replace("ı", "i").replace("İ", "i")
    nfkd = unicodedata.normalize("NFKD", name.lower().strip())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _get(url: str, retries: int = 3, **kwargs) -> dict | list:
    for attempt in range(retries):
        try:
            r = _http.get(url, timeout=12, **kwargs)
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)


# ---------------------------------------------------------------------------
# API-Football season stats (bundled) — canonical pool + stats
# ---------------------------------------------------------------------------

def load_pl_stats(path: str = "data/pl_stats_2025.json") -> list[dict]:
    """Load harvested API-Football 2025/26 PL season stats. [] if missing."""
    p = Path(path)
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def build_apif_lookup(pl_stats: list[dict]) -> dict[str, dict]:
    """Index API-Football season records by norm name (+ ``__last__`` fallback)
    for ``starter_rate`` and detail-stat fallback. Highest-minutes entry wins.
    """
    lookup: dict[str, dict] = {}

    def _put(key: str, rec: dict) -> None:
        prev = lookup.get(key)
        if prev is None or _num(rec.get("minutes")) >= _num(prev.get("minutes")):
            lookup[key] = rec

    for rec in pl_stats:
        name = rec.get("norm_name") or _norm_name(rec.get("name") or "")
        if name:
            _put(_norm_name(name), rec)
        last = _norm_name(rec.get("lastname") or "")
        if last:
            _put(f"__last__{last}", rec)
    return lookup


def _apif_detail(rec: dict) -> dict:
    """Detail-stat counts from an API-Football record (fallback when a player
    isn't in Sleeper). Missing stats (crosses/aerials/clearances/clean sheets)
    are simply absent."""
    return {
        "goals":               _num(rec.get("goals")),
        "assists":             _num(rec.get("assists")),
        "shots_on_target":     _num(rec.get("shots_on_target")),
        "key_passes":          _num(rec.get("key_passes")),
        "successful_dribbles": _num(rec.get("dribbles_success")),
        "tackles_won":         _num(rec.get("tackles_total")),
        "interceptions":       _num(rec.get("interceptions")),
        "blocked_shots":       _num(rec.get("tackles_blocks")),
        "saves":               _num(rec.get("saves")),
        "yellow_card":         _num(rec.get("yellow_cards")),
        "red_card":            _num(rec.get("red_cards")) + _num(rec.get("yellowred_cards")),
        # extras usable for bottom-up scoring / validation
        "penalty_drawn":       _num(rec.get("penalties_won")),
        "penalties_missed":    _num(rec.get("penalties_missed")),
        "penalties_saved":     _num(rec.get("penalties_saved")),
        "goals_against":       _num(rec.get("goals_conceded")),
    }


# ---------------------------------------------------------------------------
# Bottom-up scoring — raw stats × Fantrax points-per-stat (position-aware).
# Used to validate the feed against Fantrax's own FPts, and (once validated)
# to drive the per-stat projection.
# ---------------------------------------------------------------------------

def score_from_stats(stats: dict, position: str) -> float:
    """Fantrax fantasy points for a raw-stat dict at a given position.

    ``stats`` is keyed by FANTRAX_SCORING stat names (the keys Sleeper's lookup
    already uses). Missing stats score 0.
    """
    pos = position.upper()
    pts = 0.0
    for stat_name, rule in FANTRAX_SCORING.items():
        val = _num(stats.get(stat_name))
        if val == 0:
            continue
        mult = rule.get(pos, 0) if isinstance(rule, dict) else float(rule)
        pts += val * mult
    return round(pts, 2)


def validate_scoring(fantrax_players: list[dict], stat_lookup: dict,
                     source_name: str = "Sleeper") -> dict:
    """Compare bottom-up points (raw stats × Fantrax scoring) against Fantrax's
    own FPts for every player that matches ``stat_lookup`` by name.

    Returns a report: n, correlation, MAE, mean signed bias, per-position bias,
    and the biggest over/under-estimates — so we can prove the feed reproduces
    Fantrax before trusting a projection built on it.
    """
    rows = []
    for fx in fantrax_players:
        if fx["total_pts"] <= 0:
            continue
        nkey = _norm_name(fx["name"])
        last = nkey.split()[-1] if nkey else ""
        sl = stat_lookup.get(nkey) or (stat_lookup.get(f"__last__{last}") if last else None)
        if not sl:
            continue
        calc = score_from_stats(sl, fx["position"])
        rows.append({"name": fx["name"], "pos": fx["position"],
                     "fantrax": fx["total_pts"], "calc": calc,
                     "diff": round(calc - fx["total_pts"], 1)})

    n = len(rows)
    if n == 0:
        return {"source": source_name, "n": 0}

    diffs = [r["diff"] for r in rows]
    fpts  = [r["fantrax"] for r in rows]
    calcs = [r["calc"] for r in rows]
    mae   = round(sum(abs(d) for d in diffs) / n, 2)
    bias  = round(sum(diffs) / n, 2)

    # Pearson correlation
    mf, mc = sum(fpts) / n, sum(calcs) / n
    cov = sum((f - mf) * (c - mc) for f, c in zip(fpts, calcs))
    vf  = sum((f - mf) ** 2 for f in fpts) ** 0.5
    vc  = sum((c - mc) ** 2 for c in calcs) ** 0.5
    corr = round(cov / (vf * vc), 4) if vf and vc else None

    pos_bias = {}
    for pos in POSITION_ORDER:
        d = [r["diff"] for r in rows if r["pos"] == pos]
        if d:
            pos_bias[pos] = round(sum(d) / len(d), 1)

    worst = sorted(rows, key=lambda r: abs(r["diff"]), reverse=True)[:15]
    return {"source": source_name, "n": n, "correlation": corr, "mae": mae,
            "bias": bias, "pos_bias": pos_bias, "worst": worst}


# ---------------------------------------------------------------------------
# Fantrax export (bundled CSV) — canonical pool: real points, PPG, position
# ---------------------------------------------------------------------------

def load_fantrax_players(path: str = "data/fantrax_players_2025.csv") -> list[dict]:
    """Load the Fantrax league export. Returns canonical player records with
    real 25/26 points/PPG/games/position/club. [] if the file is missing."""
    p = Path(path)
    if not p.exists():
        return []
    out: list[dict] = []
    with p.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("Player") or "").strip()
            pos  = (row.get("Position") or "").strip().upper()
            if not name or pos not in POSITION_ORDER:
                continue
            fpts = _num(row.get("FPts"))
            fppg = _num(row.get("FP/G"))
            games = round(fpts / fppg) if fppg > 0 else 0
            code = (row.get("Team") or "").strip().upper()
            out.append({
                "fantrax_id": (row.get("ID") or "").strip(),
                "name":       name,
                "team_code":  code,
                "team":       _EPL_TEAM.get(code, code or "—"),
                "position":   pos,
                "total_pts":  round(fpts, 1),
                "ppg":        round(fppg, 2),
                "games":      games,
                "rank_ov":    int(_num(row.get("RkOv"))) or None,
            })
    return out


# ---------------------------------------------------------------------------
# FPL API — cost, ownership (ADP proxy), club. Never FPL points or positions.
# ---------------------------------------------------------------------------

def get_fpl_bootstrap() -> dict:
    return _get(f"{FPL_API}/bootstrap-static/")


def build_fpl_lookup(bootstrap: dict) -> dict[str, dict]:
    """Return a lookup of {cost, ownership_pct, team_name} keyed by full norm
    name AND ``__last__<lastname>`` fallback, for joining onto the Fantrax pool
    (whose names are full). Higher-minutes entry wins on key collision.
    """
    team_map = {t["id"]: t["name"] for t in bootstrap.get("teams", [])}
    lookup: dict[str, dict] = {}

    def _put(key: str, entry: dict) -> None:
        prev = lookup.get(key)
        if prev is None or entry["minutes"] >= prev["minutes"]:
            lookup[key] = entry

    for p in bootstrap.get("elements", []):
        full = f"{p.get('first_name','')} {p.get('second_name','')}".strip()
        entry = {
            "full_name":     full,
            "cost":          round((p.get("now_cost") or 0) / 10, 1),
            "ownership_pct": _num(p.get("selected_by_percent")),
            "team_name":     team_map.get(p.get("team"), ""),
            "minutes":       _num(p.get("minutes")),
        }
        nkey = _norm_name(full)
        if nkey:
            _put(nkey, entry)
        # Also index by the FPL web_name (often the common short name) and lastname.
        web = _norm_name(p.get("web_name") or "")
        if web and web != nkey:
            _put(web, entry)
        last = _norm_name(p.get("second_name") or p.get("web_name") or "")
        if last:
            _put(f"__last__{last}", entry)
    return lookup


# ---------------------------------------------------------------------------
# Sleeper API — real tackles-won + defensive stats (same Opta feed as Fantrax).
# Free, no key. Used to override API-Football's defensive/gap stats ONLY.
# ---------------------------------------------------------------------------

def get_sleeper_players() -> dict:
    """{player_id: player_info} for all clubsoccer:epl players (name mapping)."""
    return _get(f"{SLEEPER_API}/players/{SLEEPER_SPORT}")


def get_sleeper_season_stats(year: int = 2025) -> dict:
    """{player_id: stats_dict} of Sleeper season stats for the given year."""
    return _get(f"{SLEEPER_API}/stats/{SLEEPER_SPORT}/regular/{year}")


def build_sleeper_lookup(players: dict, season_stats: dict) -> dict[str, dict]:
    """Return {norm_name: {fantrax_stat: value}} of Sleeper's raw stats for each
    real player. Also indexes a ``__last__<lastname>`` fallback key.

    Matched by name (Sleeper player_id ≠ API-Football id), reusing the same
    accent-stripping normalisation used everywhere else. Team-aggregate/garbage
    rows in the stats endpoint are skipped because they have no player entry.
    A second yellow (``yc2``) is folded into ``red_card`` (a red in Fantrax).

    ``__last__`` entries carry ``_ambiguous=True`` when more than one distinct
    Sleeper player shares that surname (the highest-minutes one wins the key), so
    a last-name-only join to it is flagged as higher risk in the debug view.
    """
    lookup: dict[str, dict] = {}
    last_players: dict[str, set] = {}  # lastname → distinct full-name norms
    for pid, raw in season_stats.items():
        info = players.get(pid) or {}
        full_name = (
            info.get("full_name")
            or info.get("name")
            or " ".join(filter(None, [info.get("first_name"), info.get("last_name")]))
        )
        if not full_name:
            continue
        vals: dict[str, float] = {}
        for stat, code in _SLEEPER_FIELD.items():
            v = raw.get(code)
            if v is not None:
                vals[stat] = _num(v)
        if not vals:
            continue
        if "second_yellow" in vals:
            vals["red_card"] = vals.get("red_card", 0.0) + vals.pop("second_yellow")
        vals.setdefault("minutes", 0.0)
        key = _norm_name(full_name)
        prev = lookup.get(key)
        if prev is None or vals["minutes"] >= prev.get("minutes", 0):
            lookup[key] = vals
        last = _norm_name(info.get("last_name") or "")
        if last:
            last_players.setdefault(last, set()).add(key)
            lk = f"__last__{last}"
            prevl = lookup.get(lk)
            if prevl is None or vals["minutes"] >= prevl.get("minutes", 0):
                lookup[lk] = vals
    # Flag surnames shared by more than one player.
    for last, names in last_players.items():
        if len(names) > 1:
            lookup[f"__last__{last}"]["_ambiguous"] = True
    return lookup


# ---------------------------------------------------------------------------
# Player database builder — Fantrax pool is canonical; the rest is enrichment.
# ---------------------------------------------------------------------------

MIN_GW = 15  # below this, projected_pts = 0 (insufficient sample)


def _detail_source(sl: Optional[dict], apif_rec: Optional[dict]) -> tuple[dict, dict]:
    """Return (values, source) dicts over DETAIL_STATS, preferring Sleeper's real
    Opta counts, falling back to API-Football, else marking the stat missing."""
    apif = _apif_detail(apif_rec) if apif_rec else {}
    values, source = {}, {}
    for stat in DETAIL_STATS:
        if sl and stat in sl:
            values[stat] = round(_num(sl[stat]))
            source[stat] = "Sleeper"
        elif stat in apif:
            values[stat] = round(_num(apif[stat]))
            source[stat] = "API-Football"
        else:
            values[stat] = None
            source[stat] = "missing"
    return values, source


def build_player_stats(
    fantrax_players: list[dict],
    fpl_lookup:      Optional[dict] = None,
    sleeper_lookup:  Optional[dict] = None,
    apif_lookup:     Optional[dict] = None,
) -> dict[str, dict]:
    """Build enriched records from the canonical Fantrax pool.

    25/26 points, PPG, games and position come straight from Fantrax. Sleeper /
    API-Football supply the stat-detail columns and ``starter_rate``; FPL supplies
    cost and ownership (ADP proxy). The 26/27 projection is a Bayesian blend of
    the player's real Fantrax PPG with a position prior × 34 GWs × availability.

    Returns {player_key: record}. player_key is the Fantrax id (or norm name).
    """
    fpl_lookup     = fpl_lookup or {}
    sleeper_lookup = sleeper_lookup or {}
    apif_lookup    = apif_lookup or {}

    def _match(nkey: str, last: str, table: dict):
        """(entry, match_type, ambiguous) — full-name key then lastname fallback."""
        e = table.get(nkey)
        if e is not None:
            return e, "full", False
        if last:
            e = table.get(f"__last__{last}")
            if e is not None:
                return e, "lastname", bool(e.get("_ambiguous"))
        return None, "none", False

    # ------------------------------------------------------------------
    # Pass 1 — join enrichment, carry real Fantrax points/PPG/games/position.
    # ------------------------------------------------------------------
    interim: list[dict] = []
    for fx in fantrax_players:
        nkey = _norm_name(fx["name"])
        last = nkey.split()[-1] if nkey else ""

        sl, s_match, s_amb = _match(nkey, last, sleeper_lookup)
        ap, _, _           = _match(nkey, last, apif_lookup)
        fpl                = fpl_lookup.get(nkey) or (fpl_lookup.get(f"__last__{last}") if last else None)

        values, source = _detail_source(sl, ap)
        starter_rate = _num(ap.get("starter_rate")) if ap else 1.0
        starter_rate = starter_rate or 1.0

        interim.append({"fx": fx, "sl": sl, "ap": ap, "fpl": fpl,
                        "match_type": s_match, "ambiguous_last": s_amb,
                        "values": values, "source": source,
                        "starter_rate": starter_rate})

    # ------------------------------------------------------------------
    # Pass 2 — position-average PPG (qualified players) as the Bayesian prior.
    # ------------------------------------------------------------------
    pos_ppg_acc: dict[str, list[float]] = {p: [] for p in POSITION_ORDER}
    for it in interim:
        fx = it["fx"]
        if fx["games"] >= MIN_GW and fx["ppg"] > 0:
            pos_ppg_acc[fx["position"]].append(fx["ppg"])
    pos_avg = {
        pos: round(sum(v) / len(v), 3) if v else 8.0
        for pos, v in pos_ppg_acc.items()
    }

    # ------------------------------------------------------------------
    # Pass 3 — projection + final records.
    # ------------------------------------------------------------------
    result: dict[str, dict] = {}
    for it in interim:
        fx, fpl = it["fx"], it["fpl"]
        pos, games, ppg = fx["position"], fx["games"], fx["ppg"]
        starter_rate = it["starter_rate"]

        if games >= MIN_GW and ppg > 0:
            prior_ppg = pos_avg.get(pos, 8.0)
            # Adaptive shrinkage: full-season veterans keep ~83% of own PPG;
            # fringe starters shrink harder toward the position prior.
            k           = max(3.0, 40.0 / (games ** 0.5))
            blended_ppg = (games * ppg + k * prior_ppg) / (games + k)
            raw_rate    = min(1.0, games / 34) * min(1.0, starter_rate)
            participation = max(0.75, raw_rate) if games >= 25 else raw_rate
            projected_pts = round(blended_ppg * 34 * participation, 1)
        else:
            projected_pts = 0.0

        vals = it["values"]
        key = fx["fantrax_id"] or _norm_name(fx["name"])
        result[key] = {
            "name":            fx["name"],
            "web_name":        fx["name"].split()[-1],
            "team":            (fpl["team_name"] if fpl and fpl.get("team_name") else fx["team"]),
            "position":        pos,
            "total_pts":       fx["total_pts"],
            "ppg":             ppg,
            "games":           games,
            "rank_ov":         fx["rank_ov"],
            "starter_rate":    round(starter_rate, 3),
            "projected_pts":   projected_pts,
            # Stat-detail columns (Sleeper → API-Football → None)
            "goals":           vals["goals"],
            "assists":         vals["assists"],
            "shots_on_target": vals["shots_on_target"],
            "key_passes":      vals["key_passes"],
            "successful_dribbles": vals["successful_dribbles"],
            "accurate_crosses": vals["accurate_crosses"],
            "tackles_won":     vals["tackles_won"],
            "interceptions":   vals["interceptions"],
            "blocked_shots":   vals["blocked_shots"],
            "aerials_won":     vals["aerials_won"],
            "clearances":      vals["clearances"],
            "clean_sheets":    vals["clean_sheets"],
            "saves":           vals["saves"],
            "yellow_cards":    vals["yellow_card"],
            "red_cards":       vals["red_card"],
            # FPL-sourced (cost + community consensus only)
            "cost":            fpl["cost"]          if fpl else None,
            "ownership_pct":   fpl["ownership_pct"] if fpl else None,
            "has_fpl":         fpl is not None,
            "has_sleeper":     it["sl"] is not None,
            "has_apif":        it["ap"] is not None,
            "match_type":      it["match_type"],
            "ambiguous_last":  it["ambiguous_last"],
            "_detail_source":  it["source"],
        }

    # ADP proxy rank: community consensus via FPL ownership %.
    ranked = sorted(
        ((k, d) for k, d in result.items() if d["ownership_pct"] is not None),
        key=lambda x: x[1]["ownership_pct"],
        reverse=True,
    )
    for rank, (key, _) in enumerate(ranked, 1):
        result[key]["adp_rank"] = rank
    for d in result.values():
        d.setdefault("adp_rank", None)

    return result


# ---------------------------------------------------------------------------
# Fantrax API — best-effort live player pool + draft board (needs auth cookie)
# ---------------------------------------------------------------------------

class FantraxAPI:
    """Thin, defensive wrapper over Fantrax's unofficial message API.

    Fantrax exposes a single POST endpoint that takes a list of ``msgs``. Most
    league data requires a logged-in session, supplied here as a raw Cookie
    header string (copy from a browser dev-tools request, or store in
    ``st.secrets['fantrax_cookie']``). Every call is wrapped so a missing or
    expired cookie degrades to an empty result rather than raising.
    """

    def __init__(self, league_id: str, cookie: Optional[str] = None):
        self.league_id = league_id
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; FantraxDraftAssistant/1.0)",
            "Content-Type": "application/json",
        })
        if cookie:
            self.session.headers["Cookie"] = cookie

    def _req(self, method: str, data: dict) -> Optional[dict]:
        body = {"msgs": [{"method": method, "data": data}]}
        try:
            r = self.session.post(FANTRAX_REQ, json=body, timeout=12)
            r.raise_for_status()
            payload = r.json()
        except (requests.RequestException, ValueError):
            return None
        responses = payload.get("responses") or []
        if not responses:
            return None
        return responses[0].get("data")

    def get_league_players(self) -> list[dict]:
        """Return the Fantrax draftable player pool (raw rows), or [].

        Response shape varies; this pulls rows from the common
        ``statsTable``/``rows`` containers and is tolerant of absence.
        """
        data = self._req("getLeaguePlayers", {
            "leagueId": self.league_id,
            "statusOrTeamFilter": "ALL_AVAILABLE",
            "pageNumber": "1",
            "maxResultsPerPage": "500",
            "view": "STATS",
        })
        if not data:
            return []
        for container in ("statsTable", "rows", "players"):
            rows = data.get(container)
            if isinstance(rows, list):
                return rows
        return []

    def get_draft_picks(self) -> list[dict]:
        """Return live draft picks (raw rows), or []. Endpoint/shape best-effort."""
        data = self._req("getDraftPicks", {"leagueId": self.league_id})
        if not data:
            return []
        for container in ("draftPicks", "picks", "rows"):
            rows = data.get(container)
            if isinstance(rows, list):
                return rows
        return []


# ---------------------------------------------------------------------------
# Heavy loaders for @st.cache_data
# ---------------------------------------------------------------------------

def fetch_sources(fantrax_path: str = "data/fantrax_players_2025.csv",
                  stats_path: str = "data/pl_stats_2025.json",
                  sleeper_year: int = 2025) -> dict:
    """Load all inputs: the bundled Fantrax pool (canonical) + bundled
    API-Football stats, plus live FPL and Sleeper enrichment.

    Kept separate from record-building so the network fetch caches once. FPL and
    Sleeper failures degrade gracefully — the core (points, PPG, position,
    projections) runs entirely off the two bundled files.
    """
    fantrax_players = load_fantrax_players(fantrax_path)
    apif_lookup = build_apif_lookup(load_pl_stats(stats_path))

    fpl_lookup: Optional[dict] = None
    fpl_loaded = False
    fpl_error: Optional[str] = None
    try:
        fpl_lookup = build_fpl_lookup(get_fpl_bootstrap())
        fpl_loaded = True
    except Exception as exc:  # noqa: BLE001 - surfaced in the UI status line
        fpl_error = str(exc)

    sleeper_lookup: Optional[dict] = None
    sleeper_loaded = False
    sleeper_error: Optional[str] = None
    try:
        sleeper_lookup = build_sleeper_lookup(
            get_sleeper_players(), get_sleeper_season_stats(sleeper_year)
        )
        sleeper_loaded = True
    except Exception as exc:  # noqa: BLE001 - surfaced in the UI status line
        sleeper_error = str(exc)

    return {
        "fantrax_players": fantrax_players,
        "apif_lookup":     apif_lookup,
        "fpl_lookup":      fpl_lookup,
        "sleeper_lookup":  sleeper_lookup,
        "fantrax_loaded":  bool(fantrax_players),
        "apif_loaded":     bool(apif_lookup),
        "fpl_loaded":      fpl_loaded,
        "fpl_error":       fpl_error,
        "sleeper_loaded":  sleeper_loaded,
        "sleeper_error":   sleeper_error,
    }


def build_from_sources(sources: dict) -> dict:
    """Build the enriched player DB from loaded sources."""
    player_data = build_player_stats(
        sources["fantrax_players"], sources.get("fpl_lookup"),
        sources.get("sleeper_lookup"), sources.get("apif_lookup"),
    )
    # Validate the stat feed reproduces Fantrax's own points (Sleeper preferred).
    validation = None
    if sources.get("sleeper_lookup"):
        validation = validate_scoring(
            sources["fantrax_players"], sources["sleeper_lookup"], "Sleeper")
    return {
        "player_data":     player_data,
        "validation":      validation,
        "fantrax_loaded":  sources["fantrax_loaded"],
        "apif_loaded":     sources.get("apif_loaded", False),
        "fpl_loaded":      sources["fpl_loaded"],
        "fpl_error":       sources["fpl_error"],
        "sleeper_loaded":  sources.get("sleeper_loaded", False),
        "sleeper_error":   sources.get("sleeper_error"),
        "sleeper_matched": sum(1 for d in player_data.values() if d.get("has_sleeper")),
        "apif_matched":    sum(1 for d in player_data.values() if d.get("has_apif")),
        "num_players":     len(player_data),
    }


def fetch_player_db(fantrax_path: str = "data/fantrax_players_2025.csv",
                    stats_path: str = "data/pl_stats_2025.json") -> dict:
    """Convenience: load sources + build in one call (used by tests/CLI)."""
    return build_from_sources(fetch_sources(fantrax_path, stats_path))


# ---------------------------------------------------------------------------
# Draft state — snake board + my-team tracking
# ---------------------------------------------------------------------------

class DraftState:
    """Holds the enriched player DB plus live snake-draft bookkeeping.

    Fantrax live picks require an authenticated API session; when unavailable
    the board renders the empty snake skeleton (still useful for planning your
    slot's pick numbers). ``drafted_keys`` is also driven locally so users can
    mark picks by hand if the API is not connected.
    """

    def __init__(self, league_id: str, num_teams: int = 10, num_rounds: int = 16,
                 my_slot: Optional[int] = None):
        self.league_id  = league_id
        self.num_teams  = num_teams
        self.num_rounds = num_rounds
        self.my_slot    = my_slot

        self.player_data: dict[str, dict] = {}
        self.fantrax_loaded = False
        self.apif_loaded    = False
        self.fpl_loaded     = False
        self.fpl_error: Optional[str] = None
        self.sleeper_loaded = False
        self.sleeper_error: Optional[str] = None
        self.sleeper_matched = 0
        self.apif_matched    = 0
        self.validation: Optional[dict] = None

        # overall_pick_number → {"key": player_key, "slot": int}
        self.picks: dict[int, dict] = {}

    # -- data injection -------------------------------------------------
    def inject_player_db(self, db: dict) -> None:
        self.player_data     = db.get("player_data", {})
        self.fantrax_loaded  = db.get("fantrax_loaded", False)
        self.apif_loaded     = db.get("apif_loaded", False)
        self.fpl_loaded      = db.get("fpl_loaded", False)
        self.fpl_error       = db.get("fpl_error")
        self.sleeper_loaded  = db.get("sleeper_loaded", False)
        self.sleeper_error   = db.get("sleeper_error")
        self.sleeper_matched = db.get("sleeper_matched", 0)
        self.apif_matched    = db.get("apif_matched", 0)
        self.validation      = db.get("validation")

    # -- board geometry -------------------------------------------------
    @property
    def total_picks(self) -> int:
        return self.num_teams * self.num_rounds

    @property
    def current_pick(self) -> int:
        return len(self.picks) + 1

    def slot_on_the_clock(self, overall: int) -> int:
        """Draft slot picking at a given overall pick number (1-indexed, snake)."""
        n = self.num_teams
        rnd = (overall - 1) // n + 1
        pos_in_round = (overall - 1) % n + 1
        return pos_in_round if rnd % 2 == 1 else (n + 1 - pos_in_round)

    def my_next_picks(self) -> list[int]:
        """Upcoming overall pick numbers for my slot, from current pick on."""
        if self.my_slot is None:
            return []
        n = self.num_teams
        out = []
        for rnd in range(1, self.num_rounds + 1):
            pick_in_round = self.my_slot if rnd % 2 == 1 else (n + 1 - self.my_slot)
            overall = (rnd - 1) * n + pick_in_round
            if overall >= self.current_pick:
                out.append(overall)
        return out

    # -- picks ----------------------------------------------------------
    @property
    def drafted_keys(self) -> set[str]:
        return {p["key"] for p in self.picks.values()}

    def record_pick(self, key: str) -> None:
        """Append a pick at the next overall number for the correct slot."""
        overall = self.current_pick
        self.picks[overall] = {"key": key, "slot": self.slot_on_the_clock(overall)}

    def undo_last_pick(self) -> None:
        if self.picks:
            del self.picks[max(self.picks)]

    def reset_picks(self) -> None:
        self.picks.clear()

    # -- queries --------------------------------------------------------
    def get_available(self, position: Optional[str] = None,
                      sort_by: str = "projected_pts") -> list[dict]:
        drafted = self.drafted_keys
        key = sort_by if sort_by in ("projected_pts", "ppg", "total_pts") else "projected_pts"
        out = [
            {**d, "_key": k}
            for k, d in self.player_data.items()
            if k not in drafted and (position is None or d["position"] == position)
        ]
        return sorted(out, key=lambda x: x.get(key) or 0, reverse=True)

    def get_my_picks(self) -> list[dict]:
        if self.my_slot is None:
            return []
        out = []
        for overall, pick in sorted(self.picks.items()):
            if pick["slot"] == self.my_slot:
                d = self.player_data.get(pick["key"])
                if d:
                    out.append({**d, "_key": pick["key"], "_overall": overall})
        return out

    def get_positional_counts(self) -> dict[str, int]:
        counts = {pos: 0 for pos in POSITION_ORDER}
        for p in self.get_my_picks():
            if p["position"] in counts:
                counts[p["position"]] += 1
        return counts

    def get_pick_grid(self) -> list[list[Optional[dict]]]:
        """2D grid [round_idx][slot_idx]; columns are consistent draft slots."""
        n, r = self.num_teams, self.num_rounds
        grid: list[list[Optional[dict]]] = [[None] * n for _ in range(r)]
        for overall, pick in self.picks.items():
            rnd  = (overall - 1) // n
            slot = pick["slot"] - 1
            if 0 <= rnd < r and 0 <= slot < n:
                d = self.player_data.get(pick["key"])
                if d:
                    grid[rnd][slot] = {**d, "_overall": overall}
        return grid
