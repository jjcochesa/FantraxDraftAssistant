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


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None

    db = de.fetch_player_db()["player_data"]
    ranked = sorted((d for d in db.values() if d.get("blend") is not None),
                    key=lambda d: d["blend"])
    if limit:
        ranked = ranked[:limit]

    rows, skipped, off_pool = [], [], []
    for d in ranked:
        if not d.get("team_code"):
            skipped.append(d["name"])
            continue
        rows.append((d["name"], d["team_code"]))
        if not d.get("in_pool", True):
            off_pool.append(d["name"])

    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)   # no header — the format is positional

    print(f"Wrote {OUT}: {len(rows)} players in Blend order")
    if skipped:
        print(f"  skipped {len(skipped)} with no team code: {', '.join(skipped)}")
    if off_pool:
        print(f"  {len(off_pool)} not in the Fantrax export, so the name may not "
              f"match on import: {', '.join(off_pool)}")
    print("\nUpload with the FIRST format option "
          "(Column 1 = First Last, Column 2 = Team abbreviation).")


if __name__ == "__main__":
    main()
