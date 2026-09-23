"""Strategi sinyal. Setiap strategi bersifat murni: frame masuk → StrategyOutcome keluar."""

from engine.strategies.base import Strategy, StrategyContext
from engine.strategies.breakout import BreakoutStrategy
from engine.strategies.foreign_flow import ForeignFlowStrategy
from engine.strategies.money_flow_proxy import MoneyFlowProxyStrategy
from engine.strategies.reversal import ReversalStrategy
from engine.strategies.smart_money import SmartMoneyStrategy
from engine.strategies.trend_pullback import TrendPullbackStrategy

DEFAULT_STRATEGIES: tuple[type[Strategy], ...] = (
    TrendPullbackStrategy,
    BreakoutStrategy,
    ReversalStrategy,
    ForeignFlowStrategy,
    SmartMoneyStrategy,
    MoneyFlowProxyStrategy,
)

__all__ = [
    "DEFAULT_STRATEGIES",
    "BreakoutStrategy",
    "ForeignFlowStrategy",
    "MoneyFlowProxyStrategy",
    "ReversalStrategy",
    "SmartMoneyStrategy",
    "Strategy",
    "StrategyContext",
    "TrendPullbackStrategy",
]
