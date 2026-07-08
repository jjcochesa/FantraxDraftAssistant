"""
Fantrax EPL Draft Assistant — Streamlit UI.

Live Online Standard Snake draft helper for the Wiregrass Futbol Association
(Fantrax league wxgdnh5dmrbb90nb): 16 rounds, roster of 16 (11 active + 5
reserve + 1 IR), positions G/D/M/F.

Points use Fantrax scoring only (see draft_engine.FANTRAX_SCORING). Season stats
come from the bundled API-Football 2025/26 file; FPL supplies cost / ownership
(an ADP proxy until Fantrax community drafts start) and clean-sheet gap-fill.

Caching:
  @st.cache_data(ttl=3600)  — heavy player DB (bundled stats + FPL)
  @st.cache_resource        — DraftState (holds live/manual picks)
"""

import pandas as pd
import streamlit as st

from draft_engine import (
    DraftState,
    FANTRAX_SCORING,
    FantraxAPI,
    POSITION_ORDER,
    _norm_name,
    build_from_sources,
    fetch_sources,
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
def _load_sources() -> dict:
    """Slow network fetch (bundled stats + FPL) — cached for an hour."""
    return fetch_sources()


@st.cache_data(ttl=3600, show_spinner=False)
def _build_db(tackle_win_rate: float) -> dict:
    """Cheap re-score keyed on the tackle-win rate (sources are cached)."""
    return build_from_sources(_load_sources(), tackle_win_rate)


@st.cache_resource(show_spinner="Preparing draft…")
def _get_draft_state(league_id: str, num_teams: int, num_rounds: int) -> DraftState:
    return DraftState(league_id, num_teams=num_teams, num_rounds=num_rounds)


def _auto_dp_score(p: dict) -> float:
    """Primary signal is projected_pts (already full Fantrax scoring). Players
    with no projection fall to the bottom, ordered by FPL ownership proxy."""
    proj = p.get("projected_pts") or 0.0
    if proj > 0:
        return proj
    return (p.get("ownership_pct") or 0.0) - 1000.0


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

    tackle_win_rate = st.slider(
        "Tackle-won rate (fallback)", min_value=0.50, max_value=1.00, value=0.65,
        step=0.05,
        help="Players matched in Sleeper use real tackles-WON directly. This only "
             "affects players NOT found in Sleeper, discounting API-Football's "
             "total tackles (≈0.65 league average). Set to 1.00 to use totals.",
    )

    # Build the DB now (cheap; sources are cached) so DP-text mutations can run
    # BEFORE the text_area widget — Streamlit forbids writing a widget-backed
    # session_state key after its widget exists.
    player_db = _build_db(tackle_win_rate)

    if st.session_state.pop("_trigger_auto_dp", False):
        ranked = sorted(player_db["player_data"].values(), key=_auto_dp_score, reverse=True)
        st.session_state["dp_rankings_text"] = "\n".join(
            p["name"] for p in ranked[:150] if p.get("name")
        )
    if st.session_state.pop("_trigger_clear_dp", False):
        st.session_state["dp_rankings_text"] = ""

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
                     help="Generate DP rankings from the projection model"):
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
    stats_icon = "✅" if ds.stats_loaded else "⚠️"
    fpl_icon = "✅" if ds.fpl_loaded else "⚠️"
    slp_icon = f"✅ {ds.sleeper_matched}" if ds.sleeper_loaded else "⚠️"
    dp_icon = f"✅ {len(dp_lookup)}" if dp_lookup else "—"
    st.caption(
        f"Stats {stats_icon} {player_db.get('num_players', 0)}  ·  "
        f"Sleeper {slp_icon}  ·  FPL {fpl_icon}  ·  DP {dp_icon}"
    )
    if not ds.sleeper_loaded:
        st.caption(
            "⚠️ Sleeper not reachable — falling back to the API-Football tackle "
            "proxy (tune with the tackle-won rate) and FPL clean-sheet gap-fill."
        )
        if ds.sleeper_error:
            with st.expander("Sleeper error"):
                st.code(ds.sleeper_error, language=None)
    if not ds.fpl_loaded:
        st.caption(
            "⚠️ FPL not reachable — cost and ownership (ADP proxy) are unavailable."
        )
        if ds.fpl_error:
            with st.expander("FPL error"):
                st.code(ds.fpl_error, language=None)


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
    ("KP",  "key_passes"), ("Drb", "successful_dribbles"),
    ("Tkl", "tackles_won"), ("Int", "interceptions"), ("Blk", "blocked_shots"),
    ("CS",  "clean_sheets"), ("Sv", "saves"),
    ("YC",  "yellow_cards"), ("RC", "red_cards"),
]


def _rankings_df(players: list[dict], detail: bool) -> pd.DataFrame:
    rows = []
    for p in players:
        norm = _norm_name(p["name"])
        row = {
            "Name":       p["name"],
            "Pos":        POS_LABELS.get(p["position"], p["position"]),
            "Club":       p["team"],
            "25/26 Pts":  p["total_pts"],
            "PPG":        p["ppg"],
            "GW":         p["games"],
            "26/27 Proj": p["projected_pts"],
            "ADP":        p.get("adp_rank"),
            "DP Rec":     dp_lookup.get(norm),
            "Own%":       p.get("ownership_pct"),
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
        "26/27 Proj": st.column_config.NumberColumn("26/27 Proj", format="%.1f"),
        "Own%":       st.column_config.NumberColumn("Own%", format="%.1f"),
        "ADP":        st.column_config.NumberColumn("ADP", help="FPL ownership rank (proxy until Fantrax drafts start)"),
        "DP Rec":     st.column_config.NumberColumn("DP Rec", help="Your recommended draft order (sidebar)"),
    }
    return cfg


# Readable labels for the raw Fantrax scoring stats (debug view).
STAT_LABELS = {
    "goals": "Goals", "assists": "Assists", "shots_on_target": "SoT",
    "key_passes": "Key passes", "successful_dribbles": "Dribbles (CoS)",
    "accurate_crosses": "Acc. crosses (ACNC)", "penalty_drawn": "Pens drawn",
    "clean_sheets": "Clean sheets", "tackles_won": "Tackles won",
    "interceptions": "Interceptions", "blocked_shots": "Blocked shots",
    "aerials_won": "Aerials won", "clearances": "Clearances", "saves": "Saves",
    "penalties_saved": "Pens saved", "high_claims": "High claims",
    "smothers": "Smothers", "goals_against": "Goals against",
    "yellow_card": "Yellow", "red_card": "Red", "own_goals": "Own goals",
    "penalties_missed": "Pens missed", "dispossessed": "Dispossessed",
}


def _render_data_source_debug(ds: DraftState) -> None:
    """Per-player stat provenance, risky name matches, and top unmatched starters."""
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
            f"Sleeper matches **{len(players) - len(unm)} / {len(players)}**  ·  "
            f"full-name {full}  ·  last-name {lastn} ({amb} on a shared surname)  ·  "
            f"unmatched {len(unm)}"
        )
        if not ds.sleeper_loaded:
            st.warning(
                "Sleeper wasn't loaded this session — provenance below reflects the "
                "API-Football / FPL fallback, not a live Sleeper join."
            )

        # 1) Per-player stat provenance
        pick = st.selectbox("Inspect a player's stat sources",
                            sorted(p["name"] for p in players), key="_dbg_player")
        p = next((x for x in players if x["name"] == pick), None)
        if p:
            badge = {"full": "✅ full-name", "lastname": "⚠️ last-name only",
                     "none": "❌ no Sleeper match"}.get(p.get("match_type"), "—")
            if p.get("ambiguous_last"):
                badge += "  ·  ⚠️ shared surname"
            st.markdown(
                f"**{p['name']}** — {POS_LABELS.get(p['position'])} · {p['team']}  |  "
                f"Sleeper: {badge}  ·  FPL: {'✅' if p.get('has_fpl') else '—'}"
            )
            prov, vals = p.get("_provenance", {}), p.get("_stats", {})
            rows = [{"Stat": STAT_LABELS.get(s, s), "Value": vals.get(s, 0),
                     "Source": prov.get(s, "—")} for s in FANTRAX_SCORING]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                         height=min(36 * len(rows) + 40, 620))

        # 2) Last-name-only matches (highest risk first: shared surname, then minutes)
        risky = sorted((p for p in players if p.get("match_type") == "lastname"),
                       key=lambda x: (not x.get("ambiguous_last"), -x.get("minutes", 0)))
        if risky:
            st.markdown("**Last-name-only matches** — verify these (shared-surname first)")
            st.dataframe(pd.DataFrame([
                {"Name": p["name"], "Pos": POS_LABELS.get(p["position"]),
                 "Club": p["team"], "Min": p["minutes"],
                 "Shared surname": "⚠️" if p.get("ambiguous_last") else ""}
                for p in risky[:25]
            ]), hide_index=True, width="stretch")

        # 3) Top unmatched by minutes — a high-minutes miss is a join bug to chase
        st.markdown("**Top unmatched starters by minutes** (a high-minutes miss = a join bug)")
        top_unm = sorted(unm, key=lambda x: x.get("minutes", 0), reverse=True)[:10]
        if top_unm:
            st.dataframe(pd.DataFrame([
                {"Name": p["name"], "Pos": POS_LABELS.get(p["position"]),
                 "Club": p["team"], "Min": p["minutes"]}
                for p in top_unm
            ]), hide_index=True, width="stretch")
        else:
            st.caption("No unmatched players — every player joined to Sleeper.")


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab_ranks, tab_draft, tab_mine, tab_adp = st.tabs(
    ["📊 Rankings", "🐍 Live Draft", "👤 My Team", "📈 ADP / Value"]
)


# ── Rankings ────────────────────────────────────────────────────────────────
with tab_ranks:
    st.subheader("Player Rankings")

    rc1, rc2, rc3, rc4 = st.columns([3, 2, 1, 1])
    with rc1:
        pos_filter = st.radio("Position", ["All"] + [POS_LABELS[p] for p in POSITION_ORDER],
                              horizontal=True, key="ranks_pos")
    with rc2:
        sort_mode = st.radio("Sort by", ["26/27 Projected", "25/26 Total", "PPG"],
                             horizontal=True, key="ranks_sort")
    with rc3:
        top_n = st.selectbox("Show", [25, 50, 100, 200], index=1, key="ranks_n")
    with rc4:
        show_detail = st.toggle("Detail cols", value=False, key="ranks_detail")

    sort_field = {"26/27 Projected": "projected_pts", "25/26 Total": "total_pts",
                  "PPG": "ppg"}[sort_mode]
    inv_pos = {v: k for k, v in POS_LABELS.items()}
    pos_arg = None if pos_filter == "All" else inv_pos[pos_filter]

    available = ds.get_available(pos_arg, sort_by=sort_field)

    # DP-ranked players float to the top when a DP list is present.
    if dp_lookup:
        available.sort(key=lambda p: (dp_lookup.get(_norm_name(p["name"]), 10**9),))
        ranked_part = [p for p in available if _norm_name(p["name"]) in dp_lookup]
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
        f"**26/27 Proj** = Bayesian-blended PPG (player + position prior) × 34 GWs "
        f"× participation rate · min 15 GWs required. Tackles won, interceptions, "
        f"blocks, crosses, clean sheets, aerials & dispossessed come from Sleeper "
        f"(same Opta feed as Fantrax); unmatched players fall back to the "
        f"API-Football tackle proxy at ~{tackle_win_rate:.0%}. **ADP / Own%** = "
        f"FPL 25/26 ownership proxy (community consensus) until Fantrax community "
        f"drafts open in August."
    )

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
        avail_for_pick = ds.get_available(sort_by="projected_pts")
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
        avail = ds.get_available(pos_a, sort_by="projected_pts")[:40]
        rows_a = [{
            "Player": p["web_name"],
            "Pos":    POS_LABELS.get(p["position"]),
            "Club":   p["team"],
            "Proj":   p["projected_pts"],
            "DP":     dp_lookup.get(_norm_name(p["name"])),
        } for p in avail]
        df_a = pd.DataFrame(rows_a)
        if not df_a.empty:
            df_a.index = range(1, len(df_a) + 1)
        st.dataframe(df_a, width='stretch',
                     column_config={
                         "Player": st.column_config.TextColumn("Player", pinned="left"),
                         "Proj": st.column_config.NumberColumn("Proj", format="%.1f"),
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
            "26/27 Proj": p["projected_pts"],
        } for p in my_picks]
        df_m = pd.DataFrame(rows_m).sort_values(["Pos", "26/27 Proj"], ascending=[True, False])
        df_m.index = range(1, len(df_m) + 1)
        st.dataframe(df_m, width='stretch', column_config={
            "Name": st.column_config.TextColumn("Name", pinned="left"),
            "25/26 Pts": st.column_config.NumberColumn("25/26 Pts", format="%.1f"),
            "PPG": st.column_config.NumberColumn("PPG", format="%.2f"),
            "26/27 Proj": st.column_config.NumberColumn("26/27 Proj", format="%.1f"),
        })
        st.caption(f"Squad total 26/27 projection: **{sum(p['projected_pts'] for p in my_picks):.0f}** pts")

    # Positional needs → best available per position
    remaining = ds.num_rounds - len(my_picks)
    if remaining > 0:
        st.divider()
        st.subheader(f"Best available per position  ({remaining} picks left)")
        exp_cols = st.columns(len(POSITION_ORDER))
        for col, pos in zip(exp_cols, POSITION_ORDER):
            room = POS_CAPS[pos] - counts.get(pos, 0)
            col.markdown(f"**{POS_LABELS[pos]}**  ·  {room} slot{'s' if room != 1 else ''} left")
            for p in ds.get_available(pos, sort_by="projected_pts")[:5]:
                norm = _norm_name(p["name"])
                dp_tag = f" · DP#{dp_lookup[norm]}" if norm in dp_lookup else ""
                col.markdown(f"- {p['web_name']} *({p['projected_pts']:.0f}{dp_tag})*")


# ── ADP / Value ─────────────────────────────────────────────────────────────
with tab_adp:
    st.subheader("ADP / Value")
    st.caption(
        "Compares each player's projection rank against the community ADP proxy "
        "(FPL ownership). **Own Rank − Proj Rank** > 0 means the crowd rates a "
        "player *higher* than your Fantrax projection (potential reach); < 0 means "
        "the projection likes them more than the crowd (potential value). Real "
        "Fantrax ADP replaces this once community drafts open in August."
    )
    st.divider()

    all_avail = ds.get_available(sort_by="projected_pts")
    rows_v = []
    for i, p in enumerate(all_avail, 1):
        norm = _norm_name(p["name"])
        adp = p.get("adp_rank")
        diff = (adp - i) if adp is not None else None
        rows_v.append({
            "Name":       p["name"],
            "Pos":        POS_LABELS.get(p["position"]),
            "Club":       p["team"],
            "26/27 Proj": p["projected_pts"],
            "Proj Rank":  i,
            "Own Rank":   adp,
            "Δ (Own−Proj)": diff,
            "Own%":       p.get("ownership_pct"),
            "DP Rec":     dp_lookup.get(norm),
        })
    df_v = pd.DataFrame(rows_v)
    if not df_v.empty:
        df_v.index = range(1, len(df_v) + 1)
    st.dataframe(df_v, width='stretch', height=680, column_config={
        "Name": st.column_config.TextColumn("Name", pinned="left"),
        "26/27 Proj": st.column_config.NumberColumn("26/27 Proj", format="%.1f"),
        "Δ (Own−Proj)": st.column_config.NumberColumn(
            "Δ (Own−Proj)", help="Positive = crowd rates higher than projection (reach); negative = value"),
        "Own%": st.column_config.NumberColumn("Own%", format="%.1f"),
    })
