"""Laminar Stage-1 daily report: what we're watching, what it would earn, and
what looks wrong. NOT an ops health check — see `laminar.report` for what each
section can and cannot answer and why (Stage 1 has no orders, so no fills, no
fees, no PnL; see that module's docstring before changing this one).

Cron: 00:15 UTC daily, reporting on the just-completed UTC day (the reward
epoch is a UTC calendar day).
"""

import statistics as st
import subprocess
from datetime import UTC, date, datetime, timedelta

import polars as pl

from laminar import calendar as cal
from laminar import collect, gate_check, report, store
from laminar import track as trk

EARLY_WARNING_RATIO = 2.5  # denominator bound ratio; hard abandon gate is 3.0
DRIFT_ALERT_CENTS_MULT = 1.5  # alert if 30-min drift exceeds this * max_spread

# Sample-loss abandon trigger, straight from the pre-registration: "3 consecutive
# days of >5% sample loss". Nothing was watching this until 2026-08-25 — coverage
# was written to metrics_history and never compared against anything.
MIN_COVERAGE = 0.95
COVERAGE_STREAK_DAYS = 3

FIXES_LOG = [
    ("2026-08-14", "Denominator bound formula was inverted on lopsided books "
                    "(lower could exceed upper) — caught by intent tests, fixed "
                    "before any data was collected under it."),
    ("2026-08-14", "Book store partitioned by day; at ~1.1k rows/sample that "
                    "meant rewriting a growing same-day file on every 60s "
                    "append. Repartitioned to per-hour files."),
]


def _yesterday_utc() -> str:
    return (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")


def _fmt_money(lo: float, hi: float) -> str:
    return f"${lo:.2f}–${hi:.2f}" if hi > 0 else "$0.00"


def _risk_row(r: dict, risk_hist: pl.DataFrame, day: str) -> str:
    """One <tr> for the directional-risk-proxy table: today's count/mean plus
    a trailing-7d rollup (`risk_hist` already includes today's own row, since
    the caller appends before loading)."""
    start = (date.fromisoformat(day) - timedelta(days=6)).isoformat()
    tok_hist = risk_hist.filter(
        (pl.col("token_id") == r["token_id"]) & (pl.col("day") >= start) & (pl.col("day") <= day)
    )
    n7 = int(tok_hist["n_large_trades"].sum()) if tok_hist.height else 0
    sum7 = float(tok_hist["signed_drift_sum"].sum()) if tok_hist.height else 0.0
    mean_today = r["signed_drift_sum"] / r["n_large_trades"] if r["n_large_trades"] else 0.0
    mean7 = f"{sum7 / n7:+.4f}" if n7 else "—"
    return (
        f"<tr><td>{r['market_slug'][:45]}</td><td>{r['outcome']}</td><td>{r['n_large_trades']}</td>"
        f"<td>{mean_today:+.4f}</td><td>{n7}</td><td>{mean7}</td></tr>"
    )


def build(day: str) -> tuple[str, bool]:
    watchlist = collect.load_watchlist() if collect.WATCHLIST_PATH.exists() else None
    obs = report.observe(day)
    obs = report.label_from_watchlist(obs, watchlist)
    missing = report.missing_from_watchlist(obs, watchlist)

    # Two tiers, deliberately. `alerts` are the conditions the pre-registration
    # would act on — they set the email to ALERT. `notes` are diagnostics that
    # fire most days by design (pool drift runs 8-13 markets/day, shocks are
    # frequent, the yield-move threshold is explicitly uncalibrated); they are
    # still reported, but escalating on them made every single email an ALERT
    # and would have buried the sample-loss trigger they exist alongside.
    alerts: list[str] = []
    notes: list[str] = []
    sections: list[str] = []

    # 1+2+4: pools, reference-quote params, modelled reward — one table,
    # because the params are per-pool (max_spread/min_size vary) and the
    # estimate is meaningless without them alongside it.
    if not obs:
        sections.append("<p><b>No book samples for this day.</b></p>")
        alerts.append(f"zero observations on {day}")
    else:
        rows = []
        total_lo = total_hi = 0.0
        for o in sorted(obs, key=lambda x: -x.est_usd_hi):
            total_lo += o.est_usd_lo
            total_hi += o.est_usd_hi
            flag = []
            if o.band_breach:
                flag.append("BAND")
            if o.denom_hi / o.denom_lo > EARLY_WARNING_RATIO if o.denom_lo > 0 else False:
                flag.append(f"DENOM {o.denom_hi/o.denom_lo:.1f}x")
            if o.drift_cents is not None and o.drift_cents > DRIFT_ALERT_CENTS_MULT * o.max_spread:
                flag.append(f"DRIFT {o.drift_cents:.1f}c")
            rows.append(
                f"<tr><td>{o.market_slug[:45]}</td><td>{o.outcome}</td>"
                f"<td>{o.midpoint:.3f}</td><td>{o.max_spread:.1f}c</td>"
                f"<td>{o.min_size:.0f}</td><td>±{o.ref_offset:.2f}c</td>"
                f"<td>${o.daily_rate:.0f}</td>"
                f"<td>{_fmt_money(o.est_usd_lo, o.est_usd_hi)}</td>"
                f"<td>{' '.join(flag)}</td></tr>"
            )
            if flag:
                notes.append(f"{o.market_slug}: {' '.join(flag)}")
        sections.append(
            "<h3>1–2. Pools observed &amp; reference-quote parameters</h3>"
            "<p>Reference quote per pool: <code>min_size</code> shares each side, "
            "offset = min(1.0c, max_spread×0.5) from the adjusted midpoint. "
            "This is a MODEL INPUT for sizing the estimate below — Stage 1 places "
            "no live orders.</p>"
            "<table border=1 cellpadding=4><tr><th>market</th><th>side</th>"
            "<th>mid</th><th>max_spread</th><th>min_size</th><th>ref offset</th>"
            "<th>pool $/day</th><th>4. modelled reward (bounded)</th><th>flags</th></tr>"
            + "".join(rows) + "</table>"
            f"<p><b>Modelled reward total (this reference quote, all pools): "
            f"{_fmt_money(total_lo, total_hi)}/day.</b> Bounds come from the "
            "denominator being unreconstructible exactly from the public book "
            "(size is per price level, not per maker) — see "
            "laminar_20260814_preregistration.md § known unknowns. Assumes "
            "the straightforward daily-epoch reading (docs also mention 10,080 "
            "samples/epoch = 7 days, unresolved; both hypotheses coincide once "
            "7 days of stable data exist to compare against)."
        )

    # 3: the honest non-answer, plus the nearest defensible proxy
    real_pnl_note = (
        "<h3>3. Fills / fees / realised PnL (past 24h)</h3>"
        "<p><b>N/A by design.</b> Stage 1 has placed zero orders — "
        "<code>laminar.clob</code> has no signing and no POST endpoint, so there "
        "is no fill, no fee, and no position to report a PnL on. This becomes "
        "answerable at Stage 3 (live orders), which is blocked on the "
        "jurisdiction/entity decision, not on anything this report could "
        "compute differently. (Maker/taker base fee on every sampled market is "
        "0 regardless, for what it's worth at that stage.)</p>"
    )
    touch = report.fill_touch(day, obs) if obs else {}
    if touch:
        t_rows = []
        gross_lo = 0.0
        for o in sorted(obs, key=lambda x: -sum(touch.get(x.token_id, (0, 0)))):
            bid_t, ask_t = touch.get(o.token_id, (0.0, 0.0))
            if bid_t == 0.0 and ask_t == 0.0:
                continue
            spread = 2 * o.ref_offset / 100.0
            capped = min(bid_t, ask_t, o.ref_size)
            gross_lo += capped * spread
            t_rows.append(
                f"<tr><td>{o.market_slug[:45]}</td><td>{o.outcome}</td>"
                f"<td>{bid_t:.0f}</td><td>{ask_t:.0f}</td>"
                f"<td>${capped * spread:.3f}</td></tr>"
            )
        real_pnl_note += (
            "<p><b>Simulated fill-touch (real trade tape, NOT a live fill) — "
            "an UPPER BOUND, not a probability:</b> real executed trades that "
            "printed through our reference bid/ask. Queue position among "
            "competing makers is unobservable from public data, so an actual "
            "resting order could have captured less than shown, including "
            "zero. 'gross spread if capped-filled' assumes both sides filled "
            "up to min(touch, our size) and ignores adverse selection, "
            "inventory carry, and that a fill reduces resting size (and thus "
            "reward-earning uptime) until replaced. Also compares every trade "
            "against today's LATEST reference price, not the price at each "
            "trade's own moment — fine for this slow-moving watchlist, would "
            "mislead on a market that moved a lot intraday.</p>"
            + (
                "<table border=1 cellpadding=4><tr><th>market</th><th>side</th>"
                "<th>bid-touch shares</th><th>ask-touch shares</th>"
                "<th>gross spread (capped)</th></tr>" + "".join(t_rows) + "</table>"
                f"<p>Sum, capped gross spread across pools with any touch: "
                f"${gross_lo:.2f}. This is a rough baseline for comparing "
                f"against Stage 3 reality later, not a forecast.</p>"
                if t_rows
                else "<p>No trades touched any reference quote in this window.</p>"
            )
        )
    sections.append(real_pnl_note)

    # 5: deltas from expectation
    ratios = report.denom_ratio_distribution(day)  # pooled across the WHOLE day, not one sample
    find_rows = "".join(f"<li>{d}: {msg}</li>" for d, msg in FIXES_LOG)
    if ratios:
        over_gate = sum(r > 3 for r in ratios)
        find_rows += (
            f"<li>{day}: denominator bound ratio, pooled across all {len(ratios)} "
            f"(sample × token) observations — median {st.median(ratios):.2f}x, "
            f"max {max(ratios):.2f}x (abandon gate: 3.0x; {over_gate} over it, "
            f"{over_gate/len(ratios):.1%} of observations).</li>"
        )
        if over_gate / len(ratios) > 0.05:
            alerts.append(f"{over_gate/len(ratios):.1%} of observations over the 3x denom gate")
    drift = report.pool_drift(obs, watchlist)
    if drift:
        find_rows += "".join(
            f"<li>{day}: <b>pool size moved</b> on {slug} — frozen watchlist "
            f"${frozen:.0f}/day → live ${live:.0f}/day "
            f"({(live-frozen)/frozen:+.0%}). The frozen watchlist's rate is a "
            f"snapshot; every estimate above already uses the live value, this "
            f"just surfaces how much it has drifted.</li>"
            for slug, frozen, live in drift
        )
        notes.append(f"{len(drift)} market(s) with pool size drift > 30%")
    if missing:
        missing_slugs = sorted({slug for slug, _ in missing})
        find_rows += (
            f"<li>{day}: {len(missing)} watchlist token(s) across "
            f"{len(missing_slugs)} market(s) absent from the latest sample "
            f"(lost a qualifying side, or the market closed/stopped accepting "
            f"orders): {', '.join(missing_slugs[:5])}"
            f"{' …' if len(missing_slugs) > 5 else ''}.</li>"
        )
        alerts.append(f"{len(missing)} watchlist token(s) unobservable")
    sections.append(f"<h3>5. Findings / deltas from expectation</h3><ul>{find_rows}</ul>")

    # 6: anomaly alerts — book-derived proxies only, labelled as such
    sections.append(
        "<h3>6. Anomaly alerts</h3>"
        "<p>No live position exists, so a literal one-sided loss can't occur yet; "
        "the closest honest proxy is <b>DRIFT</b> — how far the midpoint moved "
        "in the last ~30 min relative to the reward band width, i.e. whether the "
        "market would have run through our reference quote. <b>BAND</b> = midpoint "
        "left [0.10, 0.90] (loses two-sided relief). <b>DENOM</b> = the "
        "reconstruction bound widened past the early-warning threshold. See the "
        "flags column above" + (" — none fired today." if not notes else ".")
    )

    # 8: shock ledger — record sudden collapses, categorised, so 21 days of
    # them can answer "is one category structurally unsafe to make markets in".
    new_shocks = report.detect_shocks(day, obs, watchlist)
    store.append_shocks(new_shocks)
    shocks = store.load_shocks()
    if new_shocks:
        notes.append(f"{len(new_shocks)} shock(s) detected")
    s_html = "<h3>8. Shock ledger (sudden collapses)</h3>"
    if new_shocks:
        s_html += (
            "<p><b>New today:</b></p><table border=1 cellpadding=4><tr><th>kind</th>"
            "<th>category</th><th>market</th><th>before</th><th>after</th>"
            "<th>change</th><th>window</th><th>scheduled event</th></tr>"
            + "".join(
                f"<tr><td>{s['kind']}</td><td>{s['category']}</td>"
                f"<td>{s['market_slug'][:38]}</td><td>{s['before']:.4g}</td>"
                f"<td>{s['after']:.4g}</td><td>{s['pct_change']:+.0%}</td>"
                f"<td>{s['window_minutes']:.0f}m</td>"
                f"<td>{s['scheduled_event'] or '—'}</td></tr>"
                for s in new_shocks
            )
            + "</table>"
        )
    else:
        s_html += "<p>No new shocks detected today.</p>"
    if not shocks.is_empty():
        by_cat = (
            shocks.group_by(["category", "kind"]).len()
            .sort("len", descending=True)
        )
        s_html += (
            "<p><b>Cumulative, all days — the actual deliverable here:</b> if "
            "collapses concentrate in one category, that category comes off the "
            "market-making list. Too few days to conclude anything yet.</p>"
            "<table border=1 cellpadding=4><tr><th>category</th><th>kind</th><th>count</th></tr>"
            + "".join(
                f"<tr><td>{r['category']}</td><td>{r['kind']}</td><td>{r['len']}</td></tr>"
                for r in by_cat.iter_rows(named=True)
            )
            + f"</table><p>Total logged: {shocks.height} across "
            f"{shocks['market_slug'].n_unique()} markets.</p>"
        )
    sections.append(s_html)

    # 10: PolyBeats leads — a SEPARATE observation series, deliberately not
    # merged into anything above. The frozen watchlist is 30 low-news long-dated
    # markets; these leads are the opposite by construction, so overlap is near
    # zero and the two must not be pooled. Recording only: no view is formed on
    # whether a lead is worth acting on until the window closes and there are
    # enough cases to look at. See the pre-registration's addendum log.
    leads = store.load_leads()
    l_html = "<h3>10. PolyBeats leads (separate series — recording only)</h3>"
    if leads.is_empty():
        l_html += "<p>No leads recorded yet.</p>"
    else:
        today = leads.filter(pl.col("posted_at").str.starts_with(day))
        signals = leads.filter(pl.col("is_smart_money"))
        trackable = signals.filter(pl.col("trackable"))
        l_html += (
            f"<p>Cumulative: <b>{leads.height}</b> posts, <b>{signals.height}</b> called a "
            f"position, <b>{trackable.height}</b> of those resolve to a pool and are "
            f"trackable. New on {day}: <b>{today.height}</b>.</p>"
            "<p>A post is only trackable when it links a Polymarket event; the "
            "channel's free tier withholds the link (and usually the stake) on "
            "the rest, which is a recorded miss, not a dropped row. Stake shown "
            "as — means undisclosed, never a zero-size bet.</p>"
        )
        today_signals = today.filter(pl.col("is_smart_money"))
        recent = today_signals if today_signals.height else signals.tail(8)
        if recent.height:
            rows = []
            for r in recent.sort("msg_id", descending=True).iter_rows(named=True):
                stake = f"${r['stake_usd']:,.0f}" if r["stake_usd"] else "—"
                # 0.0 means "the post did not state it", so it must not render as
                # a real 0% — an unparsed price shown as 0 would look like a
                # market that collapsed to nothing.
                entry = f"{r['avg_entry_pct']:.0f}%" if r["avg_entry_pct"] else "—"
                now = f"{r['current_pct']:.0f}%" if r["current_pct"] else "—"
                move = "—" if entry == now == "—" else f"{entry} → {now}"
                pool = r["event_slug"][:34] if r["event_slug"] else "— not resolvable"
                rows.append(
                    f"<tr><td>{r['posted_at'][5:16]}</td><td>{r['direction'] or '—'}</td>"
                    f"<td>{stake}</td><td>{r['best_win_rate']:.0%}</td><td>{move}</td>"
                    f"<td>{pool}</td><td>{r['headline'][:44]}</td></tr>"
                )
            l_html += (
                "<table border=1 cellpadding=4><tr><th>posted</th><th>dir</th>"
                "<th>stake</th><th>best win rate</th><th>entry→now</th>"
                "<th>pool (event slug)</th><th>headline</th></tr>"
                + "".join(rows)
                + "</table>"
            )
    # Tracker state. Deliberately a status line, not an analysis: the pool
    # series exists to be read at a review checkpoint, not summarised daily
    # into a number that invites a conclusion before there are enough cases.
    lm = store.load_lead_markets()
    tracks = store.load_lead_tracks(day)
    if not lm.is_empty():
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        active = lm.filter((~pl.col("closed")) & (pl.col("track_until") > now_ms))
        l_html += (
            "<p><b>Pool tracker:</b> "
            f"{lm['msg_id'].n_unique()} leads resolved to {lm['condition_id'].n_unique()} "
            f"markets ({lm.height} lead-token pairs). Currently in the "
            f"{trk.TRACK_DAYS}-day window: <b>{active['msg_id'].n_unique()} leads / "
            f"{active['condition_id'].n_unique()} markets / {active.height} tokens</b>. "
            f"Snapshots stored on {day}: <b>{tracks.height}</b> rows across "
            f"{tracks['ts'].n_unique() if tracks.height else 0} hourly passes.</p>"
            "<p>Pool rate, book depth and the competing field are live state with no "
            "history endpoint — captured hourly because they cannot be reconstructed "
            "later. Price and trade history are backfillable and deliberately are not "
            "duplicated here.</p>"
        )
        if tracks.height:
            gap = 24 - tracks["ts"].n_unique()
            if gap > 2:
                alerts.append(f"lead tracker: only {tracks['ts'].n_unique()}/24 hourly passes")
    sections.append(l_html)

    # 9: scheduled macro calendar — separates "expected catalyst" from "surprise"
    y, mth, dd = (int(x) for x in day.split("-"))
    today_events = cal.events_on(date(y, mth, dd))
    upcoming = cal.next_events(date(y, mth, dd))
    sections.append(
        "<h3>9. Macro calendar</h3>"
        + (
            f"<p><b>Scheduled events on {day}: {'; '.join(today_events)}</b> — "
            "flags and shocks above on this day should be read as expected "
            "catalyst response, not anomaly.</p>"
            if today_events
            else f"<p>No scheduled FOMC/CPI event on {day} — any shock above is "
            "an unscheduled surprise, which is the more informative kind.</p>"
        )
        + "<p>Next up: "
        + ("; ".join(f"<b>{d}</b> {what}" for d, what in upcoming) if upcoming else "none scheduled")
        + ". Only the Fed rate markets in the watchlist carry a scheduled "
        "catalyst; the rest are deliberately low-news.</p>"
    )

    # 7: Polymarket's OWN competitiveness index, discovered 2026-08-14 — tracked
    # in parallel, not yet substituted for our own denominator-bound model.
    comp = store.load_competitiveness(day)
    if not comp.is_empty():
        latest = comp.filter(pl.col("ts") == comp["ts"].max())
        by_slug = {o.market_slug: o for o in obs}
        c_rows = []
        for r in latest.sort("competitiveness", descending=True).iter_rows(named=True):
            match = by_slug.get(r["market_slug"])
            ours = f"{match.denom_hi/match.denom_lo:.2f}x" if match and match.denom_lo > 0 else "—"
            c_rows.append(
                f"<tr><td>{r['market_slug'][:45]}</td><td>{r['competitiveness']:.1f}</td>"
                f"<td>${r['config_rate_per_day']:.0f}</td><td>{ours}</td></tr>"
            )
        sections.append(
            "<h3>7. Polymarket's own competitiveness index (bonus, uncalibrated)</h3>"
            "<p>Discovered 2026-08-14 at <code>clob.polymarket.com/rewards/markets/&lt;condition_id&gt;</code> "
            "— not in any docs found, formula unknown. Its <code>rewards_config.rate_per_day</code> has "
            "disagreed with <code>/sampling-markets</code>'s rate for the same market at the same "
            "moment (seen live: 500 vs 200 on fed-rate-hike-in-2026); not yet explained. Collected "
            "to see whether it tracks our own denominator-bound ratio over the observation window "
            "— if it does, it may replace that model outright; until then it's a second opinion, "
            "not a correction.</p>"
            "<table border=1 cellpadding=4><tr><th>market</th><th>competitiveness</th>"
            "<th>config rate/day</th><th>our denom ratio</th></tr>" + "".join(c_rows) + "</table>"
        )

    # 11: gate spot-check — 3 random 2h windows sampled from today, re-checked
    # at the pre-registration's ACTUAL 200-share reference quote (section 1-2's
    # est_usd above uses each market's own min_size instead — see
    # worklogs/2026-08-22_laminar-yield-gate-check_v1.md for why that's not the
    # same number). Observation only: nothing here changes what any other
    # section computes or does — the "action" added is this section existing.
    books_day = store.load_books(day)
    trades_day = store.load_trades(day)
    gate_rows = gate_check.daily_spotcheck(day, books_day, trades_day, watchlist)
    store.append_gate_windows(gate_rows)
    risk_rows = gate_check.directional_risk_proxy(day, books_day, trades_day, watchlist)
    store.append_gate_risk(risk_rows)

    g_html = "<h3>11. Gate spot-check (3×2h windows, 200-share reference quote)</h3>"
    if not gate_rows:
        g_html += "<p>No book coverage for this day's sampled windows.</p>"
    else:
        pattern = gate_check.window_pattern_summary(gate_rows)
        g_html += (
            "<p>Same annualised-yield gate as the pre-registration (200 shares/side, "
            "upper bound), from 3 random non-overlapping 2h slots instead of one "
            "end-of-day snapshot — for spotting a time-of-day pattern (a), not a "
            "gate decision by itself (that's the 21-day/9-04 review).</p>"
            "<table border=1 cellpadding=4><tr><th>window (UTC)</th><th>tokens</th>"
            "<th>median yield (upper)</th><th>median liquidity</th>"
            "<th>volume ($ / trades)</th></tr>"
            + "".join(
                f"<tr><td>{datetime.fromtimestamp(p['window_start']/1000, UTC).strftime('%H:%M')}"
                f"–{datetime.fromtimestamp(p['window_end']/1000, UTC).strftime('%H:%M')}</td>"
                f"<td>{p['n_tokens']}</td><td>{p['median_yield_hi_pct']:.0f}%</td>"
                f"<td>{p['median_liquidity']:.2f}</td>"
                f"<td>${p['total_volume_notional']:.0f} / {p['total_volume_count']}</td></tr>"
                for p in sorted(pattern.values(), key=lambda p: p["window_start"])
            )
            + "</table>"
        )

        history = store.load_gate_windows()
        today_summary = gate_check.pool_day_summary(gate_rows)
        yield_flags = gate_check.flag_pool_changes(today_summary, history, day)
        if yield_flags:
            g_html += (
                "<p><b>Pool yield moved vs its trailing baseline (b) — threshold "
                f"±{gate_check.YIELD_CHANGE_FLAG_PCT:.0f}%, first-pass and not yet "
                "calibrated:</b></p>"
                "<table border=1 cellpadding=4><tr><th>market</th><th>side</th><th>today</th>"
                "<th>baseline</th><th>change</th></tr>"
                + "".join(
                    f"<tr><td>{f['market_slug'][:45]}</td><td>{f['outcome']}</td>"
                    f"<td>{f['today_pct']:.0f}%</td>"
                    f"<td>{f['baseline_pct']:.0f}%</td><td>{f['change_pct']:+.0f}%</td></tr>"
                    for f in yield_flags
                )
                + "</table>"
            )
            notes.append(f"{len(yield_flags)} pool(s) with yield moved >{gate_check.YIELD_CHANGE_FLAG_PCT:.0f}% vs trailing baseline")
        else:
            g_html += "<p>No pool's yield moved past the (uncalibrated) threshold vs its trailing baseline.</p>"

        if risk_rows:
            risk_hist = store.load_gate_risk()
            g_html += (
                "<p><b>Directional-risk proxy (b) — NOT a loss number</b> (no live "
                "position exists to lose on, see §3): mean signed mid-price drift "
                f"{gate_check.RISK_HORIZON_MIN} min after large trades "
                f"(≥${gate_check.LARGE_USD:.0f}) in this pool. Positive = price kept "
                "moving with the trade (continuation); negative = reversed. Today's "
                "count is usually 0-2 per pool — read the trailing-7d column, not "
                "today's alone.</p>"
                "<table border=1 cellpadding=4><tr><th>market</th><th>side</th><th>n today</th>"
                "<th>mean drift today</th><th>n trailing 7d</th><th>mean drift trailing 7d</th></tr>"
                + "".join(
                    _risk_row(r, risk_hist, day)
                    for r in sorted(risk_rows, key=lambda r: -abs(r["signed_drift_sum"]))
                )
                + "</table>"
            )
    sections.append(g_html)

    # Metrics-history row — the day-7/14/21 checkpoints read this trend log
    # instead of re-deriving it from a stack of individual emails.
    books_today = store.load_books(day)
    coverage = (books_today["ts"].n_unique() / 1440) if not books_today.is_empty() else 0.0
    store.append_metrics_history(
        {
            "day": day,
            "coverage": coverage,
            "n_observed": len(obs),
            "median_denom_ratio": st.median(ratios) if ratios else 0.0,
            "max_denom_ratio": max(ratios) if ratios else 0.0,
            "est_usd_lo": total_lo if obs else 0.0,
            "est_usd_hi": total_hi if obs else 0.0,
            "missing_count": len(missing),
            "pool_drift_count": len(drift),
        }
    )

    # Sample-loss trigger. `coverage` has been logged since day one but nothing
    # ever compared it to anything, so the pre-registration's only hard abandon
    # condition had no alarm on it. Read the streak back out of metrics_history
    # (today's row is already appended above, and it upserts on `day`).
    if coverage < MIN_COVERAGE:
        # No raw "<": these strings are interpolated straight into the HTML body.
        alerts.append(f"sample coverage {coverage:.1%} on {day}, below {MIN_COVERAGE:.0%}")
    hist = store.load_metrics_history()
    if hist.height:
        recent = (
            hist.filter(pl.col("day") <= day)
            .sort("day")
            .tail(COVERAGE_STREAK_DAYS)
        )
        if (
            recent.height == COVERAGE_STREAK_DAYS
            and bool((recent["coverage"] < MIN_COVERAGE).all())
        ):
            alerts.append(
                f"ABANDON TRIGGER: {COVERAGE_STREAK_DAYS} consecutive days below "
                f"{MIN_COVERAGE:.0%} coverage "
                f"({', '.join(f'{d}={c:.1%}' for d, c in zip(recent['day'], recent['coverage']))})"
            )

    banner = (
        "<h3 style='color:#b00'>Alerts</h3><ul>"
        + "".join(f"<li>{a}</li>" for a in alerts)
        + "</ul>"
        if alerts
        else "<p><b>No alerts.</b> Diagnostics below fire routinely and do not "
             "escalate — see the flags column and §5/§8.</p>"
    )
    ok = not alerts
    body = banner + "".join(sections)
    return body, ok


def main() -> int:
    day = _yesterday_utc()
    body, ok = build(day)
    status = "OK" if ok else "ALERT"
    subject = f"laminar — {day}" + ("" if ok else " — flags raised")
    subprocess.run(
        ["/opt/farseer/.venv/bin/python", "/opt/farseer/ops/notify.py", status, subject],
        input=body, text=True, check=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
