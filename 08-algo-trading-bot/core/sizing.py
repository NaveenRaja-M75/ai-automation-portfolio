"""Instrument specifications and currency-correct position risk.

The naive risk formula, abs(entry - stop) * qty, is only correct when the instrument's
quote currency is the same as the account currency. It is wrong for USD/JPY on a USD
account, wrong for EUR/GBP on any account that is not GBP, and wrong for XAU/USD on an
INR account. The failure is silent: the number still looks plausible, so a bot can run for
months believing it risks 1% per trade while actually risking a different amount on every
pair.

This module makes the conversion explicit and mandatory. Every risk figure is returned in
account currency, and a missing or stale exchange rate raises rather than defaulting.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

FOREX = "FOREX"
METAL = "METAL"
CRYPTO = "CRYPTO"
EQUITY = "EQUITY"
INDEX = "INDEX"


class SizingError(Exception):
    """Any failure to establish a trustworthy risk figure. Fail-secure: the caller
    must treat this as an order rejection, never as a zero-risk order."""


@dataclass(frozen=True)
class Instrument:
    symbol: str
    asset_class: str
    base: str              # what you are long when you buy
    quote: str             # what the price is denominated in
    contract_size: float   # units of base per 1.0 of qty
    tick_size: float
    pip: float             # smallest conventional increment, for reporting
    max_leverage: float    # 1.0 means cash only, no margin
    session: str
    qty_step: float
    min_qty: float
    # Sane stop-distance band, as a fraction of price. These are per instrument class
    # because a single global band cannot be right for both: a 5 pip EUR/USD stop is
    # 0.046% of price and completely normal, while a 0.046% stop on an equity is a data
    # error. The floor exists to catch bad data and to keep the sizing divisor away from
    # zero, not to express an opinion about strategy.
    min_stop_pct: float = 0.0005
    max_stop_pct: float = 0.10

    @property
    def is_cross(self) -> bool:
        """True when P&L lands in a currency that is neither side of a USD account."""
        return self.quote != "USD"


# Sessions are named here and enforced by the calendar gate, not by this module.
_FX = dict(asset_class=FOREX, contract_size=100_000.0, max_leverage=30.0,
           session="FX_24_5", qty_step=0.01, min_qty=0.01,
           min_stop_pct=0.0001,   # about 1 pip on a major
           max_stop_pct=0.05)

INSTRUMENTS: dict[str, Instrument] = {
    "EURUSD": Instrument("EURUSD", base="EUR", quote="USD", tick_size=0.00001, pip=0.0001, **_FX),
    "GBPUSD": Instrument("GBPUSD", base="GBP", quote="USD", tick_size=0.00001, pip=0.0001, **_FX),
    "AUDUSD": Instrument("AUDUSD", base="AUD", quote="USD", tick_size=0.00001, pip=0.0001, **_FX),
    "USDJPY": Instrument("USDJPY", base="USD", quote="JPY", tick_size=0.001, pip=0.01, **_FX),
    "USDCHF": Instrument("USDCHF", base="USD", quote="CHF", tick_size=0.00001, pip=0.0001, **_FX),
    "EURGBP": Instrument("EURGBP", base="EUR", quote="GBP", tick_size=0.00001, pip=0.0001, **_FX),
    "EURJPY": Instrument("EURJPY", base="EUR", quote="JPY", tick_size=0.001, pip=0.01, **_FX),

    # Gold and silver quote in USD per ounce. Contract is 100 oz, same as COMEX.
    "XAUUSD": Instrument("XAUUSD", asset_class=METAL, base="XAU", quote="USD",
                         contract_size=100.0, tick_size=0.01, pip=0.01, max_leverage=20.0,
                         session="FX_24_5", qty_step=0.01, min_qty=0.01,
                         min_stop_pct=0.0002, max_stop_pct=0.08),
    "XAGUSD": Instrument("XAGUSD", asset_class=METAL, base="XAG", quote="USD",
                         contract_size=5_000.0, tick_size=0.001, pip=0.001, max_leverage=20.0,
                         session="FX_24_5", qty_step=0.01, min_qty=0.01,
                         min_stop_pct=0.0002, max_stop_pct=0.10),

    # Crypto: qty is in coins, so contract size is 1. Leverage 1.0 by default because
    # CLAUDE.md puts crypto on a swing track, and leverage plus weekend gaps is how
    # swing crypto accounts die.
    # Wide max_stop_pct: a 20% swing stop is legitimate in crypto, and CLAUDE.md puts
    # crypto on a multi-day track where the 1% TDS forces large gross targets anyway.
    "BTCUSDT": Instrument("BTCUSDT", asset_class=CRYPTO, base="BTC", quote="USDT",
                          contract_size=1.0, tick_size=0.01, pip=0.01, max_leverage=1.0,
                          session="CRYPTO_24_7", qty_step=0.00001, min_qty=0.00001,
                          min_stop_pct=0.002, max_stop_pct=0.25),
    "ETHUSDT": Instrument("ETHUSDT", asset_class=CRYPTO, base="ETH", quote="USDT",
                          contract_size=1.0, tick_size=0.01, pip=0.01, max_leverage=1.0,
                          session="CRYPTO_24_7", qty_step=0.0001, min_qty=0.0001,
                          min_stop_pct=0.002, max_stop_pct=0.25),

    # Indian leg. Quote currency INR, cash only.
    "GOLDBEES": Instrument("GOLDBEES", asset_class=EQUITY, base="GOLDBEES", quote="INR",
                           contract_size=1.0, tick_size=0.01, pip=0.01, max_leverage=1.0,
                           session="NSE", qty_step=1.0, min_qty=1.0),
}


class FxRates:
    """A snapshot of exchange rates with the time it was taken.

    Rates are keyed by concatenated pair, e.g. {"EURUSD": 1.0850, "USDJPY": 150.25}.
    Inverses are derived automatically; triangulation through USD is supported for
    crosses. Anything that cannot be established raises.
    """

    def __init__(self, rates: dict[str, float], as_of: datetime, max_age_s: float = 60.0):
        self._r = dict(rates)
        self.as_of = as_of
        self.max_age_s = max_age_s

    def assert_fresh(self, now: datetime) -> None:
        if self.as_of.tzinfo is None or now.tzinfo is None:
            raise SizingError("fx timestamps must be timezone-aware")
        age = (now - self.as_of).total_seconds()
        if age > self.max_age_s:
            raise SizingError(f"fx rates {age:.1f}s old, max {self.max_age_s}s")

    def rate(self, frm: str, to: str) -> float:
        if frm == to:
            return 1.0
        # Stablecoins are treated as USD for conversion. Documented simplification:
        # a depeg makes this wrong, which is why crypto risk is also capped by notional.
        frm_n, to_n = _norm(frm), _norm(to)
        if frm_n == to_n:
            return 1.0
        direct = self._r.get(frm_n + to_n)
        if direct:
            return _positive(direct, f"{frm_n}{to_n}")
        inverse = self._r.get(to_n + frm_n)
        if inverse:
            return 1.0 / _positive(inverse, f"{to_n}{frm_n}")
        # Triangulate through USD.
        if frm_n != "USD" and to_n != "USD":
            try:
                return self.rate(frm_n, "USD") * self.rate("USD", to_n)
            except SizingError:
                pass
        raise SizingError(f"no exchange rate available for {frm_n}->{to_n}")

    def convert(self, amount: float, frm: str, to: str) -> float:
        return amount * self.rate(frm, to)


def _norm(ccy: str) -> str:
    return "USD" if ccy.upper() in ("USDT", "USDC", "BUSD") else ccy.upper()


def _positive(v: float, label: str) -> float:
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v <= 0:
        raise SizingError(f"exchange rate {label} must be a positive finite number, got {v!r}")
    return float(v)


def risk_in_account(
    inst: Instrument,
    entry: float,
    stop: float,
    qty: float,
    fx: FxRates,
    account_ccy: str,
    now: Optional[datetime] = None,
) -> float:
    """Money at risk between entry and stop, expressed in account currency.

    Worked example, USD/JPY on a USD account: a 50 pip stop is 0.50 of price. One
    standard lot is 100,000 USD of base, so risk is 0.50 * 100,000 = 50,000 JPY. At
    USDJPY 150 that is 333.33 USD, not 50,000 of anything. The naive formula would have
    reported 50,000.
    """
    if now is not None:
        fx.assert_fresh(now)
    for label, v in (("entry", entry), ("stop", stop), ("qty", qty)):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
            raise SizingError(f"{label} must be a positive finite number, got {v!r}")
    units = qty * inst.contract_size
    risk_quote = abs(entry - stop) * units          # denominated in inst.quote
    return fx.convert(risk_quote, inst.quote, account_ccy)


def notional_in_account(
    inst: Instrument, price: float, qty: float, fx: FxRates, account_ccy: str
) -> float:
    """Full exposure in account currency. Margin is derived from this, and it is also
    the number that bounds a stablecoin depeg or a gapped stop."""
    units = qty * inst.contract_size
    return fx.convert(units * price, inst.quote, account_ccy)


def margin_required(
    inst: Instrument, price: float, qty: float, fx: FxRates, account_ccy: str
) -> float:
    if inst.max_leverage <= 0:
        raise SizingError(f"{inst.symbol} has invalid leverage {inst.max_leverage}")
    return notional_in_account(inst, price, qty, fx, account_ccy) / inst.max_leverage


def suggest_qty(
    inst: Instrument,
    entry: float,
    stop: float,
    risk_budget: float,
    fx: FxRates,
    account_ccy: str,
) -> float:
    """Largest qty whose risk stays within budget, floored to the instrument's step.

    Always floors, never rounds, because rounding up breaches the risk mandate. Returns
    0.0 when even the minimum size would exceed the budget, and the caller must treat
    that as skip-the-trade rather than as trade-the-minimum.
    """
    unit_risk = risk_in_account(inst, entry, stop, inst.qty_step, fx, account_ccy)
    if unit_risk <= 0:
        raise SizingError("per-step risk computed as zero; stop is too close to entry")
    steps = math.floor(risk_budget / unit_risk)
    qty = steps * inst.qty_step
    if qty < inst.min_qty:
        return 0.0
    # Floating point: 0.01 steps accumulate error, so snap to the step grid.
    return round(qty, 8)
