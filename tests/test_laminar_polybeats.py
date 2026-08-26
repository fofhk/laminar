"""Intent tests for PolyBeats lead parsing.

These assert on what a lead is FOR, not on regex mechanics: a lead is only
usable downstream if we can tell (a) whether a position was actually called,
(b) which pool it points at, and (c) that re-scraping never double-counts a
case. Everything else the parser extracts is a bonus and may legitimately be
absent, because the channel's free tier withholds it.

Fixtures are verbatim message bodies captured from t.me/s/PolyBeats_Bot on
2026-08-18, one per format tier actually observed.
"""

from laminar import polybeats

# Richest tier: market link, wallet, stake, entry and current price.
FULL = """<div class="tgme_widget_message js-widget_message" data-post="PolyBeats_Bot/1749">
<a href="https://polymarket.com/?via=PolyBeats">x</a>
<a href="https://polymarket.com/event/will-stripe-acquire-any-part-of-paypal-in-2026/will-stripe-acquire-any-part-of-paypal-in-2026?via=PolyBeats">m</a>
<a href="https://polymarket.com/profile/0xcc792a28d90f3c5d8fe91e48a184913a65031c2a?via=PolyBeats">p</a>
<div class="tgme_widget_message_text js-message_text">Stripe $53B 报价收购 PayPal，聪明钱预测 Stripe 年内难完成收购公告<br/><br/>在预测市场 Polymarket 上，1 名聪明钱在「Stripe 会在 2026 年收购 PayPal 的任何部分吗？」投入 $650「否」，平均买入概率为 43.3%，目前「是」概率为 61.5%。<br/><br/>该账户与本市场最佳相关板块为科技，板块净盈利 $10.0k。其在该板块共 43 笔已结算交易的胜率为 40/43（93%）。<br/>---------------------------------<br/>让你更早看到未来，关注 @PolyBeats_Bot</div>
<time datetime="2026-08-17T08:24:11+00:00"></time></div>"""

# Middle tier: event link and per-account stats, no stake disclosed.
PARTIAL = """<div class="tgme_widget_message js-widget_message" data-post="PolyBeats_Bot/1751">
<a href="https://polymarket.com/?via=PolyBeats">x</a>
<a href="https://polymarket.com/event/iran-strait-fee-by-dec-31?via=PolyBeats">m</a>
<div class="tgme_widget_message_text js-message_text">伊朗-阿曼接近敲定霍尔木兹航道方案，3 名聪明钱预测伊朗何时公布海峡收费机制<br/><br/>在预测市场 Polymarket 上，3 名聪明钱在伊朗是否在具体时间节点前收取海峡通行费用买入「是」。<br/><br/>账户1 与本市场最佳相关板块为地缘政治，板块净盈利 $77.9k。其在该板块共 214 笔已结算交易的胜率为 131/214（61%）。<br/>账户2 与本市场最佳相关板块为伊朗，板块净盈利 $483k。其在该板块共 543 笔已结算交易的胜率为 358/543（66%）。<br/>账户3 与本市场最佳相关板块为伊朗，板块净盈利 $14.3k。其在该板块共 284 笔已结算交易的胜率为 143/284（50%）。<br/>订阅BlockBeats会员可查看完整预测市场新闻内容</div>
<time datetime="2026-08-17T10:20:29+00:00"></time></div>"""

# Vaguest tier: a position is called but no link — cannot be tied to a pool.
VAGUE = """<div class="tgme_widget_message js-widget_message" data-post="PolyBeats_Bot/1740">
<a href="https://polymarket.com/?via=PolyBeats">x</a>
<div class="tgme_widget_message_text js-message_text">俄乌前线消息密集<br/><br/>在预测市场 Polymarket 上，1 名盈利超 3 百万美元、交易时长超 4 年的聪明钱买入俄乌冲突相关市场。<br/><br/>该账户板块净盈利 $3.2m。其在该板块共 100 笔已结算交易的胜率为 60/100（60%）。</div>
<time datetime="2026-08-14T10:34:00+00:00"></time></div>"""

# No smart money at all — ordinary commentary that must not enter the case set.
NEWS = """<div class="tgme_widget_message js-widget_message" data-post="PolyBeats_Bot/1752">
<a href="https://polymarket.com/event/will-cristiano-ronaldo-announce-his-retirement-in-2026?via=PolyBeats">m</a>
<div class="tgme_widget_message_text js-message_text">C 罗称职业生涯进入最后一年，退役时间尚未确定<br/><br/>在预测市场 Polymarket 上，事件「C 罗是否将在 2026 年宣布退役？」目前「是」概率为 18%。</div>
<time datetime="2026-08-17T10:56:01+00:00"></time></div>"""


def test_a_called_position_is_distinguishable_from_commentary():
    """The case set is positions, not news. If commentary leaked in, every
    later hit-rate statistic would be diluted by markets nobody bet on."""
    assert polybeats.parse_message(FULL)["is_smart_money"] is True
    assert polybeats.parse_message(VAGUE)["is_smart_money"] is True
    assert polybeats.parse_message(NEWS)["is_smart_money"] is False


def test_lead_resolves_to_a_pool_when_the_post_names_one():
    """A lead is only trackable if it points at a market. The event slug is the
    hand-off to the pool tracker; without it the lead is a recorded miss."""
    full = polybeats.parse_message(FULL)
    assert full["event_slug"] == "will-stripe-acquire-any-part-of-paypal-in-2026"
    assert full["market_slug"] == "will-stripe-acquire-any-part-of-paypal-in-2026"

    assert polybeats.parse_message(PARTIAL)["event_slug"] == "iran-strait-fee-by-dec-31"
    # No market path on the event link — event only, still trackable.
    assert polybeats.parse_message(PARTIAL)["market_slug"] == ""

    # Vague posts must not silently inherit the referral link as their market.
    assert polybeats.parse_message(VAGUE)["event_slug"] == ""


def test_position_detail_is_captured_when_disclosed_and_zero_when_withheld():
    """Stake and entry price decide whether a case can be scored at all. The
    free tier withholds them on most posts, so 0.0 must mean 'not disclosed'
    and never be confused with a real zero-size bet."""
    full = polybeats.parse_message(FULL)
    assert full["stake_usd"] == 650.0
    assert full["direction"] == "否"
    assert full["avg_entry_pct"] == 43.3
    assert full["current_pct"] == 61.5

    partial = polybeats.parse_message(PARTIAL)
    assert partial["stake_usd"] == 0.0  # withheld, not zero-size
    assert partial["direction"] == "是"


def test_account_quality_survives_multi_account_posts():
    """PolyBeats sometimes names three accounts of very different quality.
    Collapsing them to one number must keep the best win rate, not an average,
    and must total the sector PnL rather than take the first."""
    partial = polybeats.parse_message(PARTIAL)
    assert partial["n_accounts"] == 3
    assert round(partial["best_win_rate"], 4) == round(358 / 543, 4)
    assert partial["sector_pnl_usd"] == 77_900 + 483_000 + 14_300


def test_k_m_suffixes_are_scaled_not_truncated():
    """The iKnow digest read '$53B' as 53 dollars because its regex stopped at
    the digits. A silently mis-scaled stake would corrupt any size-weighted
    analysis, so suffixes are asserted explicitly."""
    assert polybeats._num("3.2", "m") == 3_200_000
    assert polybeats._num("483", "k") == 483_000
    assert polybeats._num("650") == 650
    assert polybeats._num("1,200") == 1200


def test_boilerplate_is_stripped_but_the_body_is_otherwise_verbatim():
    """The body is the fallback when the channel changes format. It must keep
    the analytic text and drop only the identical subscription pitch."""
    partial = polybeats.parse_message(PARTIAL)
    assert "订阅BlockBeats" not in partial["body"]
    assert "让你更早看到未来" not in polybeats.parse_message(FULL)["body"]
    assert "板块净盈利 $483k" in partial["body"]
    assert partial["headline"].startswith("伊朗-阿曼接近敲定霍尔木兹航道方案")


def test_message_id_is_the_stable_case_identifier():
    """Cases are counted by msg_id. If it drifted between scrapes, the same
    lead would enter the case set twice and skew every rate we later compute."""
    assert polybeats.parse_message(FULL)["msg_id"] == 1749
    assert polybeats.parse_message(FULL)["posted_at"] == "2026-08-17T08:24:11+00:00"
    assert polybeats.parse_message("<div>not a message</div>") is None
