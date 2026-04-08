from app.strategies.base import BaseStrategy, Signal, SignalDirection
from app.strategies.ema_crossover import EMACrossoverStrategy
from app.strategies.breakout import BreakoutStrategy
from app.strategies.mean_reversion import MeanReversionStrategy
from app.strategies.quantum_order_flow_scalper import QuantumOrderFlowScalper

STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {
    "ema_crossover": EMACrossoverStrategy,
    "breakout": BreakoutStrategy,
    "mean_reversion": MeanReversionStrategy,
    "quantum_order_flow_scalper": QuantumOrderFlowScalper,
}

__all__ = [
    "BaseStrategy", "Signal", "SignalDirection",
    "EMACrossoverStrategy", "BreakoutStrategy", "MeanReversionStrategy",
    "QuantumOrderFlowScalper",
    "STRATEGY_REGISTRY",
]
