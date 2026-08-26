"""Intent tests for the PolyBeats pool tracker.

What these protect, in order of how badly a silent failure would hurt:

1. The tracked set stays bounded. One event can hold 26 markets; if fan-out or
   the time window leaked, the hourly job would grow without limit and start
   dropping the snapshots it exists to take.
2. The market the post actually named is never evicted by the cap. Losing it
   would leave a case whose own market is missing while its siblings are kept.
3. `track_until` is anchored to the POST, not to whenever the resolver ran.
   Re-resolving must not silently extend a case's window and make its length
   depend on cron timing.
4. Settled markets never enter the snapshot set — they have no live pool, so
   polling them is pure waste that also dilutes any later per-hour statistics.
"""

from laminar import store, track


def _market(slug, vol, closed=False, n_tokens=2):
    return {
        "condition_id": f"0x{slug}",
        "market_slug": slug,
        "question": f"q {slug}",
        "end_date_iso": "2026-12-31T00:00:00Z",
        "tokens": [
            {"token_id": f"{slug}-t{i}", "outcome": ["Yes", "No"][i]} for i in range(n_tokens)
        ],
        "closed": closed,
        "active": True,
        "volume_24hr": vol,
        "liquidity": 100.0,
        "max_spread": 3.5,
        "min_size": 50.0,
    }


def _lead(msg_id=1, event="ev", named="", posted="2026-08-17T08:00:00+00:00"):
    return {
        "msg_id": msg_id,
        "posted_at": posted,
        "fetched_at": 0,
        "headline": "h",
        "body": "b",
        "is_smart_money": True,
        "n_accounts": 1,
        "direction": "是",
        "stake_usd": 0.0,
        "avg_entry_pct": 0.0,
        "current_pct": 0.0,
        "event_slug": event,
        "market_slug": named,
        "wallets": "",
        "best_win_rate": 0.0,
        "sector_pnl_usd": 0.0,
        "trackable": True,
        "url": "u",
    }


def _resolve(monkeypatch, tmp_path, leads, markets):
    monkeypatch.setattr(store, "LAMINAR_DIR", tmp_path)
    store.append_leads(leads)
    monkeypatch.setattr(track.clob, "event_markets", lambda slug: markets)
    return track.resolve_leads()


def test_fan_out_is_capped_so_the_hourly_job_stays_bounded(monkeypatch, tmp_path):
    """A 26-leg event must not put 26 markets into an hourly poll."""
    markets = [_market(f"m{i}", vol=float(i)) for i in range(26)]
    _resolve(monkeypatch, tmp_path, [_lead()], markets)

    rows = store.load_lead_markets()
    assert rows["market_slug"].n_unique() == track.MAX_MARKETS_PER_LEAD
    assert rows.height == track.MAX_MARKETS_PER_LEAD * 2  # two tokens each


def test_the_market_the_post_named_survives_the_cap(monkeypatch, tmp_path):
    """The named market has the lowest volume here, so a pure volume ranking
    would drop precisely the market the lead was about."""
    markets = [_market(f"m{i}", vol=100.0 * (i + 1)) for i in range(12)]
    markets.append(_market("the-named-one", vol=0.0))
    _resolve(monkeypatch, tmp_path, [_lead(named="the-named-one")], markets)

    rows = store.load_lead_markets()
    kept = set(rows["market_slug"].to_list())
    assert "the-named-one" in kept
    assert rows.filter(rows["named_by_lead"])["market_slug"].unique().to_list() == [
        "the-named-one"
    ]


def test_track_window_is_anchored_to_the_post_not_the_resolve_time(monkeypatch, tmp_path):
    """Otherwise a lead resolved late would get a longer window than one
    resolved promptly, and case length would depend on cron timing."""
    posted = "2026-08-17T08:00:00+00:00"
    _resolve(monkeypatch, tmp_path, [_lead(posted=posted)], [_market("m", 1.0)])

    row = store.load_lead_markets().row(0, named=True)
    expected = track._iso_to_ms(posted) + track.TRACK_DAYS * 86_400_000
    assert row["track_until"] == expected


def test_re_resolving_does_not_extend_an_existing_window(monkeypatch, tmp_path):
    """Dedupe keeps FIRST. A second pass must be a no-op, not a refresh."""
    monkeypatch.setattr(store, "LAMINAR_DIR", tmp_path)
    store.append_leads([_lead()])
    monkeypatch.setattr(track.clob, "event_markets", lambda slug: [_market("m", 1.0)])

    track.resolve_leads()
    first = store.load_lead_markets()["track_until"].to_list()
    resolved, added = track.resolve_leads()

    assert (resolved, added) == (0, 0)  # already known, no gamma call spent
    assert store.load_lead_markets()["track_until"].to_list() == first


def test_settled_and_expired_markets_leave_the_snapshot_set(monkeypatch, tmp_path):
    """Three ways out of the active set, all of which must work: settled,
    window elapsed, and resolved-in-the-future (never, but guards clock skew)."""
    monkeypatch.setattr(store, "LAMINAR_DIR", tmp_path)
    now = 2_000_000_000_000
    store.append_lead_markets(
        [
            {
                "msg_id": mid,
                "event_slug": "e",
                "condition_id": "0x1",
                "market_slug": "m",
                "question": "q",
                "token_id": f"t{mid}",
                "outcome": "Yes",
                "end_date_iso": "",
                "resolved_at": resolved_at,
                "track_until": until,
                "closed": closed,
                "named_by_lead": True,
                "volume_24hr": 0.0,
            }
            for mid, closed, until, resolved_at in [
                (1, False, now + 1, now),  # live -> kept
                (2, True, now + 1, now),  # settled -> dropped
                (3, False, now - 1, now),  # window elapsed -> dropped
                (4, False, now + 1, now + 999),  # resolved in the future -> dropped
            ]
        ]
    )
    assert [t["msg_id"] for t in track.active_tracks(now)] == [1]


def test_only_leads_that_called_a_position_and_resolve_are_tracked(monkeypatch, tmp_path):
    """Commentary and unresolvable posts are recorded as leads but must never
    consume a gamma call or enter the tracked set."""
    monkeypatch.setattr(store, "LAMINAR_DIR", tmp_path)
    news = {**_lead(msg_id=2), "is_smart_money": False}
    vague = {**_lead(msg_id=3), "trackable": False, "event_slug": ""}
    store.append_leads([_lead(msg_id=1), news, vague])

    called: list[str] = []

    def _spy(slug):
        called.append(slug)
        return [_market("m", 1.0)]

    monkeypatch.setattr(track.clob, "event_markets", _spy)
    resolved, _ = track.resolve_leads()

    assert resolved == 1
    assert called == ["ev"]
    assert store.load_lead_markets()["msg_id"].unique().to_list() == [1]


def _stub_book(monkeypatch, bids, asks, rewards):
    monkeypatch.setattr(
        track.clob,
        "book",
        lambda tid: {
            "bids": [{"price": p, "size": s} for p, s in bids],
            "asks": [{"price": p, "size": s} for p, s in asks],
        },
    )
    monkeypatch.setattr(track.clob, "rewards_market", lambda cid: rewards)


def _one_track(monkeypatch, tmp_path, now):
    monkeypatch.setattr(store, "LAMINAR_DIR", tmp_path)
    store.append_lead_markets(
        [
            {
                "msg_id": 1, "event_slug": "e", "condition_id": "0x1", "market_slug": "m",
                "question": "q", "token_id": "t1", "outcome": "Yes", "end_date_iso": "",
                "resolved_at": now - 1, "track_until": now + 86_400_000,
                "closed": False, "named_by_lead": True, "volume_24hr": 0.0,
            }
        ]
    )


def test_snapshot_separates_in_band_depth_from_total_depth(monkeypatch, tmp_path):
    """Band depth is the reward-earning size and total depth is the whole book.
    Conflating them would make a market look like it kept its liquidity when
    only the out-of-band part remained."""
    now = 2_000_000_000_000
    _one_track(monkeypatch, tmp_path, now)
    # max_spread 3.5c around a 0.50 midpoint: 0.48/0.52 are in band, 0.30/0.70 are not.
    _stub_book(
        monkeypatch,
        bids=[("0.48", "100"), ("0.30", "900")],
        asks=[("0.52", "200"), ("0.70", "800")],
        # Real shape, verified live 2026-08-18: rewards_config is a LIST of
        # per-asset configs and the band lives at the top level. An earlier
        # version of this fixture invented a flat dict and passed against code
        # that read the same fiction — the live call is what settled it.
        rewards={
            "market_competitiveness": 42.0,
            "rewards_max_spread": 3.5,
            "rewards_min_size": 20,
            "rewards_config": [
                {"rate_per_day": 100, "asset_address": "0xA"},
                {"rate_per_day": 20, "asset_address": "0xB"},
            ],
        },
    )
    assert track.snapshot_pools(now) == 2

    rows = {r["outcome"]: r for r in store.load_lead_tracks(store._day(now)).to_dicts()}
    assert rows["Yes/bid"]["band_depth"] == 100 and rows["Yes/bid"]["book_depth"] == 1000
    assert rows["Yes/ask"]["band_depth"] == 200 and rows["Yes/ask"]["book_depth"] == 1000
    assert rows["Yes/bid"]["daily_rate"] == 120
    assert rows["Yes/bid"]["competitiveness"] == 42.0
    assert round(rows["Yes/bid"]["midpoint"], 4) == 0.50
    assert round(rows["Yes/ask"]["spread"], 4) == 0.04


def test_a_market_that_loses_its_reward_pool_keeps_producing_price_rows(monkeypatch, tmp_path):
    """A pool going to zero is a headline finding, not a reason to stop
    observing — the case must not silently end when the subsidy does."""
    now = 2_000_000_000_000
    _one_track(monkeypatch, tmp_path, now)
    _stub_book(monkeypatch, bids=[("0.40", "10")], asks=[("0.60", "10")], rewards=None)

    assert track.snapshot_pools(now) == 2
    row = store.load_lead_tracks(store._day(now)).to_dicts()[0]
    assert row["daily_rate"] == 0.0
    assert row["band_depth"] == 0.0  # no band without a config — not a fake number
    assert round(row["midpoint"], 4) == 0.50  # price still recorded
    assert row["best_bid"] == 0.40 and row["best_ask"] == 0.60


def test_a_delisted_event_is_retried_rather_than_poisoning_the_batch(monkeypatch, tmp_path):
    """Gamma returns [] for a slug that no longer exists. That lead must be
    skipped without a row and without blocking the leads after it."""
    monkeypatch.setattr(store, "LAMINAR_DIR", tmp_path)
    store.append_leads([_lead(msg_id=1, event="dead"), _lead(msg_id=2, event="alive")])
    monkeypatch.setattr(
        track.clob,
        "event_markets",
        lambda slug: [] if slug == "dead" else [_market("m", 1.0)],
    )
    resolved, _ = track.resolve_leads()

    assert resolved == 1
    assert store.load_lead_markets()["msg_id"].unique().to_list() == [2]
    # Nothing recorded for the dead slug, so the next run tries it again.
    assert 1 not in store.load_lead_markets()["msg_id"].to_list()
