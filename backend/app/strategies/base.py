from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from app.exchange.base import Candle


class SignalDirection(str, Enum):
    long = "long"
    short = "short"
    exit = "exit"
    none = "none"


@dataclass
class Signal:
    direction: SignalDirection
    symbol: str
    timeframe: str
    confidence: float           # 0.0 – 1.0
    suggested_entry: float
    suggested_sl: Optional[float] = None
    suggested_tp: Optional[float] = None
    reasoning: str = ""
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class BaseStrategy(ABC):
    """
    All strategies extend this class.
    Implement generate_signal() only — risk checks and execution
    are handled by the execution agent.
    """

    name: str = "base"
    description: str = ""
    default_parameters: dict = {}

    def __init__(self, parameters: dict = None):
        self.parameters = {**self.default_parameters, **(parameters or {})}

    @abstractmethod
    def generate_signal(self, candles: list[Candle], symbol: str, timeframe: str) -> Signal:
        """
        Pure function: takes historical candles, returns a Signal.
        Must NOT have side effects (no DB, no orders).
        """
        ...

    def validate_parameters(self) -> bool:
        return True

    def get_parameter(self, key: str, default=None):
        return self.parameters.get(key, default)
