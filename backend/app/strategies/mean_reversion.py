"""
Mean Reversion Strategy
Signal: buy when price is N std devs below its rolling mean; sell when above.
"""
import numpy as np
from app.exchange.base import Candle
from app.strategies.base import BaseStrategy, Signal, SignalDirection


class MeanReversionStrategy(BaseStrategy):
    name = "mean_reversion"
    description = "Bollinger Band mean reversion — range markets"
    default_parameters = {
        "period": 20,
        "std_dev": 2.0,
        "rsi_period": 14,
        "rsi_oversold": 30,
        "rsi_overbought": 70,
    }

    def generate_signal(self, candles: list[Candle], symbol: str, timeframe: str) -> Signal:
        period = self.get_parameter("period")
        if len(candles) < period + 5:
            return Signal(
                direction=SignalDirection.none, symbol=symbol, timeframe=timeframe,
                confidence=0.0, suggested_entry=candles[-1].close, reasoning="Not enough candles",
            )

        closes = np.array([c.close for c in candles], dtype=float)
        rolling_mean = np.mean(closes[-period:])
        rolling_std = np.std(closes[-period:])
        std_mult = self.get_parameter("std_dev")

        upper_band = rolling_mean + std_mult * rolling_std
        lower_band = rolling_mean - std_mult * rolling_std
        price = closes[-1]
        rsi = self._rsi(closes, self.get_parameter("rsi_period"))

        if price < lower_band and rsi < self.get_parameter("rsi_oversold"):
            confidence = min(0.9, (lower_band - price) / rolling_std)
            return Signal(
                direction=SignalDirection.long,
                symbol=symbol, timeframe=timeframe,
                confidence=confidence,
                suggested_entry=price,
                suggested_sl=price * 0.98,
                suggested_tp=rolling_mean,
                reasoning=(
                    f"Price {price:.4f} below lower band {lower_band:.4f} | "
                    f"RSI {rsi:.1f} < {self.get_parameter('rsi_oversold')} | "
                    f"Mean={rolling_mean:.4f}"
                ),
            )
        elif price > upper_band and rsi > self.get_parameter("rsi_overbought"):
            confidence = min(0.9, (price - upper_band) / rolling_std)
            return Signal(
                direction=SignalDirection.short,
                symbol=symbol, timeframe=timeframe,
                confidence=confidence,
                suggested_entry=price,
                suggested_sl=price * 1.02,
                suggested_tp=rolling_mean,
                reasoning=(
                    f"Price {price:.4f} above upper band {upper_band:.4f} | "
                    f"RSI {rsi:.1f} > {self.get_parameter('rsi_overbought')} | "
                    f"Mean={rolling_mean:.4f}"
                ),
            )
        else:
            return Signal(
                direction=SignalDirection.none, symbol=symbol, timeframe=timeframe,
                confidence=0.0, suggested_entry=price,
                reasoning=(
                    f"Price {price:.4f} within bands [{lower_band:.4f}, {upper_band:.4f}] | RSI {rsi:.1f}"
                ),
            )

    @staticmethod
    def _rsi(closes: np.ndarray, period: int) -> float:
        if len(closes) < period + 1:
            return 50.0
        deltas = np.diff(closes[-(period + 1):])
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains) if gains.any() else 1e-10
        avg_loss = np.mean(losses) if losses.any() else 1e-10
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
