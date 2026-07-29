"""
Fantrax EPL Draft Assistant — Streamlit UI.

Live Online Standard Snake draft helper for the Wiregrass Futbol Association
(Fantrax league wxgdnh5dmrbb90nb): 16 rounds, roster of 16 (11 active + 5
reserve + 1 IR), positions G/D/M/F.

Points, PPG and positions come straight from the bundled Fantrax export
(data/fantrax_players_2025.csv) — ground truth. Sleeper / API-Football supply
the stat-detail columns and starter rate; ADP comes from real online drafts
aggregated in data/adp.csv (see build_adp.py).

Caching:
  @st.cache_data(ttl=3600)  — player DB (bundled Fantrax pool + enrichment)
  @st.cache_resource        — DraftState (holds live/manual picks)
"""

import pandas as pd
import streamlit as st

from draft_engine import (
    DETAIL_STATS,
    DraftState,
    FantraxAPI,
    POSITION_ORDER,
    _norm_name,
    fetch_player_db,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

LEAGUE_ID = "wxgdnh5dmrbb90nb"

st.set_page_config(
    page_title="Fantrax EPL Draft Assistant",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded",
)

POS_LABELS = {"G": "GK", "D": "DEF", "M": "MID", "F": "FWD"}


# ---------------------------------------------------------------------------
# Cached loaders
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600, show_spinner="Loading player database…")
def _load_player_db() -> dict:
    """Load the Fantrax pool + enrichment (Sleeper, ADP). Cached for an hour."""
    return fetch_player_db()


@st.cache_resource(show_spinner="Preparing draft…")
def _get_draft_state(league_id: str, num_teams: int, num_rounds: int) -> DraftState:
    return DraftState(league_id, num_teams=num_teams, num_rounds=num_rounds)


def _auto_dp_key(p: dict) -> tuple:
    """Auto-rank order = the consensus draft board (ascending; unranked last)."""
    br = p.get("board_rank")
    return (br is None, br or 0.0)


# Build the DB up front so DP-text mutations happen BEFORE the text_area widget
# (Streamlit forbids writing a widget-backed session_state key after its widget
# exists).
player_db = _load_player_db()

if st.session_state.pop("_trigger_auto_dp", False):
    ranked = sorted(player_db["player_data"].values(), key=_auto_dp_key)
    st.session_state["dp_rankings_text"] = "\n".join(
        p["name"] for p in ranked[:150] if p.get("name")
    )
if st.session_state.pop("_trigger_clear_dp", False):
    st.session_state["dp_rankings_text"] = ""


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("⚽ Fantrax Draft Assistant")
    st.caption("EPL · Snake · 16 rounds · roster 16")
    st.divider()

    st.markdown("**Draft setup**")
    num_teams = st.number_input("Number of teams", min_value=2, max_value=30,
                                value=10, step=1)
    num_rounds = st.number_input("Rounds", min_value=1, max_value=30,
                                 value=16, step=1)
    my_slot = st.number_input("My draft slot", min_value=1, max_value=int(num_teams),
                              value=1, step=1)

    # Snake pick schedule for this slot: odd rounds run forward, even rounds back.
    _n, _r, _slot = int(num_teams), int(num_rounds), int(my_slot)
    my_picks_all = [
        (rnd - 1) * _n + (_slot if rnd % 2 == 1 else _n + 1 - _slot)
        for rnd in range(1, _r + 1)
    ]
    st.caption("**Your picks** (snake)")
    st.markdown(
        " ".join(f"`R{i}·{p}`" for i, p in enumerate(my_picks_all, 1)),
        help="Round·overall pick number. Even rounds reverse, so your gap alternates.",
    )

    st.divider()

    st.markdown("**DP Recommended rankings**")
    st.caption("One player per line, in your preferred draft order.")
    dp_text = st.text_area(
        "DP rankings",
        key="dp_rankings_text",
        placeholder="Haaland\nBruno Fernandes\nRice\n…",
        height=200,
        label_visibility="collapsed",
    )
    auto_col, clear_col = st.columns(2)
    with auto_col:
        if st.button("🤖 Auto-rank", width='stretch',
                     help="Fill the DP list from the consensus draft board"):
            st.session_state["_trigger_auto_dp"] = True
            st.rerun()
    with clear_col:
        if st.button("🗑 Clear", width='stretch'):
            st.session_state["_trigger_clear_dp"] = True
            st.rerun()

    st.divider()
    if st.button("🔄 Reload player DB", width='stretch'):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    status_slot = st.empty()


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------

ds = _get_draft_state(LEAGUE_ID, int(num_teams), int(num_rounds))
ds.num_teams = int(num_teams)
ds.num_rounds = int(num_rounds)
ds.my_slot = int(my_slot)
ds.inject_player_db(player_db)

# Parse DP rankings  {norm_name → rank}
dp_lookup: dict[str, int] = {}
if dp_text.strip():
    for i, line in enumerate(dp_text.strip().splitlines(), 1):
        name = line.strip()
        if name:
            dp_lookup[_norm_name(name)] = i


# ---------------------------------------------------------------------------
# Sidebar status line
# ---------------------------------------------------------------------------

with status_slot.container():
    fx_icon  = "✅" if ds.fantrax_loaded else "❌"
    slp_icon = f"✅ {ds.sleeper_matched}" if ds.sleeper_loaded else "⚠️"
    adp_icon = f"✅ {ds.adp_players} ({ds.adp_drafts} drafts)" if ds.adp_players else "—"
    con_icon = f"✅ {ds.consensus_players}" if ds.consensus_players else "—"
    dp_icon  = f"✅ {len(dp_lookup)}" if dp_lookup else "—"
    st.caption(
        f"**Blend {ds.board_players}** ranked  ·  ADP {adp_icon}  ·  "
        f"Consensus {con_icon}  ·  Mine {ds.override_players or '—'}  ·  DP {dp_icon}"
    )
    st.caption(
        f"Pool {fx_icon} {player_db.get('num_players', 0)}"
        + (f" (+{ds.off_pool_players} drafted but outside the Fantrax export)"
           if ds.off_pool_players else "")
        + f"  ·  Sleeper {slp_icon}"
    )
    if not ds.fantrax_loaded:
        st.error("Fantrax pool (data/fantrax_players_2025.csv) not found — "
                 "the app has no player data. Re-export from Fantrax into data/.")
    if not ds.sleeper_loaded:
        st.caption(
            "⚠️ Sleeper not reachable — stat-detail columns fall back to "
            "API-Football (points/PPG/positions are unaffected: they come from Fantrax)."
        )
        if ds.sleeper_error:
            with st.expander("Sleeper error"):
                st.code(ds.sleeper_error, language=None)
    if not ds.adp_players:
        st.caption("ADP: no drafts collected yet — add drafts to data/adp_drafts/ "
                   "and run build_adp.py to populate the ADP column.")


# ---------------------------------------------------------------------------
# Top status bar
# ---------------------------------------------------------------------------

available_count = len(ds.player_data) - len(ds.drafted_keys)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Current Pick", f"{ds.current_pick} / {ds.total_picks}")
c2.metric("Drafted", len(ds.picks))
c3.metric("Available", available_count)
c4.metric("My Slot", ds.my_slot or "—")
st.divider()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

DETAIL_FIELDS = [
    ("G",   "goals"),   ("A",   "assists"), ("SoT", "shots_on_target"),
    ("KP",  "key_passes"), ("Drb", "successful_dribbles"), ("AcX", "accurate_crosses"),
    ("Tkl", "tackles_won"), ("Int", "interceptions"), ("Blk", "blocked_shots"),
    ("Aer", "aerials_won"), ("Clr", "clearances"),
    ("CS",  "clean_sheets"), ("Sv", "saves"),
    ("YC",  "yellow_cards"), ("RC", "red_cards"),
]


def _rankings_df(players: list[dict], detail: bool) -> pd.DataFrame:
    rows = []
    for p in players:
        norm = _norm_name(p["name"])
        _pr, _t = p.get("pos_rank"), p.get("tier")
        row = {
            "Blend":      p.get("blend"),
            "Name":       p["name"],
            "Tier":       (f"{POS_LABELS.get(p['position'])}{_t}" if _t else None),
            "PosRk":      (f"{p['position']}{_pr}" if _pr else None),
            "⚠":          "⚠️" if p.get("split") else None,
            "Pos":        POS_LABELS.get(p["position"], p["position"]),
            "Club":       p["team"],
            "nB":         p.get("n_boards") or None,
            "ADP":        p.get("adp"),
            "nD":         p.get("adp_drafts") or None,
            "Cons":       p.get("consensus"),
            "Mine":       p.get("my_rank"),
            "25/26 Pts":  p["total_pts"] or None,
            "PPG":        p["ppg"] or None,
            "GW":         p["games"] or None,
            "DP Rec":     dp_lookup.get(norm),
        }
        if detail:
            for label, field in DETAIL_FIELDS:
                row[label] = p.get(field)
        rows.append(row)
    df = pd.DataFrame(rows)
    if not df.empty:
        df.index = range(1, len(df) + 1)
    return df


def _rankings_column_config(detail: bool) -> dict:
    cfg = {
        "Name":       st.column_config.TextColumn("Name", pinned="left", width="medium"),
        "25/26 Pts":  st.column_config.NumberColumn("25/26 Pts", format="%.1f"),
        "PPG":        st.column_config.NumberColumn("PPG", format="%.2f"),
        "Tier":       st.column_config.TextColumn("Tier", help="Positional tier — a new tier starts at a real drop-off in board rank"),
        "PosRk":      st.column_config.TextColumn("PosRk", help="Rank within position"),
        "⚠":          st.column_config.TextColumn("⚠", width="small", help="Sources disagree sharply about this player — see the Tiers & Splits tab"),
        "Blend":      st.column_config.NumberColumn("Blend", format="%.1f", help="Average across EVERY board — your real drafts plus each expert's list, one vote each"),
        "nB":         st.column_config.NumberColumn("nB", help="Total boards this player appears on (drafts + expert lists)"),
        "ADP":        st.column_config.NumberColumn("ADP", format="%.1f", help="Average draft position from the real drafts only"),
        "nD":         st.column_config.NumberColumn("nD", help="How many real drafts this player appeared in"),
        "Cons":       st.column_config.NumberColumn("Cons", format="%.1f", help="Expert panel's own aggregate rank (counts unranked as 200, so it reads harsher than Blend)"),
        "Mine":       st.column_config.NumberColumn("Mine", format="%.1f", help="Your manual override (data/my_overrides.csv)"),
        "DP Rec":     st.column_config.NumberColumn("DP Rec", help="Your recommended draft order (sidebar)"),
    }
    return cfg


# Readable labels for the detail stats (debug view).
STAT_LABELS = {
    "goals": "Goals", "assists": "Assists", "shots_on_target": "SoT",
    "key_passes": "Key passes", "successful_dribbles": "Dribbles (CoS)",
    "accurate_crosses": "Acc. crosses (ACNC)", "tackles_won": "Tackles won",
    "interceptions": "Interceptions", "blocked_shots": "Blocked shots",
    "aerials_won": "Aerials won", "clearances": "Clearances",
    "clean_sheets": "Clean sheets", "saves": "Saves",
    "yellow_card": "Yellow", "red_card": "Red",
}
# DETAIL_STATS key → record field name (a couple pluralise).
_STAT_FIELD = {"yellow_card": "yellow_cards", "red_card": "red_cards"}


def _render_data_source_debug(ds: DraftState) -> None:
    """Per-player detail-stat source, risky name matches, and top players missing
    a Sleeper join. Points/PPG/positions always come from Fantrax; this only
    audits the enrichment join used for the stat-detail columns."""
    with st.expander("🔎 Data sources & match quality (debug)", expanded=False):
        players = list(ds.player_data.values())
        if not players:
            st.info("No player data loaded.")
            return

        full  = sum(1 for p in players if p.get("match_type") == "full")
        lastn = sum(1 for p in players if p.get("match_type") == "lastname")
        amb   = sum(1 for p in players if p.get("ambiguous_last"))
        unm   = [p for p in players if not p.get("has_sleeper")]
        st.caption(
            f"Points/PPG/position come from the **Fantrax export** for all "
            f"{len(players)} players. Detail-stat join → Sleeper "
            f"**{len(players) - len(unm)} / {len(players)}** "
            f"(full-name {full} · last-name {lastn}, {amb} shared-surname)  ·  "
            f"API-Football matched {ds.apif_matched}."
        )
        if not ds.sleeper_loaded:
            st.warning(
                "Sleeper wasn't loaded this session — detail columns fall back to "
                "API-Football. Points, PPG and positions are unaffected (Fantrax)."
            )

        # Scoring-feed validation: bottom-up (Opta stats × Fantrax scoring) vs Fantrax FPts
        val = ds.validation
        st.markdown("**Scoring-feed validation** — does raw Opta × Fantrax scoring reproduce Fantrax's FPts?")
        if not val or not val.get("n"):
            st.caption("Needs a live Sleeper join (unavailable this session). "
                       "Run `python validate.py` where Sleeper is reachable.")
        else:
            vc1, vc2, vc3 = st.columns(3)
            vc1.metric("Correlation", val["correlation"])
            vc2.metric("Mean abs error", f"{val['mae']} pts")
            vc3.metric("Mean bias", f"{val['bias']:+} pts")
            st.caption(f"n={val['n']} matched · per-position bias {val['pos_bias']} "
                       "· high correlation + low bias ⇒ the feed reproduces Fantrax, "
                       "so a per-stat projection built on it is trustworthy.")
            with st.expander("Biggest bottom-up vs Fantrax gaps"):
                st.dataframe(pd.DataFrame(val["worst"]), hide_index=True, width="stretch")

        # 1) Per-player detail-stat source
        pick = st.selectbox("Inspect a player's stat sources",
                            sorted(p["name"] for p in players), key="_dbg_player")
        p = next((x for x in players if x["name"] == pick), None)
        if p:
            badge = {"full": "✅ full-name", "first+last": "✅ first+last",
                     "lastname": "⚠️ surname only", "none": "❌ no Sleeper match"
                     }.get(p.get("match_type"), "—")
            if p.get("ambiguous_last"):
                badge += "  ·  ⚠️ shared surname"

            st.markdown(
                f"**{p['name']}** — {POS_LABELS.get(p['position'])} · {p['team']} · "
                f"{p['total_pts']} pts (Fantrax)  |  Sleeper: {badge}  ·  "
                f"API-Football: {'✅' if p.get('has_apif') else '—'}  ·  "
                f"ADP: {p['adp'] if p.get('adp') is not None else '—'}  ·  "
                f"Consensus: {p['consensus'] if p.get('consensus') is not None else '—'}"
                + (f" (n={p['n_experts']}, best {p['expert_best']} / worst {p['expert_worst']})"
                   if p.get('n_experts') else "")
            )
            src_bits = []
            if p.get("my_rank") is not None:
                src_bits.append(f"your override **{p['my_rank']}**")
            if p.get("adp") is not None:
                src_bits.append(f"ADP {p['adp']} over {p.get('adp_drafts')} draft(s)")
            if p.get("n_experts"):
                src_bits.append(f"{p['n_experts']} expert list(s)"
                                + (f" (+{p['n_unranked']} left him unranked)"
                                   if p.get("n_unranked") else ""))
            st.caption(
                f"Board rank **{p.get('board_rank') if p.get('board_rank') is not None else '—'}**"
                + (f" (blend {p['blend']} over {p.get('n_boards')} boards)"
                   if p.get("blend") is not None else "")
                + (" — from " + ", ".join(src_bits) if src_bits else " — no draft data yet")
                + (f"  ·  _{p['my_note']}_" if p.get("my_note") else "")
                + ("" if p.get("in_pool", True) else "  ·  ⚠️ not in the Fantrax export snapshot")
            )
            src = p.get("_detail_source", {})
            rows = [{"Stat": STAT_LABELS.get(s, s),
                     "Value": p.get(_STAT_FIELD.get(s, s)),
                     "Source": src.get(s, "—")} for s in DETAIL_STATS]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                         height=min(36 * len(rows) + 40, 620))

        # 2) Last-name-only matches (highest risk first: shared surname, then games)
        risky = sorted((p for p in players if p.get("match_type") == "lastname"),
                       key=lambda x: (not x.get("ambiguous_last"), -x.get("games", 0)))
        if risky:
            st.markdown("**Last-name-only Sleeper matches** — verify these (shared-surname first)")
            st.dataframe(pd.DataFrame([
                {"Name": p["name"], "Pos": POS_LABELS.get(p["position"]),
                 "Club": p["team"], "GW": p["games"],
                 "Shared surname": "⚠️" if p.get("ambiguous_last") else ""}
                for p in risky[:25]
            ]), hide_index=True, width="stretch")

        # 3) Top players missing a Sleeper join, by games — biggest missing detail
        st.markdown("**Top players with no Sleeper detail-stats, by GW played**")
        top_unm = sorted(unm, key=lambda x: x.get("games", 0), reverse=True)[:10]
        if top_unm:
            st.dataframe(pd.DataFrame([
                {"Name": p["name"], "Pos": POS_LABELS.get(p["position"]),
                 "Club": p["team"], "GW": p["games"],
                 "API-Football": "✅" if p.get("has_apif") else "—"}
                for p in top_unm
            ]), hide_index=True, width="stretch")
        else:
            st.caption("Every player joined to Sleeper.")


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab_ranks, tab_draft, tab_mine, tab_adp, tab_tiers = st.tabs(
    ["📊 Rankings", "🐍 Live Draft", "👤 My Team", "📈 ADP / Value", "🪜 Tiers & Splits"]
)


# ── Rankings ────────────────────────────────────────────────────────────────
with tab_ranks:
    st.subheader("Player Rankings")

    rc1, rc2, rc3, rc4 = st.columns([3, 2, 1, 1])
    with rc1:
        pos_filter = st.radio("Position", ["All"] + [POS_LABELS[p] for p in POSITION_ORDER],
                              horizontal=True, key="ranks_pos")
    with rc2:
        sort_mode = st.radio("Sort by", ["Blend", "25/26 Total", "PPG"],
                             horizontal=True, key="ranks_sort")
    with rc3:
        top_n = st.selectbox("Show", [25, 50, 100, 200], index=1, key="ranks_n")
    with rc4:
        show_detail = st.toggle("Detail cols", value=False, key="ranks_detail")

    sort_field = {"Blend": "board_rank", "25/26 Total": "total_pts",
                  "PPG": "ppg"}[sort_mode]
    inv_pos = {v: k for k, v in POS_LABELS.items()}
    pos_arg = None if pos_filter == "All" else inv_pos[pos_filter]

    available = ds.get_available(pos_arg, sort_by=sort_field)

    # DP-ranked players float to the top when a DP list is present.
    if dp_lookup:
        available.sort(key=lambda p: (dp_lookup.get(_norm_name(p["name"]), 10**9),))
        ranked_part = [p for p in available if _norm_name(p["name"]) in dp_lookup]
        if sort_field == "board_rank":
            rest = sorted([p for p in available if _norm_name(p["name"]) not in dp_lookup],
                          key=lambda p: (p.get("board_rank") is None, p.get("board_rank") or 0))
        else:
            rest = sorted([p for p in available if _norm_name(p["name"]) not in dp_lookup],
                          key=lambda p: p.get(sort_field) or 0, reverse=True)
        available = ranked_part + rest

    available = available[:top_n]

    if not available:
        st.info("No players available for this filter.")
    else:
        df = _rankings_df(available, show_detail)
        cfg = _rankings_column_config(show_detail)
        st.dataframe(df, width='stretch', column_config=cfg,
                     height=min(36 * len(df) + 40, 720))

    st.caption(
        "**Blend** is the draft order: every board pooled — each real draft and each "
        "expert's list counts as one vote (**nB** = how many). **ADP** is the real "
        "drafts alone (**nD** = how many), **Cons** is the panel's own aggregate. An "
        "expert who left a player outside their top 150 still counts, at ~175, so one "
        "bullish ranking can't leapfrog the field. **Mine** shows your overrides for "
        "reference — they no longer reorder the list. 25/26 Pts / PPG are last "
        "season's actual Fantrax numbers, context only."
    )

    with st.expander("❓ What the columns mean", expanded=False):
        st.markdown("""
| Column | Meaning |
| --- | --- |
| **Blend** | **What you draft by.** Every board pooled — each mock draft and each expert's list counts once. Lower = take earlier. |
| **Tier** | Positional tier. A new tier starts at a real drop-off, so `DEF1` are interchangeable and `DEF2` is the group after the gap. |
| **PosRk** | Rank within position — `D7` is the 7th-best defender on the board. |
| **⚠️** | **Sources disagree sharply about this player.** Either the expert panel and the draft room are 30+ picks apart (with 3+ drafts, so it isn't one stray pick), or his actual picks ranged 45+ places across boards. Not good or bad — just *uncertain*. Check the Tiers & Splits tab for the reason. |
| **nB** | How many boards he appears on (5 drafts + 9 experts = 14 max). Low nB = thin evidence. |
| **ADP** | Average pick in the **real mock drafts only** — the best guide to *when he'll actually be gone*. |
| **nD** | How many real drafts he appeared in. |
| **Cons** | The expert panel's own published aggregate. Reads harsher than Blend because it counts "unranked" as 200. |
| **Mine** | Your manual override from `data/my_overrides.csv` — shown for reference; it does not reorder the list. |
| **25/26 Pts / PPG / GW** | Last season's **actual** Fantrax output — context only, does not affect the order. |
| **DP Rec** | Your hand-written draft order from the sidebar. |
""")

    st.divider()
    st.subheader("Your pick plan")
    st.caption(
        f"Slot **{int(my_slot)}** of **{int(num_teams)}** — who the board says should "
        "still be there at each of your picks. A player is listed under a pick if his "
        "Board rank lands in that window; **bold** = his board rank is at or after "
        "your pick, so he should survive to you."
    )
    _ranked_pp = sorted(
        (p for p in ds.get_available(sort_by="board_rank")
         if p.get("board_rank") is not None),
        key=lambda x: x["board_rank"],
    )
    if not _ranked_pp:
        st.info("No board ranks yet.")
    else:
        _picks = my_picks_all[:8]
        _cols = st.columns(min(4, len(_picks)))
        for i, pk in enumerate(_picks):
            nxt = _picks[i + 1] if i + 1 < len(_picks) else pk + int(num_teams) * 2
            col = _cols[i % len(_cols)]
            # candidates whose board rank sits in this pick's neighbourhood
            cands = [p for p in _ranked_pp if pk - 4 <= p["board_rank"] < nxt][:7]
            col.markdown(f"**R{i+1} · pick {pk}**")
            if not cands:
                col.caption("—")
            for p in cands:
                safe = p["board_rank"] >= pk
                nm = f"**{p['web_name']}**" if safe else p["web_name"]
                col.markdown(
                    f"<span style='font-size:0.85em'>{nm} "
                    f"<code>{p['board_rank']:.0f}</code> "
                    f"{POS_LABELS.get(p['position'])}"
                    f"{' ⚠️' if p.get('split') else ''}</span>",
                    unsafe_allow_html=True,
                )

    st.divider()
    _render_data_source_debug(ds)


# ── Live Draft ──────────────────────────────────────────────────────────────
with tab_draft:
    st.subheader("Live Snake Draft")

    try:
        fantrax_cookie = st.secrets.get("fantrax_cookie")
    except Exception:
        fantrax_cookie = None  # no secrets.toml configured — manual entry only
    with st.expander("Fantrax connection", expanded=False):
        if fantrax_cookie:
            if st.button("Sync picks from Fantrax"):
                api = FantraxAPI(LEAGUE_ID, cookie=fantrax_cookie)
                picks = api.get_draft_picks()
                st.info(f"Fantrax returned {len(picks)} pick rows. "
                        "Automatic pick mapping is best-effort; use manual entry "
                        "below if names do not resolve.")
        else:
            st.caption(
                "No `fantrax_cookie` in secrets. Fantrax's live draft API needs a "
                "logged-in session cookie. Add one to `.streamlit/secrets.toml` to "
                "enable auto-sync, or mark picks manually below — the snake board, "
                "on-the-clock tracking and your pick schedule all work either way."
            )

    # Your next picks banner
    my_next = ds.my_next_picks()
    if my_next:
        gap = my_next[0] - ds.current_pick
        timing = "**now — you're on the clock**" if gap == 0 else f"in **{gap}** pick{'s' if gap != 1 else ''}"
        nxt = ", ".join(str(n) for n in my_next[:8])
        st.info(f"Your upcoming picks: **{nxt}**{'…' if len(my_next) > 8 else ''} — next is #{my_next[0]} ({timing}).")

    # Manual pick entry
    mc1, mc2, mc3 = st.columns([3, 1, 1])
    with mc1:
        avail_for_pick = ds.get_available(sort_by="board_rank")
        pick_options = {f"{p['name']} ({POS_LABELS.get(p['position'])}, {p['team']})": p["_key"]
                        for p in avail_for_pick[:400]}
        slot_now = ds.slot_on_the_clock(ds.current_pick)
        picked_label = st.selectbox(
            f"Record pick #{ds.current_pick} — slot {slot_now}"
            + (" (you)" if slot_now == ds.my_slot else ""),
            options=["—"] + list(pick_options.keys()),
        )
    with mc2:
        st.write("")
        st.write("")
        if st.button("✅ Draft", width='stretch', disabled=(picked_label == "—")):
            if picked_label in pick_options:
                ds.record_pick(pick_options[picked_label])
                st.rerun()
    with mc3:
        st.write("")
        st.write("")
        if st.button("↩ Undo", width='stretch', disabled=not ds.picks):
            ds.undo_last_pick()
            st.rerun()

    col_board, col_avail = st.columns([3, 2])

    with col_board:
        st.markdown("**Snake board**")
        grid = ds.get_pick_grid()
        board_rows = []
        for r_idx, round_row in enumerate(grid, 1):
            row = {"Rd": r_idx}
            for s_idx, cell in enumerate(round_row, 1):
                overall = (r_idx - 1) * ds.num_teams + (s_idx if r_idx % 2 == 1 else (ds.num_teams + 1 - s_idx))
                col_name = f"S{s_idx}" + ("★" if s_idx == ds.my_slot else "")
                if cell:
                    row[col_name] = f"{cell['web_name']} ({POS_LABELS.get(cell['position'])})"
                elif overall == ds.current_pick:
                    row[col_name] = "⏳ OTC"
                else:
                    row[col_name] = "—"
            board_rows.append(row)
        df_board = pd.DataFrame(board_rows)
        st.dataframe(df_board, width='stretch', hide_index=True,
                     height=min(36 * ds.num_rounds + 40, 620),
                     column_config={"Rd": st.column_config.NumberColumn("Rd", pinned="left", width="small")})
        st.caption("★ = your slot · ⏳ OTC = on the clock · snake order, even rounds reverse.")

    with col_avail:
        st.markdown("**Best available**")
        pos_f = st.radio("Pos", ["All"] + [POS_LABELS[p] for p in POSITION_ORDER],
                         horizontal=True, key="draft_pos_filter")
        pos_a = None if pos_f == "All" else inv_pos[pos_f]
        avail = ds.get_available(pos_a, sort_by="board_rank")[:40]
        rows_a = [{
            "Blend":  p.get("board_rank"),
            "Player": p["web_name"],
            "ADP":    p.get("adp"),
            "Pos":    POS_LABELS.get(p["position"]),
            "Club":   p["team"],
            "DP":     dp_lookup.get(_norm_name(p["name"])),
        } for p in avail]
        df_a = pd.DataFrame(rows_a)
        if not df_a.empty:
            df_a.index = range(1, len(df_a) + 1)
        st.dataframe(df_a, width='stretch',
                     column_config={
                         "Player": st.column_config.TextColumn("Player", pinned="left"),
                         "Blend": st.column_config.NumberColumn("Blend", format="%.1f"),
                         "ADP": st.column_config.NumberColumn("ADP", format="%.1f"),
                     },
                     height=min(36 * ds.num_rounds + 40, 620))


# ── My Team ─────────────────────────────────────────────────────────────────
with tab_mine:
    st.subheader("My Drafted Squad")

    # Positional caps for this league (max per position).
    POS_CAPS = {"G": 3, "D": 8, "M": 8, "F": 6}
    counts = ds.get_positional_counts()
    cols = st.columns(len(POSITION_ORDER))
    for col, pos in zip(cols, POSITION_ORDER):
        col.metric(POS_LABELS[pos], f"{counts.get(pos, 0)} / {POS_CAPS[pos]}")

    st.divider()

    my_picks = ds.get_my_picks()
    if not my_picks:
        st.info("No picks recorded for your slot yet. Mark picks in the Live Draft tab.")
    else:
        rows_m = [{
            "Pick":       p["_overall"],
            "Name":       p["name"],
            "Pos":        POS_LABELS.get(p["position"]),
            "Club":       p["team"],
            "25/26 Pts":  p["total_pts"],
            "PPG":        p["ppg"],
            "Blend":      p.get("board_rank"),
        } for p in my_picks]
        df_m = pd.DataFrame(rows_m).sort_values(["Pos", "Blend"], ascending=[True, True])
        df_m.index = range(1, len(df_m) + 1)
        st.dataframe(df_m, width='stretch', column_config={
            "Name": st.column_config.TextColumn("Name", pinned="left"),
            "25/26 Pts": st.column_config.NumberColumn("25/26 Pts", format="%.1f"),
            "PPG": st.column_config.NumberColumn("PPG", format="%.2f"),
            "Blend": st.column_config.NumberColumn("Blend", format="%.1f"),
        })
        _bd = [p["board_rank"] for p in my_picks if p.get("board_rank") is not None]
        if _bd:
            st.caption(f"Average blend rank of your picks: **{sum(_bd)/len(_bd):.1f}**")

    # Positional needs → best available per position
    remaining = ds.num_rounds - len(my_picks)
    if remaining > 0:
        st.divider()
        st.subheader(f"Best available per position  ({remaining} picks left)")
        exp_cols = st.columns(len(POSITION_ORDER))
        for col, pos in zip(exp_cols, POSITION_ORDER):
            room = POS_CAPS[pos] - counts.get(pos, 0)
            col.markdown(f"**{POS_LABELS[pos]}**  ·  {room} slot{'s' if room != 1 else ''} left")
            for p in ds.get_available(pos, sort_by="board_rank")[:5]:
                norm = _norm_name(p["name"])
                dp_tag = f" · DP#{dp_lookup[norm]}" if norm in dp_lookup else ""
                _br = p.get("board_rank")
                col.markdown(f"- {p['web_name']} *(blend {_br:.0f}{dp_tag})*"
                             if _br is not None else f"- {p['web_name']} *(unranked{dp_tag})*")


# ── ADP / Value ─────────────────────────────────────────────────────────────
with tab_adp:
    st.subheader("ADP / Value")
    st.caption(
        "Where the **expert panel** and the **actual drafts** disagree. "
        "**Cons − ADP** > 0 = the panel ranks them worse than drafters actually "
        "take them (the room reaches); < 0 = the panel likes them more than the "
        "room does (they fall). Add drafts to data/adp_drafts/ and run "
        "build_adp.py to refresh."
    )
    st.divider()

    if not ds.adp_players:
        st.info("No ADP yet — share online drafts and I'll aggregate them into "
                "data/adp.csv. Once there's data, this tab ranks value vs reach.")
    else:
        avail = ds.get_available(sort_by="board_rank")
        rows_v = []
        for p in avail:
            if p.get("adp") is None and p.get("consensus") is None:
                continue
            norm = _norm_name(p["name"])
            adp, cons = p.get("adp"), p.get("consensus")
            gap = round(cons - adp, 1) if (adp is not None and cons is not None) else None
            rows_v.append({
                "Blend":      p.get("board_rank"),
                "Name":       p["name"],
                "Pos":        POS_LABELS.get(p["position"]),
                "Club":       p["team"],
                "ADP":        adp,
                "Cons":       cons,
                "Δ (Cons−ADP)": gap,
                "Range":      (f"{p.get('adp_min')}–{p.get('adp_max')}"
                               if p.get("adp_min") else None),
                "n":          p.get("adp_drafts") or None,
                "Mine":       p.get("my_rank"),
                "DP Rec":     dp_lookup.get(norm),
            })
        df_v = pd.DataFrame(rows_v).sort_values("Blend")
        if not df_v.empty:
            df_v.index = range(1, len(df_v) + 1)
        st.dataframe(df_v, width='stretch', height=680, column_config={
            "Name": st.column_config.TextColumn("Name", pinned="left"),
            "Blend": st.column_config.NumberColumn("Blend", format="%.1f"),
            "ADP": st.column_config.NumberColumn("ADP", format="%.1f"),
            "Cons": st.column_config.NumberColumn("Cons", format="%.1f", help="Expert consensus rank"),
            "Mine": st.column_config.NumberColumn("Mine", format="%.1f"),
            "Δ (Cons−ADP)": st.column_config.NumberColumn(
                "Δ (Cons−ADP)", help="Positive = room drafts earlier than the panel rates them; negative = they fall past the panel's rank"),
        })


# ── Tiers & Splits ──────────────────────────────────────────────────────────
with tab_tiers:
    st.subheader("Positional tiers")
    st.caption(
        "Players at each position grouped by board rank, split wherever there's a "
        "real drop-off. **Next gap** is how many board picks separate a player from "
        "the next one at his position — a big gap means waiting costs you, a small "
        "one means you can safely take another position first."
    )

    avail_t = ds.get_available(sort_by="board_rank")
    ranked_t = [p for p in avail_t if p.get("board_rank") is not None]

    if not ranked_t:
        st.info("No board ranks yet — add drafts to data/adp_drafts/ and run build_adp.py.")
    else:
        show_taken = st.toggle("Only show players still available", value=True,
                               key="_tiers_avail_only")
        pool_t = ranked_t if show_taken else [
            p for p in ({**d, "_key": k} for k, d in ds.player_data.items())
            if p.get("board_rank") is not None
        ]

        cols_t = st.columns(len(POSITION_ORDER))
        for col, pos in zip(cols_t, POSITION_ORDER):
            grp = sorted((p for p in pool_t if p["position"] == pos),
                         key=lambda x: x["board_rank"])
            col.markdown(f"### {POS_LABELS[pos]}")
            if not grp:
                col.caption("none left")
                continue
            cur_tier = None
            for p in grp[:22]:
                if p.get("tier") != cur_tier:
                    cur_tier = p.get("tier")
                    col.markdown(f"**Tier {cur_tier}**")
                gap = p.get("next_gap")
                cliff = " ⛰️" if (gap is not None and gap >= 10) else ""
                flag = " ⚠️" if p.get("split") else ""
                mine = " 📌" if p.get("my_rank") is not None else ""
                col.markdown(
                    f"<span style='font-size:0.86em'>"
                    f"<code>{p['board_rank']:>5.1f}</code> {p['web_name']}"
                    f"{mine}{flag}{cliff}</span>",
                    unsafe_allow_html=True,
                )
        st.caption("⛰️ = 10+ pick gap to the next player at that position (a cliff)  ·  "
                   "⚠️ = sources disagree  ·  📌 = your override")

    st.divider()
    st.subheader("Where the sources disagree")
    st.caption(
        "Players the expert panel and the draft room see very differently, or whose "
        "actual picks ranged widely. **Gap** = consensus − ADP: positive means the "
        "room takes him earlier than the panel rates him (the room is high on him); "
        "negative means he falls past where the panel would have him (possible value)."
    )
    splits = [p for p in ranked_t if p.get("split")]
    if not splits:
        st.info("No sharp disagreements flagged yet.")
    else:
        splits.sort(key=lambda p: -(abs(p.get("panel_gap") or 0)))
        st.dataframe(pd.DataFrame([{
            "Blend":  p.get("board_rank"),
            "Name":   p["name"],
            "Pos":    POS_LABELS.get(p["position"]),
            "Club":   p["team"],
            "ADP":    p.get("adp"),
            "Cons":   p.get("consensus"),
            "Exp":    (f"{p.get('n_experts')}/{(p.get('n_experts') or 0)+(p.get('n_unranked') or 0)}"
                       if p.get("n_experts") is not None else None),
            "Gap":    p.get("panel_gap"),
            "Picks":  (f"{p.get('adp_min')}–{p.get('adp_max')}" if p.get("adp_min") else None),
            "Spread": p.get("adp_spread"),
            "Why":    p.get("split_why"),
        } for p in splits]), hide_index=True, width="stretch", height=520,
            column_config={
                "Name":  st.column_config.TextColumn("Name", pinned="left"),
                "Blend": st.column_config.NumberColumn("Blend", format="%.1f"),
                "ADP":   st.column_config.NumberColumn("ADP", format="%.1f"),
                "Cons":  st.column_config.NumberColumn("Cons", format="%.1f"),
                "Exp":   st.column_config.TextColumn("Exp", help="Experts who ranked him / total experts"),
                "Gap":   st.column_config.NumberColumn("Gap", format="%+.1f"),
                "Why":   st.column_config.TextColumn("Why", width="large"),
            })
