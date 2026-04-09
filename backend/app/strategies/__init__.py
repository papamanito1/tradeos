from app.strategies.base import BaseStrategy, Signal, SignalDirection
from app.strategies.ema_crossover import EMACrossoverStrategy
from app.strategies.breakout import BreakoutStrategy
from app.strategies.mean_reversion import MeanReversionStrategy
from app.strategies.quantum_order_flow_scalper import QuantumOrderFlowScalper
from app.strategies.smart_money_sweep import SmartMoneySweep
from app.strategies.hft_book_scalper import HftBookScalper
from app.strategies.btc_momentum_velocity import BtcMomentumVelocity
from app.strategies.hft_vwap_scalper import HftVwapScalper

STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {
    "ema_crossover": EMACrossoverStrategy,
    "breakout": BreakoutStrategy,
    "mean_reversion": MeanReversionStrategy,
    "quantum_order_flow_scalper": QuantumOrderFlowScalper,
    "smart_money_sweep": SmartMoneySweep,
    "hft_book_scalper": HftBookScalper,
    "btc_momentum_velocity": BtcMomentumVelocity,
    "hft_vwap_scalper": HftVwapScalper,
}

__all__ = [
    "BaseStrategy", "Signal", "SignalDirection",
    "EMACrossoverStrategy", "BreakoutStrategy", "MeanReversionStrategy",
    "QuantumOrderFlowScalper", "SmartMoneySweep", "HftBookScalper",
    "BtcMomentumVelocity", "HftVwapScalper",
    "STRATEGY_REGISTRY",
]
