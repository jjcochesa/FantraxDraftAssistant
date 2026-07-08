"""
Draft engine for the Fantrax EPL Draft Assistant.

Data flow
---------
Points are computed with **Fantrax** scoring rules only. The canonical player
pool and per-player stats come from the harvested API-Football 2025/26 PL
season file (``data/pl_stats_2025.json``). FPL is used **only** to fill stat
gaps that API-Football's season aggregate does not expose (clean sheets, own
goals) and to supply cost / ownership% (an ADP proxy until Fantrax community
drafts start in August) and club display names — never FPL points or FPL
positions.

The Fantrax API (``fantrax.com/fxpa/req``) is wired in as a best-effort,
optional source for the live player pool and draft board. It requires a logged
-in session cookie, so everything degrades gracefully when it is unavailable
(pre-draft research mode still works fully offline off the bundled JSON + FPL).
"""

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

# ---------------------------------------------------------------------------
# Fantrax scoring rules
#
# Two scoring groups: goalkeepers (G) and outfielders (D / M / F share the same
# rules). Values are points-per-stat. A float applies to every position; a dict
# gives per-position values (missing position → 0).
#
# NOTE on Clean Sheets (CS): the league's Fantrax setup awards clean-sheet
# points on a positional tier, matching real Fantrax EPL scoring and the
# reference Sleeper assistant — GK +8, D +6, M +1, F +0 — rather than a flat
# +6 to every outfielder. Adjust here if your league differs.
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


def _apif_to_fantrax_stats(rec: dict, tackle_win_rate: float = 0.65) -> dict:
    """Map an API-Football season record to Fantrax raw-stat inputs.

    Stats API-Football's season aggregate does not expose are omitted (they
    default to 0 in scoring): clean_sheets, own_goals, accurate_crosses,
    aerials_won, clearances, high_claims, smothers, dispossessed. Clean sheets
    and own goals are filled from FPL later; the rest remain 0 until the Fantrax
    API pool is wired in.
    """
    return {
        "goals":               _num(rec.get("goals")),
        "assists":             _num(rec.get("assists")),
        "shots_on_target":     _num(rec.get("shots_on_target")),
        "key_passes":          _num(rec.get("key_passes")),
        "successful_dribbles": _num(rec.get("dribbles_success")),
        # API-Football reports TOTAL tackles, but Fantrax scores tackles *won*.
        # Discount by tackle_win_rate (league-average success ≈ 0.65) so high-
        # volume tacklers aren't over-credited. Set to 1.0 to use totals as-is.
        "tackles_won":         _num(rec.get("tackles_total")) * tackle_win_rate,
        "interceptions":       _num(rec.get("interceptions")),
        "blocked_shots":       _num(rec.get("tackles_blocks")),
        "penalty_drawn":       _num(rec.get("penalties_won")),
        "penalties_missed":    _num(rec.get("penalties_missed")),
        "penalties_saved":     _num(rec.get("penalties_saved")),
        "saves":               _num(rec.get("saves")),
        "goals_against":       _num(rec.get("goals_conceded")),
        "yellow_card":         _num(rec.get("yellow_cards")),
        # A second yellow is a red card in Fantrax terms.
        "red_card":            _num(rec.get("red_cards")) + _num(rec.get("yellowred_cards")),
        # Not in API-Football's season aggregate — filled from FPL when present.
        "clean_sheets":        0.0,
        "own_goals":           0.0,
    }


def _calc_pts(stats: dict, position: str) -> float:
    """Fantrax fantasy points for a raw-stat dict at a given position."""
    pos = position.upper()
    pts = 0.0
    for stat_name, rule in FANTRAX_SCORING.items():
        val = _num(stats.get(stat_name))
        if val == 0:
            continue
        mult = rule.get(pos, 0) if isinstance(rule, dict) else float(rule)
        pts += val * mult
    return round(pts, 2)


# ---------------------------------------------------------------------------
# FPL API — cost, ownership (ADP proxy), club, and CS/OG gap-fill ONLY.
# Never use FPL points or FPL element_type (position).
# ---------------------------------------------------------------------------

def get_fpl_bootstrap() -> dict:
    return _get(f"{FPL_API}/bootstrap-static/")


def build_fpl_lookup(bootstrap: dict) -> dict[str, dict]:
    """Return {norm_lastname: {cost, ownership_pct, team_name, clean_sheets,
    own_goals, penalties_saved, goals_conceded}} for cross-source matching.

    Keyed by normalised last name (API-Football abbreviates first names, so a
    full-name join fails — last name is the reliable key). On collision the
    higher-minutes entry wins so the regular starter is matched, not a reserve.
    """
    team_map = {t["id"]: t["name"] for t in bootstrap.get("teams", [])}
    lookup: dict[str, dict] = {}
    for p in bootstrap.get("elements", []):
        key = _norm_name(p.get("second_name") or p.get("web_name") or "")
        if not key:
            continue
        entry = {
            "full_name":       f"{p.get('first_name','')} {p.get('second_name','')}".strip(),
            "cost":            round((p.get("now_cost") or 0) / 10, 1),
            "ownership_pct":   _num(p.get("selected_by_percent")),
            "team_name":       team_map.get(p.get("team"), ""),
            "clean_sheets":    _num(p.get("clean_sheets")),
            "own_goals":       _num(p.get("own_goals")),
            "penalties_saved": _num(p.get("penalties_saved")),
            "goals_conceded":  _num(p.get("goals_conceded")),
            "minutes":         _num(p.get("minutes")),
        }
        prev = lookup.get(key)
        if prev is None or entry["minutes"] >= prev["minutes"]:
            lookup[key] = entry
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
    """
    lookup: dict[str, dict] = {}
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
            lk = f"__last__{last}"
            prevl = lookup.get(lk)
            if prevl is None or vals["minutes"] >= prevl.get("minutes", 0):
                lookup[lk] = vals
    return lookup


# ---------------------------------------------------------------------------
# Player database builder
# ---------------------------------------------------------------------------

MIN_GW = 15  # below this, projected_pts = 0 (insufficient sample)


def build_player_stats(
    pl_stats:        list[dict],
    fpl_lookup:      Optional[dict] = None,
    tackle_win_rate: float = 0.65,
    sleeper_lookup:  Optional[dict] = None,
) -> dict[str, dict]:
    """Merge API-Football season stats + Sleeper defensive stats + FPL gap-fill
    into enriched records, compute Fantrax season points and the 26/27 projection.

    When a player matches ``sleeper_lookup``, Sleeper's real Opta stats (tackles
    won, interceptions, blocked shots, accurate crosses, clean sheets, aerials,
    clearances, dispossessed, own goals) override API-Football's — this is the
    same feed Fantrax scores on. ``tackle_win_rate`` only affects the API-Football
    tackle proxy for players NOT found in Sleeper (1.0 = totals unchanged).

    Returns {player_key: record}. player_key is norm_name (unique per record).
    """
    fpl_lookup     = fpl_lookup or {}
    sleeper_lookup = sleeper_lookup or {}

    # ------------------------------------------------------------------
    # Pass 1 — assemble raw records, join Sleeper + FPL, compute season points.
    # ------------------------------------------------------------------
    interim: list[dict] = []
    for rec in pl_stats:
        pos = _APIF_POS.get((rec.get("position") or "").lower())
        if pos is None:
            continue  # skip records with an unrecognised position

        last = _norm_name(rec.get("lastname") or "")
        fpl  = fpl_lookup.get(last)

        stats = _apif_to_fantrax_stats(rec, tackle_win_rate)

        # Sleeper override — real Opta defensive stats (name-matched; the
        # abbreviated API-Football first name means the lastname key does the work).
        sl = sleeper_lookup.get(_norm_name(rec.get("norm_name") or rec.get("name") or ""))
        if not sl and last:
            sl = sleeper_lookup.get(f"__last__{last}")
        if sl:
            for stat, v in sl.items():
                if stat != "minutes":
                    stats[stat] = v

        # FPL fills only what Sleeper didn't provide.
        if fpl:
            if "clean_sheets" not in (sl or {}):
                stats["clean_sheets"] = fpl["clean_sheets"]
            if "own_goals" not in (sl or {}):
                stats["own_goals"] = fpl["own_goals"]
            if not stats["penalties_saved"]:
                stats["penalties_saved"] = fpl["penalties_saved"]
            if not stats["goals_against"]:
                stats["goals_against"] = fpl["goals_conceded"]

        total_pts = _calc_pts(stats, pos)
        minutes   = int(_num(rec.get("minutes")))
        games     = min(38, round(minutes / 90)) if minutes > 0 else 0
        ppg       = round(total_pts / games, 2) if games >= MIN_GW else 0.0

        interim.append({"rec": rec, "pos": pos, "fpl": fpl, "stats": stats,
                        "has_sleeper": bool(sl),
                        "total_pts": total_pts, "minutes": minutes,
                        "games": games, "ppg": ppg})

    # ------------------------------------------------------------------
    # Pass 2 — position-average PPG (qualified players only) as Bayesian prior.
    # ------------------------------------------------------------------
    pos_ppg_acc: dict[str, list[float]] = {p: [] for p in POSITION_ORDER}
    for it in interim:
        if it["games"] >= MIN_GW and it["ppg"] > 0:
            pos_ppg_acc[it["pos"]].append(it["ppg"])
    pos_avg = {
        pos: round(sum(v) / len(v), 3) if v else 8.0
        for pos, v in pos_ppg_acc.items()
    }

    # ------------------------------------------------------------------
    # Pass 3 — projection + final records.
    # ------------------------------------------------------------------
    result: dict[str, dict] = {}
    for it in interim:
        rec, pos, fpl, stats = it["rec"], it["pos"], it["fpl"], it["stats"]
        games, ppg = it["games"], it["ppg"]

        starter_rate = _num(rec.get("starter_rate")) or 1.0

        if games >= MIN_GW:
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

        full_name = rec.get("name") or f"{rec.get('firstname','')} {rec.get('lastname','')}".strip()
        # Prefer FPL's full first name when available (API-Football abbreviates).
        display_name = (fpl["full_name"] if fpl and fpl.get("full_name") else full_name)
        key = _norm_name(rec.get("norm_name") or full_name) or full_name

        result[key] = {
            "name":            display_name,
            "web_name":        rec.get("lastname") or display_name,
            "team":            (fpl["team_name"] if fpl and fpl.get("team_name") else rec.get("club", "—")),
            "position":        pos,
            "total_pts":       it["total_pts"],
            "ppg":             ppg,
            "games":           games,
            "minutes":         it["minutes"],
            "starter_rate":    round(starter_rate, 3),
            "projected_pts":   projected_pts,
            "rating":          _num(rec.get("rating")) or None,
            # Fantrax stat breakdown (season 25/26)
            "goals":           int(stats["goals"]),
            "assists":         int(stats["assists"]),
            "shots_on_target": int(stats["shots_on_target"]),
            "key_passes":      int(stats["key_passes"]),
            "successful_dribbles": round(_num(stats["successful_dribbles"])),
            # Real tackles-won when Sleeper-matched, else API-Football total ×
            # tackle_win_rate. Round rather than truncate.
            "tackles_won":     round(_num(stats["tackles_won"])),
            "interceptions":   round(_num(stats["interceptions"])),
            "blocked_shots":   round(_num(stats["blocked_shots"])),
            "clean_sheets":    round(_num(stats["clean_sheets"])),
            "saves":           int(stats["saves"]),
            "yellow_cards":    int(stats["yellow_card"]),
            "red_cards":       int(stats["red_card"]),
            # FPL-sourced (cost + community consensus only)
            "cost":            fpl["cost"]          if fpl else None,
            "ownership_pct":   fpl["ownership_pct"] if fpl else None,
            "has_fpl":         fpl is not None,
            "has_sleeper":     it["has_sleeper"],
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

def fetch_sources(stats_path: str = "data/pl_stats_2025.json",
                  sleeper_year: int = 2025) -> dict:
    """Fetch the slow inputs only: bundled stats + FPL + Sleeper defensive stats.

    Kept separate from scoring so the network fetch can be cached once while
    cheap re-scoring (e.g. changing the tackle-win rate) runs on every rerun.
    FPL/Sleeper failures degrade gracefully — the app still runs off the bundled
    stats (with the API-Football tackle proxy) if either is unreachable.
    """
    pl_stats = load_pl_stats(stats_path)

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
        "pl_stats":       pl_stats,
        "fpl_lookup":     fpl_lookup,
        "sleeper_lookup": sleeper_lookup,
        "stats_loaded":   bool(pl_stats),
        "fpl_loaded":     fpl_loaded,
        "fpl_error":      fpl_error,
        "sleeper_loaded": sleeper_loaded,
        "sleeper_error":  sleeper_error,
    }


def build_from_sources(sources: dict, tackle_win_rate: float = 0.65) -> dict:
    """Build the enriched player DB from cached sources (cheap; rate-dependent)."""
    player_data = build_player_stats(
        sources["pl_stats"], sources.get("fpl_lookup"), tackle_win_rate,
        sources.get("sleeper_lookup"),
    )
    matched = sum(1 for d in player_data.values() if d.get("has_sleeper"))
    return {
        "player_data":    player_data,
        "stats_loaded":   sources["stats_loaded"],
        "fpl_loaded":     sources["fpl_loaded"],
        "fpl_error":      sources["fpl_error"],
        "sleeper_loaded": sources.get("sleeper_loaded", False),
        "sleeper_error":  sources.get("sleeper_error"),
        "sleeper_matched": matched,
        "num_players":    len(player_data),
    }


def fetch_player_db(stats_path: str = "data/pl_stats_2025.json",
                    tackle_win_rate: float = 0.65) -> dict:
    """Convenience: fetch sources + build in one call (used by tests/CLI)."""
    return build_from_sources(fetch_sources(stats_path), tackle_win_rate)


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
        self.stats_loaded  = False
        self.fpl_loaded    = False
        self.fpl_error: Optional[str] = None
        self.sleeper_loaded = False
        self.sleeper_error: Optional[str] = None
        self.sleeper_matched = 0

        # overall_pick_number → {"key": player_key, "slot": int}
        self.picks: dict[int, dict] = {}

    # -- data injection -------------------------------------------------
    def inject_player_db(self, db: dict) -> None:
        self.player_data    = db.get("player_data", {})
        self.stats_loaded   = db.get("stats_loaded", False)
        self.fpl_loaded     = db.get("fpl_loaded", False)
        self.fpl_error      = db.get("fpl_error")
        self.sleeper_loaded = db.get("sleeper_loaded", False)
        self.sleeper_error  = db.get("sleeper_error")
        self.sleeper_matched = db.get("sleeper_matched", 0)

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
