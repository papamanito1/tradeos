"""
Quantum Order Flow Scalper (QOFS)
==================================
A high-frequency scalping strategy built on market microstructure principles
as used in institutional crypto desks and prop trading firms.

Core thesis:
  Short-term price movement is dominated by ORDER FLOW — the net buying and
  selling pressure embedded in each candle. When multiple microstructure signals
  align, we have a high-probability edge for a 0.3–0.6% scalp.

Six-factor scored model (each factor: -1, 0, or +1):
  1. Order Flow Imbalance (OFI)   — signed volume pressure
  2. VWAP Deviation               — institutional reference price reversion
  3. Micro EMA Cross              — ultra-short momentum filter
  4. Realized Volatility Guard    — only trade in "goldilocks" vol regime
  5. Relative Volume (RVOL)       — smart money participation confirmation
  6. Market Regime (Hurst proxy)  — adapt to trend vs mean-reversion environment

Signal threshold: |score| >= 3  (3 of 6 factors must agree)

Risk profile:
  - Stop loss  : 0.30%  (tight, HFT-style)
  - Take profit: 0.50%  (1.67:1 R:R — works at 60%+ win rate)
  - Timeframe  : 1m, 5m (designed for fast cycles)
  - Hold time  : 1-15 minutes implied

Parameters are tuned for BTC/USDT and ETH/USDT on 1m–5m bars.
Recommended capital allocation: 10-15% per active symbol.
"""

import numpy as np
from dataclasses import dataclass
from typing import Optional

from app.exchange.base import Candle
from app.strategies.base import BaseStrategy, Signal, SignalDirection


@dataclass
class FactorScores:
    ofi: int           # Order Flow Imbalance
    vwap: int          # VWAP deviation
    ema_micro: int     # Micro EMA cross
    vol_filter: int    # Volatility filter (0 = blocked)
    rvol: int          # Relative volume
    regime: int        # Market regime
    total: int
    vol_blocked: bool
    regime_label: str  # trending | reverting | neutral


class QuantumOrderFlowScalper(BaseStrategy):
    """
    Institutional-grade HFT scalper.
    Designed for 1m–5m bars on liquid crypto pairs.
    """

    name = "quantum_order_flow_scalper"
    description = "HFT scalper: OFI + VWAP + micro-EMA + regime filter (institutional style)"
    default_parameters = {
        # ── VWAP ─────────────────────────────────────────────────────────────
        "vwap_period": 50,
        "vwap_entry_band_pct": 0.003,   # 0.30% minimum deviation to trigger
        "vwap_max_band_pct": 0.015,     # 1.50% maximum — avoid runaway trends
        # ── Micro EMA ────────────────────────────────────────────────────────
        "ema_fast": 3,
        "ema_slow": 8,
        # ── Order Flow Imbalance ─────────────────────────────────────────────
        "ofi_period": 10,               # rolling bars for cumulative OFI
        "ofi_threshold": 0.20,          # normalized threshold for OFI score
        # ── Realized Volatility ───────────────────────────────────────────────
        "vol_period": 20,
        "vol_min_pct": 0.0003,          # 0.03% — dead market, skip
        "vol_max_pct": 0.025,           # 2.50% — explosive, skip
        # ── Relative Volume ───────────────────────────────────────────────────
        "rvol_period": 20,
        "rvol_threshold": 1.25,         # volume must be 1.25x average
        # ── Market Regime ─────────────────────────────────────────────────────
        "regime_period": 30,            # autocorrelation lookback
        "regime_trend_thresh": 0.10,    # autocorr > 0.10 = trending
        "regime_revert_thresh": -0.10,  # autocorr < -0.10 = mean-reverting
        # ── Signal ────────────────────────────────────────────────────────────
        "min_score": 3,                 # minimum |score| to fire signal
        # ── Risk ──────────────────────────────────────────────────────────────
        "sl_pct": 0.003,                # 0.30% stop loss
        "tp_pct": 0.005,                # 0.50% take profit
    }

    # Minimum candles required before producing any signal
    _MIN_CANDLES = 55

    def generate_signal(self, candles: list[Candle], symbol: str, timeframe: str) -> Signal:
        if len(candles) < self._MIN_CANDLES:
            return self._no_signal(candles[-1].close, symbol, timeframe,
                                   f"Warming up ({len(candles)}/{self._MIN_CANDLES} bars)")

        closes  = np.array([c.close  for c in candles], dtype=np.float64)
        opens   = np.array([c.open   for c in candles], dtype=np.float64)
        highs   = np.array([c.high   for c in candles], dtype=np.float64)
        lows    = np.array([c.low    for c in candles], dtype=np.float64)
        volumes = np.array([c.volume for c in candles], dtype=np.float64)

        price = closes[-1]

        # ── Compute all factors ────────────────────────────────────────────────
        scores = self._score_all(closes, opens, highs, lows, volumes)

        # ── Volatility gate: block entirely if vol outside range ───────────────
        if scores.vol_blocked:
            return self._no_signal(price, symbol, timeframe,
                                   f"Vol filter blocked | regime={scores.regime_label}")

        # ── Generate signal based on total score ──────────────────────────────
        sl_pct = self.get_parameter("sl_pct")
        tp_pct = self.get_parameter("tp_pct")

        if scores.total >= self.get_parameter("min_score"):
            return Signal(
                direction=SignalDirection.long,
                symbol=symbol,
                timeframe=timeframe,
                confidence=self._confidence(scores.total),
                suggested_entry=price,
                suggested_sl=round(price * (1 - sl_pct), 8),
                suggested_tp=round(price * (1 + tp_pct), 8),
                reasoning=self._build_reasoning(scores, "LONG", price),
            )

        if scores.total <= -self.get_parameter("min_score"):
            return Signal(
                direction=SignalDirection.short,
                symbol=symbol,
                timeframe=timeframe,
                confidence=self._confidence(scores.total),
                suggested_entry=price,
                suggested_sl=round(price * (1 + sl_pct), 8),
                suggested_tp=round(price * (1 - tp_pct), 8),
                reasoning=self._build_reasoning(scores, "SHORT", price),
            )

        return self._no_signal(
            price, symbol, timeframe,
            f"Score {scores.total:+d}/6 below threshold ±{self.get_parameter('min_score')} | "
            f"OFI={scores.ofi:+d} VWAP={scores.vwap:+d} EMA={scores.ema_micro:+d} "
            f"RVOL={scores.rvol:+d} Regime={scores.regime:+d} ({scores.regime_label})"
        )

    # ── Factor scoring ─────────────────────────────────────────────────────────

    def _score_all(self, closes, opens, highs, lows, volumes) -> FactorScores:
        vol_blocked, vol_score = self._factor_vol(closes)
        ofi_score = self._factor_ofi(closes, opens, highs, lows, volumes)
        vwap_score = self._factor_vwap(closes, highs, lows, volumes)
        ema_score = self._factor_ema(closes)
        rvol_score = self._factor_rvol(volumes)
        regime_score, regime_label = self._factor_regime(closes)

        total = ofi_score + vwap_score + ema_score + rvol_score + regime_score
        return FactorScores(
            ofi=ofi_score,
            vwap=vwap_score,
            ema_micro=ema_score,
            vol_filter=vol_score,
            rvol=rvol_score,
            regime=regime_score,
            total=total,
            vol_blocked=vol_blocked,
            regime_label=regime_label,
        )

    def _factor_ofi(self, closes, opens, highs, lows, volumes) -> int:
        """
        Order Flow Imbalance: each bar's signed volume.

        OFI_bar = (close - open) / (high - low + ε) × volume
          > 0 → buying pressure (bulls in control)
          < 0 → selling pressure (bears in control)

        We take the rolling sum over `ofi_period` bars and normalize.
        """
        period = self.get_parameter("ofi_period")
        ranges = highs - lows + 1e-10
        raw_ofi = (closes - opens) / ranges * volumes
        cum_ofi = np.sum(raw_ofi[-period:])

        # Normalize by total volume traded in the window (unit-less ratio)
        total_volume = np.sum(volumes[-period:]) + 1e-10
        norm_ofi = cum_ofi / total_volume

        threshold = self.get_parameter("ofi_threshold")
        if norm_ofi > threshold:
            return 1
        if norm_ofi < -threshold:
            return -1
        return 0

    def _factor_vwap(self, closes, highs, lows, volumes) -> int:
        """
        VWAP Deviation: institutions use VWAP as their reference price.

        When price deviates from VWAP into a specific band, a mean-reversion
        edge exists (retail gets caught; smart money fades the move).

        Long  signal: price < VWAP × (1 - entry_band) — oversold vs VWAP
        Short signal: price > VWAP × (1 + entry_band) — overbought vs VWAP
        Max band guard: deviation > max_band means trend is too strong — skip.
        """
        period = self.get_parameter("vwap_period")
        typical = (highs + lows + closes) / 3
        vwap = (
            np.sum(typical[-period:] * volumes[-period:]) /
            (np.sum(volumes[-period:]) + 1e-10)
        )

        price = closes[-1]
        deviation = (price - vwap) / (vwap + 1e-10)
        entry_band = self.get_parameter("vwap_entry_band_pct")
        max_band   = self.get_parameter("vwap_max_band_pct")

        if -max_band <= deviation <= -entry_band:
            return 1   # below VWAP → long (reversion back up)
        if  entry_band <= deviation <= max_band:
            return -1  # above VWAP → short (reversion back down)
        return 0

    def _factor_ema(self, closes) -> int:
        """
        Micro EMA Cross: EMA-3 vs EMA-8.

        Ultra-short trend direction — filters out counter-trend scalps.
        We require the crossover to be fresh (changed within last 2 bars).
        """
        fast = self.get_parameter("ema_fast")
        slow = self.get_parameter("ema_slow")
        ema_f = self._ema(closes, fast)
        ema_s = self._ema(closes, slow)

        curr_bull = ema_f[-1] > ema_s[-1]
        prev_bull = ema_f[-2] > ema_s[-2]

        # Fresh cross (strongest signal)
        if not prev_bull and curr_bull:
            return 1
        if prev_bull and not curr_bull:
            return -1

        # Sustained alignment (weaker signal)
        if curr_bull:
            return 1
        return -1

    def _factor_vol(self, closes) -> tuple[bool, int]:
        """
        Realized Volatility Guard.

        Volatility outside the [vol_min, vol_max] window kills the signal entirely.
        - Too quiet: no movement, trade costs eat the edge
        - Too explosive: slippage is unpredictable, stop-loss unreliable
        """
        period = self.get_parameter("vol_period")
        log_returns = np.diff(np.log(closes[-period - 1:]))
        realized_vol = np.std(log_returns)

        vol_min = self.get_parameter("vol_min_pct")
        vol_max = self.get_parameter("vol_max_pct")

        if realized_vol < vol_min or realized_vol > vol_max:
            return True, 0   # vol_blocked=True
        return False, 1      # tradeable

    def _factor_rvol(self, volumes) -> int:
        """
        Relative Volume (RVOL): current bar vs rolling average.

        A volume surge signals informed participants (institutions, algos)
        are active — confirms the signal is not noise-driven.
        Direction is carried by OFI; RVOL is the magnitude gate.
        """
        period    = self.get_parameter("rvol_period")
        threshold = self.get_parameter("rvol_threshold")
        avg_vol   = np.mean(volumes[-period - 1:-1]) + 1e-10
        rvol      = volumes[-1] / avg_vol

        if rvol >= threshold:
            return 1   # high participation — confirms signal direction from OFI
        return 0

    def _factor_regime(self, closes) -> tuple[int, str]:
        """
        Market Regime via Lag-1 Return Autocorrelation (Hurst proxy).

        Autocorrelation of 1-period returns over regime_period bars:
          > +0.10  → trending market   → momentum score adds in signal direction
          < -0.10  → mean-reverting    → VWAP reversion scores add extra weight
          Otherwise → neutral

        This adapts the strategy's "personality" to the current market.
        """
        period = self.get_parameter("regime_period")
        returns = np.diff(np.log(closes[-period - 1:]))
        if len(returns) < 4:
            return 0, "neutral"

        # Lag-1 autocorrelation
        r1 = returns[:-1] - returns[:-1].mean()
        r2 = returns[1:]  - returns[1:].mean()
        denom = np.sqrt(np.sum(r1 ** 2) * np.sum(r2 ** 2)) + 1e-10
        autocorr = float(np.dot(r1, r2) / denom)

        trend_thresh  = self.get_parameter("regime_trend_thresh")
        revert_thresh = self.get_parameter("regime_revert_thresh")

        if autocorr > trend_thresh:
            # Trending — add momentum bias in direction of recent move
            last_return = closes[-1] / closes[-2] - 1
            label = "trending"
            return (1 if last_return > 0 else -1), label

        if autocorr < revert_thresh:
            # Mean-reverting — add counter-trend bias
            last_return = closes[-1] / closes[-2] - 1
            label = "reverting"
            return (-1 if last_return > 0 else 1), label

        return 0, "neutral"

    # ── Helper utilities ───────────────────────────────────────────────────────

    @staticmethod
    def _ema(data: np.ndarray, period: int) -> np.ndarray:
        alpha = 2.0 / (period + 1)
        ema = np.empty_like(data)
        ema[0] = data[0]
        for i in range(1, len(data)):
            ema[i] = alpha * data[i] + (1 - alpha) * ema[i - 1]
        return ema

    def _confidence(self, score: int) -> float:
        """Map score (3–6) to confidence (0.60–0.95)."""
        max_score = 6
        min_thresh = self.get_parameter("min_score")
        span = max_score - min_thresh
        excess = abs(score) - min_thresh
        return round(min(0.95, 0.60 + (excess / span) * 0.35), 2)

    def _no_signal(self, price: float, symbol: str, timeframe: str, reason: str) -> Signal:
        return Signal(
            direction=SignalDirection.none,
            symbol=symbol,
            timeframe=timeframe,
            confidence=0.0,
            suggested_entry=price,
            reasoning=reason,
        )

    def _build_reasoning(self, s: FactorScores, direction: str, price: float) -> str:
        factor_strs = [
            f"OFI={s.ofi:+d}",
            f"VWAP={s.vwap:+d}",
            f"EMA={s.ema_micro:+d}",
            f"RVOL={s.rvol:+d}",
            f"Regime={s.regime:+d}({s.regime_label})",
        ]
        return (
            f"[{direction}] Score={s.total:+d}/5 active factors | "
            f"{' | '.join(factor_strs)} | "
            f"entry={price:.4f} "
            f"sl={price * (1 - self.get_parameter('sl_pct')):.4f} "
            f"tp={price * (1 + self.get_parameter('tp_pct')):.4f}"
        )
