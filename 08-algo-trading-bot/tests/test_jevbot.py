"""Jevbot: every safety rule and every scoring rule has a test that proves it fires."""

from __future__ import annotations

import asyncio
import json
import re
import sys
from collections import deque
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jevbot import config as cfgmod
from jevbot.broker import BrokerError, PaperBroker, floor_step, sign
from jevbot.config import Config
from jevbot.evaluator import Evaluator, trade_return
from jevbot.features import Features, MidHistory, describe, trend_word
from jevbot.feed import SymbolBook, SymbolRules, Trade
from jevbot.policy import decide, round_trip_cost
from jevbot.signals import JevSignal, LocalMLSignal


def feats(vol_1s=0.0002, ask=100.01, bid=99.99, symbol="BTCUSDT", ts=1000.0, **kw) -> Features:
    base = dict(symbol=symbol, ts=ts, mid=(ask + bid) / 2, bid=bid, ask=ask, spread_pct=(ask - bid) / ((ask + bid) / 2),
                ret={10: 0.0, 60: 0.0, 300: 0.0, 900: 0.0}, vol_1s=vol_1s, vol_ratio=1.0,
                imbalance={10: 0.0, 25: 0.0}, flow={60: 0.0, 300: 0.0}, intensity=1.0)
    base.update(kw)
    return Features(**base)


def book(symbol="BTCUSDT") -> SymbolBook:
    b = SymbolBook(symbol, bid=99.99, ask=100.01, bid_qty=1, ask_qty=1)
    b.bids = [(99.99, 1.0), (99.98, 2.0), (99.90, 5.0)]
    b.asks = [(100.01, 1.0), (100.02, 2.0), (100.10, 5.0)]
    b.updated = 1.0
    return b


RULES = {"BTCUSDT": SymbolRules("BTCUSDT", step_size=0.001, min_qty=0.001, tick_size=0.01, min_notional=5.0)}


# ---- exchange plumbing -----------------------------------------------------------

def test_signature_matches_binance_documented_example():
    secret = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
    query = ("symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1"
             "&recvWindow=5000&timestamp=1499827319559")
    assert sign(secret, query) == "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"


def test_floor_step_never_rounds_up():
    assert floor_step(0.3, 0.1) == pytest.approx(0.3)
    assert floor_step(0.12999999, 0.001) == pytest.approx(0.129)
    assert floor_step(1.0005, 0.001) == pytest.approx(1.0)


# ---- features --------------------------------------------------------------------

def test_history_never_looks_forward():
    h = MidHistory()
    for t, m in [(1, 100.0), (2, 101.0), (3, 102.0)]:
        h.add(t, m)
    assert h.at_or_before(2.5) == 101.0
    assert h.at_or_before(0.5) is None
    assert h.ret(1) == pytest.approx(102 / 101 - 1)


def test_history_rejects_double_samples_and_flat_vol_is_not_missing():
    h = MidHistory()
    h.add(10.98, 100.0)
    h.add(10.999, 100.0)     # same second: rejected
    h.add(9.0, 100.0)        # out of order: rejected
    assert len(h.points) == 1
    for t in range(11, 80):
        h.add(float(t), 100.0)
    assert h.vol_1s(60) == pytest.approx(1e-7)   # calm, but still a usable number


def test_jev_state_has_words_not_numbers():
    f = feats(ret={10: 0.001, 60: -0.004, 300: 0.0, 900: 0.02}, flow={60: 0.6, 300: -0.2})
    state = describe(f, 300)
    for section in ("price_trend", "taker_flow"):
        for v in state[section].values():
            assert not re.search(r"\d", v), v
    assert "100" not in json.dumps(state)   # no price leaks into the state
    assert trend_word(3.0) == "rising sharply" and trend_word(0.1) == "flat"


# ---- evaluation ------------------------------------------------------------------

def make_eval(tmp_path, **cfg_kw) -> Evaluator:
    return Evaluator(Config(horizons_s=(60,), trade_horizon_s=60, symbols=("BTCUSDT",), **cfg_kw),
                     tmp_path / "out.jsonl")


def hist_with(points) -> dict:
    h = MidHistory()
    for t, m in points:
        h.add(t, m)
    return {"BTCUSDT": h}


def test_flat_market_trade_loses_exactly_the_costs():
    c = Config(apply_tds=True)
    r = trade_return(c, 100.0, 0.0, 100.0, tds=True)
    assert r == pytest.approx(((1 - 0.001 - 0.01) / 1.001 - 1) * 100)
    assert r < -1.0   # TDS alone makes a flat round trip lose more than 1%


def test_brier_hits_and_ties(tmp_path):
    ev = make_eval(tmp_path)
    ev.record("jev", feats(ts=0.0, ask=100.0, bid=100.0), {60: 0.8})
    ev.record("jev", feats(ts=1.0, ask=100.0, bid=100.0), {60: 0.8})
    assert ev.resolve(hist_with([(0, 100.0), (60, 101.0), (61, 100.0)]), now=100) == 1  # second is a tie
    st = ev.stats[("jev", "BTCUSDT", 60)]
    assert st.n == 1 and st.hits == 1 and st.brier_sum == pytest.approx(0.04)


def test_trade_samples_do_not_overlap(tmp_path):
    ev = make_eval(tmp_path, min_edge_multiple=0.0)
    for t in (0.0, 15.0, 30.0, 60.0, 75.0):
        ev.record("jev", feats(ts=t, ask=100.0, bid=100.0), {60: 0.9})
    pts = [(t, 100.0 + t / 100) for t in range(0, 200)]
    ev.resolve(hist_with(pts), now=500)
    st = ev.stats[("jev", "BTCUSDT", 60)]
    assert st.n == 5
    assert len(st.trades) == 2   # t=0 and t=60; the others overlap an open window


def test_replay_rebuilds_stats(tmp_path):
    ev = make_eval(tmp_path)
    ev.record("ml", feats(ts=0.0, ask=100.0, bid=100.0), {60: 0.7})
    ev.resolve(hist_with([(0, 100.0), (60, 99.0)]), now=100)
    ev2 = make_eval(tmp_path)
    assert ev2.replay() == 1
    assert ev2.stats[("ml", "BTCUSDT", 60)].n == 1


# ---- policy ----------------------------------------------------------------------

def run(cfg, f, p, holding=False, entry_ts=None, last_exit=-1e9, now=10_000.0, p_cal=None, fades=0):
    return decide(cfg, f, p, holding, entry_ts, last_exit, now, equity=1000.0, cash=1000.0,
                  p_cal=p_cal, fades=fades)


def test_hysteresis_holds_between_thresholds():
    c = Config(apply_tds=False)
    assert run(c, feats(), 0.52, holding=True, entry_ts=9_880.0, fades=5).action == "hold"
    assert run(c, feats(), 0.40, holding=True, entry_ts=9_880.0, fades=1).action == "sell"


def test_time_exit_and_cooldown():
    c = Config(apply_tds=False)
    assert run(c, feats(), 0.9, holding=True, entry_ts=10_000.0 - 300).action == "sell"
    assert run(c, feats(vol_1s=0.01), 0.9, last_exit=10_000.0 - 10).reason == "cooldown"


def test_cost_filter_blocks_when_tds_makes_trade_hopeless():
    f = feats(vol_1s=0.0002)   # ~0.35% sigma over 5 min
    assert run(Config(apply_tds=True), f, 0.9).action == "hold"
    assert "costs" in run(Config(apply_tds=True), f, 0.9).reason


def test_entry_respects_risk_and_notional_caps():
    c = Config(apply_tds=False)
    d = run(c, feats(vol_1s=0.002), 0.9, p_cal=0.8)
    assert d.action == "buy" and d.stop < 100.01
    assert (100.01 - d.stop) * d.qty <= 1000 * c.risk_pct + 1e-9
    assert d.qty * 100.01 <= 1000 * c.max_notional_pct + 1e-9


def test_round_trip_cost_includes_tds_toggle():
    assert round_trip_cost(Config(apply_tds=True), 0.0) == pytest.approx(0.012)
    assert round_trip_cost(Config(apply_tds=False), 0.0) == pytest.approx(0.002)


# ---- paper broker ----------------------------------------------------------------

def paper(tmp_path, **kw):
    return PaperBroker(Config(**kw), {"BTCUSDT": book()}, RULES, tmp_path / "state.json")


def test_paper_buy_walks_the_book_and_sell_charges_tds(tmp_path):
    b = paper(tmp_path, apply_tds=True)
    fill = asyncio.run(b.buy("BTCUSDT", 2.0, "id1", stop=95.0))
    assert fill.avg_price == pytest.approx((100.01 + 100.02) / 2)
    out = asyncio.run(b.sell("BTCUSDT", "id2"))
    assert out.avg_price == pytest.approx((99.99 + 99.98) / 2)
    assert out.tds_usdt == pytest.approx(2.0 * out.avg_price * 0.01)
    assert b.state.realized < 0   # crossing the spread, fees and TDS on a flat market


def test_paper_rejects_dust_and_oversize(tmp_path):
    b = paper(tmp_path)
    with pytest.raises(BrokerError):
        asyncio.run(b.buy("BTCUSDT", 0.0004, "id", stop=95.0))    # floors to 0 < min qty
    with pytest.raises(BrokerError):
        asyncio.run(b.buy("BTCUSDT", 50.0, "id", stop=95.0))      # deeper than the visible book


def test_paper_state_survives_restart(tmp_path):
    b = paper(tmp_path)
    asyncio.run(b.buy("BTCUSDT", 1.0, "id", stop=95.0))
    assert paper(tmp_path).position("BTCUSDT") == pytest.approx(1.0)


# ---- signals ---------------------------------------------------------------------

def test_jev_request_uses_noul_per_horizon_and_no_prices():
    c = Config(jev_provider="vercel", jev_api_key="k", horizons_s=(60, 300), trade_horizon_s=300)
    body = JevSignal(c, c.horizons_s).request_body(feats())
    assert body["model"] == "typesafe-ai/jev"
    assert set(body["questions"]) == {"up_60", "up_300"}
    assert all(q["type"] == "noul" for q in body["questions"].values())
    assert "horizon" not in body["state"] and "100" not in json.dumps(body["state"])
    assert c.jev_url == "https://ai-gateway.vercel.sh/typesafe/v1/systemone"


def test_jev_budget_guard_stops_calls():
    c = Config(jev_provider="vercel", jev_api_key="k", jev_daily_budget_usd=0.0)
    j = JevSignal(c, (300,))
    assert asyncio.run(j.predict(feats())) is None
    assert j.stats["skipped_budget"] == 1 and j.stats["calls"] == 0


def test_local_ml_is_silent_until_trained_then_learns():
    ml = LocalMLSignal((60,))
    assert asyncio.run(ml.predict(feats())) is None
    for i in range(400):
        up = i % 2 == 0
        v = [0.0] * 10
        v[6] = 0.8 if up else -0.8       # taker flow decides the outcome
        ml.learn(60, v, up)
    p_up = asyncio.run(ml.predict(feats(flow={60: 0.8, 300: 0.0})))[60]
    p_dn = asyncio.run(ml.predict(feats(flow={60: -0.8, 300: 0.0})))[60]
    assert p_up > 0.7 and p_dn < 0.3


# ---- config interlocks -----------------------------------------------------------

def test_live_needs_confirm_phrase_and_keys():
    with pytest.raises(ValueError):
        Config(mode="live", binance_api_key="a", binance_api_secret="b").validate()
    with pytest.raises(ValueError):
        Config(mode="testnet").validate()
    Config(mode="live", binance_api_key="a", binance_api_secret="b",
           live_confirm=cfgmod.LIVE_CONFIRM_PHRASE).validate()


def test_risk_above_hard_ceiling_rejected():
    with pytest.raises(ValueError):
        Config(risk_pct=0.03).validate()


def test_features_vector_matches_ml_input_width():
    assert len(feats().vector()) == 10


# ---- review fixes (critic pass, 2026-10-07) ----------------------------------------

from jevbot.broker import BinanceBroker  # noqa: E402


class FakeExchange(BinanceBroker):
    """In-memory Binance: balances, market fills at the book, stop orders by client id."""

    def __init__(self, tmp_path, user_btc=1.0, stop_rejects=False):
        cfg = Config(mode="testnet", binance_api_key="k", binance_api_secret="s", apply_tds=False)
        super().__init__(cfg, {"BTCUSDT": book()}, RULES, tmp_path / "live.json")
        self.bal = {"USDT": 10_000.0, "BTC": user_btc}
        self.orders: dict[str, dict] = {}
        self.stop_rejects = stop_rejects
        self.market_sells = 0

    async def _signed(self, method, path, params, _retry=True):
        await asyncio.sleep(0)   # yield, like a real network call, so races can interleave
        if path == "/api/v3/account":
            return {"balances": [{"asset": a, "free": str(v), "locked": "0"} for a, v in self.bal.items()]}
        if path == "/api/v3/order" and method == "GET":
            return self.orders[params["origClientOrderId"]]
        if path == "/api/v3/order" and method == "DELETE":
            self.orders[params["origClientOrderId"]]["status"] = "CANCELED"
            return {}
        if path == "/api/v3/order" and params["type"] == "MARKET":
            q = float(params["quantity"])
            px = 100.01 if params["side"] == "BUY" else 99.99
            if params["side"] == "BUY":
                self.bal["BTC"] += q; self.bal["USDT"] -= q * px
            else:
                self.market_sells += 1
                self.bal["BTC"] -= q; self.bal["USDT"] += q * px
            return {"executedQty": str(q), "cummulativeQuoteQty": str(q * px), "fills": []}
        if path == "/api/v3/order" and params["type"] == "STOP_LOSS_LIMIT":
            if self.stop_rejects:
                raise BrokerError("Binance 400: Stop price would trigger immediately")
            self.orders[params["newClientOrderId"]] = {"status": "NEW", "executedQty": "0",
                                                       "cummulativeQuoteQty": "0", "qty": float(params["quantity"])}
            return {}
        raise AssertionError(f"unexpected call {method} {path}")

    def fire_stop(self):
        sid = self.own["stops"]["BTCUSDT"]["id"]
        o = self.orders[sid]
        self.bal["BTC"] -= o["qty"]; self.bal["USDT"] += o["qty"] * 95
        o.update(status="FILLED", executedQty=str(o["qty"]), cummulativeQuoteQty=str(o["qty"] * 95))


def test_stop_fill_never_leads_to_selling_user_coins(tmp_path):
    ex = FakeExchange(tmp_path, user_btc=1.0)
    asyncio.run(ex.buy("BTCUSDT", 0.1, "b1", stop=95.0))
    assert ex.position("BTCUSDT") == pytest.approx(0.1)
    ex.fire_stop()
    asyncio.run(ex.refresh())
    assert ex.position("BTCUSDT") == 0.0
    assert ex.events and ex.events[0]["reason"] == "exchange stop filled"
    assert ex.bal["BTC"] == pytest.approx(1.0)   # the user's own coin is untouched


def test_failed_stop_placement_flattens_instead_of_leaving_naked_position(tmp_path):
    ex = FakeExchange(tmp_path, user_btc=0.5, stop_rejects=True)
    with pytest.raises(BrokerError):
        asyncio.run(ex.buy("BTCUSDT", 0.1, "b1", stop=95.0))
    assert ex.position("BTCUSDT") == 0.0
    assert ex.bal["BTC"] == pytest.approx(0.5)


def test_concurrent_exits_sell_once(tmp_path, monkeypatch):
    from jevbot import engine as eng
    monkeypatch.setattr(eng, "DATA_DIR", tmp_path)
    e = eng.Engine(Config(symbols=("BTCUSDT",)))
    ex = FakeExchange(tmp_path, user_btc=1.0)
    e.broker = ex

    async def go():
        await ex.buy("BTCUSDT", 0.1, "b1", stop=95.0)
        await asyncio.gather(e._exit("BTCUSDT", "a"), e._exit("BTCUSDT", "b"))
    asyncio.run(go())
    assert ex.market_sells == 1
    assert ex.bal["BTC"] == pytest.approx(1.0)


def test_live_cannot_disable_tds_or_edge_requirement():
    base = dict(mode="live", binance_api_key="a", binance_api_secret="b", live_confirm=cfgmod.LIVE_CONFIRM_PHRASE)
    with pytest.raises(ValueError):
        Config(apply_tds=False, **base).validate()
    with pytest.raises(ValueError):
        Config(require_edge_for_live=False, **base).validate()


def test_flat_outcome_still_counts_as_a_losing_trade(tmp_path):
    ev = make_eval(tmp_path, min_edge_multiple=0.0)
    ev.record("jev", feats(ts=0.0, ask=100.0, bid=100.0), {60: 0.9})
    ev.resolve(hist_with([(0, 100.0), (60, 100.0)]), now=100)
    st = ev.stats[("jev", "BTCUSDT", 60)]
    assert st.n == 0                    # not scored as a forecast
    assert len(st.trades) == 1 and st.trades[0] < 0   # but the trade's costs are counted


def test_replay_drops_trades_recorded_under_other_cost_rules(tmp_path):
    ev = make_eval(tmp_path, min_edge_multiple=0.0, apply_tds=False)
    ev.record("jev", feats(ts=0.0, ask=100.0, bid=100.0), {60: 0.9})
    ev.resolve(hist_with([(0, 100.0), (60, 102.0)]), now=100)
    ev2 = make_eval(tmp_path, min_edge_multiple=0.0, apply_tds=True)
    ev2.replay()
    st = ev2.stats[("jev", "BTCUSDT", 60)]
    assert st.n == 1 and st.trades == []


# ---- loophole guards (2026-10-07, after the first paper session) -------------------

from jevbot.guards import Guards  # noqa: E402
from jevbot.policy import expected_value  # noqa: E402


def guards(tmp_path, **kw):
    return Guards(Config(**kw), tmp_path / "g.json", tmp_path / "KILL_SWITCH")


def ok_entry(g, now=1000.0, equity=1000.0, open_n=0.0, new_n=100.0, spread=0.0001, vol=1.0, slip=0.0):
    return g.check_entry(now, equity, open_n, new_n, spread, vol, slip)


def test_fade_exit_needs_min_hold_and_confirmation():
    c = Config(apply_tds=False, min_hold_s=60, exit_confirm=2)
    early = run(c, feats(), 0.20, holding=True, entry_ts=10_000.0 - 15)
    assert early.action == "hold" and "min" in early.reason          # the 15-second exit from session 1
    first = decide(c, feats(), 0.20, True, 10_000.0 - 120, -1e9, 10_000.0, 1000, 1000, fades=0)
    assert first.action == "hold" and "1/2" in first.reason
    second = decide(c, feats(), 0.20, True, 10_000.0 - 120, -1e9, 10_000.0, 1000, 1000, fades=1)
    assert second.action == "sell"


def test_stop_and_time_exits_do_not_wait_for_confirmation():
    c = Config(apply_tds=False, min_hold_s=600)
    assert run(c, feats(), 0.9, holding=True, entry_ts=10_000.0 - 300).action == "sell"


def test_unproven_confidence_cannot_buy_its_way_in():
    c = Config(apply_tds=False, min_edge_multiple=0.0)
    f = feats(vol_1s=0.002)
    assert run(c, f, 0.96).action == "hold"                           # p_cal defaults to 0.5
    d = decide(c, f, 0.96, False, None, -1e9, 10_000.0, 1000, 1000, p_cal=0.70)
    assert d.action == "buy"
    assert expected_value(c, f, 0.70) > 0 > expected_value(c, f, 0.5)


def test_calibration_uses_measured_hit_rate(tmp_path):
    ev = make_eval(tmp_path)
    assert ev.calibrate("momentum", "BTCUSDT", 60, 0.95) == 0.5
    for i in range(70):   # says 95% every time, right only 40% of the time
        ev.record("momentum", feats(ts=float(i * 100), ask=100.0, bid=100.0), {60: 0.95})
    pts = [(i * 100 + dt, 100.0 + (0.5 if (i % 5) < 2 else -0.5) * (dt > 0)) for i in range(70) for dt in (0, 60)]
    ev.resolve(hist_with(sorted(pts)), now=1e9)
    p = ev.calibrate("momentum", "BTCUSDT", 60, 0.95)
    assert 0.40 < p < 0.46            # overconfidence exposed: 95% claimed, ~43% earned


def test_loss_streak_pauses_and_survives_restart(tmp_path):
    g = guards(tmp_path, max_loss_streak=3, pause_after_streak_s=3600)
    for i in range(3):
        g.on_entry("BTCUSDT", 100.0, 0.1)
        g.on_exit("BTCUSDT", 99.0, 0.1, now=1000.0)
    assert "paused" in ok_entry(guards(tmp_path, max_loss_streak=3), now=2000.0)
    assert ok_entry(guards(tmp_path, max_loss_streak=3), now=1000.0 + 3601) is None


def test_drawdown_from_peak_trips_kill_switch(tmp_path):
    g = guards(tmp_path, max_drawdown_pct=0.05)
    g.mark_equity(1100.0)
    assert g.mark_equity(1050.0) is None
    assert "kill switch" in g.mark_equity(1040.0)
    assert (tmp_path / "KILL_SWITCH").exists()


def test_overtrading_exposure_spread_shock_and_slippage_guards(tmp_path):
    g = guards(tmp_path, max_trades_per_day=2)
    g.on_entry("BTCUSDT", 1, 0); g.on_entry("ETHUSDT", 1, 0)
    assert "daily trade limit" in ok_entry(g)
    g2 = guards(tmp_path / "x") if (tmp_path / "x").mkdir() is None else None
    assert "exposure" in ok_entry(g2, open_n=450.0, new_n=100.0)
    assert "spread" in ok_entry(g2, spread=0.002)
    assert "volatility" in ok_entry(g2, vol=4.0)
    assert "slip" in ok_entry(g2, slip=0.001)
    assert ok_entry(g2) is None


def test_closed_trade_pnl_counts_fees(tmp_path):
    g = guards(tmp_path)
    g.on_entry("ETHUSDT", 0.0967 * 2580.20 + 0.2495, 0.2495)
    pnl = g.on_exit("ETHUSDT", 0.0967 * 2579.81 - 0.2495, 0.2495)
    assert pnl == pytest.approx(-0.5367, abs=1e-3)                 # session 1's ETH trade, to the cent
    assert g.summary(999.0)["closed"] == 1 and g.s.loss_streak == 1


def test_live_cannot_disable_positive_ev():
    with pytest.raises(ValueError):
        Config(mode="live", binance_api_key="a", binance_api_secret="b",
               live_confirm=cfgmod.LIVE_CONFIRM_PHRASE, require_positive_ev=False).validate()


# ---- copy-trader signal and ensemble -----------------------------------------------

from jevbot import copytrade as ct  # noqa: E402
from jevbot.signals import EnsembleSignal  # noqa: E402


def lb_row(addr, value, month_roi, all_roi, month_vlm):
    return {"ethAddress": addr, "accountValue": str(value), "windowPerformances": [
        ["day", {"pnl": "0", "roi": "0", "vlm": "0"}], ["week", {"pnl": "0", "roi": "0", "vlm": "0"}],
        ["month", {"pnl": "1", "roi": str(month_roi), "vlm": str(month_vlm)}],
        ["allTime", {"pnl": "1", "roi": str(all_roi), "vlm": "0"}]]}


def test_wallet_selection_excludes_market_makers_small_and_one_hit_wonders():
    rows = [lb_row("good", 500_000, 0.30, 1.2, 5e6),
            lb_row("mm", 60e6, 0.50, 0.04, 40e9),        # 650x turnover: liquidity provider
            lb_row("small", 50_000, 0.90, 2.0, 1e5),
            lb_row("lucky", 900_000, 0.80, -0.5, 1e6),     # great month, loses all-time
            lb_row("ok", 300_000, 0.10, 0.3, 1e6)]
    assert ct.select_wallets(rows) == ["good", "ok"]       # ranked by month ROI


def test_positioning_is_notional_weighted_and_damped():
    states = {"a": {"assetPositions": [{"position": {"coin": "BTC", "szi": "2", "positionValue": "300000"}}]},
              "b": {"assetPositions": [{"position": {"coin": "BTC", "szi": "-1", "positionValue": "100000"}},
                                       {"position": {"coin": "ETH", "szi": "5", "positionValue": "20000"}}]}}
    net, holders = ct.positioning(states, "BTC")
    assert net == pytest.approx(0.5) and holders == 2
    assert ct.to_probability(net, holders) == pytest.approx(0.5 + 0.4 * 0.5 * 0.4)   # 2 holders = weak evidence
    assert ct.describe(net, holders) == "a few top traders hold it, mostly long"


def test_ensemble_ignores_unskilled_signals_and_weights_skilled_ones(tmp_path):
    ev = make_eval(tmp_path)
    ens = EnsembleSignal(ev, (60,))
    assert ens.combine("BTCUSDT", {"momentum": {60: 0.9}}) is None    # no record: silent
    # momentum: right 80% of the time at 0.9; random-ish: no skill
    for i in range(100):
        up = i % 5 != 0
        ev._apply("momentum", "BTCUSDT", 60, float(i), 0.9 if up else 0.85, up, None, None)
        ev._apply("copy", "BTCUSDT", 60, float(i), 0.9, i % 2 == 0, None, None)
    out = ens.combine("BTCUSDT", {"momentum": {60: 0.9}, "copy": {60: 0.9}, "random": {60: 0.99}})
    assert out is not None and 0.6 < out[60] < 0.9
    assert "momentum" in ens.last_weights["BTCUSDT"] and "copy" not in ens.last_weights["BTCUSDT"]


def test_jev_gets_top_trader_context_as_words():
    c = Config(jev_provider="vercel", jev_api_key="k", horizons_s=(300,), trade_horizon_s=300)
    body = JevSignal(c, (300,)).request_body(feats(), {"top_traders": "many top traders hold it, mostly long"})
    assert body["state"]["top_traders"].endswith("mostly long")
    assert "`top_traders`" in body["questions"]["up_300"]["instructions"]


# ---- news signal, edge decay, performance stats ------------------------------------

from jevbot import news as nw  # noqa: E402

RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>SEC approves spot ETF</title><link>https://x/1</link><pubDate>Wed, 07 Oct 2026 10:00:00 +0000</pubDate></item>
<item><title>Undated item</title><link>https://x/2</link></item></channel></rss>"""
ATOM_XML = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Exchange hacked</title><link href="https://y/1"/><updated>2026-10-07T09:00:00Z</updated></entry></feed>"""


def test_parse_rss_and_atom():
    now = 1_791_400_000.0
    items = nw.parse_feed(RSS, "T", now=now)
    from datetime import datetime as _dt, timezone as _tz
    expect = _dt(2026, 10, 7, 10, 0, tzinfo=_tz.utc).timestamp()
    assert items[0][0] == "SEC approves spot ETF" and items[0][2] == pytest.approx(expect)
    assert items[1][2] == now                                        # undated -> first seen
    atom = nw.parse_feed(ATOM_XML, "A", now=now)
    assert atom[0][:2] == ("Exchange hacked", "https://y/1")


def test_news_tone_is_relevance_weighted_and_decays():
    now = 10_000.0
    hs = [nw.Headline("btc good", "s", now - 60, {"btc": 0.9, "market": 0.1, "good": 0.9, "bad": 0.05}),
          nw.Headline("sol bad", "s", now - 60, {"btc": 0.0, "market": 0.1, "good": 0.0, "bad": 0.9}),  # irrelevant to BTC
          nw.Headline("old bad", "s", now - 7300, {"btc": 0.9, "market": 0.9, "good": 0.0, "bad": 1.0})]  # outside window
    tone, w, n = nw.aggregate(hs, "BTCUSDT", now)
    assert n == 1 and tone == pytest.approx(0.85)
    fresh = nw.aggregate([nw.Headline("t", "s", now, {"market": 1, "good": 1, "bad": 0})], "BTCUSDT", now)[1]
    aged = nw.aggregate([nw.Headline("t", "s", now - 1800, {"market": 1, "good": 1, "bad": 0})], "BTCUSDT", now)[1]
    assert aged == pytest.approx(fresh / 2)                           # 30-minute half-life
    assert nw.tone_words(0.85, 1).startswith("mostly good news")


def test_news_signal_silent_without_enough_relevant_news():
    s = nw.NewsSignal(("BTCUSDT",), jev=None, horizons=(300,))
    assert asyncio.run(s.predict(feats())) is None
    assert set(s.questions) == {"btc", "market", "good", "bad"}
    assert all(q["type"] == "noul" for q in s.questions.values())


def test_jev_account_errors_back_off_instead_of_spamming():
    class Resp:
        status = 403
        headers = {}
        async def text(self): return "requires a valid credit card"
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class Sess:
        calls = 0
        def post(self, *a, **k):
            Sess.calls += 1
            return Resp()

    c = Config(jev_provider="vercel", jev_api_key="k")
    j = JevSignal(c, (300,))
    j.session = Sess()
    assert asyncio.run(j.ask({}, {})) is None
    assert asyncio.run(j.ask({}, {})) is None                        # paused: no second HTTP call
    assert Sess.calls == 1 and "credit card" in j.stats["last_error"]


def test_decayed_edge_gets_benched(tmp_path):
    ev = make_eval(tmp_path)
    for i in range(300):                    # long good record...
        up = i % 4 != 0
        ev._apply("momentum", "BTCUSDT", 60, float(i), 0.8 if up else 0.3, up, None, None)
    assert ev.usable("momentum", "BTCUSDT", 60)
    for i in range(200):                    # ...then it starts getting it backwards
        up = i % 2 == 0
        ev._apply("momentum", "BTCUSDT", 60, 300.0 + i, 0.2 if up else 0.8, up, None, None)
    assert ev.skill("momentum", "BTCUSDT", 60)[0] > -1                # lifetime number still looks okay-ish
    assert not ev.usable("momentum", "BTCUSDT", 60)                    # but recent skill benches it


def test_performance_stats(tmp_path):
    g = guards(tmp_path)
    for cost, proceeds in ((100, 103), (100, 99), (100, 98)):
        g.on_entry("BTCUSDT", cost, 0.1)
        g.on_exit("BTCUSDT", proceeds, 0.1, now=0)
    g.mark_equity(1000); g.mark_equity(980)
    s = g.summary(980)
    assert s["closed"] == 3 and s["win_rate"] == pytest.approx(0.333, abs=1e-3)
    assert s["expectancy"] == pytest.approx(0.0) and s["profit_factor"] == pytest.approx(1.0)
    assert s["avg_win"] == 3 and s["avg_loss"] == 1.5 and s["max_drawdown_pct"] == pytest.approx(2.0)


# ---- Vercel credit guard: Jev must never quietly spend real money -----------------

class _CreditSess:
    def __init__(self, seq):
        self.seq = list(seq)

    def get(self, *a, **k):
        body = self.seq.pop(0)

        class R:
            status = 200
            async def json(self_inner): return body
            async def __aenter__(self_inner): return self_inner
            async def __aexit__(self_inner, *x): return False
        return R()


def _jev_with(tmp_path, monkeypatch, seq):
    monkeypatch.setattr(Config, "jev_paused_file", property(lambda self: tmp_path / "JEV_PAUSED"))
    j = JevSignal(Config(jev_provider="vercel", jev_api_key="k"), (300,))
    j.session = _CreditSess(seq)
    return j


def test_credit_guard_pauses_when_free_credit_runs_low(tmp_path, monkeypatch):
    j = _jev_with(tmp_path, monkeypatch, [{"balance": "5.00", "total_used": "0"},
                                          {"balance": "0.20", "total_used": "4.80"}])
    asyncio.run(j.check_credits())
    assert j.credit_guard is None and j._budget_ok()
    asyncio.run(j.check_credits())
    assert "credit down" in j.credit_guard and not j._budget_ok()
    assert (tmp_path / "JEV_PAUSED").exists()


def test_credit_guard_pauses_when_usage_grows_with_zero_balance(tmp_path, monkeypatch):
    j = _jev_with(tmp_path, monkeypatch, [{"balance": "0", "total_used": "0"},
                                          {"balance": "0", "total_used": "0.01"}])
    asyncio.run(j.check_credits())
    assert j.credit_guard is None
    asyncio.run(j.check_credits())
    assert "billing your card" in j.credit_guard


def test_credit_pause_survives_restart(tmp_path, monkeypatch):
    (tmp_path / "JEV_PAUSED").write_text("paused earlier\n", encoding="utf-8")
    j = _jev_with(tmp_path, monkeypatch, [{"balance": "4", "total_used": "1"}])
    asyncio.run(j.check_credits())
    assert j.credit_guard == "paused earlier" and not j._budget_ok()
