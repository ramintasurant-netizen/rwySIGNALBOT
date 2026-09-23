"""Strategi sinyal. Setiap strategi bersifat murni: frame masuk → StrategyOutcome keluar."""

from engine.strategies.base import Strategy, StrategyContext
from engine.strategies.breakout import BreakoutStrategy
from engine.strategies.foreign_flow import ForeignFlowStrategy
from engine.strategies.reversal import ReversalStrategy
from engine.strategies.trend_pullback import TrendPullbackStrategy

DEFAULT_STRATEGIES: tuple[type[Strategy], ...] = (
    TrendPullbackStrategy,
    BreakoutStrategy,
    ReversalStrategy,
    ForeignFlowStrategy,
)

__all__ = [
    "DEFAULT_STRATEGIES",
    "BreakoutStrategy",
    "ForeignFlowStrategy",
    "ReversalStrategy",
    "Strategy",
    "StrategyContext",
    "TrendPullbackStrategy",
]
