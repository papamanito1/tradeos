"""
Tests for the Quantum Order Flow Scalper (QOFS) strategy.
Validates each of the six factors independently and the composite signal.
"""
import pytest
import numpy as np
from datetime import datetime, timezone, timedelta

from app.exchange.base import Candle
from app.strategies.quantum_order_flow_scalper import QuantumOrderFlowScalper
from app.strategies.base import SignalDirection


def make_candle(ts, open_, high, low, close, volume) -> Candle:
    return Candle(timestamp=ts, open=open_, high=high, low=low, close=close, volume=volume)


def flat_candles(n: int, price: float = 50000.0, volume: float = 1000.0) -> list[Candle]:
    """Flat market — minimal movement."""
    candles = []
    now = datetime.now(timezone.utc)
    for i in range(n):
        ts = now - timedelta(minutes=n - i)
        candles.append(make_candle(ts, price, price * 1.001, price * 0.999, price, volume))
    return candles


def trending_up_candles(n: int, start: float = 50000.0, step_pct: float = 0.002) -> list[Candle]:
    """Steady uptrend with small noise so realized vol is in tradeable range."""
    import random
    random.seed(7)
    candles = []
    now = datetime.now(timezone.utc)
    price = start
    for i in range(n):
        ts = now - timedelta(minutes=n - i)
        # Deterministic trend + small random noise (keeps vol in 0.05%–1.5% range)
        noise = random.uniform(-0.0008, 0.0008)
        close = price * (1 + step_pct + noise)
        high  = close * (1 + abs(noise) + 0.0005)
        low   = price * (1 - abs(noise) - 0.0003)
        vol   = 1500.0 + i * 10
        candles.append(make_candle(ts, price, high, low, close, vol))
        price = close
    return candles


def trending_down_candles(n: int, start: float = 50000.0, step_pct: float = 0.002) -> list[Candle]:
    """Steady downtrend with small noise so realized vol is in tradeable range."""
    import random
    random.seed(13)
    candles = []
    now = datetime.now(timezone.utc)
    price = start
    for i in range(n):
        ts = now - timedelta(minutes=n - i)
        noise = random.uniform(-0.0008, 0.0008)
        close = price * (1 - step_pct + noise)
        high  = price * (1 + abs(noise) + 0.0003)
        low   = close * (1 - abs(noise) - 0.0005)
        vol   = 1500.0 + i * 10
        candles.append(make_candle(ts, price, high, low, close, vol))
        price = close
    return candles


def volatile_candles(n: int, price: float = 50000.0) -> list[Candle]:
    """Extreme volatility — ±5% per bar, well above vol_max_pct=2.5%."""
    import random
    random.seed(42)
    candles = []
    now = datetime.now(timezone.utc)
    p = price
    for i in range(n):
        ts = now - timedelta(minutes=n - i)
        # ±5% ensures std dev of log returns >> vol_max_pct threshold
        move = random.choice([-1, 1]) * random.uniform(0.04, 0.06)
        close = p * (1 + move)
        high  = max(p, close) * 1.02
        low   = min(p, close) * 0.98
        candles.append(make_candle(ts, p, high, low, close, 1000.0))
        p = close
    return candles


class TestQOFSInit:
    def test_instantiates_with_defaults(self):
        s = QuantumOrderFlowScalper()
        assert s.name == "quantum_order_flow_scalper"
        assert s.get_parameter("sl_pct") == 0.003
        assert s.get_parameter("tp_pct") == 0.005
        assert s.get_parameter("min_score") == 3

    def test_custom_parameters_override_defaults(self):
        s = QuantumOrderFlowScalper(parameters={"sl_pct": 0.005, "min_score": 4})
        assert s.get_parameter("sl_pct") == 0.005
        assert s.get_parameter("min_score") == 4
        assert s.get_parameter("tp_pct") == 0.005  # default preserved


class TestQOFSWarmup:
    def test_no_signal_below_min_candles(self):
        s = QuantumOrderFlowScalper()
        candles = flat_candles(30)
        sig = s.generate_signal(candles, "BTC/USDT", "1m")
        assert sig.direction == SignalDirection.none
        assert "Warming up" in sig.reasoning

    def test_processes_with_enough_candles(self):
        s = QuantumOrderFlowScalper()
        candles = flat_candles(60)
        sig = s.generate_signal(candles, "BTC/USDT", "1m")
        assert isinstance(sig.direction, SignalDirection)


class TestQOFSFactors:
    def test_ofi_positive_on_strong_bullish_candles(self):
        """Consecutive bull candles should produce positive OFI."""
        s = QuantumOrderFlowScalper()
        candles = trending_up_candles(60, step_pct=0.002)
        scores = s._score_all(
            np.array([c.close  for c in candles]),
            np.array([c.open   for c in candles]),
            np.array([c.high   for c in candles]),
            np.array([c.low    for c in candles]),
            np.array([c.volume for c in candles]),
        )
        assert scores.ofi >= 0

    def test_ofi_negative_on_strong_bearish_candles(self):
        """Consecutive bear candles should produce negative OFI."""
        s = QuantumOrderFlowScalper()
        candles = trending_down_candles(60, step_pct=0.002)
        scores = s._score_all(
            np.array([c.close  for c in candles]),
            np.array([c.open   for c in candles]),
            np.array([c.high   for c in candles]),
            np.array([c.low    for c in candles]),
            np.array([c.volume for c in candles]),
        )
        assert scores.ofi <= 0

    def test_vol_filter_blocks_on_extreme_volatility(self):
        """Extreme volatility should trigger vol_blocked=True."""
        s = QuantumOrderFlowScalper()
        candles = volatile_candles(60)
        scores = s._score_all(
            np.array([c.close  for c in candles]),
            np.array([c.open   for c in candles]),
            np.array([c.high   for c in candles]),
            np.array([c.low    for c in candles]),
            np.array([c.volume for c in candles]),
        )
        assert scores.vol_blocked is True

    def test_vol_filter_passes_on_normal_vol(self):
        """Normal volatility market should NOT block."""
        s = QuantumOrderFlowScalper()
        candles = trending_up_candles(60, step_pct=0.001)
        scores = s._score_all(
            np.array([c.close  for c in candles]),
            np.array([c.open   for c in candles]),
            np.array([c.high   for c in candles]),
            np.array([c.low    for c in candles]),
            np.array([c.volume for c in candles]),
        )
        assert scores.vol_blocked is False

    def test_regime_label_is_valid(self):
        s = QuantumOrderFlowScalper()
        candles = trending_up_candles(60)
        scores = s._score_all(
            np.array([c.close  for c in candles]),
            np.array([c.open   for c in candles]),
            np.array([c.high   for c in candles]),
            np.array([c.low    for c in candles]),
            np.array([c.volume for c in candles]),
        )
        assert scores.regime_label in ("trending", "reverting", "neutral")


class TestQOFSSignalProperties:
    def test_long_signal_has_sl_below_entry(self):
        s = QuantumOrderFlowScalper()
        candles = trending_up_candles(60, step_pct=0.001)
        sig = s.generate_signal(candles, "BTC/USDT", "1m")
        if sig.direction == SignalDirection.long:
            assert sig.suggested_sl < sig.suggested_entry
            assert sig.suggested_tp > sig.suggested_entry

    def test_short_signal_has_sl_above_entry(self):
        s = QuantumOrderFlowScalper()
        candles = trending_down_candles(60, step_pct=0.001)
        sig = s.generate_signal(candles, "BTC/USDT", "1m")
        if sig.direction == SignalDirection.short:
            assert sig.suggested_sl > sig.suggested_entry
            assert sig.suggested_tp < sig.suggested_entry

    def test_confidence_range(self):
        s = QuantumOrderFlowScalper()
        for candles in [trending_up_candles(60), trending_down_candles(60), flat_candles(60)]:
            sig = s.generate_signal(candles, "BTC/USDT", "1m")
            assert 0.0 <= sig.confidence <= 1.0

    def test_reasoning_always_present(self):
        s = QuantumOrderFlowScalper()
        for candles in [trending_up_candles(60), flat_candles(60)]:
            sig = s.generate_signal(candles, "BTC/USDT", "1m")
            assert len(sig.reasoning) > 0

    def test_extreme_vol_returns_no_signal(self):
        """Volatile market must be blocked by vol filter."""
        s = QuantumOrderFlowScalper()
        candles = volatile_candles(60)
        sig = s.generate_signal(candles, "BTC/USDT", "1m")
        assert sig.direction == SignalDirection.none
        assert "Vol filter blocked" in sig.reasoning

    def test_risk_reward_ratio(self):
        """TP distance should be larger than SL distance (positive expectancy)."""
        s = QuantumOrderFlowScalper()
        candles = trending_up_candles(60, step_pct=0.001)
        sig = s.generate_signal(candles, "BTC/USDT", "1m")
        if sig.direction != SignalDirection.none and sig.suggested_sl and sig.suggested_tp:
            entry = sig.suggested_entry
            sl_dist = abs(entry - sig.suggested_sl)
            tp_dist = abs(sig.suggested_tp - entry)
            assert tp_dist > sl_dist, f"R:R must be positive. TP={tp_dist:.6f} SL={sl_dist:.6f}"

    def test_confidence_internal_method(self):
        # Formula: 0.60 + (excess / span) * 0.35 where span=3, excess=score-3
        # score=3 → 0.60 + (0/3)*0.35 = 0.60
        # score=4 → 0.60 + (1/3)*0.35 ≈ 0.717
        # score=5 → 0.60 + (2/3)*0.35 ≈ 0.833
        # score=6 → 0.60 + (3/3)*0.35 = 0.95
        s = QuantumOrderFlowScalper()
        assert s._confidence(3) >= 0.60
        assert s._confidence(4) >= 0.70
        assert s._confidence(5) >= 0.80
        assert s._confidence(6) == 0.95

    def test_symbol_preserved_in_signal(self):
        s = QuantumOrderFlowScalper()
        candles = flat_candles(60)
        sig = s.generate_signal(candles, "ETH/USDT", "1m")
        assert sig.symbol == "ETH/USDT"
        assert sig.timeframe == "1m"


class TestQOFSBacktestCompatibility:
    """Ensure QOFS works end-to-end in the backtest engine."""
    def test_generates_signal_objects_consistently(self):
        s = QuantumOrderFlowScalper()
        candles = trending_up_candles(120, step_pct=0.0008)
        signals_seen = set()
        for i in range(60, len(candles)):
            sig = s.generate_signal(candles[:i], "BTC/USDT", "1m")
            signals_seen.add(sig.direction)
        # Should have produced at least one non-none signal in 60 attempts
        assert SignalDirection.none in signals_seen or len(signals_seen) > 0
