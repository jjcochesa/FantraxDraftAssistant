"""
Export the blended draft board as a CSV for Fantrax's "Import rankings" dialog.

Fantrax's first import format is two positional columns and **no header**:

    <First name> <Last name>,<Team abbreviation>

Rows are written in YOUR board order (best first) — Blend, except where
my_overrides.csv sets a Rank — which is the ranking Fantrax will apply. Names
and team codes come straight from the Fantrax export, so they match Fantrax's
own spelling by construction.

Usage:
    python export_fantrax_rankings.py            # -> fantrax_rankings_import.csv
    python export_fantrax_rankings.py 150        # only the top 150
"""

import csv
import sys

import draft_engine as de

OUT = "fantrax_rankings_import.csv"
ALIASES = "data/fantrax_name_aliases.csv"


def load_aliases(path: str = ALIASES) -> tuple[dict, set]:
    """Read name fixes for the Fantrax importer.

    Fantrax matches on a player's LEGAL name, not the display name in its own
    export — so "Savio" has to be sent as "Savio Moreira de Oliveira". Columns:
      Name        the name as it appears on our board
      FantraxName what to write instead (blank = leave unchanged)
      Exclude     "Y" to drop the player (Fantrax reports them ineligible)
    """
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return {}, set()
    alias, drop = {}, set()
    with p.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("Name") or "").strip()
            if not name:
                continue
            if (row.get("Exclude") or "").strip().upper().startswith("Y"):
                drop.add(name)
            elif (row.get("FantraxName") or "").strip():
                alias[name] = row["FantraxName"].strip()
    return alias, drop


def _initial_only(name: str) -> bool:
    """True for board names that never got a real first name ("D. Maeda").

    Draft boards abbreviate, and a player absent from the Fantrax export has no
    canonical spelling to recover it from. Fantrax matches on a full legal name,
    so shipping one of these guarantees a "player not found" on import — better
    to leave it out and say so than to pad the file with rows that fail.
    """
    head = name.split()[0] if name.split() else ""
    return len(head.rstrip(".")) <= 1


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None

    db = de.fetch_player_db()["player_data"]
    # Order by YOUR board, not the raw consensus: a Rank in my_overrides.csv
    # wins over Blend, exactly as Auto-rank does. The point of importing
    # rankings into Fantrax is to see your own board during the draft, so an
    # override you set has to survive the trip.
    ranked = sorted((d for d in db.values() if d.get("blend") is not None),
                    key=lambda d: d.get("my_rank") or d["blend"])
    if limit:
        ranked = ranked[:limit]

    alias, drop = load_aliases()

    rows, skipped, off_pool, renamed, dropped, partial, faded = [], [], [], [], [], [], []
    for d in ranked:
        name = d["name"]
        if d.get("do_not_draft"):
            faded.append(name)
            continue
        if name in drop:
            dropped.append(name)
            continue
        if not d.get("team_code"):
            skipped.append(name)
            continue
        if _initial_only(name):
            partial.append(f"{name} ({d['team_code']})")
            continue
        out_name = alias.get(name, name)
        if out_name != name:
            renamed.append(f"{name} -> {out_name}")
        rows.append((out_name, d["team_code"]))
        if not d.get("in_pool", True):
            off_pool.append(name)

    # Plain \n endings and no BOM. Python's csv default is \r\n; a naive parser
    # that splits on \n would leave the \r stuck to the team code ("MUN\r") and
    # fail every lookup.
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh, lineterminator="\n").writerows(rows)  # no header: positional format

    print(f"Wrote {OUT}: {len(rows)} players in board order (Blend, with my_overrides.csv Ranks applied)")
    if renamed:
        print(f"  {len(renamed)} rewritten to their Fantrax legal name")
    if dropped:
        print(f"  {len(dropped)} dropped as ineligible: {', '.join(dropped)}")
    if faded:
        print(f"  {len(faded)} on your do-not-draft list: {', '.join(faded)}")
    if skipped:
        print(f"  skipped {len(skipped)} with no team code: {', '.join(skipped)}")
    if partial:
        print(f"  held back {len(partial)} still on a board initial (need a full "
              f"first name to match): {', '.join(partial)}")
    if off_pool:
        print(f"  {len(off_pool)} not in the Fantrax export, so the name may not "
              f"match on import: {', '.join(off_pool)}")
    print("\nUpload with the FIRST format option "
          "(Column 1 = First Last, Column 2 = Team abbreviation).")


if __name__ == "__main__":
    main()
