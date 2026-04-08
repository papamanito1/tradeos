"""
Breakout Strategy
Signal: buy when price breaks above N-period high; sell when below N-period low.
"""
import numpy as np
from app.exchange.base import Candle
from app.strategies.base import BaseStrategy, Signal, SignalDirection


class BreakoutStrategy(BaseStrategy):
    name = "breakout"
    description = "N-period high/low breakout — momentum"
    default_parameters = {
        "lookback": 20,
        "volume_confirmation": True,
        "volume_multiplier": 1.5,
        "sl_pct": 0.02,
        "tp_pct": 0.04,
    }

    def generate_signal(self, candles: list[Candle], symbol: str, timeframe: str) -> Signal:
        lookback = self.get_parameter("lookback")
        if len(candles) < lookback + 2:
            return Signal(
                direction=SignalDirection.none, symbol=symbol, timeframe=timeframe,
                confidence=0.0, suggested_entry=candles[-1].close, reasoning="Not enough candles",
            )

        prev_candles = candles[-(lookback + 1):-1]
        current = candles[-1]

        period_high = max(c.high for c in prev_candles)
        period_low = min(c.low for c in prev_candles)
        avg_volume = np.mean([c.volume for c in prev_candles])

        price = current.close
        volume_ok = (not self.get_parameter("volume_confirmation")) or \
                    (current.volume > avg_volume * self.get_parameter("volume_multiplier"))

        if price > period_high and volume_ok:
            confidence = min(0.9, (price - period_high) / period_high * 100)
            return Signal(
                direction=SignalDirection.long,
                symbol=symbol, timeframe=timeframe,
                confidence=confidence,
                suggested_entry=price,
                suggested_sl=price * (1 - self.get_parameter("sl_pct")),
                suggested_tp=price * (1 + self.get_parameter("tp_pct")),
                reasoning=(
                    f"Price {price:.4f} broke above {lookback}-period high {period_high:.4f}. "
                    f"Volume: {current.volume:.0f} vs avg {avg_volume:.0f}"
                ),
            )
        elif price < period_low and volume_ok:
            confidence = min(0.9, (period_low - price) / period_low * 100)
            return Signal(
                direction=SignalDirection.short,
                symbol=symbol, timeframe=timeframe,
                confidence=confidence,
                suggested_entry=price,
                suggested_sl=price * (1 + self.get_parameter("sl_pct")),
                suggested_tp=price * (1 - self.get_parameter("tp_pct")),
                reasoning=(
                    f"Price {price:.4f} broke below {lookback}-period low {period_low:.4f}. "
                    f"Volume: {current.volume:.0f} vs avg {avg_volume:.0f}"
                ),
            )
        else:
            return Signal(
                direction=SignalDirection.none, symbol=symbol, timeframe=timeframe,
                confidence=0.0, suggested_entry=price,
                reasoning=f"No breakout — range [{period_low:.4f}, {period_high:.4f}]",
            )
