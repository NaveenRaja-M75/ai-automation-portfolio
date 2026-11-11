"""Transaction cost models.

A backtest that ignores costs is not an optimistic backtest, it is a different backtest of
a strategy nobody can trade. For Indian equities the drag is small but real; for Indian
crypto the 1% TDS on gross sale value is large enough to invert the sign of a strategy's
result, which is why it gets its own model rather than a fudge factor.

RATES CHANGE. The defaults below encode the correct *structure* of each charge. The
numbers must be verified against your broker's live rate card before any result is trusted,
and the structure is what matters for design decisions either way.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    """Base: a flat proportional cost on both sides, plus slippage."""
    slippage_pct: float = 0.0005   # half a tenth of a percent each way

    def entry_cost(self, price: float, qty: float) -> float:
        raise NotImplementedError

    def exit_cost(self, price: float, qty: float) -> float:
        raise NotImplementedError

    def slip(self, price: float, side: str, entering: bool) -> float:
        """Slippage always moves against you: worse fill entering and exiting.

        Modeled as a fraction of price rather than a fixed tick because it scales with
        the instrument, and because assuming a fixed tick flatters low-priced assets.
        """
        adverse_up = (side == "buy") == entering
        return price * (1 + self.slippage_pct) if adverse_up else price * (1 - self.slippage_pct)


@dataclass(frozen=True)
class IndianEquityDelivery(CostModel):
    """NSE cash-segment delivery, discount-broker structure (Dhan, Upstox, Zerodha).

    Delivery brokerage is zero at the discount brokers, so STT dominates. Verify each
    rate below against your broker's current rate card.
    """
    brokerage_pct: float = 0.0        # delivery is free at discount brokers
    brokerage_cap: float = 0.0
    stt_buy_pct: float = 0.001        # 0.1% on buy
    stt_sell_pct: float = 0.001       # 0.1% on sell
    exchange_txn_pct: float = 0.0000297
    sebi_pct: float = 0.000001        # Rs 10 per crore
    stamp_duty_buy_pct: float = 0.00015
    gst_pct: float = 0.18             # on brokerage + exchange + sebi
    dp_charge_per_sell: float = 15.93  # flat, on the sell leg only

    def _brokerage(self, turnover: float) -> float:
        b = turnover * self.brokerage_pct
        return min(b, self.brokerage_cap) if self.brokerage_cap else b

    def entry_cost(self, price: float, qty: float) -> float:
        t = price * qty
        brok = self._brokerage(t)
        txn = t * (self.exchange_txn_pct + self.sebi_pct)
        return (brok + txn + (brok + txn) * self.gst_pct
                + t * self.stt_buy_pct + t * self.stamp_duty_buy_pct)

    def exit_cost(self, price: float, qty: float) -> float:
        t = price * qty
        brok = self._brokerage(t)
        txn = t * (self.exchange_txn_pct + self.sebi_pct)
        return (brok + txn + (brok + txn) * self.gst_pct
                + t * self.stt_sell_pct + self.dp_charge_per_sell)


@dataclass(frozen=True)
class IndianEquityIntraday(IndianEquityDelivery):
    """Intraday differs in three places: brokerage applies, STT is sell-side only and
    lower, and there is no DP charge because nothing is delivered."""
    brokerage_pct: float = 0.0003     # 0.03%...
    brokerage_cap: float = 20.0       # ...capped at Rs 20 per executed order
    stt_buy_pct: float = 0.0
    stt_sell_pct: float = 0.00025
    stamp_duty_buy_pct: float = 0.00003
    dp_charge_per_sell: float = 0.0


@dataclass(frozen=True)
class IndianCrypto(CostModel):
    """FIU-registered exchange, Indian resident.

    The 1% TDS is levied on the gross consideration of the sale, not on profit. That is
    the number that decides whether a crypto strategy is viable: a round trip grossing
    under about 1.2% after exchange fees is TDS-negative before the strategy is even
    evaluated. TDS is creditable against your annual tax liability, so it is not a
    permanent loss, but it is locked-up capital for up to a year and it is modeled as a
    cost here because that is what it does to a compounding account.

    The 30% flat tax on gains, and the fact that losses cannot be set off against gains,
    are annual-level effects and are deliberately not modeled per trade. Any per-trade
    approximation of them would be wrong in both directions.
    """
    exchange_fee_pct: float = 0.001   # 0.1% taker, typical
    tds_pct: float = 0.01             # on gross sale value
    slippage_pct: float = 0.001       # crypto books are thinner

    def entry_cost(self, price: float, qty: float) -> float:
        return price * qty * self.exchange_fee_pct

    def exit_cost(self, price: float, qty: float) -> float:
        t = price * qty
        return t * self.exchange_fee_pct + t * self.tds_pct


@dataclass(frozen=True)
class ZeroCost(CostModel):
    """For isolating strategy behaviour from cost drag in tests only. Never a valid
    basis for a promotion decision."""
    slippage_pct: float = 0.0

    def entry_cost(self, price: float, qty: float) -> float:
        return 0.0

    def exit_cost(self, price: float, qty: float) -> float:
        return 0.0


def breakeven_move_pct(model: CostModel, price: float, qty: float) -> float:
    """The gross percentage move required just to cover costs on a round trip.

    Run this before designing any strategy. If your average target is smaller than this
    number, the strategy cannot work no matter how accurate the signal is.
    """
    if price <= 0 or qty <= 0:
        raise ValueError("price and qty must be positive")
    total = model.entry_cost(price, qty) + model.exit_cost(price, qty)
    slip = 2 * price * qty * model.slippage_pct
    return (total + slip) / (price * qty)
