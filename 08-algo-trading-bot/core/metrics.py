"""Strategy scorecard. The one module whose job is to refuse to flatter you.

Three failure modes destroy more retail algo accounts than bad strategies do:

1. Judging a strategy by win rate. A 90% win rate that loses 10x on the loser is a
   losing strategy. Expectancy, the average R per trade, is the number that decides
   whether a system makes money. Win rate alone is close to meaningless.

2. Reading an edge into a small sample. Thirty trades cannot distinguish a good system
   from a lucky one. This module always reports a confidence interval, never a bare
   average, and refuses to call anything significant below a minimum sample.

3. Multiple testing. Backtest forty strategies, keep the best, and its apparent edge is
   inflated by selection alone: with forty coin flips one will look brilliant. Every
   result here is adjusted for how many variants were tried, and the caller is required
   to declare that count honestly.

Everything is computed in R multiples, net P&L divided by the risk planned at entry, so
results are comparable across instruments, currencies, and account sizes. An R from a
USD/JPY lot and an R from a GOLDBEES position mean the same thing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from statistics import NormalDist
from typing import Optional, Sequence

# Below this, no claim of edge is permitted regardless of how good the numbers look.
MIN_SAMPLE_FOR_SIGNIFICANCE = 30

INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
NO_EDGE_DEMONSTRATED = "NO_EDGE_DEMONSTRATED"
NEGATIVE_EDGE = "NEGATIVE_EDGE"
EDGE_PLAUSIBLE = "EDGE_PLAUSIBLE"


@dataclass(frozen=True)
class Scorecard:
    n: int
    win_rate: float           # informational only, never a verdict input
    avg_r: float              # expectancy, the number that actually matters
    r_std: float
    total_r: float
    profit_factor: float
    payoff_ratio: float
    max_drawdown_r: float
    longest_losing_streak: int
    t_stat: float
    t_required: float         # threshold after adjusting for strategies_tested
    ci_low: float
    ci_high: float
    strategies_tested: int
    significant: bool
    trades_needed: Optional[int]
    verdict: str

    def as_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        """Plain-language report. Deliberately leads with the verdict, not the win rate,
        because the win rate is the number people latch onto and it is the least useful."""
        lines = [
            f"VERDICT: {self.verdict}",
            f"  sample            {self.n} trades"
            + ("" if self.n >= MIN_SAMPLE_FOR_SIGNIFICANCE else "   (below the 30 minimum)"),
            f"  expectancy        {self.avg_r:+.3f}R per trade",
            f"  95% CI            {self.ci_low:+.3f}R to {self.ci_high:+.3f}R",
            f"  total             {self.total_r:+.2f}R",
            f"  win rate          {self.win_rate:.1%}   (informational, not a verdict)",
            f"  payoff ratio      {self.payoff_ratio:.2f}  avg win / avg loss",
            f"  profit factor     {self.profit_factor:.2f}",
            f"  max drawdown      {self.max_drawdown_r:.2f}R",
            f"  worst streak      {self.longest_losing_streak} losses in a row",
            f"  t-stat            {self.t_stat:.2f} against a required {self.t_required:.2f}"
            f" ({self.strategies_tested} variant(s) tested)",
        ]
        if self.trades_needed:
            lines.append(
                f"  NOTE: at this effect size you need about {self.trades_needed} trades"
                " before the result can be distinguished from luck."
            )
        if self.ci_low < 0 < self.ci_high:
            lines.append(
                "  NOTE: the confidence interval spans zero. A true expectancy of exactly"
                " zero is consistent with this data."
            )
        return "\n".join(lines)


def max_drawdown_r(r_multiples: Sequence[float]) -> float:
    """Deepest peak-to-trough decline of the cumulative R curve, as a positive number.

    Computed on the trade-ordered cumulative curve, so it answers "what is the worst
    losing run this strategy actually put me through", which is the number that decides
    whether a human can keep running it.
    """
    peak = 0.0
    cum = 0.0
    worst = 0.0
    for r in r_multiples:
        cum += r
        peak = max(peak, cum)
        worst = max(worst, peak - cum)
    return worst


def longest_losing_streak(r_multiples: Sequence[float]) -> int:
    streak = worst = 0
    for r in r_multiples:
        streak = streak + 1 if r < 0 else 0
        worst = max(worst, streak)
    return worst


def required_sample_size(avg_r: float, r_std: float, alpha: float = 0.05,
                         power: float = 0.80) -> Optional[int]:
    """Trades needed to distinguish this effect size from zero at the given confidence
    and power. Returns None when the observed edge is not positive.

    This is the number nobody computes and everybody needs. A strategy averaging 0.05R
    with a 1.0R standard deviation needs roughly 3,100 trades before the edge is
    demonstrable. If your plan produces two trades a day, that is six years.
    """
    if avg_r <= 0 or r_std <= 0:
        return None
    z_a = NormalDist().inv_cdf(1 - alpha / 2)
    z_p = NormalDist().inv_cdf(power)
    return math.ceil((z_a + z_p) ** 2 * (r_std / avg_r) ** 2)


def _t_threshold(alpha: float, strategies_tested: int) -> float:
    """Bonferroni-adjusted critical value. Testing k variants and keeping the best means
    the effective significance threshold is alpha/k, not alpha."""
    k = max(1, int(strategies_tested))
    return NormalDist().inv_cdf(1 - (alpha / k) / 2)


def scorecard(
    r_multiples: Sequence[float],
    strategies_tested: int = 1,
    alpha: float = 0.05,
) -> Scorecard:
    """Score a set of closed trades.

    strategies_tested must be the honest count of every variant, parameter set, and
    symbol combination evaluated before settling on this one. Passing 1 after trying
    fifty is not a shortcut, it is how a random walk gets promoted to production.
    """
    rs = [float(r) for r in r_multiples]
    n = len(rs)
    if n == 0:
        raise ValueError("cannot score an empty trade list")
    for r in rs:
        if not math.isfinite(r):
            raise ValueError("R multiples must all be finite")

    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    total = math.fsum(rs)
    avg = total / n
    var = math.fsum((r - avg) ** 2 for r in rs) / (n - 1) if n > 1 else 0.0
    std = math.sqrt(var)

    gross_win = math.fsum(wins)
    gross_loss = abs(math.fsum(losses))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else math.inf
    payoff = ((gross_win / len(wins)) / (gross_loss / len(losses))
              if wins and losses else math.inf if wins else 0.0)

    se = std / math.sqrt(n) if std > 0 and n > 1 else 0.0
    t_stat = avg / se if se > 0 else (math.inf if avg > 0 else -math.inf if avg < 0 else 0.0)
    t_req = _t_threshold(alpha, strategies_tested)
    z_ci = NormalDist().inv_cdf(1 - alpha / 2)
    ci_low, ci_high = (avg - z_ci * se, avg + z_ci * se) if se > 0 else (avg, avg)

    enough = n >= MIN_SAMPLE_FOR_SIGNIFICANCE
    significant = enough and abs(t_stat) >= t_req

    if not enough:
        verdict = INSUFFICIENT_DATA
    elif significant and avg > 0:
        verdict = EDGE_PLAUSIBLE
    elif significant and avg < 0:
        verdict = NEGATIVE_EDGE
    else:
        verdict = NO_EDGE_DEMONSTRATED

    return Scorecard(
        n=n,
        win_rate=len(wins) / n,
        avg_r=avg,
        r_std=std,
        total_r=total,
        profit_factor=profit_factor,
        payoff_ratio=payoff,
        max_drawdown_r=max_drawdown_r(rs),
        longest_losing_streak=longest_losing_streak(rs),
        t_stat=t_stat,
        t_required=t_req,
        ci_low=ci_low,
        ci_high=ci_high,
        strategies_tested=max(1, int(strategies_tested)),
        significant=significant,
        trades_needed=required_sample_size(avg, std, alpha),
        verdict=verdict,
    )


def vs_benchmark(strategy_return_pct: float, benchmark_return_pct: float,
                 strategy_max_dd_pct: float, benchmark_max_dd_pct: float) -> dict:
    """Compare a strategy against simply buying and holding the same instrument.

    This is the question that invalidates most published backtests. A strategy that
    returned 400% on Bitcoin since 2015 sounds extraordinary until you notice buy-and-hold
    returned more with less work. The honest verdict has to account for drawdown too: a
    strategy that matches the benchmark's return while halving its drawdown has earned its
    complexity, and one that beats it by 5% while doubling the drawdown has not.
    """
    excess = strategy_return_pct - benchmark_return_pct
    beat = excess > 0
    dd_better = strategy_max_dd_pct < benchmark_max_dd_pct
    if beat and dd_better:
        verdict = "BEATS_BENCHMARK"
    elif beat:
        verdict = "BEATS_ON_RETURN_ONLY"
    elif dd_better:
        verdict = "LOWER_RETURN_LOWER_RISK"
    else:
        verdict = "UNDERPERFORMS"
    return {
        "strategy_return_pct": strategy_return_pct,
        "benchmark_return_pct": benchmark_return_pct,
        "excess_return_pct": excess,
        "strategy_max_dd_pct": strategy_max_dd_pct,
        "benchmark_max_dd_pct": benchmark_max_dd_pct,
        "verdict": verdict,
    }


def sharpe(period_returns: Sequence[float], periods_per_year: int) -> float:
    """Annualized Sharpe on periodic returns, risk-free assumed zero.

    Reported for comparability with published results, and it should be read with
    suspicion on any sample under a couple of years: Sharpe is as vulnerable to small
    samples and to multiple testing as expectancy is, and it is quoted far more often
    than it is earned.
    """
    rets = [float(r) for r in period_returns]
    if len(rets) < 2:
        raise ValueError("need at least two periods")
    mean = math.fsum(rets) / len(rets)
    var = math.fsum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return math.inf if mean > 0 else 0.0
    return (mean / sd) * math.sqrt(periods_per_year)
