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

    for pos in ("F", "M"):
        _fit_position(fx, raw_lookup, pos)


# Sleeper raw code → our assumed Fantrax point value (outfield). `cs` differs by
# position; `gp/gs/sat` are candidates we do NOT currently score (assumed 0).
_ASSUMED = {
    "g": 9, "at": 6, "sot": 2, "kp": 2, "cos": 1, "acnc": 1, "tkw": 2,
    "int": 1.5, "bs": 1.5, "aer": 0.5, "clr": 0.0, "dis": -0.5, "pkd": 2,
    "pkm": -4, "og": -5, "yc": -2, "rc": -7,
    "gp": 0.0, "gs": 0.0, "sat": 0.0,
}


def _fit_position(fx: list, raw_lookup: dict, pos: str) -> None:
    """Multivariate least-squares: recover each stat's true Fantrax point value
    for one position, controlling for the others. A recovered value far from our
    assumption (or a big intercept / candidate coefficient) is the bias source."""
    assumed = dict(_ASSUMED)
    assumed["cs"] = 1.0 if pos == "M" else 0.0
    feats = list(assumed)

    X, y = [], []
    for p in fx:
        if p["position"] != pos or p["total_pts"] <= 0:
            continue
        r, _ = de.match_entry(p["name"], raw_lookup)
        if not r:
            continue
        row = [1.0]  # intercept
        for f in feats:
            if f == "rc":
                row.append(de._num(r.get("rc")) + de._num(r.get("yc2")))
            else:
                row.append(de._num(r.get(f)))
        X.append(row)
        y.append(p["total_pts"])

    if len(X) < 40:
        print(f"\n({pos}: only {len(X)} matches — too few to fit.)")
        return

    coef = _solve_ols(X, y)
    yh = [sum(c * xi for c, xi in zip(coef, row)) for row in X]
    my = sum(y) / len(y)
    ss_res = sum((a - b) ** 2 for a, b in zip(y, yh))
    ss_tot = sum((a - my) ** 2 for a in y)
    r2 = 1 - ss_res / ss_tot if ss_tot else 0

    label = {"F": "FORWARDS", "M": "MIDFIELDERS"}[pos]
    print(f"\n=== Recovered scoring — {label} (n={len(X)}, R²={r2:.3f}) ===")
    print(f"  intercept (per-season constant): {coef[0]:+.1f}")
    print(f"  {'stat':6} {'recovered':>10} {'assumed':>8} {'Δ':>7}")
    for i, f in enumerate(feats, start=1):
        rec, asm = coef[i], assumed[f]
        flag = "  <-- off" if abs(rec - asm) >= 0.75 and abs(rec) >= 0.4 else ""
        print(f"  {f:6} {rec:10.2f} {asm:8.1f} {rec - asm:+7.2f}{flag}")


def _solve_ols(X: list, y: list, ridge: float = 1.0) -> list:
    """Ordinary least squares via ridge-stabilised normal equations (numpy if
    available, else pure-Python Gaussian elimination)."""
    k = len(X[0])
    try:
        import numpy as np
        A = np.array(X, float)
        b = np.array(y, float)
        AtA = A.T @ A + ridge * np.eye(k)
        AtA[0, 0] -= ridge  # don't penalise the intercept
        return list(np.linalg.solve(AtA, A.T @ b))
    except Exception:
        pass
    AtA = [[0.0] * k for _ in range(k)]
    Atb = [0.0] * k
    for row, yi in zip(X, y):
        for a in range(k):
            Atb[a] += row[a] * yi
            for c in range(k):
                AtA[a][c] += row[a] * row[c]
    for a in range(1, k):
        AtA[a][a] += ridge
    # Gaussian elimination with partial pivoting
    M = [AtA[i] + [Atb[i]] for i in range(k)]
    for col in range(k):
        piv = max(range(col, k), key=lambda r: abs(M[r][col]))
        M[col], M[piv] = M[piv], M[col]
        if abs(M[col][col]) < 1e-9:
            continue
        for r in range(k):
            if r != col:
                f = M[r][col] / M[col][col]
                for cc in range(col, k + 1):
                    M[r][cc] -= f * M[col][cc]
    return [M[i][k] / M[i][i] if abs(M[i][i]) > 1e-9 else 0.0 for i in range(k)]


if __name__ == "__main__":
    main()
