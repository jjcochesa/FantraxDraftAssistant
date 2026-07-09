"""
Validate that bottom-up Fantrax scoring reproduces Fantrax's own points.

For every player in the bundled Fantrax export, this joins Sleeper's raw Opta
stats by name, computes points as (raw stats × Fantrax points-per-stat), and
compares to Fantrax's actual FPts. A high correlation and low bias mean the feed
is trustworthy — i.e. a per-stat projection built on Sleeper's numbers will land
on the same scale as Fantrax.

Sleeper is free and needs no key, but must be reachable from where you run this
(it is blocked in some sandboxes). Usage:

    python validate.py
"""

import draft_engine as de


def main() -> None:
    fx = de.load_fantrax_players()
    if not fx:
        print("No Fantrax export at data/fantrax_players_2025.csv — nothing to validate.")
        return
    print(f"Fantrax players loaded: {len(fx)}")

    try:
        players = de.get_sleeper_players()
        season  = de.get_sleeper_season_stats(2025)
        sleeper = de.build_sleeper_lookup(players, season)
    except Exception as exc:  # noqa: BLE001
        print(f"Sleeper fetch failed ({exc}). Run where api.sleeper.app is reachable.")
        return
    print(f"Sleeper stat rows: {len(season)}")

    rep = de.validate_scoring(fx, sleeper, "Sleeper")
    if not rep.get("n"):
        print("No name matches between the Fantrax pool and Sleeper — check names.")
        return

    print(f"\nMatched players: {rep['n']}")
    print(f"Correlation (bottom-up vs Fantrax FPts): {rep['correlation']}")
    print(f"Mean absolute error: {rep['mae']} pts")
    print(f"Mean signed bias:    {rep['bias']:+} pts")
    print(f"Per-position bias:   {rep['pos_bias']}")
    print("\nBiggest gaps (calc − Fantrax):")
    for r in rep["worst"]:
        print(f"  {r['name'][:28]:30} {r['pos']}  "
              f"fantrax={r['fantrax']:7.1f}  calc={r['calc']:7.1f}  diff={r['diff']:+.1f}")


if __name__ == "__main__":
    main()
