"""Account-level guards: the loopholes a per-order risk gate cannot see.

core/risk_gate.py judges one order at a time. These rules judge the account's recent
history, which is where real accounts die: a losing streak traded through, a slow bleed
from a high-water mark, overtrading into fees, three "different" coins that are really
one bet, entering a book too thin or too wild to fill fairly.

State persists to disk, so a restart can never reset a breaker that has tripped.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import Config


@dataclass
class GuardState:
    peak_equity: float = 0.0
    loss_streak: int = 0
    paused_until: float = 0.0
    day: str = ""
    trades_today: int = 0
    open_cost: dict = field(default_factory=dict)    # symbol -> USDT paid incl. fee, for closed-trade P&L
    closed: int = 0
    wins: int = 0
    gross_win: float = 0.0
    gross_loss: float = 0.0
    fees: float = 0.0
    max_drawdown_pct: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0


class Guards:
    def __init__(self, cfg: Config, path: Path, kill_switch: Path):
        self.cfg, self.path, self.kill_switch = cfg, path, kill_switch
        self.s = GuardState()
        if path.exists():
            self.s = GuardState(**json.loads(path.read_text(encoding="utf-8")))

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self.s), indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def _roll_day(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self.s.day:
            self.s.day, self.s.trades_today = today, 0

    # -- called every decision ------------------------------------------------------
    def mark_equity(self, equity: float) -> str | None:
        """Track the high-water mark. A drawdown past the limit trips the kill switch:
        the same file the risk gate reads, so a human must clear it on purpose."""
        if equity > self.s.peak_equity:
            self.s.peak_equity = equity
            self._save()
        dd = 1 - equity / self.s.peak_equity if self.s.peak_equity > 0 else 0.0
        if dd > self.s.max_drawdown_pct:
            self.s.max_drawdown_pct = dd
            self._save()
        if dd >= self.cfg.max_drawdown_pct and not self.kill_switch.exists():
            self.kill_switch.write_text(
                f"{datetime.now(timezone.utc).isoformat()} drawdown {dd:.2%} from peak "
                f"{self.s.peak_equity:.2f} breached {self.cfg.max_drawdown_pct:.2%}\n", encoding="utf-8")
            return f"drawdown {dd:.2%} breached; kill switch tripped"
        return None

    def check_entry(self, now: float, equity: float, open_notional: float, new_notional: float,
                    spread_pct: float, vol_ratio: float, slippage_pct: float) -> str | None:
        """None means allowed; otherwise the reason the entry is refused."""
        self._roll_day()
        c = self.cfg
        if now < self.s.paused_until:
            return f"paused after {c.max_loss_streak} losses in a row ({int(self.s.paused_until - now)}s left)"
        if self.s.trades_today >= c.max_trades_per_day:
            return f"daily trade limit {c.max_trades_per_day} reached (overtrading guard)"
        if equity > 0 and (open_notional + new_notional) / equity > c.max_total_exposure_pct:
            return (f"total exposure would be {(open_notional + new_notional) / equity:.0%} of equity, "
                    f"cap {c.max_total_exposure_pct:.0%} (BTC/ETH/SOL move together)")
        if spread_pct * 1e4 > c.max_spread_bps:
            return f"spread {spread_pct * 1e4:.1f} bps above {c.max_spread_bps} (thin or stressed book)"
        if vol_ratio > c.max_vol_ratio:
            return f"volatility {vol_ratio:.1f}x normal (news shock); standing aside"
        if slippage_pct * 1e4 > c.max_slippage_bps:
            return f"filling this size would slip {slippage_pct * 1e4:.1f} bps, cap {c.max_slippage_bps}"
        return None

    # -- called on fills ------------------------------------------------------------
    def on_entry(self, symbol: str, cost_usdt: float, fee: float) -> None:
        self._roll_day()
        self.s.trades_today += 1
        self.s.open_cost[symbol] = self.s.open_cost.get(symbol, 0.0) + cost_usdt
        self.s.fees += fee
        self._save()

    def on_exit(self, symbol: str, proceeds_usdt: float, fee: float, now: float | None = None) -> float | None:
        """Proceeds are net of fee and TDS. Returns the closed trade's P&L."""
        cost = self.s.open_cost.pop(symbol, None)
        self.s.fees += fee
        if cost is None:
            self._save()
            return None
        pnl = proceeds_usdt - cost
        self.s.closed += 1
        self.s.best_trade = max(self.s.best_trade, pnl)
        self.s.worst_trade = min(self.s.worst_trade, pnl)
        if pnl > 0:
            self.s.wins += 1
            self.s.gross_win += pnl
            self.s.loss_streak = 0
        else:
            self.s.gross_loss += -pnl
            self.s.loss_streak += 1
            if self.s.loss_streak >= self.cfg.max_loss_streak:
                self.s.paused_until = (now or time.time()) + self.cfg.pause_after_streak_s
                self.s.loss_streak = 0
        self._save()
        return pnl

    def summary(self, equity: float) -> dict:
        s = self.s
        return {
            "peak_equity": round(s.peak_equity, 2),
            "drawdown_pct": round((1 - equity / s.peak_equity) * 100, 3) if s.peak_equity else 0.0,
            "loss_streak": s.loss_streak, "paused_s": max(0, int(s.paused_until - time.time())),
            "trades_today": s.trades_today, "closed": s.closed, "wins": s.wins,
            "net_closed": round(s.gross_win - s.gross_loss, 4), "fees": round(s.fees, 4),
            "profit_factor": round(s.gross_win / s.gross_loss, 2) if s.gross_loss else None,
            "win_rate": round(s.wins / s.closed, 3) if s.closed else None,
            "avg_win": round(s.gross_win / s.wins, 4) if s.wins else None,
            "avg_loss": round(s.gross_loss / (s.closed - s.wins), 4) if s.closed - s.wins else None,
            "expectancy": round((s.gross_win - s.gross_loss) / s.closed, 4) if s.closed else None,
            "max_drawdown_pct": round(s.max_drawdown_pct * 100, 3),
            "best_trade": round(s.best_trade, 4), "worst_trade": round(s.worst_trade, 4),
        }
