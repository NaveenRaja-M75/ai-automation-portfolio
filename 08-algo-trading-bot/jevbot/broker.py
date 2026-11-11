"""Order routing. Paper and Binance (testnet or live) share one interface, so the code
path tested in paper is the code path that trades live.

Spot only, long only. Binance blocks margin and futures for Indian IPs, and an Indian
resident's spot account cannot short.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import math
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from urllib.parse import urlencode

import aiohttp

from .config import Config
from .feed import SymbolBook, SymbolRules

log = logging.getLogger("jevbot.broker")

BINANCE_LIVE = "https://api.binance.com"
BINANCE_TESTNET = "https://testnet.binance.vision"


def floor_step(qty: float, step: float) -> float:
    """Round down to the exchange step with Decimal, so 0.1 + 0.2 style float error can
    never round a quantity up past what the account holds."""
    if step <= 0:
        return qty
    # Round away binary noise first (0.6 - 0.5 = 0.09999999999999998), then floor.
    q = (Decimal(str(round(qty, 10))) / Decimal(str(step))).to_integral_value(rounding=ROUND_DOWN)
    return float(q * Decimal(str(step)))


def floor_tick(price: float, tick: float) -> float:
    return floor_step(price, tick)


def sign(secret: str, query: str) -> str:
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


@dataclass
class Fill:
    symbol: str
    side: str
    qty: float
    avg_price: float
    fee_usdt: float
    tds_usdt: float = 0.0
    client_id: str = ""
    simulated: bool = True


class BrokerError(Exception):
    pass


def check_rules(rules: SymbolRules, qty: float, price: float) -> None:
    if qty < rules.min_qty or qty <= 0:
        raise BrokerError(f"{rules.symbol}: qty {qty} below min {rules.min_qty}")
    if qty * price < rules.min_notional:
        raise BrokerError(f"{rules.symbol}: notional {qty * price:.2f} below min {rules.min_notional}")


# ---- paper ---------------------------------------------------------------------------

@dataclass
class PaperState:
    cash: float
    positions: dict = field(default_factory=dict)   # symbol -> {"qty", "cost", "stop"}
    realized: float = 0.0
    fees: float = 0.0
    tds: float = 0.0
    day: str = ""
    day_start_equity: float = 0.0


class PaperBroker:
    """Fills market orders by walking the real top-20 book, so size moves the price the
    way it would live. Stops are checked every second against the real best bid."""
    name = "paper"

    def __init__(self, cfg: Config, books: dict[str, SymbolBook], rules: dict[str, SymbolRules], path: Path):
        self.cfg, self.books, self.rules, self.path = cfg, books, rules, path
        self.state = PaperState(cash=cfg.paper_equity_usdt)
        if path.exists():
            self.state = PaperState(**json.loads(path.read_text(encoding="utf-8")))

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self.state), indent=1), encoding="utf-8")
        tmp.replace(self.path)  # atomic on the same volume: a crash never leaves half a file

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        self._save()

    def _walk(self, symbol: str, side: str, qty: float) -> float:
        if qty <= 0:
            raise BrokerError(f"{symbol}: quantity {qty} rounds to nothing")
        book = self.books[symbol]
        levels = book.asks if side == "buy" else book.bids
        left, cost = qty, 0.0
        for price, size in levels:
            take = min(left, size)
            cost += take * price
            left -= take
            if left <= 1e-12:
                return cost / qty
        raise BrokerError(f"{symbol}: order larger than visible top-20 depth")

    async def equity(self) -> tuple[float, datetime]:
        value = self.state.cash
        for sym, pos in self.state.positions.items():
            value += pos["qty"] * self.books[sym].bid
        self._roll_day(value)
        return value, datetime.now(timezone.utc)

    def _roll_day(self, equity: float) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self.state.day:
            self.state.day, self.state.day_start_equity = today, equity

    def position(self, symbol: str) -> float:
        return self.state.positions.get(symbol, {}).get("qty", 0.0)

    async def buy(self, symbol: str, qty: float, client_id: str, stop: float) -> Fill:
        rules = self.rules[symbol]
        qty = floor_step(qty, rules.step_size)
        px = self._walk(symbol, "buy", qty)
        check_rules(rules, qty, px)
        fee = qty * px * self.cfg.fee_pct
        if qty * px + fee > self.state.cash:
            raise BrokerError(f"insufficient paper cash for {symbol}")
        self.state.cash -= qty * px + fee
        self.state.fees += fee
        pos = self.state.positions.setdefault(symbol, {"qty": 0.0, "cost": 0.0, "stop": 0.0})
        pos["qty"] += qty
        pos["cost"] += qty * px + fee
        pos["stop"] = stop
        pos.setdefault("ts", time.time())   # persisted, so a restart keeps the holding clock
        self._save()
        return Fill(symbol, "buy", qty, px, fee, client_id=client_id)

    async def sell(self, symbol: str, client_id: str) -> Fill:
        pos = self.state.positions.get(symbol)
        if not pos or pos["qty"] <= 0:
            raise BrokerError(f"no {symbol} position to sell")
        qty = pos["qty"]
        px = self._walk(symbol, "sell", qty)
        gross = qty * px
        fee = gross * self.cfg.fee_pct
        tds = gross * self.cfg.tds_pct if self.cfg.apply_tds else 0.0
        self.state.cash += gross - fee - tds
        self.state.fees += fee
        self.state.tds += tds
        self.state.realized += gross - fee - tds - pos["cost"]
        del self.state.positions[symbol]
        self._save()
        return Fill(symbol, "sell", qty, px, fee, tds, client_id)

    @property
    def day_start_equity(self) -> float:
        return self.state.day_start_equity

    def entry_ts(self, symbol: str) -> float | None:
        return self.state.positions.get(symbol, {}).get("ts")

    def protected(self, symbol: str) -> bool:
        return True   # paper stops are checked every second by the engine

    async def refresh(self) -> None:
        pass

    events: list = []   # paper has no exchange-side fills to reconcile

    def stop_hit(self, symbol: str) -> bool:
        pos = self.state.positions.get(symbol)
        return bool(pos) and self.books[symbol].bid <= pos["stop"]

    def summary(self) -> dict:
        return {"cash": round(self.state.cash, 2), "realized": round(self.state.realized, 4),
                "fees": round(self.state.fees, 4), "tds": round(self.state.tds, 4),
                "positions": {s: {"qty": p["qty"], "entry": p["cost"] / p["qty"], "stop": p["stop"]}
                              for s, p in self.state.positions.items()},
                "day_start_equity": round(self.state.day_start_equity, 2)}


# ---- Binance (testnet or live) -------------------------------------------------------

class BinanceBroker:
    """Signed REST. Places an exchange-side STOP_LOSS_LIMIT after every entry, so the
    position stays protected if this process, the laptop or the internet dies.

    It only ever sells what it bought. Holdings and orders you made yourself on the same
    account are never touched: the bot's quantity per symbol lives in a state file, and
    its stop order is reconciled by its own client order id, never inferred from balance.
    Any network error on the buy leg is resolved by re-reading the balance, and a position
    the bot cannot protect with a stop is closed immediately rather than left naked.

    The API key must be created with spot trading ONLY: no withdrawals, no transfers,
    restricted to your IP. That is your step, never Claude's.
    """

    STOP_LIMIT_GAP = 0.01   # limit 1% under the trigger: wide enough to fill through a fast drop

    def __init__(self, cfg: Config, books: dict[str, SymbolBook], rules: dict[str, SymbolRules], path: Path):
        self.cfg, self.books, self.rules, self.path = cfg, books, rules, path
        self.base = BINANCE_LIVE if cfg.mode == "live" else BINANCE_TESTNET
        self.name = cfg.mode
        self.session: aiohttp.ClientSession | None = None
        self.offset_ms = 0
        self.balances: dict[str, float] = {}
        self.events: list[dict] = []   # stop fills found during reconciliation, drained by the engine
        self.own: dict = {"owned": {}, "stops": {}, "entry_ts": {}, "day": "", "day_start_equity": 0.0}
        if path.exists():
            self.own.update(json.loads(path.read_text(encoding="utf-8")))

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.own, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    async def _sync_clock(self) -> None:
        async with self.session.get(f"{self.base}/api/v3/time", timeout=aiohttp.ClientTimeout(total=10)) as r:
            server = (await r.json())["serverTime"]
        self.offset_ms = server - int(time.time() * 1000)
        if abs(self.offset_ms) > 1000:
            log.warning("local clock is %d ms off Binance; sync Windows time (NTP)", self.offset_ms)

    async def start(self) -> None:
        self.session = aiohttp.ClientSession(headers={"X-MBX-APIKEY": self.cfg.binance_api_key})
        await self._sync_clock()

    async def close(self) -> None:
        self._save()
        if self.session:
            await self.session.close()

    async def _signed(self, method: str, path: str, params: dict, _retry: bool = True) -> dict | list:
        """Network failures surface as BrokerError so callers have one thing to catch.
        A -1021 (timestamp outside recvWindow) resyncs the clock and retries once."""
        sent = {**params, "timestamp": int(time.time() * 1000) + self.offset_ms, "recvWindow": 5000}
        query = urlencode(sent)
        url = f"{self.base}{path}?{query}&signature={sign(self.cfg.binance_api_secret, query)}"
        try:
            async with self.session.request(method, url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                body = await r.json(content_type=None)
                status = r.status
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise BrokerError(f"network error on {path}: {exc!r}") from exc
        if status != 200:
            if _retry and isinstance(body, dict) and body.get("code") == -1021:
                await self._sync_clock()
                return await self._signed(method, path, params, _retry=False)
            raise BrokerError(f"Binance {status} on {path}: {body}")
        return body

    @staticmethod
    def base_asset(symbol: str) -> str:
        return symbol[:-4] if symbol.endswith("USDT") else symbol

    @staticmethod
    def _q(x: float) -> str:
        return f"{x:.8f}".rstrip("0").rstrip(".")

    async def refresh(self) -> None:
        acct = await self._signed("GET", "/api/v3/account", {"omitZeroBalances": "true"})
        self.balances = {b["asset"]: float(b["free"]) + float(b["locked"]) for b in acct["balances"]}
        await self._reconcile_stops()
        for sym, owned in list(self.own["owned"].items()):
            held = self.balances.get(self.base_asset(sym), 0.0)
            if held < owned:   # never claim more than the account holds
                self.own["owned"][sym] = held
        self._save()

    async def _reconcile_stops(self) -> None:
        """Ask the exchange what happened to each of our stop orders, by our own id."""
        for sym, stop in list(self.own["stops"].items()):
            o = await self._signed("GET", "/api/v3/order", {"symbol": sym, "origClientOrderId": stop["id"]})
            executed = float(o.get("executedQty", 0.0))
            new = executed - stop.get("executed_seen", 0.0)   # executedQty is cumulative
            if new > 0:
                left = max(self.own["owned"].get(sym, 0.0) - new, 0.0)
                price = float(o["cummulativeQuoteQty"]) / executed
                self.events.append({"ts": time.time(), "symbol": sym, "side": "sell", "qty": new,
                                    "price": price, "fee": 0.0, "tds": new * price * self.cfg.tds_pct
                                    if self.cfg.apply_tds else 0.0, "reason": "exchange stop filled",
                                    "simulated": False})
                if o["status"] == "FILLED" or left < self.rules[sym].min_qty:
                    self.own["owned"].pop(sym, None)
                    self.own["stops"].pop(sym, None)
                    self.own["entry_ts"].pop(sym, None)
                else:
                    self.own["owned"][sym] = left
                    stop["executed_seen"] = executed
            elif o.get("status") in ("CANCELED", "EXPIRED", "REJECTED"):
                log.warning("%s: our stop %s is %s; position unprotected until re-placed", sym, stop["id"], o["status"])
                self.own["stops"].pop(sym, None)

    async def equity(self) -> tuple[float, datetime]:
        await self.refresh()  # always fresh: the gate rejects anything older than 30 s
        value = self.balances.get("USDT", 0.0)
        for sym, qty in self.own["owned"].items():
            value += qty * self.books[sym].bid
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self.own["day"]:
            self.own["day"], self.own["day_start_equity"] = today, value
        return value, datetime.now(timezone.utc)

    def position(self, symbol: str) -> float:
        return self.own["owned"].get(symbol, 0.0)

    def entry_ts(self, symbol: str) -> float | None:
        return self.own["entry_ts"].get(symbol)

    def protected(self, symbol: str) -> bool:
        return symbol in self.own["stops"]

    def _fee_usdt(self, symbol: str, fills: list) -> float:
        fee = 0.0
        for f in fills:
            c, asset = float(f["commission"]), f["commissionAsset"]
            if asset == "USDT":
                fee += c
            elif asset == self.base_asset(symbol):
                fee += c * float(f["price"])
            else:
                log.info("fee paid in %s not converted to USDT in the report", asset)
        return fee

    async def place_stop(self, symbol: str, stop: float, client_id: str) -> None:
        rules = self.rules[symbol]
        stop_id = client_id + "s"
        await self._signed("POST", "/api/v3/order", {
            "symbol": symbol, "side": "SELL", "type": "STOP_LOSS_LIMIT", "timeInForce": "GTC",
            "quantity": self._q(floor_step(self.own["owned"][symbol], rules.step_size)),
            "stopPrice": self._q(floor_tick(stop, rules.tick_size)),
            "price": self._q(floor_tick(stop * (1 - self.STOP_LIMIT_GAP), rules.tick_size)),
            "newClientOrderId": stop_id})
        self.own["stops"][symbol] = {"id": stop_id, "price": stop}
        self._save()

    async def buy(self, symbol: str, qty: float, client_id: str, stop: float) -> Fill:
        rules = self.rules[symbol]
        qty = floor_step(qty, rules.step_size)
        check_rules(rules, qty, self.books[symbol].ask)
        await self.refresh()
        before = self.balances.get(self.base_asset(symbol), 0.0)
        r: dict = {}
        try:
            r = await self._signed("POST", "/api/v3/order", {
                "symbol": symbol, "side": "BUY", "type": "MARKET", "quantity": self._q(qty),
                "newClientOrderId": client_id, "newOrderRespType": "FULL"})
        except BrokerError as exc:
            log.warning("%s buy: %s; checking whether it filled anyway", symbol, exc)
        await self.refresh()
        gained = floor_step(self.balances.get(self.base_asset(symbol), 0.0) - before, rules.step_size)
        if gained <= 0:
            raise BrokerError(f"{symbol} buy did not fill")
        self.own["owned"][symbol] = self.own["owned"].get(symbol, 0.0) + gained
        self.own["entry_ts"][symbol] = time.time()
        self._save()
        try:
            await self.place_stop(symbol, stop, client_id)
        except BrokerError as exc:
            # A position without a stop is not allowed to exist. Close it now.
            log.error("%s: stop placement failed (%s); flattening", symbol, exc)
            await self.sell(symbol, client_id + "f")
            raise BrokerError(f"{symbol}: stop rejected, position closed: {exc}") from exc
        filled = float(r.get("executedQty", gained))
        quote = float(r.get("cummulativeQuoteQty", gained * self.books[symbol].ask))
        return Fill(symbol, "buy", filled, quote / filled if filled else 0.0,
                    self._fee_usdt(symbol, r.get("fills", [])), client_id=client_id, simulated=False)

    async def sell(self, symbol: str, client_id: str) -> Fill:
        rules = self.rules[symbol]
        await self.refresh()
        qty = floor_step(self.position(symbol), rules.step_size)
        check_rules(rules, qty, self.books[symbol].bid)   # before cancelling the stop, never after
        stop = self.own["stops"].get(symbol)
        if stop:
            try:
                await self._signed("DELETE", "/api/v3/order", {"symbol": symbol, "origClientOrderId": stop["id"]})
            except BrokerError as exc:
                log.info("%s: stop %s not cancellable (%s)", symbol, stop["id"], exc)
            self.own["stops"].pop(symbol, None)
        try:
            r = await self._signed("POST", "/api/v3/order", {
                "symbol": symbol, "side": "SELL", "type": "MARKET", "quantity": self._q(qty),
                "newClientOrderId": client_id, "newOrderRespType": "FULL"})
        except BrokerError:
            await self.refresh()
            if self.position(symbol) >= rules.min_qty and stop:
                await self.place_stop(symbol, stop["price"], client_id + "r")   # re-protect, then report
            raise
        filled, quote = float(r["executedQty"]), float(r["cummulativeQuoteQty"])
        for k in ("owned", "entry_ts"):
            self.own[k].pop(symbol, None)
        self._save()
        tds = quote * self.cfg.tds_pct if self.cfg.apply_tds else 0.0
        return Fill(symbol, "sell", filled, quote / filled if filled else 0.0,
                    self._fee_usdt(symbol, r.get("fills", [])), tds, client_id, simulated=False)

    def stop_hit(self, symbol: str) -> bool:
        return False  # the exchange-side stop handles it; refresh() reconciles afterwards

    @property
    def day_start_equity(self) -> float:
        return self.own["day_start_equity"]

    def summary(self) -> dict:
        return {"venue": self.base, "usdt": round(self.balances.get("USDT", 0.0), 2),
                "bot_positions": self.own["owned"], "stops": self.own["stops"],
                "day_start_equity": round(self.day_start_equity, 2)}
