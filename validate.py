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

    diagnose_bias(fx, players, season)


def diagnose_bias(fx: list, players: dict, season: dict) -> None:
    """Find which raw Sleeper field best explains the residual (Fantrax − calc)
    for MID+FWD, where the bias lives. A field NOT already scored that lines up
    with the residual is the missing/under-credited stat; its implied slope ≈ its
    Fantrax point value."""
    # Index raw season rows by name for matching.
    raw_lookup: dict = {}
    seen: dict = {}
    for pid, row in season.items():
        info = players.get(pid) or {}
        name = (info.get("full_name") or info.get("name")
                or " ".join(filter(None, [info.get("first_name"), info.get("last_name")])))
        if not name:
            continue
        entry = dict(row)
        entry["minutes"] = de._num(row.get("min"))
        de._index_entry(raw_lookup, seen, name, entry)
    de._flag_ambiguous(raw_lookup, seen)

    scored = set(de._SLEEPER_FIELD.values())  # Sleeper codes we already score
    resid, raws = [], []
    for p in fx:
        if p["position"] not in ("M", "F") or p["total_pts"] <= 0:
            continue
        rawrow, _ = de.match_entry(p["name"], raw_lookup)
        if not rawrow:
            continue
        calc = de.score_from_stats(
            {ft: de._num(rawrow.get(code)) for ft, code in de._SLEEPER_FIELD.items()},
            p["position"])
        resid.append(p["total_pts"] - calc)
        raws.append(rawrow)

    n = len(resid)
    if n < 20:
        print("\n(Not enough MID/FWD matches to diagnose the bias.)")
        return
    mr = sum(resid) / n

    # candidate fields: any numeric field present on many players
    fields = {}
    for row in raws:
        for k, v in row.items():
            if k.startswith("pos_") or k.endswith("90") or k == "minutes":
                continue
            if isinstance(v, (int, float)):
                fields.setdefault(k, 0)
                fields[k] += 1
    results = []
    for k in fields:
        xs = [de._num(r.get(k)) for r in raws]
        mx = sum(xs) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        if sxx <= 0:
            continue
        sxr = sum((x - mx) * (rr - mr) for x, rr in zip(xs, resid))
        srr = sum((rr - mr) ** 2 for rr in resid)
        slope = sxr / sxx
        corr = sxr / (sxx ** 0.5 * srr ** 0.5) if srr > 0 else 0
        results.append((abs(corr), corr, slope, mx, k))
    results.sort(reverse=True)

    print("\n=== Bias diagnosis (MID+FWD residual = Fantrax − calc, mean "
          f"{mr:+.1f}) ===")
    print("Raw field vs residual — a high-|corr| field we DON'T already score is")
    print("the culprit; 'slope' ≈ its Fantrax points-per-unit.\n")
    print(f"  {'field':10} {'corr':>6} {'slope':>7} {'avg':>7}  scored?")
    for _, corr, slope, mx, k in results[:15]:
        print(f"  {k:10} {corr:6.2f} {slope:7.2f} {mx:7.1f}  "
              f"{'yes' if k in scored else 'NO  <-- candidate'}")


if __name__ == "__main__":
    main()
