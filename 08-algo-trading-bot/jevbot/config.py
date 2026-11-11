"""Runtime configuration, read from the environment and an optional .env file.

Every default is the safe one: paper mode, Jev off until a key is present, live orders
blocked unless three separate switches agree. Nothing in a running session can raise a
limit; edit the file and restart.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "jevbot"

LIVE_CONFIRM_PHRASE = "I_ACCEPT_REAL_MONEY_RISK"

# Gateway routes for Jev, from the TypeSafe SDK docs (api-reference snapshot 2026-09-25).
JEV_PROVIDERS = {
    "typesafe": ("https://api.typesafe.ai", "jev-1.13.0"),
    "vercel": ("https://ai-gateway.vercel.sh/typesafe", "typesafe-ai/jev"),
    "openrouter": ("https://openrouter.ai/api", "~typesafe/jev-latest"),
}


def load_dotenv(path: Path) -> None:
    """Minimal .env reader. Existing environment variables win, so a shell export can
    always override the file."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default).strip()


def _float(key: str, default: float) -> float:
    return float(_env(key, str(default)))


def _bool(key: str, default: bool) -> bool:
    return _env(key, "true" if default else "false").lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    mode: str = "paper"                       # paper | testnet | live
    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    decision_interval_s: float = 15.0
    horizons_s: tuple[int, ...] = (60, 300, 900)
    trade_horizon_s: int = 300                # the horizon the policy actually trades

    # Signals
    primary_signal: str = "auto"              # auto | jev | ml | momentum | random
    jev_provider: str = "off"                 # off | typesafe | vercel | openrouter
    jev_api_key: str = field(default="", repr=False)
    jev_model: str = ""
    jev_interval_s: float = 30.0
    jev_daily_budget_usd: float = 0.16        # $5 per 30 days, the Vercel free allowance
    jev_timeout_s: float = 2.5
    jev_usd_per_mtok: float = 0.042
    jev_min_credit_usd: float = 0.25          # pause Jev before the free credit runs out

    @property
    def jev_paused_file(self) -> Path:
        return ROOT / "JEV_PAUSED"

    # Policy
    entry_prob: float = 0.60                  # p(up) needed to open a long
    exit_prob: float = 0.45                   # hysteresis: close only below this
    min_edge_multiple: float = 1.5            # expected move must beat costs by this factor
    stop_vol_multiple: float = 2.0            # stop distance in horizon-volatility units
    cooldown_s: float = 60.0
    min_hold_s: float = 60.0                  # a fade exit can't fire before this (stop and time exits can)
    exit_confirm: int = 2                     # fade must show on this many decisions in a row
    require_positive_ev: bool = True          # enter only if calibrated EV after costs is > 0
    calibration_k: float = 30.0               # shrinkage: pseudo-samples pulling unproven p toward 0.5

    # Costs (Binance spot, Indian resident)
    fee_pct: float = 0.001                    # 0.1% per side
    apply_tds: bool = True                    # 1% TDS on gross sale value
    tds_pct: float = 0.01

    # Risk (paper equity is simulated; live equity is always fetched from the exchange)
    paper_equity_usdt: float = 1000.0
    risk_pct: float = 0.01
    daily_loss_limit_pct: float = 0.03
    max_notional_pct: float = 0.25            # per symbol, of equity
    max_feed_age_s: float = 3.0
    max_drawdown_pct: float = 0.05            # from the equity high-water mark -> kill switch
    max_loss_streak: int = 3                  # losses in a row -> pause
    pause_after_streak_s: float = 3600.0
    max_trades_per_day: int = 20
    max_total_exposure_pct: float = 0.50      # all open positions together
    max_spread_bps: float = 10.0
    max_vol_ratio: float = 3.0                # 1-min vol vs 15-min vol: above this is a shock
    max_slippage_bps: float = 5.0

    # Exchange keys and the live interlock
    binance_api_key: str = field(default="", repr=False)
    binance_api_secret: str = field(default="", repr=False)
    live_confirm: str = field(default="", repr=False)
    require_edge_for_live: bool = True

    copy_enabled: bool = True                 # read-only Hyperliquid top-trader positions
    alloc_enabled: bool = True                # daily paper allocation (sma200 hysteresis to half)
    alloc_symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")
    # Extra paper books run side by side, each pre-registered before any forward data:
    # "BTC" was added 2026-10-28 because BTC alone passed both halves of the backtest.
    alloc_extra_books: tuple[str, ...] = ("BTCUSDT",)
    alloc_capital: float = 1000.0
    alloc_lot_method: str = "fifo"            # fifo | average: ask your CA which your returns use
    dashboard_port: int = 8765

    @property
    def strategies_tested(self) -> int:
        """Honest multiple-comparison count for the scorecard: every signal on every
        horizon on every symbol is a separate hypothesis."""
        return 7 * len(self.horizons_s) * len(self.symbols)   # jev, ml, momentum, random, copy, news, ensemble

    @property
    def jev_enabled(self) -> bool:
        return self.jev_provider in JEV_PROVIDERS and bool(self.jev_api_key)

    @property
    def jev_url(self) -> str:
        return JEV_PROVIDERS[self.jev_provider][0] + "/v1/systemone"

    @property
    def jev_model_id(self) -> str:
        return self.jev_model or JEV_PROVIDERS[self.jev_provider][1]

    def validate(self) -> None:
        if self.mode not in ("paper", "testnet", "live"):
            raise ValueError(f"MODE must be paper, testnet or live, got {self.mode!r}")
        if not 0.5 < self.entry_prob < 1.0 or not 0.0 < self.exit_prob < self.entry_prob:
            raise ValueError("need 0 < EXIT_PROB < ENTRY_PROB and 0.5 < ENTRY_PROB < 1")
        if self.trade_horizon_s not in self.horizons_s:
            raise ValueError("TRADE_HORIZON_S must be one of HORIZONS_S")
        if self.risk_pct > 0.02:
            raise ValueError("RISK_PCT above the 2% hard ceiling")
        if self.mode in ("testnet", "live") and not (self.binance_api_key and self.binance_api_secret):
            raise ValueError(f"MODE={self.mode} needs BINANCE_API_KEY and BINANCE_API_SECRET")
        if self.mode == "live" and self.live_confirm != LIVE_CONFIRM_PHRASE:
            raise ValueError(f"MODE=live needs LIVE_CONFIRM={LIVE_CONFIRM_PHRASE}")
        # CLAUDE.md §6c and §6f: live trades only on proven edge, measured with the real
        # Indian cost model. Neither can be switched off for live by an env var.
        if self.mode == "live" and not self.require_edge_for_live:
            raise ValueError("REQUIRE_EDGE_FOR_LIVE cannot be false in live mode (CLAUDE.md §6c)")
        if self.alloc_lot_method not in ("fifo", "average"):
            raise ValueError("ALLOC_LOT_METHOD must be fifo or average (HIFO is analysis-only)")
        if self.mode == "live" and not self.require_positive_ev:
            raise ValueError("REQUIRE_POSITIVE_EV cannot be false in live mode")
        if self.exit_confirm < 1 or self.max_loss_streak < 1 or not 0 < self.max_drawdown_pct < 1:
            raise ValueError("EXIT_CONFIRM and MAX_LOSS_STREAK must be >= 1; 0 < MAX_DRAWDOWN_PCT < 1")
        if self.mode == "live" and not self.apply_tds:
            raise ValueError("APPLY_TDS cannot be false in live mode (CLAUDE.md §6f)")

    @property
    def cost_fingerprint(self) -> str:
        """Scored trade returns are only comparable under the same cost and entry rules.
        Outcomes recorded under a different fingerprint keep their probability scores but
        their simulated trades are not replayed into the edge test."""
        return f"e{self.entry_prob}|x{self.exit_prob}|f{self.fee_pct}|t{self.tds_pct if self.apply_tds else 0}|m{self.min_edge_multiple}"


def from_env(env_file: Path | None = None) -> Config:
    load_dotenv(env_file or ROOT / ".env")
    cfg = Config(
        mode=_env("MODE", "paper").lower(),
        symbols=tuple(s.strip().upper() for s in _env("SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT").split(",") if s.strip()),
        decision_interval_s=_float("DECISION_INTERVAL_S", 15.0),
        horizons_s=tuple(int(h) for h in _env("HORIZONS_S", "60,300,900").split(",")),
        trade_horizon_s=int(_env("TRADE_HORIZON_S", "300")),
        primary_signal=_env("PRIMARY_SIGNAL", "auto").lower(),
        jev_provider=_env("JEV_PROVIDER", "off").lower(),
        jev_api_key=_env("JEV_API_KEY", ""),
        jev_model=_env("JEV_MODEL", ""),
        jev_interval_s=_float("JEV_INTERVAL_S", 30.0),
        jev_daily_budget_usd=_float("JEV_DAILY_BUDGET_USD", 0.16),
        jev_timeout_s=_float("JEV_TIMEOUT_S", 2.5),
        jev_min_credit_usd=_float("JEV_MIN_CREDIT_USD", 0.25),
        entry_prob=_float("ENTRY_PROB", 0.60),
        exit_prob=_float("EXIT_PROB", 0.45),
        min_edge_multiple=_float("MIN_EDGE_MULTIPLE", 1.5),
        stop_vol_multiple=_float("STOP_VOL_MULTIPLE", 2.0),
        cooldown_s=_float("COOLDOWN_S", 60.0),
        min_hold_s=_float("MIN_HOLD_S", 60.0),
        exit_confirm=int(_env("EXIT_CONFIRM", "2")),
        require_positive_ev=_bool("REQUIRE_POSITIVE_EV", True),
        max_drawdown_pct=_float("MAX_DRAWDOWN_PCT", 0.05),
        max_loss_streak=int(_env("MAX_LOSS_STREAK", "3")),
        pause_after_streak_s=_float("PAUSE_AFTER_STREAK_S", 3600.0),
        max_trades_per_day=int(_env("MAX_TRADES_PER_DAY", "20")),
        max_total_exposure_pct=_float("MAX_TOTAL_EXPOSURE_PCT", 0.50),
        max_spread_bps=_float("MAX_SPREAD_BPS", 10.0),
        max_vol_ratio=_float("MAX_VOL_RATIO", 3.0),
        max_slippage_bps=_float("MAX_SLIPPAGE_BPS", 5.0),
        fee_pct=_float("FEE_PCT", 0.001),
        apply_tds=_bool("APPLY_TDS", True),
        paper_equity_usdt=_float("PAPER_EQUITY_USDT", 1000.0),
        risk_pct=_float("RISK_PCT", 0.01),
        daily_loss_limit_pct=_float("DAILY_LOSS_LIMIT_PCT", 0.03),
        max_notional_pct=_float("MAX_NOTIONAL_PCT", 0.25),
        binance_api_key=_env("BINANCE_API_KEY", ""),
        binance_api_secret=_env("BINANCE_API_SECRET", ""),
        live_confirm=_env("LIVE_CONFIRM", ""),
        require_edge_for_live=_bool("REQUIRE_EDGE_FOR_LIVE", True),
        copy_enabled=_bool("COPY_ENABLED", True),
        alloc_enabled=_bool("ALLOC_ENABLED", True),
        alloc_symbols=tuple(s.strip().upper() for s in _env("ALLOC_SYMBOLS", "BTCUSDT,ETHUSDT").split(",") if s.strip()),
        alloc_capital=_float("ALLOC_CAPITAL", 1000.0),
        alloc_extra_books=tuple(b.strip().upper() for b in _env("ALLOC_EXTRA_BOOKS", "BTCUSDT").split(";") if b.strip()),
        alloc_lot_method=_env("ALLOC_LOT_METHOD", "fifo").lower(),
        dashboard_port=int(_env("DASHBOARD_PORT", "8765")),
    )
    cfg.validate()
    return cfg
