"""Intent tests for the shadow fill matcher (`laminar.shadow`).

The headline is V1, the degenerate test the study design makes a precondition
for quoting any result: replicate the whole displayed book with our own orders
and the tape must land entirely on us. It has a known answer, so it catches the
three things that would silently invert or deflate every downstream number —
the tape's side convention, price-priority ordering, and the queue accounting.

The rest guard the properties that make the matcher's output *interpretable*:
that the two queue extremes really bracket, that quoting inside the spread
fills (the case with no print at our price), and that we refuse to match rather
than guess when no fresh book exists.
"""

import polars as pl
import pytest

from laminar import shadow
from laminar.score import Level
from laminar.store import BOOK_SCHEMA, TRADE_SCHEMA

T0 = 1_787_000_000_000  # arbitrary ms epoch inside the observation window


def _book_row(ts, token, side, price, size, mid=0.50):
    return {
        "ts": ts, "token_id": token, "side": side, "price": price, "size": size,
        "midpoint": mid, "max_spread": 4.5, "min_size": 20.0, "daily_rate": 200.0,
    }


def _books(rows):
    return pl.DataFrame(rows, schema=BOOK_SCHEMA)


def _trade(tx, ts, token, side, price, size):
    return {
        "tx_hash": tx, "ts": ts, "condition_id": "cond", "token_id": token,
        "outcome": "Yes", "side": side, "price": price, "size": size, "wallet": "0xW",
    }


def _trades(rows):
    return pl.DataFrame(rows, schema=TRADE_SCHEMA)


# A two-level book on each side, used by most cases below.
#   bids: 0.49 x 100, 0.48 x 200      asks: 0.51 x 100, 0.52 x 200
BOOK_ROWS = [
    _book_row(T0, "tokA", "bid", 0.49, 100.0),
    _book_row(T0, "tokA", "bid", 0.48, 200.0),
    _book_row(T0, "tokA", "ask", 0.51, 100.0),
    _book_row(T0, "tokA", "ask", 0.52, 200.0),
]


def _index(rows=None):
    return shadow.BookIndex(_books(BOOK_ROWS if rows is None else rows))


# ---------------------------------------------------------------- V1


def test_v1_replicating_the_whole_book_absorbs_exactly_the_print():
    """V1, the degenerate case with a known answer.

    If our orders ARE the displayed book, a print has nowhere else to land, so
    our total fill must equal the print size exactly. Any error in the side
    convention, the price ordering, or the queue arithmetic breaks this
    identity — which is the point of asserting it before trusting any yield
    number built on top.
    """
    index = _index()
    bids, asks, _ = index.at("tokA", T0)
    orders = {"tokA": shadow.book_as_orders("tokA", bids, "bid")}
    # 150 shares sold into a bid book of 100 @ 0.49 + 200 @ 0.48
    trades = _trades([_trade("t1", T0 + 1_000, "tokA", "SELL", 0.48, 150.0)])

    fills, counters = shadow.replay(orders, trades, index, shadow.QUEUE_FRONT)

    assert counters["no_book"] == 0
    assert sum(f.size for f in fills) == pytest.approx(150.0)
    # and it lands top-down: 100 at the better price, 50 at the worse one
    by_price = {f.price: f.size for f in fills}
    assert by_price[0.49] == pytest.approx(100.0)
    assert by_price[0.48] == pytest.approx(50.0)


def test_v1_holds_on_the_ask_side_too():
    """Same identity mirrored — asks fill low-price-first, and a convention that
    only happens to work on bids is not a working convention."""
    index = _index()
    _, asks, _ = index.at("tokA", T0)
    orders = {"tokA": shadow.book_as_orders("tokA", asks, "ask")}
    trades = _trades([_trade("t1", T0 + 1_000, "tokA", "BUY", 0.52, 250.0)])

    fills, _ = shadow.replay(orders, trades, index, shadow.QUEUE_FRONT)

    assert sum(f.size for f in fills) == pytest.approx(250.0)
    by_price = {f.price: f.size for f in fills}
    assert by_price[0.51] == pytest.approx(100.0)
    assert by_price[0.52] == pytest.approx(150.0)


def test_v1_caps_at_the_book_when_the_print_is_bigger_than_what_we_saw():
    """Our snapshot can be up to 60s stale and `books` stores only in-band
    levels, so a print CAN exceed the depth we recorded. The identity then
    holds against the observable book, not against the print — silently
    inventing depth to absorb the rest would fabricate fills."""
    index = _index()
    bids, _, _ = index.at("tokA", T0)
    orders = {"tokA": shadow.book_as_orders("tokA", bids, "bid")}
    trades = _trades([_trade("t1", T0 + 1_000, "tokA", "SELL", 0.48, 5_000.0)])

    fills, _ = shadow.replay(orders, trades, index, shadow.QUEUE_FRONT)

    assert sum(f.size for f in fills) == pytest.approx(300.0)  # 100 + 200 displayed


# ---------------------------------------------- side convention


def test_a_print_never_fills_the_untouched_side():
    """A sell into the bids must not fill our ask. This is the failure mode the
    tape's ambiguous BUY/SELL labelling invites, and it inverts every
    adverse-selection reading downstream if it slips through."""
    index = _index()
    orders = {"tokA": [shadow.ShadowOrder("tokA", "ask", 0.51, 500.0)]}
    trades = _trades([_trade("t1", T0 + 1_000, "tokA", "SELL", 0.49, 100.0)])

    fills, _ = shadow.replay(orders, trades, index, shadow.QUEUE_FRONT)

    assert fills == []


def test_the_two_tape_conventions_are_exact_opposites():
    """`tape_is_taker` is a real switch, not a decorative argument. Which
    resting side a print ate is the single assumption that inverts every
    downstream fill, so pin both readings explicitly."""
    assert shadow.consumed_side("BUY", tape_is_taker=True) == "ask"
    assert shadow.consumed_side("BUY", tape_is_taker=False) == "bid"
    assert shadow.consumed_side("SELL", tape_is_taker=True) == "bid"
    assert shadow.consumed_side("SELL", tape_is_taker=False) == "ask"


def test_replay_honours_the_convention_switch():
    """A BUY print at the bid price fills our bid ONLY under the maker-side
    reading; under the taker reading the same print consumes asks and reaches
    nothing we hold. Same tape, same orders, opposite outcome — which is what
    makes settling the convention empirically worth doing.

    (Note the two readings cannot both fill from one print with an uncrossed
    two-sided quote: the price that reaches our bid is out of reach for our ask
    by construction. Hence one print, one order, two readings.)
    """
    index = _index()
    orders = {"tokA": [shadow.ShadowOrder("tokA", "bid", 0.49, 500.0)]}
    trades = _trades([_trade("t1", T0 + 1_000, "tokA", "BUY", 0.49, 50.0)])

    taker, _ = shadow.replay(orders, trades, index, shadow.QUEUE_FRONT, tape_is_taker=True)
    maker, _ = shadow.replay(orders, trades, index, shadow.QUEUE_FRONT, tape_is_taker=False)

    assert taker == []
    assert [f.side for f in maker] == ["bid"]
    assert maker[0].size == pytest.approx(50.0)


# ---------------------------------------------- queue bracketing


def test_front_and_back_bracket_the_fill():
    """The two extremes are the honest interval, so back must never exceed
    front. Reporting a midpoint of these would be inventing queue information
    the public book does not contain."""
    index = _index()
    orders = {"tokA": [shadow.ShadowOrder("tokA", "bid", 0.49, 500.0)]}
    trades = _trades([_trade("t1", T0 + 1_000, "tokA", "SELL", 0.49, 150.0)])

    front, _ = shadow.replay(orders, trades, index, shadow.QUEUE_FRONT)
    back, _ = shadow.replay(orders, trades, index, shadow.QUEUE_BACK)

    front_size = sum(f.size for f in front)
    back_size = sum(f.size for f in back)
    assert front_size == pytest.approx(150.0)  # first in line, takes the lot
    assert back_size == pytest.approx(50.0)  # 100 displayed at 0.49 goes first
    assert back_size <= front_size


def test_back_of_queue_gets_nothing_when_the_level_absorbs_the_print():
    """The expected regime: median print ~20 shares against a level holding
    hundreds. If this ever returned a fill, the pessimistic bound would stop
    being pessimistic."""
    index = _index()
    orders = {"tokA": [shadow.ShadowOrder("tokA", "bid", 0.49, 3_000.0)]}
    trades = _trades([_trade("t1", T0 + 1_000, "tokA", "SELL", 0.49, 20.0)])

    fills, _ = shadow.replay(orders, trades, index, shadow.QUEUE_BACK)

    assert fills == []


# ---------------------------------------------- price priority


def test_quoting_inside_the_spread_fills_with_no_print_at_our_price():
    """The case that matters most and is easiest to get wrong.

    Our bid at 0.50 is liquidity that did not exist; a taker who sold down to
    0.49 would have hit us on the way. Matching only where a print landed would
    under-fill exactly the aggressive quotes the sizing work needs to price.
    """
    index = _index()
    orders = {"tokA": [shadow.ShadowOrder("tokA", "bid", 0.50, 500.0)]}
    trades = _trades([_trade("t1", T0 + 1_000, "tokA", "SELL", 0.49, 80.0)])

    fills, _ = shadow.replay(orders, trades, index, shadow.QUEUE_FRONT)

    assert len(fills) == 1
    assert fills[0].price == 0.50
    assert fills[0].size == pytest.approx(80.0)  # nothing displayed above 0.50


def test_a_bid_below_the_print_price_is_out_of_reach():
    """The taker stopped at 0.49; a bid at 0.48 was never reachable by that
    print and must not fill, or depth would earn rewards it never risked."""
    index = _index()
    orders = {"tokA": [shadow.ShadowOrder("tokA", "bid", 0.48, 500.0)]}
    trades = _trades([_trade("t1", T0 + 1_000, "tokA", "SELL", 0.49, 80.0)])

    fills, _ = shadow.replay(orders, trades, index, shadow.QUEUE_FRONT)

    assert fills == []


def test_depth_above_us_is_consumed_first():
    """Quoting deep is penalised by the book above us, with no special rule —
    `ahead` is the whole mechanism."""
    index = _index()
    orders = {"tokA": [shadow.ShadowOrder("tokA", "bid", 0.48, 500.0)]}
    trades = _trades([_trade("t1", T0 + 1_000, "tokA", "SELL", 0.48, 250.0)])

    front, _ = shadow.replay(orders, trades, index, shadow.QUEUE_FRONT)

    # 100 resting at 0.49 is better-priced and goes first; 150 reaches us
    assert sum(f.size for f in front) == pytest.approx(150.0)


# ---------------------------------------------- refusing to guess


def test_a_print_with_no_fresh_snapshot_is_counted_not_matched():
    """Silently matching against an hours-old book would manufacture fills. The
    counter exists so a fill rate can be quoted alongside how much of the tape
    was actually matchable."""
    index = _index()
    stale_ts = T0 + shadow.MAX_SNAPSHOT_STALENESS_MS + 1
    orders = {"tokA": [shadow.ShadowOrder("tokA", "bid", 0.49, 500.0)]}
    trades = _trades([_trade("t1", stale_ts, "tokA", "SELL", 0.49, 100.0)])

    fills, counters = shadow.replay(orders, trades, index, shadow.QUEUE_FRONT)

    assert fills == []
    assert counters["no_book"] == 1
    assert counters["prints"] == 1


def test_a_print_before_any_snapshot_is_not_matched_backwards():
    """A book recorded after the print says nothing about the book during it."""
    index = _index()
    orders = {"tokA": [shadow.ShadowOrder("tokA", "bid", 0.49, 500.0)]}
    trades = _trades([_trade("t1", T0 - 60_000, "tokA", "SELL", 0.49, 100.0)])

    fills, counters = shadow.replay(orders, trades, index, shadow.QUEUE_FRONT)

    assert fills == []
    assert counters["no_book"] == 1


def test_tokens_we_do_not_quote_are_counted_separately_from_book_gaps():
    """`no_orders` and `no_book` mean different things — one is our choice of
    watchlist, the other is a data hole. Collapsing them would hide the latter."""
    index = _index()
    orders = {"tokA": [shadow.ShadowOrder("tokA", "bid", 0.49, 500.0)]}
    trades = _trades([_trade("t1", T0 + 1_000, "tokB", "SELL", 0.49, 100.0)])

    fills, counters = shadow.replay(orders, trades, index, shadow.QUEUE_FRONT)

    assert fills == []
    assert counters["no_orders"] == 1
    assert counters["no_book"] == 0


# ---------------------------------------------- helpers


def test_size_ahead_rejects_an_unknown_queue():
    """A typo'd queue name must not silently behave like one of the extremes."""
    with pytest.raises(ValueError):
        shadow.size_ahead([Level(0.49, 100.0)], "bid", 0.49, "middle")


def test_tape_side_evidence_separates_lifts_from_hits():
    """The diagnostic that settles the convention on real data: a print at the
    ask can only be a lift, one at the bid can only be a hit."""
    index = _index()
    trades = _trades([
        _trade("t1", T0 + 1_000, "tokA", "BUY", 0.51, 10.0),   # at the ask
        _trade("t2", T0 + 2_000, "tokA", "SELL", 0.49, 10.0),  # at the bid
        _trade("t3", T0 + 3_000, "tokA", "BUY", 0.50, 10.0),   # inside
    ])

    ev = shadow.tape_side_evidence(trades, index)

    assert ev["BUY_at_or_above_ask"] == 1
    assert ev["SELL_at_or_below_bid"] == 1
    assert ev["inside_spread"] == 1
    assert ev["no_book"] == 0
