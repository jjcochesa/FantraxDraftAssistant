"""
Export the blended draft board as a CSV for Fantrax's "Import rankings" dialog.

Fantrax's first import format is two positional columns and **no header**:

    <First name> <Last name>,<Team abbreviation>

Rows are written in Blend order (best first), which is the ranking Fantrax will
apply. Names and team codes come straight from the Fantrax export, so they match
Fantrax's own spelling by construction.

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


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None

    db = de.fetch_player_db()["player_data"]
    ranked = sorted((d for d in db.values() if d.get("blend") is not None),
                    key=lambda d: d["blend"])
    if limit:
        ranked = ranked[:limit]

    alias, drop = load_aliases()

    rows, skipped, off_pool, renamed, dropped = [], [], [], [], []
    for d in ranked:
        name = d["name"]
        if name in drop:
            dropped.append(name)
            continue
        if not d.get("team_code"):
            skipped.append(name)
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

    print(f"Wrote {OUT}: {len(rows)} players in Blend order")
    if renamed:
        print(f"  {len(renamed)} rewritten to their Fantrax legal name")
    if dropped:
        print(f"  {len(dropped)} dropped as ineligible: {', '.join(dropped)}")
    if skipped:
        print(f"  skipped {len(skipped)} with no team code: {', '.join(skipped)}")
    if off_pool:
        print(f"  {len(off_pool)} not in the Fantrax export, so the name may not "
              f"match on import: {', '.join(off_pool)}")
    print("\nUpload with the FIRST format option "
          "(Column 1 = First Last, Column 2 = Team abbreviation).")


if __name__ == "__main__":
    main()
