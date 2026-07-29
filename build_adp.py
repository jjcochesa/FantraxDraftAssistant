"""
Aggregate real online drafts into an ADP (Average Draft Position) table.

Each draft is one file in ``data/adp_drafts/`` listing the players in pick order
(pick 1 first), one name per line. Blank lines and lines starting with ``#`` are
ignored (use ``#`` for notes, e.g. ``# 12-team, snake``). Player names are
matched to the Fantrax pool by name, so spelling just has to be close — the same
accent-stripping / first+last matcher the app uses does the rest.

For every player this computes:
  ADP     mean pick number across the drafts they appear in
  Drafts  how many drafts they were picked in (sample size)
  Min/Max earliest / latest pick seen (range)

Writes ``data/adp.csv``, which the app reads as its ADP source.

Usage:
    python build_adp.py
"""

import csv
from pathlib import Path

import draft_engine as de

DRAFTS_DIR = Path("data/adp_drafts")
OUT_CSV    = Path("data/adp.csv")


def _read_draft(path: Path) -> list[str]:
    """Return the ordered list of player names in a draft file (pick 1 first)."""
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        # tolerate "12. Player Name" or "12) Player" or "Player Name" formats
        for sep in (". ", ") ", "\t", ",", " - "):
            if sep in s and s.split(sep, 1)[0].strip().rstrip(".)").isdigit():
                s = s.split(sep, 1)[1].strip()
                break
        if s:
            names.append(s)
    return names


def main() -> None:
    if not DRAFTS_DIR.exists():
        DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    draft_files = sorted(p for p in DRAFTS_DIR.glob("*.txt"))
    if not draft_files:
        print(f"No drafts in {DRAFTS_DIR}/ yet. Add one draft per .txt file "
              "(players in pick order) and re-run.")
        return

    # Canonical pool → build a name lookup so every draft name resolves to the
    # same canonical Fantrax name (dedupes spelling variants across drafts).
    fantrax = de.load_fantrax_players()
    pool_lookup: dict = {}
    seen: dict = {}
    for fx in fantrax:
        de._index_entry(pool_lookup, seen, fx["name"], {"name": fx["name"], "minutes": 1})
    de._flag_ambiguous(pool_lookup, seen)

    picks: dict[str, list[int]] = {}
    unmatched: dict[str, int] = {}
    for f in draft_files:
        for pick_no, raw_name in enumerate(_read_draft(f), start=1):
            hit, _ = de.match_entry(raw_name, pool_lookup)
            name = hit["name"] if hit else raw_name.strip()
            if not hit:
                unmatched[raw_name.strip()] = unmatched.get(raw_name.strip(), 0) + 1
            picks.setdefault(name, []).append(pick_no)

    rows = []
    for name, ps in picks.items():
        rows.append({
            "Name":   name,
            "ADP":    round(sum(ps) / len(ps), 1),
            "Drafts": len(ps),
            "Min":    min(ps),
            "Max":    max(ps),
        })
    rows.sort(key=lambda r: r["ADP"])

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["Name", "ADP", "Drafts", "Min", "Max"])
        w.writeheader()
        w.writerows(rows)

    print(f"Aggregated {len(draft_files)} draft(s) → {OUT_CSV}")
    print(f"  {len(rows)} players with an ADP")
    if unmatched:
        print(f"  {len(unmatched)} name(s) did not match the Fantrax pool "
              "(kept as-is; fix spelling in the draft files if needed):")
        for nm, c in sorted(unmatched.items(), key=lambda x: -x[1])[:15]:
            print(f"    {nm}  (x{c})")
    print("\nTop 20 by ADP:")
    for r in rows[:20]:
        print(f"  {r['ADP']:5.1f}  {r['Name']:26} "
              f"(n={r['Drafts']}, {r['Min']}-{r['Max']})")


if __name__ == "__main__":
    main()
