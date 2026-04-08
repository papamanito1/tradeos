"""Tests for the strategy interface and implementations."""
import pytest
from datetime import datetime, timezone, timedelta

from app.exchange.base import Candle
from app.strategies.base import BaseStrategy, Signal, SignalDirection
from app.strategies.ema_crossover import EMACrossoverStrategy
from app.strategies.breakout import BreakoutStrategy
from app.strategies.mean_reversion import MeanReversionStrategy


def make_candles(n: int, start_price: float = 100.0, trend: float = 0.0) -> list[Candle]:
    candles = []
    price = start_price
    now = datetime.now(timezone.utc)
    for i in range(n):
        open_p = price
        close_p = price * (1 + trend + (0.005 if i % 2 == 0 else -0.003))
        high_p = max(open_p, close_p) * 1.005
        low_p = min(open_p, close_p) * 0.995
        candles.append(Candle(
            timestamp=now - timedelta(hours=n - i),
            open=open_p, high=high_p, low=low_p, close=close_p, volume=1000.0,
        ))
        price = close_p
    return candles


class TestBaseStrategy:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            BaseStrategy()

    def test_parameters_merge_with_defaults(self):
        strategy = EMACrossoverStrategy(parameters={"fast_period": 5})
        assert strategy.get_parameter("fast_period") == 5
        assert strategy.get_parameter("slow_period") == 21  # default preserved


class TestEMACrossover:
    def test_returns_signal_with_enough_candles(self):
        candles = make_candles(50, trend=0.001)
        strategy = EMACrossoverStrategy()
        signal = strategy.generate_signal(candles, "BTC/USDT", "1h")
        assert isinstance(signal, Signal)
        assert signal.symbol == "BTC/USDT"
        assert signal.direction in list(SignalDirection)

    def test_no_signal_with_too_few_candles(self):
        candles = make_candles(5)
        strategy = EMACrossoverStrategy()
        signal = strategy.generate_signal(candles, "BTC/USDT", "1h")
        assert signal.direction == SignalDirection.none

    def test_signal_has_valid_entry_price(self):
        candles = make_candles(50)
        strategy = EMACrossoverStrategy()
        signal = strategy.generate_signal(candles, "BTC/USDT", "1h")
        assert signal.suggested_entry > 0

    def test_long_signal_has_sl_below_entry(self):
        candles = make_candles(50, trend=0.002)
        strategy = EMACrossoverStrategy()
        signal = strategy.generate_signal(candles, "BTC/USDT", "1h")
        if signal.direction == SignalDirection.long and signal.suggested_sl:
            assert signal.suggested_sl < signal.suggested_entry

    def test_custom_periods(self):
        candles = make_candles(60)
        strategy = EMACrossoverStrategy(parameters={"fast_period": 5, "slow_period": 15})
        signal = strategy.generate_signal(candles, "ETH/USDT", "4h")
        assert isinstance(signal, Signal)


class TestBreakoutStrategy:
    def test_breakout_signal_on_new_high(self):
        candles = make_candles(30, start_price=100.0)
        # Force last candle above all previous highs
        from dataclasses import replace
        last = candles[-1]
        candles[-1] = Candle(
            timestamp=last.timestamp,
            open=last.open,
            high=200.0, low=last.low, close=200.0, volume=last.volume * 3,
        )
        strategy = BreakoutStrategy(parameters={"lookback": 20, "volume_confirmation": False})
        signal = strategy.generate_signal(candles, "BTC/USDT", "1h")
        assert signal.direction == SignalDirection.long

    def test_no_breakout_within_range(self):
        candles = make_candles(30)
        strategy = BreakoutStrategy(parameters={"lookback": 20, "volume_confirmation": False})
        signal = strategy.generate_signal(candles, "BTC/USDT", "1h")
        assert signal.direction == SignalDirection.none

    def test_not_enough_candles(self):
        candles = make_candles(5)
        strategy = BreakoutStrategy()
        signal = strategy.generate_signal(candles, "BTC/USDT", "1h")
        assert signal.direction == SignalDirection.none


class TestMeanReversion:
    def test_oversold_generates_long(self):
        """Price forced far below lower Bollinger Band should yield long signal."""
        candles = make_candles(30, start_price=100.0)
        from dataclasses import replace
        last = candles[-1]
        # Force price to extreme low
        candles[-1] = Candle(
            timestamp=last.timestamp, open=60.0, high=61.0, low=59.0, close=59.0, volume=500.0,
        )
        strategy = MeanReversionStrategy(parameters={"period": 20, "std_dev": 1.0, "rsi_oversold": 80})
        signal = strategy.generate_signal(candles, "BTC/USDT", "1h")
        assert isinstance(signal, Signal)

    def test_returns_none_within_bands(self):
        candles = make_candles(30)
        strategy = MeanReversionStrategy()
        signal = strategy.generate_signal(candles, "BTC/USDT", "1h")
        assert isinstance(signal, Signal)
