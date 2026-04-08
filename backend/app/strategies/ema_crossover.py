"""
EMA Crossover Strategy
Signal: buy when fast EMA crosses above slow EMA; sell when it crosses below.
"""
import numpy as np
from app.exchange.base import Candle
from app.strategies.base import BaseStrategy, Signal, SignalDirection


class EMACrossoverStrategy(BaseStrategy):
    name = "ema_crossover"
    description = "Fast EMA crosses slow EMA — trend-following"
    default_parameters = {
        "fast_period": 9,
        "slow_period": 21,
        "signal_period": 3,   # confirmation candles
        "sl_atr_multiplier": 1.5,
        "tp_atr_multiplier": 2.5,
        "atr_period": 14,
    }

    def generate_signal(self, candles: list[Candle], symbol: str, timeframe: str) -> Signal:
        if len(candles) < self.get_parameter("slow_period") + 5:
            return Signal(
                direction=SignalDirection.none,
                symbol=symbol, timeframe=timeframe,
                confidence=0.0, suggested_entry=candles[-1].close,
                reasoning="Not enough candles",
            )

        closes = np.array([c.close for c in candles], dtype=float)
        fast = self._ema(closes, self.get_parameter("fast_period"))
        slow = self._ema(closes, self.get_parameter("slow_period"))
        atr = self._atr(candles, self.get_parameter("atr_period"))

        current_price = closes[-1]
        sl_dist = atr * self.get_parameter("sl_atr_multiplier")
        tp_dist = atr * self.get_parameter("tp_atr_multiplier")

        # Cross detection: compare last two bars
        prev_fast_above = fast[-2] > slow[-2]
        curr_fast_above = fast[-1] > slow[-1]

        if not prev_fast_above and curr_fast_above:
            direction = SignalDirection.long
            confidence = min(0.95, abs(fast[-1] - slow[-1]) / current_price * 1000)
            return Signal(
                direction=direction,
                symbol=symbol, timeframe=timeframe,
                confidence=confidence,
                suggested_entry=current_price,
                suggested_sl=current_price - sl_dist,
                suggested_tp=current_price + tp_dist,
                reasoning=(
                    f"EMA{self.get_parameter('fast_period')} ({fast[-1]:.2f}) crossed above "
                    f"EMA{self.get_parameter('slow_period')} ({slow[-1]:.2f}). "
                    f"ATR={atr:.4f}"
                ),
            )
        elif prev_fast_above and not curr_fast_above:
            direction = SignalDirection.short
            confidence = min(0.95, abs(fast[-1] - slow[-1]) / current_price * 1000)
            return Signal(
                direction=direction,
                symbol=symbol, timeframe=timeframe,
                confidence=confidence,
                suggested_entry=current_price,
                suggested_sl=current_price + sl_dist,
                suggested_tp=current_price - tp_dist,
                reasoning=(
                    f"EMA{self.get_parameter('fast_period')} ({fast[-1]:.2f}) crossed below "
                    f"EMA{self.get_parameter('slow_period')} ({slow[-1]:.2f}). "
                    f"ATR={atr:.4f}"
                ),
            )
        else:
            trend = "above" if curr_fast_above else "below"
            return Signal(
                direction=SignalDirection.none,
                symbol=symbol, timeframe=timeframe,
                confidence=0.0,
                suggested_entry=current_price,
                reasoning=f"Fast EMA {trend} slow EMA — no crossover",
            )

    @staticmethod
    def _ema(data: np.ndarray, period: int) -> np.ndarray:
        alpha = 2 / (period + 1)
        ema = np.zeros_like(data)
        ema[0] = data[0]
        for i in range(1, len(data)):
            ema[i] = alpha * data[i] + (1 - alpha) * ema[i - 1]
        return ema

    @staticmethod
    def _atr(candles: list[Candle], period: int) -> float:
        trs = []
        for i in range(1, len(candles)):
            h = candles[i].high
            l = candles[i].low
            pc = candles[i - 1].close
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        if not trs:
            return 0.01
        return float(np.mean(trs[-period:]))
