"""The loop: sample -> features -> all signals -> score -> policy -> risk gate -> broker.

Two clocks. Every second: sample mids, resolve matured predictions, check paper stops
and time exits. Every DECISION_INTERVAL_S: ask every signal, trade on the primary one.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

from core.risk_gate import EquityQuote, OrderIntent, RiskLimits, validate_order

from . import features as feat
from .broker import BinanceBroker, BrokerError, PaperBroker
from .config import DATA_DIR, ROOT, Config
from .evaluator import Evaluator
from .feed import BinanceFeed
from .guards import Guards
from .policy import decide
from .allocator_live import LiveAllocator
from .copytrade import CopyTradeSignal
from .news import NewsSignal
from .signals import EnsembleSignal, JevSignal, LocalMLSignal, MomentumSignal, RandomSignal

log = logging.getLogger("jevbot.engine")


class Engine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.feed = BinanceFeed(cfg.symbols)
        self.hist = {s: feat.MidHistory() for s in cfg.symbols}
        self.ml = LocalMLSignal(cfg.horizons_s)
        self.signals = [self.ml, MomentumSignal(cfg.horizons_s), RandomSignal(cfg.horizons_s)]
        self.jev: JevSignal | None = None
        if cfg.jev_enabled:
            self.jev = JevSignal(cfg, cfg.horizons_s)
            self.signals.insert(0, self.jev)
        self.copy: CopyTradeSignal | None = None
        if cfg.copy_enabled:
            self.copy = CopyTradeSignal(cfg.horizons_s)
            self.signals.append(self.copy)
        self.news: NewsSignal | None = None
        if self.jev:   # headlines are judged by Jev; without it there is no news signal
            self.news = NewsSignal(cfg.symbols, self.jev, cfg.horizons_s)
            self.signals.append(self.news)
        self.evaluator = Evaluator(cfg, DATA_DIR / "outcomes.jsonl", on_label=self.ml.learn)
        self.ensemble = EnsembleSignal(self.evaluator, cfg.horizons_s)
        self.allocs: list[LiveAllocator] = []
        if cfg.alloc_enabled:
            for syms in [cfg.alloc_symbols] + [tuple(b.split(",")) for b in cfg.alloc_extra_books]:
                self.allocs.append(LiveAllocator(tuple(s.strip() for s in syms), cfg.alloc_capital, DATA_DIR,
                                                 self.feed.books, self.copy, self.news, lot_method=cfg.alloc_lot_method))
        self.broker = None
        self.limits = RiskLimits(risk_pct=cfg.risk_pct, daily_loss_limit_pct=cfg.daily_loss_limit_pct,
                                 kill_switch_path=ROOT / "KILL_SWITCH")
        self.first_seen: dict[str, float] = {}
        self.fades: dict[str, int] = {}
        self.guards = Guards(cfg, DATA_DIR / "guards.json", ROOT / "KILL_SWITCH")
        self.locks = {s: asyncio.Lock() for s in cfg.symbols}
        self.last_exit: dict[str, float] = {}
        self.latest: dict[str, dict] = {}
        self.trades: deque = deque(maxlen=200)
        self.subscribers: set[asyncio.Queue] = set()
        self.started = time.time()
        self.equity_value = 0.0

    # -- audit and dashboard plumbing ---------------------------------------------
    def audit(self, rec: dict) -> None:
        with (DATA_DIR / "audit.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str, separators=(",", ":")) + "\n")

    def publish(self, kind: str, data: dict) -> None:
        msg = json.dumps({"kind": kind, "data": data}, default=str)
        for q in list(self.subscribers):
            if q.qsize() < 100:
                q.put_nowait(msg)

    def snapshot(self) -> dict:
        return {
            "mode": self.cfg.mode, "symbols": list(self.cfg.symbols), "uptime_s": round(time.time() - self.started),
            "trade_horizon_s": self.cfg.trade_horizon_s, "horizons_s": list(self.cfg.horizons_s),
            "apply_tds": self.cfg.apply_tds, "entry_prob": self.cfg.entry_prob, "exit_prob": self.cfg.exit_prob,
            "feed": {"connected": self.feed.connected, "reconnects": self.feed.reconnects,
                     "age_s": {s: round(b.age(), 2) for s, b in self.feed.books.items()}},
            "jev": self.jev.stats | {"spend_today_usd": round(self.jev.spend_today, 5),
                                     "budget_usd": self.cfg.jev_daily_budget_usd,
                                     "credit": self.jev.credit, "credit_guard": self.jev.credit_guard} if self.jev else None,
            "ml_samples": self.ml.seen,
            "guards": self.guards.summary(self.equity_value),
            "copy": None if not self.copy else {"status": self.copy.status, "wallets": len(self.copy.wallets)},
            "ensemble_weights": self.ensemble.last_weights,
            "alloc": [a.snapshot() for a in self.allocs],
            "news": None if not self.news else {"status": self.news.status, "recent": self.news.recent(),
                                                "tone": {s: self.news.words(s) for s in self.cfg.symbols}},
            "equity": round(self.equity_value, 2),
            "broker": self.broker.summary() if self.broker else {},
            "kill_switch": self.limits.kill_switch_path.exists(),
            "latest": self.latest, "trades": list(self.trades), "scores": self.evaluator.rows(),
        }

    # -- startup ------------------------------------------------------------------
    async def run(self) -> None:
        from .dashboard import serve
        async with aiohttp.ClientSession() as http:
            await self.feed.load_rules(http)
            for sym in self.cfg.symbols:
                for ts, px in await self.feed.history_1s(http, sym, feat.HISTORY_S):
                    self.hist[sym].add(ts, px)
        replayed = self.evaluator.replay()
        log.info("replayed %d scored outcomes from earlier runs", replayed)

        if self.cfg.mode == "paper":
            self.broker = PaperBroker(self.cfg, self.feed.books, self.feed.rules, DATA_DIR / "paper_state.json")
        else:
            self.broker = BinanceBroker(self.cfg, self.feed.books, self.feed.rules,
                                        DATA_DIR / f"{self.cfg.mode}_state.json")
        await self.broker.start()
        await self.broker.refresh()
        for sym in self.cfg.symbols:
            if self.broker.position(sym) > 0:
                log.info("resuming open %s position (entered %s)", sym, self.broker.entry_ts(sym))
        if self.jev:
            await self.jev.start()
        runner = await serve(self, self.cfg.dashboard_port)
        log.info("dashboard: http://127.0.0.1:%d", self.cfg.dashboard_port)
        tasks = [asyncio.create_task(self.feed.run()), asyncio.create_task(self._sampler()),
                 asyncio.create_task(self._decider())]
        if self.copy:
            tasks.append(asyncio.create_task(self.copy.run()))
        if self.news:
            tasks.append(asyncio.create_task(self.news.run()))
        if self.jev:
            tasks.append(asyncio.create_task(self._credit_watch()))
        for a in self.allocs:
            tasks.append(asyncio.create_task(a.run()))
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()
            self.feed.stop()
            await self.broker.close()
            if self.jev:
                await self.jev.close()
            await runner.cleanup()

    async def _credit_watch(self) -> None:
        while True:
            await self.jev.check_credits()
            await asyncio.sleep(600)

    # -- 1 s clock ----------------------------------------------------------------
    async def _sampler(self) -> None:
        tick = 0
        while True:
            # Wake just after the next whole second. Sleeping "until the boundary" can
            # wake a hair early and fire twice in one second.
            await asyncio.sleep(1.0 - (time.time() % 1.0) + 0.01)
            tick += 1
            try:
                await self._sample(time.time(), reconcile=tick % 10 == 0)
            except Exception:  # one bad second must never stop the clock
                log.exception("sampler error")

    async def _sample(self, now: float, reconcile: bool) -> None:
        for sym, book in self.feed.books.items():
            if book.ready() and book.age() <= self.cfg.max_feed_age_s:
                self.hist[sym].add(now, book.mid)
        if self.evaluator.resolve(self.hist, now):
            self.publish("scores", {"scores": self.evaluator.rows(), "ml_samples": self.ml.seen})
        if reconcile and self.cfg.mode != "paper":
            try:
                await self.broker.refresh()   # picks up exchange-side stop fills
            except BrokerError as exc:
                log.warning("reconcile failed: %s", exc)
            while self.broker.events:
                rec = self.broker.events.pop(0)
                self.last_exit[rec["symbol"]] = rec["ts"]
                rec["pnl"] = self.guards.on_exit(rec["symbol"], rec["qty"] * rec["price"] - rec["tds"], 0.0)
                self.trades.appendleft(rec)
                self.audit({"event": "fill", **rec})
                self.publish("trade", rec)
        for sym in self.cfg.symbols:
            if self.broker.position(sym) > 0:
                if self.broker.stop_hit(sym):
                    await self._exit(sym, "stop-loss hit")
                elif now - self._entry_ts(sym, now) >= self.cfg.trade_horizon_s:
                    await self._exit(sym, "time exit: horizon reached")

    def _entry_ts(self, sym: str, now: float) -> float:
        """Entry time from the broker's persisted state. A position with no record (an
        old state file) starts its clock when first seen, so the time exit stays reachable."""
        ts = self.broker.entry_ts(sym)
        return ts if ts is not None else self.first_seen.setdefault(sym, now)

    # -- decision clock -----------------------------------------------------------
    async def _decider(self) -> None:
        await asyncio.sleep(3)  # let the socket fill the books first
        while True:
            t0 = time.monotonic()
            results = await asyncio.gather(*(self._decide_symbol(s) for s in self.cfg.symbols),
                                           return_exceptions=True)
            for sym, r in zip(self.cfg.symbols, results):
                if isinstance(r, Exception):   # a REST blip on one symbol must not end the process
                    log.error("decision failed on %s: %r", sym, r)
                    self.latest[sym] = {"status": f"error: {r!r}"[:200]}
            self.publish("state", self.snapshot())
            await asyncio.sleep(max(0.5, self.cfg.decision_interval_s - (time.monotonic() - t0)))

    def _primary(self, sym: str) -> str | None:
        if self.cfg.primary_signal != "auto":
            return self.cfg.primary_signal
        return self.evaluator.best_signal(sym, self.cfg.trade_horizon_s, require_edge=self.cfg.mode == "live")

    async def _decide_symbol(self, sym: str) -> None:
        book = self.feed.books[sym]
        if book.age() > self.cfg.max_feed_age_s:
            self.latest[sym] = {"status": f"feed stale ({book.age():.1f}s)"}
            return
        f = feat.compute(book, self.hist[sym])
        if f is None:
            self.latest[sym] = {"status": "warming up"}
            return
        self.evaluator.record_features(f)
        ctx = {}
        if self.copy and (w := self.copy.words(sym)):
            ctx["top_traders"] = w
        if self.news:
            ctx["recent_news"] = self.news.words(sym)
        ctx = ctx or None
        results = await asyncio.gather(*((s.predict(f, ctx) if s is self.jev else s.predict(f)) for s in self.signals),
                                       return_exceptions=True)
        probs: dict[str, dict[int, float]] = {}
        for s, r in zip(self.signals, results):
            if isinstance(r, Exception):
                log.warning("%s failed: %r", s.name, r)
            elif r:
                probs[s.name] = r
                self.evaluator.record(s.name, f, r)
        ens = self.ensemble.combine(sym, probs)
        if ens:
            probs["ensemble"] = ens
            self.evaluator.record("ensemble", f, ens)

        primary = self._primary(sym)
        p = probs.get(primary, {}).get(self.cfg.trade_horizon_s) if primary else None
        holding = self.broker.position(sym) > 0
        equity, _ = await self.broker.equity()
        self.equity_value = equity
        tripped = self.guards.mark_equity(equity)
        if tripped:
            log.error(tripped)
        cash = self.broker.state.cash if isinstance(self.broker, PaperBroker) else self.broker.balances.get("USDT", 0.0)
        p_cal = self.evaluator.calibrate(primary, sym, self.cfg.trade_horizon_s, p) if p is not None else None
        prior_fades = self.fades.get(sym, 0)
        d = decide(self.cfg, f, p, holding, self._entry_ts(sym, time.time()) if holding else None,
                   self.last_exit.get(sym, 0.0), time.time(), equity, cash, p_cal=p_cal, fades=prior_fades)
        faded = holding and p is not None and p <= self.cfg.exit_prob and d.action == "hold"
        self.fades[sym] = prior_fades + 1 if faded else 0
        if d.action == "buy":
            open_notional = sum(self.broker.position(s) * self.feed.books[s].bid for s in self.cfg.symbols)
            veto = self.guards.check_entry(time.time(), equity, open_notional, d.qty * f.ask, f.spread_pct,
                                           f.vol_ratio, self._slippage(sym, d.qty))
            if veto:
                d = type(d)("hold", f"guard: {veto}")
        if self.cfg.mode == "live" and d.action == "buy" and self.cfg.require_edge_for_live \
                and not (primary and self.evaluator.has_edge(primary, sym, self.cfg.trade_horizon_s)):
            d = type(d)("hold", "live entry blocked: primary signal has no proven edge yet")

        self.latest[sym] = {
            "status": "ok", "mid": f.mid, "spread_bps": round(f.spread_pct * 1e4, 2),
            "words": feat.describe(f, self.cfg.trade_horizon_s),
            "probs": {k: {str(h): round(v, 3) for h, v in r.items()} for k, r in probs.items()},
            "primary": primary, "p_up": p, "p_cal": None if p_cal is None else round(p_cal, 3),
            "action": d.action, "reason": d.reason,
            "position": self.broker.position(sym),
        }
        if d.action == "buy":
            await self._enter(sym, f, d)
        elif d.action == "sell":
            await self._exit(sym, d.reason)

    def _slippage(self, sym: str, qty: float) -> float:
        """Fractional cost of walking the visible asks for qty, versus the best ask.
        Larger than the visible book counts as infinite: we don't know the price."""
        book = self.feed.books[sym]
        left, cost = qty, 0.0
        for price, size in book.asks:
            take = min(left, size)
            cost += take * price
            left -= take
            if left <= 1e-12:
                return (cost / qty) / book.ask - 1.0 if qty > 0 and book.ask > 0 else 0.0
        return float("inf")

    # -- execution ----------------------------------------------------------------
    async def _enter(self, sym: str, f: feat.Features, d) -> None:
        async with self.locks[sym]:
            if self.broker.position(sym) > 0:
                return   # another path entered while we were deciding
            await self._enter_locked(sym, f, d)

    async def _enter_locked(self, sym: str, f: feat.Features, d) -> None:
        client_id = f"jb{int(time.time() * 1000)}{sym[:4].lower()}"
        qty = d.qty * 0.999   # float headroom so qty*stop-distance never exceeds the cap by rounding
        intent = OrderIntent(symbol=sym, side="buy", qty=qty, entry=f.ask, stop_loss=d.stop,
                             signal_ts=datetime.fromtimestamp(f.ts, timezone.utc), idempotency_key=client_id)
        try:
            equity, fetched = await self.broker.equity()
            day_start = self.broker.day_start_equity or equity
            validate_order(intent, EquityQuote(equity, fetched, self.broker.name), self.limits,
                           day_pnl=equity - day_start, day_start_equity=day_start,
                           audit=self.audit, account_ccy="USDT")
            fill = await self.broker.buy(sym, qty, client_id, d.stop)
        except Exception as exc:   # OrderRejected, BrokerError, or anything unexpected: no order
            self.latest.setdefault(sym, {})["reason"] = f"rejected: {exc}"
            self.audit({"ts": time.time(), "event": "entry_rejected", "symbol": sym, "reason": str(exc)})
            return
        self.guards.on_entry(sym, fill.qty * fill.avg_price + fill.fee_usdt, fill.fee_usdt)
        rec = {"ts": time.time(), "symbol": sym, "side": "buy", "qty": fill.qty, "price": fill.avg_price,
               "fee": round(fill.fee_usdt, 4), "stop": d.stop, "reason": d.reason, "simulated": fill.simulated}
        self.trades.appendleft(rec)
        self.audit({"event": "fill", **rec})
        self.publish("trade", rec)

    async def _exit(self, sym: str, reason: str) -> None:
        """Exits never pass through the entry gate: closing reduces risk, and a kill
        switch that trapped you in a position would be a risk control making risk worse.
        The per-symbol lock stops the 1 s clock and the decision clock both selling."""
        async with self.locks[sym]:
            if self.broker.position(sym) <= 0:
                return
            await self._exit_locked(sym, reason)

    async def _exit_locked(self, sym: str, reason: str) -> None:
        try:
            fill = await self.broker.sell(sym, f"jx{int(time.time() * 1000)}{sym[:4].lower()}")
        except Exception as exc:
            self.audit({"ts": time.time(), "event": "exit_failed", "symbol": sym, "reason": str(exc)})
            log.error("exit failed on %s: %s", sym, exc)
            return
        self.first_seen.pop(sym, None)
        self.fades[sym] = 0
        self.last_exit[sym] = time.time()
        pnl = self.guards.on_exit(sym, fill.qty * fill.avg_price - fill.fee_usdt - fill.tds_usdt, fill.fee_usdt)
        rec = {"ts": time.time(), "symbol": sym, "side": "sell", "qty": fill.qty, "price": fill.avg_price,
               "fee": round(fill.fee_usdt, 4), "tds": round(fill.tds_usdt, 4), "reason": reason,
               "pnl": None if pnl is None else round(pnl, 4),
               "simulated": fill.simulated}
        self.trades.appendleft(rec)
        self.audit({"event": "fill", **rec})
        self.publish("trade", rec)
