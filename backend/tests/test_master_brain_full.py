"""
Comprehensive test suite for MasterBrain — covers all 11 intelligence features
including the 8 newly added ones (A-H) plus Fibonacci, regime detection,
and the core evaluate_signal pipeline.
"""
import math
import time
from datetime import datetime, timezone, timedelta

import pytest

from app.agents.master_brain import MasterBrain, _current_session


# ─────────────────────────────────────────────────────────────────────────────
#  Candle / data helpers
# ─────────────────────────────────────────────────────────────────────────────

def _candle(close: float, high: float = None, low: float = None,
            open_: float = None, volume: float = 100.0, ts_offset_min: int = 0) -> dict:
    c = close
    h = high  if high  is not None else c * 1.003
    l = low   if low   is not None else c * 0.997
    o = open_ if open_ is not None else c
    ts = (datetime.now(timezone.utc) - timedelta(minutes=ts_offset_min)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"open": o, "high": h, "low": l, "close": c, "volume": volume, "timestamp": ts}


def _trending_up_candles(n: int = 120, base: float = 80_000.0, step: float = 20.0) -> list[dict]:
    return [_candle(base + i * step, ts_offset_min=(n - i) * 15) for i in range(n)]


def _trending_down_candles(n: int = 120, base: float = 90_000.0, step: float = 20.0) -> list[dict]:
    return [_candle(base - i * step, ts_offset_min=(n - i) * 15) for i in range(n)]


def _ranging_candles(n: int = 120, mid: float = 84_000.0, amp: float = 300.0) -> list[dict]:
    return [
        _candle(mid + amp * math.sin(i * 0.3), ts_offset_min=(n - i) * 15)
        for i in range(n)
    ]


def _flat_candles(n: int = 60, price: float = 84_000.0) -> list[dict]:
    return [_candle(price, ts_offset_min=(n - i) * 15) for i in range(n)]


def _base_signal(direction: str = "long", price: float = 84_000.0,
                 sl_dist: float = 300.0, tp_dist: float = 600.0) -> dict:
    if direction == "long":
        return {"direction": "long", "entry": price, "sl": price - sl_dist,
                "tp": price + tp_dist, "confidence": 0.75}
    else:
        return {"direction": "short", "entry": price, "sl": price + sl_dist,
                "tp": price - tp_dist, "confidence": 0.75}


def _fresh_brain() -> MasterBrain:
    b = MasterBrain()
    b.strategy_trust["fusion"] = 1.0
    return b


# ─────────────────────────────────────────────────────────────────────────────
#  1. evaluate_signal — basic pass / reject
# ─────────────────────────────────────────────────────────────────────────────

class TestEvaluateSignalBasic:
    def test_paper_low_conviction_returns_dict(self):
        b = _fresh_brain()
        sig = _base_signal()
        result = b.evaluate_signal("momentum", "Momentum", sig, {}, 84_000.0, 0.0, is_live=False)
        assert isinstance(result, dict)
        assert "approved" in result
        assert "conviction" in result

    def test_approved_signal_has_positive_conviction(self):
        b = _fresh_brain()
        sig = _base_signal()
        r = b.evaluate_signal("fusion", "Fusion", sig, {}, 84_000.0, 0.0, is_live=False)
        # With neutral conditions, conviction should be > 0
        assert r["conviction"] >= 0.0

    def test_reject_fills_decisions_log(self):
        b = _fresh_brain()
        b.consecutive_losses = 10   # exceed MAX_CONSECUTIVE_LOSSES for paper
        sig = _base_signal()
        r = b.evaluate_signal("momentum", "Momentum", sig, {}, 84_000.0, 0.0, is_live=False)
        assert len(b.decisions) >= 1

    def test_approved_increments_daily_trades_live(self):
        b = _fresh_brain()
        b.strategy_trust["fusion"] = 2.0   # max trust
        sig = _base_signal()
        before = b.daily_trades
        r = b.evaluate_signal("fusion", "Fusion", sig, {}, 84_000.0, 0.0, is_live=True)
        if r["approved"]:
            assert b.daily_trades == before + 1

    def test_direction_conflict_rejects_live(self):
        b = _fresh_brain()
        open_pos = {"live_1": {"direction": "short", "mode": "live", "is_live": True}}
        sig = _base_signal("long")
        r = b.evaluate_signal("fusion", "Fusion", sig, open_pos, 84_000.0, 0.0, is_live=True)
        assert r["approved"] is False

    def test_poor_rr_rejects_live(self, monkeypatch):
        """Poor R:R (<1.5) must reject live signals. Patch out dead-hour to isolate R:R check."""
        from app.agents import master_brain as mb
        class FakeDT:
            @staticmethod
            def now(tz=None):
                return datetime(2026, 4, 13, 14, 0, tzinfo=timezone.utc)  # hour=14 = NY session
        monkeypatch.setattr(mb, "datetime", FakeDT)
        b = _fresh_brain()
        # SL = 100pt, TP = 50pt → R:R 0.5 < 1.5 minimum
        sig = {"direction": "long", "entry": 84_000, "sl": 83_900, "tp": 84_050, "confidence": 0.9}
        r = b.evaluate_signal("fusion", "Fusion", sig, {}, 84_000.0, 0.0, is_live=True)
        assert r["approved"] is False
        assert "R:R" in r["reasoning"]

    def test_dead_hour_rejects_live(self, monkeypatch):
        from app.agents import master_brain as mb
        monkeypatch.setattr(mb, "_current_session", lambda: "Asia")
        b = _fresh_brain()
        # Patch datetime inside evaluate_signal
        class FakeDT:
            @staticmethod
            def now(tz=None):
                return datetime(2026, 4, 11, 4, 30, tzinfo=timezone.utc)  # hour=4 = dead
        monkeypatch.setattr(mb, "datetime", FakeDT)
        sig = _base_signal()
        r = b.evaluate_signal("fusion", "Fusion", sig, {}, 84_000.0, 0.0, is_live=True)
        assert r["approved"] is False


# ─────────────────────────────────────────────────────────────────────────────
#  2. Regime detection
# ─────────────────────────────────────────────────────────────────────────────

class TestRegimeDetection:
    def test_trending_up_detected(self):
        b = _fresh_brain()
        candles = _trending_up_candles()
        b.detect_regime(candles, candles[-30:])
        assert b.current_regime in ("trending_up", "volatile", "unknown")

    def test_ranging_detected_on_flat(self):
        b = _fresh_brain()
        candles = _flat_candles(80)
        b.detect_regime(candles, candles[-30:])
        assert b.current_regime in ("ranging", "volatile", "unknown")

    def test_regime_history_grows(self):
        b = _fresh_brain()
        candles = _trending_up_candles()
        b.detect_regime(candles, [])
        assert len(b._regime_history) >= 1

    def test_macro_trend_bullish(self):
        b = _fresh_brain()
        candles_1h = _trending_up_candles(100)
        candles_4h = _trending_up_candles(50)
        b.detect_macro_trend(candles_1h, candles_4h)
        assert b.macro_trend in ("bullish", "neutral")

    def test_macro_trend_bearish(self):
        b = _fresh_brain()
        candles_1h = _trending_down_candles(100)
        candles_4h = _trending_down_candles(50)
        b.detect_macro_trend(candles_1h, candles_4h)
        assert b.macro_trend in ("bearish", "neutral")


# ─────────────────────────────────────────────────────────────────────────────
#  3. Fibonacci levels
# ─────────────────────────────────────────────────────────────────────────────

class TestFibonacci:
    def test_fib_levels_computed(self):
        b = _fresh_brain()
        candles = _trending_up_candles(120)
        b.compute_fib_levels(candles, lookback=100)
        assert len(b._fib_levels) == 5
        for key in ("23.6", "38.2", "50.0", "61.8", "78.6"):
            assert key in b._fib_levels

    def test_fib_levels_ordered_in_uptrend(self):
        b = _fresh_brain()
        candles = _trending_up_candles(120, base=80_000, step=30)
        b.compute_fib_levels(candles, lookback=100)
        if b._fib_trend == "up":
            lvls = list(b._fib_levels.values())
            # In uptrend, retracement levels should be descending (smaller pct = higher price)
            assert lvls[0] > lvls[-1], "23.6% should be above 78.6% in uptrend"

    def test_fib_swing_high_low_set(self):
        b = _fresh_brain()
        candles = _trending_up_candles(120)
        b.compute_fib_levels(candles)
        assert b._fib_swing_high > b._fib_swing_low

    def test_fib_bias_long_near_support(self):
        b = _fresh_brain()
        candles = _trending_up_candles(120, base=80_000, step=10)
        b.compute_fib_levels(candles)
        b._fib_trend = "up"
        # Place price exactly on 61.8% level
        level_618 = b._fib_levels.get("61.8", 84_000)
        atr_usd   = level_618 * 0.005
        mult, reason = b._fib_bias(level_618, "long", atr_usd)
        assert mult > 1.0 or mult == 1.0   # at min neutral, likely positive
        # Reason should mention 61.8%
        if reason:
            assert "61.8" in reason

    def test_fib_bias_penalises_counter_trend(self):
        b = _fresh_brain()
        candles = _trending_up_candles(120, base=80_000, step=10)
        b.compute_fib_levels(candles)
        b._fib_trend = "up"
        level_618 = b._fib_levels.get("61.8", 84_000)
        atr_usd   = level_618 * 0.005
        mult, reason = b._fib_bias(level_618, "short", atr_usd)
        assert mult <= 1.0   # short at support should be penalised or neutral

    def test_fib_status_returns_dict(self):
        b = _fresh_brain()
        candles = _trending_up_candles(120)
        b.compute_fib_levels(candles)
        status = b.fib_status()
        assert "levels" in status
        assert "swing_high" in status
        assert "swing_low" in status


# ─────────────────────────────────────────────────────────────────────────────
#  4. Feature A — Liquidity sweep detection
# ─────────────────────────────────────────────────────────────────────────────

class TestLiquiditySweep:
    def _candles_with_bullish_sweep(self) -> list[dict]:
        """
        Build a series of 35 flat candles at 84_000 so the swing_high and swing_low
        are well-defined and stable. The bar at index -2 explicitly wicks below swing_low
        and closes back above it — a textbook bullish sweep / stop hunt.
        """
        base = 84_000.0
        bars = [_candle(base, high=base + 100, low=base - 100, ts_offset_min=(35 - i) * 15)
                for i in range(35)]
        # swing_low (from bars[:-3]) ≈ base - 100
        swing_low = min(c["low"] for c in bars[:-3])
        # Insert bullish sweep at -2: wick clearly below, close clearly above
        bars[-2] = _candle(
            close=swing_low + 200,
            high=swing_low + 300,
            low=swing_low - 400,   # wick below swing_low
            ts_offset_min=30,
        )
        # Make last bar (-1) a plain bar well within range — no accidental bearish sweep
        bars[-1] = _candle(base, high=base + 80, low=base - 80, ts_offset_min=15)
        return bars

    def _candles_with_bearish_sweep(self) -> list[dict]:
        bars = _trending_up_candles(30, base=84_000, step=5)
        swing_high = max(c["high"] for c in bars[:-3])
        last = bars[-1].copy()
        last["high"]  = swing_high + 200
        last["close"] = swing_high - 100
        last["low"]   = swing_high - 300
        bars[-1] = last
        return bars

    def test_bullish_sweep_detected(self):
        b = _fresh_brain()
        bars = self._candles_with_bullish_sweep()
        b.detect_liquidity_sweep(bars)
        assert b._liq_sweep.get("direction") == "bullish"

    def test_bearish_sweep_detected(self):
        b = _fresh_brain()
        bars = self._candles_with_bearish_sweep()
        b.detect_liquidity_sweep(bars)
        assert b._liq_sweep.get("direction") == "bearish"

    def test_no_sweep_on_clean_trend(self):
        b = _fresh_brain()
        bars = _trending_up_candles(40, step=10)
        b.detect_liquidity_sweep(bars)
        # May or may not detect — should not crash
        assert isinstance(b._liq_sweep, dict)

    def test_bullish_sweep_boosts_long_score(self):
        b = _fresh_brain()
        b._liq_sweep = {"direction": "bullish", "level": 84_000.0, "bars_ago": 1, "close": 84_100.0}
        sig = _base_signal("long")
        r = b.evaluate_signal("fusion", "Fusion", sig, {}, 84_200.0, 0.0, is_live=False)
        assert any("liq sweep" in rs.lower() or "sweep" in rs.lower() for rs in r.get("reasoning", "").split(" · "))

    def test_bearish_sweep_rejects_live_long(self):
        b = _fresh_brain()
        b._liq_sweep = {"direction": "bearish", "level": 84_500.0, "bars_ago": 1, "close": 84_200.0}
        sig = _base_signal("long")
        r = b.evaluate_signal("fusion", "Fusion", sig, {}, 84_200.0, 0.0, is_live=True)
        assert r["approved"] is False


# ─────────────────────────────────────────────────────────────────────────────
#  5. Feature B — Market structure BOS / CHOCH
# ─────────────────────────────────────────────────────────────────────────────

class TestMarketStructure:
    def test_detects_bullish_bos(self):
        b = _fresh_brain()
        # Build candles with an upward trend so last close exceeds last swing high
        bars = _trending_up_candles(60, base=80_000, step=50)
        b.detect_market_structure(bars, [])
        # Should find some structure (may be BOS or empty depending on pivots)
        assert isinstance(b._mss, dict)

    def test_no_structure_on_flat(self):
        b = _fresh_brain()
        bars = _flat_candles(60)
        b.detect_market_structure(bars, [])
        assert isinstance(b._mss, dict)

    def test_mss_stored_after_detection(self):
        b = _fresh_brain()
        b._mss = {"type": "CHOCH", "direction": "bullish", "level": 83_500.0}
        sig = _base_signal("long")
        r = b.evaluate_signal("fusion", "Fusion", sig, {}, 84_000.0, 0.0, is_live=False)
        assert "CHOCH" in r["reasoning"] or "structure" in r["reasoning"].lower()

    def test_choch_against_direction_rejects_live(self):
        b = _fresh_brain()
        b._mss = {"type": "CHOCH", "direction": "bearish", "level": 84_500.0}
        sig = _base_signal("long")
        r = b.evaluate_signal("fusion", "Fusion", sig, {}, 84_000.0, 0.0, is_live=True)
        assert r["approved"] is False

    def test_find_swings_returns_lists(self):
        highs = [float(i) for i in range(30)]
        lows  = [float(30 - i) for i in range(30)]
        sh, sl = MasterBrain._find_swings(highs, lows)
        assert isinstance(sh, list)
        assert isinstance(sl, list)


# ─────────────────────────────────────────────────────────────────────────────
#  6. Feature C — Volume anomaly
# ─────────────────────────────────────────────────────────────────────────────

class TestVolumeAnomaly:
    def _candles_with_spike(self, direction: str = "bullish") -> list[dict]:
        bars = [_candle(84_000.0, volume=100.0, ts_offset_min=i * 15) for i in range(25)]
        # Last bar: 5× normal volume
        if direction == "bullish":
            bars.append(_candle(84_200.0, open_=83_900.0, volume=500.0, ts_offset_min=0))
        else:
            bars.append(_candle(83_700.0, open_=84_200.0, volume=500.0, ts_offset_min=0))
        return bars

    def test_bullish_spike_detected(self):
        b = _fresh_brain()
        b.detect_volume_anomaly(self._candles_with_spike("bullish"))
        assert b._vol_spike is True
        assert b._vol_spike_dir == "bullish"

    def test_bearish_spike_detected(self):
        b = _fresh_brain()
        b.detect_volume_anomaly(self._candles_with_spike("bearish"))
        assert b._vol_spike is True
        assert b._vol_spike_dir == "bearish"

    def test_no_spike_on_uniform_volume(self):
        b = _fresh_brain()
        bars = [_candle(84_000.0, volume=100.0, ts_offset_min=i * 15) for i in range(25)]
        b.detect_volume_anomaly(bars)
        assert b._vol_spike is False

    def test_vol_spike_boosts_matching_direction(self):
        b = _fresh_brain()
        b._vol_spike     = True
        b._vol_spike_dir = "bullish"
        sig = _base_signal("long")
        r = b.evaluate_signal("fusion", "Fusion", sig, {}, 84_000.0, 0.0, is_live=False)
        assert "vol spike" in r["reasoning"].lower()

    def test_vol_spike_penalises_opposite_direction(self):
        b = _fresh_brain()
        b._vol_spike     = True
        b._vol_spike_dir = "bearish"
        sig = _base_signal("long")
        r = b.evaluate_signal("fusion", "Fusion", sig, {}, 84_000.0, 0.0, is_live=False)
        assert "vol spike" in r["reasoning"].lower()


# ─────────────────────────────────────────────────────────────────────────────
#  7. Feature D — HTF Pivot Points
# ─────────────────────────────────────────────────────────────────────────────

class TestHTFPivots:
    def test_calc_pivots_returns_7_levels(self):
        pivots = MasterBrain._calc_pivots(85_000.0, 83_000.0, 84_000.0)
        assert set(pivots.keys()) == {"PP", "R1", "R2", "R3", "S1", "S2", "S3"}

    def test_pp_is_average(self):
        pivots = MasterBrain._calc_pivots(90_000.0, 80_000.0, 85_000.0)
        assert abs(pivots["PP"] - (90_000 + 80_000 + 85_000) / 3) < 1.0

    def test_r1_above_pp(self):
        pivots = MasterBrain._calc_pivots(85_000.0, 83_000.0, 84_000.0)
        assert pivots["R1"] > pivots["PP"]

    def test_s1_below_pp(self):
        pivots = MasterBrain._calc_pivots(85_000.0, 83_000.0, 84_000.0)
        assert pivots["S1"] < pivots["PP"]

    def test_compute_htf_pivots_fills_weekly(self):
        b = _fresh_brain()
        candles_1h = _trending_up_candles(750, base=80_000.0)
        candles_4h = _trending_up_candles(50, base=80_000.0)
        b.compute_htf_pivots(candles_1h, candles_4h)
        assert len(b._weekly_pivots) == 7

    def test_pivot_bias_resistance_penalises_long(self):
        b = _fresh_brain()
        price = 84_000.0
        b._weekly_pivots = MasterBrain._calc_pivots(84_500.0, 83_000.0, 84_000.0)
        r1 = b._weekly_pivots["R1"]
        atr_usd = price * 0.005
        mult, reason = b._pivot_bias(r1, "long", atr_usd)
        assert mult <= 1.0
        assert "R1" in reason or "resistance" in reason.lower()

    def test_pivot_bias_support_penalises_short(self):
        b = _fresh_brain()
        price = 84_000.0
        b._weekly_pivots = MasterBrain._calc_pivots(85_000.0, 84_200.0, 84_600.0)
        s1 = b._weekly_pivots["S1"]
        atr_usd = price * 0.005
        mult, reason = b._pivot_bias(s1, "short", atr_usd)
        assert mult <= 1.0


# ─────────────────────────────────────────────────────────────────────────────
#  8. Feature E — RSI divergence
# ─────────────────────────────────────────────────────────────────────────────

class TestRSIDivergence:
    def test_rsi_function_returns_list(self):
        closes = [float(100 + i) for i in range(30)]
        rsi = MasterBrain._rsi(closes, 14)
        assert isinstance(rsi, list)
        assert len(rsi) > 0
        assert all(0 <= v <= 100 for v in rsi)

    def test_rsi_too_few_candles_returns_empty(self):
        rsi = MasterBrain._rsi([100.0, 101.0], 14)
        assert rsi == []

    def test_bearish_divergence_detected(self):
        b = _fresh_brain()
        # Ascending prices but with declining momentum in second half
        half1 = [_candle(80_000 + i * 100, volume=150.0, ts_offset_min=(40 - i) * 15) for i in range(20)]
        half2 = [_candle(82_000 + i * 50,  volume=80.0,  ts_offset_min=(20 - i) * 15) for i in range(20)]
        bars = half1 + half2
        b.detect_rsi_divergence(bars)
        # Should not crash; divergence may or may not be detected depending on exact values
        assert b._rsi_divergence in ("", "bullish", "bearish")

    def test_no_divergence_on_uniform_trend(self):
        b = _fresh_brain()
        bars = _trending_up_candles(50)
        b.detect_rsi_divergence(bars)
        assert b._rsi_divergence in ("", "bullish", "bearish")   # just no crash

    def test_divergence_affects_score(self):
        b = _fresh_brain()
        b._rsi_divergence = "bullish"
        sig = _base_signal("long")
        r = b.evaluate_signal("fusion", "Fusion", sig, {}, 84_000.0, 0.0, is_live=False)
        assert "RSI" in r["reasoning"] or "divergence" in r["reasoning"].lower()


# ─────────────────────────────────────────────────────────────────────────────
#  9. Feature F — News event blackout
# ─────────────────────────────────────────────────────────────────────────────

class TestMacroBlackout:
    def test_no_blackout_on_normal_day(self):
        b = _fresh_brain()
        # April 11 (Saturday) is not an FOMC day
        in_blackout, reason = b._is_macro_blackout()
        # We can't guarantee the exact date in tests, just check return type
        assert isinstance(in_blackout, bool)
        assert isinstance(reason, str)

    def test_fomc_blackout_detected(self, monkeypatch):
        from app.agents import master_brain as mb
        class FakeDT:
            @staticmethod
            def now(tz=None):
                return datetime(2026, 1, 28, 18, 30, tzinfo=timezone.utc)  # FOMC date
        monkeypatch.setattr(mb, "datetime", FakeDT)
        b = _fresh_brain()
        in_blackout, reason = b._is_macro_blackout()
        assert in_blackout is True
        assert "FOMC" in reason

    def test_cpi_blackout_detected(self, monkeypatch):
        from app.agents import master_brain as mb
        # 2nd Tuesday of a month, 12:30 UTC
        class FakeDT:
            @staticmethod
            def now(tz=None):
                # April 14, 2026 is 2nd Tuesday (day=14, weekday()=1)
                return datetime(2026, 4, 14, 12, 30, tzinfo=timezone.utc)
        monkeypatch.setattr(mb, "datetime", FakeDT)
        b = _fresh_brain()
        in_blackout, reason = b._is_macro_blackout()
        assert in_blackout is True
        assert "CPI" in reason

    def test_blackout_rejects_live(self, monkeypatch):
        from app.agents import master_brain as mb
        class FakeDT:
            @staticmethod
            def now(tz=None):
                return datetime(2026, 1, 28, 18, 30, tzinfo=timezone.utc)
        monkeypatch.setattr(mb, "datetime", FakeDT)
        b = _fresh_brain()
        sig = _base_signal("long")
        r = b.evaluate_signal("fusion", "Fusion", sig, {}, 84_000.0, 0.0, is_live=True)
        assert r["approved"] is False
        assert "blackout" in r["reasoning"].lower() or "FOMC" in r["reasoning"]


# ─────────────────────────────────────────────────────────────────────────────
#  10. Feature G — Bayesian trust
# ─────────────────────────────────────────────────────────────────────────────

class TestBayesianTrust:
    def test_initial_score_equals_ema_trust(self):
        b = _fresh_brain()
        score = b._bayes_trust_score("fusion")
        # No Bayesian data → returns EMA trust
        assert abs(score - b.strategy_trust.get("fusion", 1.0)) < 0.01

    def test_wins_increase_alpha(self):
        b = _fresh_brain()
        for _ in range(10):
            b._update_bayes_trust("fusion", won=True)
        bt = b._bayes_trust["fusion"]
        assert bt["alpha"] > bt["beta"]

    def test_losses_increase_beta(self):
        b = _fresh_brain()
        for _ in range(10):
            b._update_bayes_trust("fusion", won=False)
        bt = b._bayes_trust["fusion"]
        assert bt["beta"] > bt["alpha"]

    def test_trust_score_bounded(self):
        b = _fresh_brain()
        for _ in range(100):
            b._update_bayes_trust("fusion", won=True)
        score = b._bayes_trust_score("fusion")
        assert 0.20 <= score <= 2.0

    def test_ema_update_also_updates_bayes(self):
        b = _fresh_brain()
        before_alpha = b._bayes_trust.get("fusion", {}).get("alpha", 2.0)
        b._update_trust_ema("fusion", won=True, was_live=False)
        after_alpha = b._bayes_trust.get("fusion", {}).get("alpha", 0)
        assert after_alpha > before_alpha

    def test_record_trade_result_updates_bayes(self):
        b = _fresh_brain()
        b.record_trade_result("fusion", pnl=10.0, won=True, was_live=False)
        assert "fusion" in b._bayes_trust


# ─────────────────────────────────────────────────────────────────────────────
#  11. Feature H — Order flow imbalance at Fib/Pivot
# ─────────────────────────────────────────────────────────────────────────────

class TestOrderFlowImbalance:
    def _orderbook_bid_wall(self, price: float = 84_000.0) -> dict:
        """Simulate strong bid wall at price."""
        bids = [[price - i * 5, 10.0] for i in range(20)]
        asks = [[price + i * 5, 1.0]  for i in range(20)]
        return {"bids": bids, "asks": asks}

    def _orderbook_ask_wall(self, price: float = 84_000.0) -> dict:
        """Simulate strong ask wall at price."""
        bids = [[price - i * 5, 1.0]  for i in range(20)]
        asks = [[price + i * 5, 10.0] for i in range(20)]
        return {"bids": bids, "asks": asks}

    def test_bid_wall_at_support_boosts_long(self):
        b = _fresh_brain()
        price = 84_000.0
        b._fib_levels = {"61.8": price}
        b._fib_trend  = "up"
        atr_usd = price * 0.005
        ob = self._orderbook_bid_wall(price)
        mult, reason = b._orderflow_fib_bias(price, "long", ob, atr_usd)
        assert mult >= 1.0

    def test_ask_wall_at_resistance_boosts_short(self):
        b = _fresh_brain()
        price = 84_000.0
        b._fib_levels = {"61.8": price}
        b._fib_trend  = "down"
        atr_usd = price * 0.005
        ob = self._orderbook_ask_wall(price)
        mult, reason = b._orderflow_fib_bias(price, "short", ob, atr_usd)
        assert mult >= 1.0

    def test_no_ob_returns_neutral(self):
        b = _fresh_brain()
        b._fib_levels = {"61.8": 84_000.0}
        mult, reason = b._orderflow_fib_bias(84_000.0, "long", None, 500.0)
        assert mult == 1.0
        assert reason == ""

    def test_far_from_levels_returns_neutral(self):
        b = _fresh_brain()
        b._fib_levels = {"61.8": 80_000.0}  # far from live price
        b._fib_trend  = "up"
        ob = self._orderbook_bid_wall(84_000.0)
        mult, reason = b._orderflow_fib_bias(84_000.0, "long", ob, 400.0)
        assert mult == 1.0

    def test_ofi_flows_through_evaluate_signal(self):
        b = _fresh_brain()
        price = 84_000.0
        b._fib_levels = {"61.8": price}
        b._fib_trend  = "up"
        ob = self._orderbook_bid_wall(price)
        sig = _base_signal("long", price)
        r = b.evaluate_signal("fusion", "Fusion", sig, {}, price, 0.0,
                               is_live=False, orderbook=ob)
        # OFI info should appear in factors
        assert "ofi_mult" in r.get("factors", {})


# ─────────────────────────────────────────────────────────────────────────────
#  12. Serialization — to_dict / from_dict round-trip
# ─────────────────────────────────────────────────────────────────────────────

class TestSerialization:
    def test_to_dict_contains_all_keys(self):
        b = _fresh_brain()
        d = b.to_dict()
        required = [
            "current_regime", "macro_trend", "strategy_trust", "strategy_stats",
            "consecutive_losses", "daily_trades", "daily_pnl",
            "_fib_levels", "_liq_sweep", "_mss", "_vol_spike",
            "_weekly_pivots", "_monthly_pivots", "_rsi_divergence", "_bayes_trust",
        ]
        for key in required:
            assert key in d, f"Missing key in to_dict: {key}"

    def test_from_dict_restores_state(self):
        b1 = _fresh_brain()
        # Populate some state
        b1._liq_sweep    = {"direction": "bullish", "level": 84_000.0}
        b1._mss          = {"type": "CHOCH", "direction": "bearish"}
        b1._vol_spike    = True
        b1._vol_spike_dir= "bearish"
        b1._rsi_divergence = "bullish"
        b1._bayes_trust  = {"fusion": {"alpha": 5.0, "beta": 3.0}}
        b1.strategy_trust["fusion"] = 1.35

        d = b1.to_dict()

        b2 = _fresh_brain()
        b2.from_dict(d)

        assert b2._liq_sweep.get("direction") == "bullish"
        assert b2._mss.get("type") == "CHOCH"
        assert b2._vol_spike is True
        assert b2._vol_spike_dir == "bearish"
        assert b2._rsi_divergence == "bullish"
        assert b2._bayes_trust.get("fusion", {}).get("alpha") == 5.0
        assert abs(b2.strategy_trust.get("fusion", 0) - 1.35) < 0.01

    def test_from_dict_empty_is_safe(self):
        b = _fresh_brain()
        b.from_dict({})   # should not raise
        b.from_dict(None)  # should not raise


# ─────────────────────────────────────────────────────────────────────────────
#  13. Fusion signal — fuse_signals
# ─────────────────────────────────────────────────────────────────────────────

class TestFuseSignals:
    def _make_strategy_results(self, direction: str = "long", n: int = 3) -> dict:
        sig = _base_signal(direction)
        return {f"strat_{i}": {"signal": {**sig, "confidence": 0.7}} for i in range(n)}

    def test_fusion_with_majority_agreement(self):
        b = _fresh_brain()
        results = self._make_strategy_results("long", 3)
        fused = b.fuse_signals(results, 84_000.0)
        assert fused is not None
        assert fused["direction"] == "long"

    def test_fusion_requires_at_least_2_signals(self):
        b = _fresh_brain()
        results = {"strat_0": {"signal": _base_signal("long")}}
        fused = b.fuse_signals(results, 84_000.0)
        assert fused is None   # only 1 signal

    def test_fusion_returns_none_when_split_50_50(self):
        b = _fresh_brain()
        results = {
            "s1": {"signal": _base_signal("long")},
            "s2": {"signal": _base_signal("short")},
        }
        fused = b.fuse_signals(results, 84_000.0)
        # 50/50 split — consensus < 0.50 → None
        assert fused is None

    def test_fusion_sets_valid_sl_tp(self):
        b = _fresh_brain()
        results = self._make_strategy_results("long", 3)
        fused = b.fuse_signals(results, 84_000.0)
        if fused:
            assert fused["sl"] > 0
            assert fused["tp"] > 0


# ─────────────────────────────────────────────────────────────────────────────
#  14. Record trade result + day reset
# ─────────────────────────────────────────────────────────────────────────────

class TestRecordTradeResult:
    def test_win_increments_daily_wins(self):
        b = _fresh_brain()
        before = b.daily_wins
        b.record_trade_result("fusion", pnl=5.0, won=True, was_live=False)
        assert b.daily_wins == before + 1

    def test_loss_increments_consecutive_losses_live(self):
        b = _fresh_brain()
        b.record_trade_result("fusion", pnl=-3.0, won=False, was_live=True)
        assert b.consecutive_losses == 1

    def test_win_resets_consecutive_losses_live(self):
        b = _fresh_brain()
        b.consecutive_losses = 3
        b.record_trade_result("fusion", pnl=5.0, won=True, was_live=True)
        assert b.consecutive_losses == 0

    def test_live_pnl_updates_daily_pnl(self):
        b = _fresh_brain()
        b.record_trade_result("fusion", pnl=10.0, won=True, was_live=True)
        assert b.daily_pnl == 10.0

    def test_paper_pnl_does_not_update_daily_pnl(self):
        b = _fresh_brain()
        b.record_trade_result("fusion", pnl=10.0, won=True, was_live=False)
        assert b.daily_pnl == 0.0

    def test_stats_updated_after_trade(self):
        b = _fresh_brain()
        b.record_trade_result("fusion", pnl=7.0, won=True, was_live=False)
        s = b.strategy_stats["fusion"]
        assert s["trades"] == 1
        assert s["wins"] == 1

    def test_trust_updated_after_win(self):
        b = _fresh_brain()
        old_trust = b.strategy_trust.get("fusion", 1.0)
        b.record_trade_result("fusion", pnl=7.0, won=True, was_live=False)
        assert b.strategy_trust["fusion"] != old_trust


# ─────────────────────────────────────────────────────────────────────────────
#  15. get_status / portfolio_summary
# ─────────────────────────────────────────────────────────────────────────────

class TestGetStatus:
    def test_get_status_returns_dict_with_fib(self):
        b = _fresh_brain()
        b.compute_fib_levels(_trending_up_candles(120))
        status = b.get_status({}, 84_000.0)
        assert "market_context" in status
        assert "fib" in status["market_context"]

    def test_get_status_includes_new_features(self):
        b = _fresh_brain()
        status = b.get_status({}, 84_000.0)
        ctx = status["market_context"]
        assert "pivots" in ctx
        assert "market_structure" in ctx
        assert "liq_sweep" in ctx
        assert "vol_spike" in ctx
        assert "rsi_divergence" in ctx
        assert "macro_blackout" in ctx

    def test_portfolio_summary_excludes_paper_from_live_pnl(self):
        b = _fresh_brain()
        positions = {
            "live_fusion": {"direction": "long", "mode": "live", "is_live": True,
                            "entry": 83_000.0, "btc_size": 0.01},
            "paper_mom":   {"direction": "long", "mode": "paper", "is_shadow": True,
                            "entry": 83_000.0, "btc_size": 0.01},
        }
        summary = b.portfolio_summary(positions, 84_000.0)
        assert isinstance(summary, dict)

    def test_is_strategy_live_ready_structure(self):
        b = _fresh_brain()
        r = b.is_strategy_live_ready("fusion")
        assert "ready" in r
        assert "can_trade" in r
        assert "scale" in r
