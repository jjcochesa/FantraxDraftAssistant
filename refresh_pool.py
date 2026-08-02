"""
Refresh the canonical Fantrax player pool (``data/fantrax_players_2025.csv``).

The pool is what gives every player their real name spelling, club, position and
last-season points. A stale pool is the root cause of most import failures: a
player Fantrax added after the snapshot has no canonical name here, so the
rankings CSV either omits him or sends a board abbreviation Fantrax rejects.

This pulls the pool live via Fantrax's message API, which needs a logged-in
session. Supply the cookie one of two ways:

    export FANTRAX_COOKIE='<paste the Cookie header>'
    python refresh_pool.py

...or put it in ``.streamlit/secrets.toml`` as ``fantrax_cookie = "..."`` and
just run the script.

To grab the cookie: open Fantrax in a browser, DevTools -> Network -> any
request to fantrax.com -> Request Headers -> copy the whole ``Cookie:`` value.

    python refresh_pool.py                 # write data/fantrax_players_2025.csv
    python refresh_pool.py --dry-run       # show what would change, write nothing

Nothing is overwritten unless the fetch returns a plausible pool (several
hundred players with names and clubs), so a failed or half-authenticated call
leaves the existing file intact.
"""

import csv
import os
import re
import sys
from pathlib import Path

import draft_engine as de

LEAGUE_ID = "wxgdnh5dmrbb90nb"
OUT = Path("data/fantrax_players_2025.csv")
FIELDS = ["ID", "Player", "Team", "Position", "RkOv", "FPts", "FP/G"]

# A real pool is ~600+ players. Anything far below this means the session was
# rejected and Fantrax handed back a stub, which must not clobber a good file.
MIN_PLAUSIBLE = 300


def _cookie() -> str | None:
    if os.environ.get("FANTRAX_COOKIE"):
        return os.environ["FANTRAX_COOKIE"]
    secrets = Path(".streamlit/secrets.toml")
    if secrets.exists():
        m = re.search(r'fantrax_cookie\s*=\s*"(.*?)"', secrets.read_text(), re.S)
        if m:
            return m.group(1)
    return None


def _first(d: dict, *keys):
    """Fantrax's row shape is not stable; take the first key that's present."""
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _parse(row: dict) -> dict | None:
    """Map one raw API row onto our CSV columns, or None if it isn't a player."""
    scorer = row.get("scorer") or row.get("player") or row
    name = _first(scorer, "name", "playerName", "shortName")
    if not name:
        return None
    cells = row.get("cells") or []
    nums = [c.get("content") if isinstance(c, dict) else c for c in cells]

    def num(i):
        try:
            return float(str(nums[i]).replace(",", ""))
        except (IndexError, TypeError, ValueError):
            return ""

    return {
        "ID":       _first(scorer, "scorerId", "id", "playerId") or "",
        "Player":   str(name).strip(),
        "Team":     str(_first(scorer, "teamShortName", "teamName", "team") or "").strip().upper(),
        "Position": str(_first(scorer, "posShortNames", "position", "pos") or "").strip().upper()[:1],
        "RkOv":     _first(row, "rank", "rankOverall") or "",
        "FPts":     _first(row, "fantasyPoints", "fpts") or num(0),
        "FP/G":     _first(row, "fantasyPointsPerGame", "fppg") or num(1),
    }


def main() -> None:
    dry = "--dry-run" in sys.argv

    cookie = _cookie()
    if not cookie:
        sys.exit(
            "No Fantrax cookie found.\n"
            "  export FANTRAX_COOKIE='<Cookie header>'   (or set fantrax_cookie "
            "in .streamlit/secrets.toml)\n"
            "Get it from DevTools -> Network -> any fantrax.com request -> "
            "Request Headers -> Cookie."
        )

    api = de.FantraxAPI(LEAGUE_ID, cookie=cookie)
    raw = api.get_league_players()
    if not raw:
        sys.exit(
            "Fantrax returned no players. The cookie is probably expired — "
            "grab a fresh one and retry. Existing pool left untouched."
        )

    rows = [r for r in (_parse(r) for r in raw) if r and r["Player"]]
    named = [r for r in rows if r["Team"]]
    print(f"Fetched {len(raw)} raw rows -> {len(rows)} players ({len(named)} with a club)")

    if len(rows) < MIN_PLAUSIBLE:
        sys.exit(
            f"Only {len(rows)} players came back (expected {MIN_PLAUSIBLE}+). "
            "That usually means a partially-authenticated session or a changed "
            "response shape. Refusing to overwrite the pool.\n"
            f"First raw row, for debugging:\n  {str(raw[0])[:400]}"
        )

    old = {r["Player"] for r in csv.DictReader(OUT.open(encoding="utf-8-sig"))} \
        if OUT.exists() else set()
    new = {r["Player"] for r in rows}
    added, gone = sorted(new - old), sorted(old - new)
    print(f"  +{len(added)} new, -{len(gone)} no longer listed")
    for n in added[:20]:
        print(f"    + {n}")
    if len(added) > 20:
        print(f"    … and {len(added) - 20} more")
    for n in gone[:10]:
        print(f"    - {n}")

    if dry:
        print("\n--dry-run: nothing written.")
        return

    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"\nWrote {OUT} ({len(rows)} players)")
    print("Next: python build_adp.py && python export_fantrax_rankings.py")


if __name__ == "__main__":
    main()
