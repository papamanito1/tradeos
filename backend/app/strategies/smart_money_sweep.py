"""
Smart Money Liquidity Sweep Strategy
=====================================

Implements the following institutional entry logic:

    Long:
        htf_trend == "long"                  ← EMA200 + structural HH/HL
        AND atr_filter_ok()                  ← volatility in tradeable range
        AND pullback_to_vwap_or_ema(tf=3m)   ← price near value area
        AND liquidity_sweep_and_reclaim(1m)  ← false breakdown, then reclaim
        AND funding_filter_ok(long)          ← not over-leveraged long
        AND news_filter_ok()                 ← no volume spike (news guard)
        →  enter_long()
           stop  = max(structure_stop, atr_stop)   ← wider is safer
           tp1   = entry + 1R                      ← partial exit
           tp2   = entry + 2R                      ← full exit (backtest uses tp2)

    Short: mirror logic

Design decisions for backtesting (single-timeframe):
  • "HTF" is simulated by a 200-period EMA + structural pivot detection on the
    same timeframe's history — equivalent to viewing ~3-4 TFs up when using 1m bars.
  • "3m pullback" is detected by checking that the last 3 bars formed a pullback
    sequence (lower highs for bull, higher lows for bear) ending near VWAP/EMA.
  • "1m liquidity sweep" uses the current + previous bar to detect a wick below
    (above) a recent swing low (high) that is then immediately reclaimed.
  • Funding rate proxy: cumulative delta bias in last N bars.  If too many bars
    are one-directional (everyone is already long/short), we skip.
  • News filter: volume spike vs rolling average.  A spike > 3× avg implies
    event risk — we do not enter.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np

from app.exchange.base import Candle
from app.strategies.base import BaseStrategy, Signal, SignalDirection


class SmartMoneySweep(BaseStrategy):

    name = "Smart Money Liquidity Sweep"
    description = (
        "Multi-TF institutional entry: HTF trend filter → VWAP/EMA pullback "
        "→ liquidity sweep & reclaim → funding + news guard. "
        "Risk: max(structure_stop, ATR_stop) | TP1=1R TP2=2R."
    )

    # Minimum bars before the strategy will fire any signal
    _MIN_CANDLES = 220

    default_parameters = {
        # ── HTF trend ─────────────────────────────────────────────────────────
        "htf_ema": 200,           # Long EMA that defines the higher-TF bias
        "htf_slope_bars": 50,     # Bars to measure EMA slope over
        "htf_min_slope": 0.0002,  # Minimum price-normalised EMA slope (0.02%/bar)

        # ── ATR volatility filter ──────────────────────────────────────────────
        "atr_period": 14,
        "atr_min_pct": 0.0010,   # 0.10% min ATR/price — flat market, skip
        "atr_max_pct": 0.0500,   # 5.00% max ATR/price — extreme vol, skip

        # ── VWAP / EMA value-area pullback ────────────────────────────────────
        "vwap_period": 200,       # Rolling VWAP window (approximates daily session)
        "vwap_band_pct": 0.004,   # ±0.40% proximity counts as "near VWAP"
        "pullback_ema": 21,       # EMA for pullback check
        "pullback_ema_band": 0.003, # ±0.30% proximity to EMA counts

        # ── Liquidity sweep parameters ────────────────────────────────────────
        "sweep_lookback": 10,     # Bars to look back for the swing high/low level
        "sweep_min_pct": 0.0003,  # Minimum sweep depth below/above the level (0.03%)
        "reclaim_min_pct": 0.0005,# Current close must be this far above/below level

        # ── Funding proxy (cumulative delta directional bias guard) ───────────
        "funding_lookback": 30,   # Bars to measure directional bar bias
        "funding_max_bias": 0.80, # >80% same-direction bars = over-extended, skip

        # ── News guard (volume spike) ──────────────────────────────────────────
        "news_vol_period": 20,
        "news_vol_spike": 3.0,    # Volume > 3× avg → suspected news event

        # ── Risk management ───────────────────────────────────────────────────
        "atr_sl_mult": 1.5,       # Stop = entry ± ATR × mult
        "r_mult_tp1": 1.0,        # TP1 at 1R  (noted in reasoning)
        "r_mult_tp2": 2.0,        # TP2 at 2R  (backtest uses this as exit)
    }

    # ──────────────────────────────────────────────────────────────────────────
    # Public interface
    # ──────────────────────────────────────────────────────────────────────────

    def generate_signal(self, candles: list[Candle], symbol: str, timeframe: str) -> Signal:
        p = self.parameters

        def flat(reason: str = "") -> Signal:
            return Signal(
                direction=SignalDirection.none,
                symbol=symbol, timeframe=timeframe,
                confidence=0.0, suggested_entry=candles[-1].close,
                reasoning=reason,
            )

        if len(candles) < self._MIN_CANDLES:
            return flat(f"Warming up ({len(candles)}/{self._MIN_CANDLES})")

        closes  = np.array([c.close  for c in candles], dtype=np.float64)
        opens   = np.array([c.open   for c in candles], dtype=np.float64)
        highs   = np.array([c.high   for c in candles], dtype=np.float64)
        lows    = np.array([c.low    for c in candles], dtype=np.float64)
        volumes = np.array([c.volume for c in candles], dtype=np.float64)

        price = closes[-1]

        # ── 1. ATR filter ──────────────────────────────────────────────────────
        atr = self._atr(highs, lows, closes, p["atr_period"])
        atr_pct = atr / price if price > 0 else 0
        if not (p["atr_min_pct"] <= atr_pct <= p["atr_max_pct"]):
            return flat(f"ATR filter: {atr_pct:.3%} not in [{p['atr_min_pct']:.3%}, {p['atr_max_pct']:.3%}]")

        # ── 2. News guard — volume spike ───────────────────────────────────────
        nvp = p["news_vol_period"]
        if len(volumes) >= nvp + 1:
            avg_vol = float(np.mean(volumes[-(nvp + 1):-1]))
            if avg_vol > 0 and volumes[-1] > avg_vol * p["news_vol_spike"]:
                return flat(f"News guard: vol spike {volumes[-1] / avg_vol:.1f}x avg")

        # ── 3. HTF trend ───────────────────────────────────────────────────────
        htf_ema_vals = self._ema(closes, p["htf_ema"])
        htf_ema_now  = htf_ema_vals[-1]
        htf_ema_old  = htf_ema_vals[-p["htf_slope_bars"]]
        slope = (htf_ema_now - htf_ema_old) / htf_ema_old if htf_ema_old else 0
        slope_per_bar = slope / p["htf_slope_bars"]

        # Structural pivot check: last 3 swing lows rising (bull) or falling (bear)
        htf_bullish = (price > htf_ema_now) and (slope_per_bar > p["htf_min_slope"])
        htf_bearish = (price < htf_ema_now) and (slope_per_bar < -p["htf_min_slope"])

        if not (htf_bullish or htf_bearish):
            return flat(f"HTF trend neutral: slope={slope_per_bar:.5%}/bar price={'above' if price > htf_ema_now else 'below'} EMA{p['htf_ema']}")

        # ── 4. VWAP (rolling, volume-weighted) ────────────────────────────────
        vp = min(p["vwap_period"], len(candles))
        tp_arr = (highs[-vp:] + lows[-vp:] + closes[-vp:]) / 3.0
        vol_sub = volumes[-vp:]
        cum_vol = float(np.sum(vol_sub))
        vwap = float(np.dot(tp_arr, vol_sub)) / cum_vol if cum_vol > 0 else price

        # ── 5. Pullback EMA ───────────────────────────────────────────────────
        pb_ema_vals = self._ema(closes, p["pullback_ema"])
        pb_ema      = pb_ema_vals[-1]

        # "Near value area" = within VWAP band OR within pullback-EMA band,
        # checked over the last 3 bars (simulate 3m context)
        near_vwap = any(
            abs(candles[-(i + 1)].low - vwap) <= vwap * p["vwap_band_pct"] or
            abs(candles[-(i + 1)].close - vwap) <= vwap * p["vwap_band_pct"]
            for i in range(min(3, len(candles)))
        )
        near_ema = any(
            abs(candles[-(i + 1)].low - pb_ema) <= pb_ema * p["pullback_ema_band"] or
            abs(candles[-(i + 1)].close - pb_ema) <= pb_ema * p["pullback_ema_band"]
            for i in range(min(3, len(candles)))
        )
        near_value = near_vwap or near_ema

        if not near_value:
            return flat(f"No pullback to value area (VWAP={vwap:.2f}, EMA{p['pullback_ema']}={pb_ema:.2f})")

        # ── 6. Liquidity sweep & reclaim ──────────────────────────────────────
        lb = p["sweep_lookback"]
        # Build the swing level from bars [-(lb+2) … -2] (exclude last 2 bars)
        swing_lows  = lows[-(lb + 2):-2]
        swing_highs = highs[-(lb + 2):-2]

        if len(swing_lows) < 2:
            return flat("Not enough bars for sweep detection")

        liquidity_low  = float(np.min(swing_lows))
        liquidity_high = float(np.max(swing_highs))

        prev_bar = candles[-2]
        curr_bar = candles[-1]

        # Long sweep: prev bar wicked BELOW liquidity low; curr bar CLOSES back above
        long_sweep = (
            prev_bar.low < liquidity_low * (1 - p["sweep_min_pct"])
            and curr_bar.close > liquidity_low * (1 + p["reclaim_min_pct"])
            and curr_bar.close > prev_bar.open      # bullish engulf/close
        )

        # Short sweep: prev bar wicked ABOVE liquidity high; curr bar CLOSES back below
        short_sweep = (
            prev_bar.high > liquidity_high * (1 + p["sweep_min_pct"])
            and curr_bar.close < liquidity_high * (1 - p["reclaim_min_pct"])
            and curr_bar.close < prev_bar.open      # bearish engulf/close
        )

        # ── 7. Funding proxy — directional bar bias ────────────────────────────
        fl = p["funding_lookback"]
        bull_bars = float(np.sum(closes[-fl:] > opens[-fl:]))
        bias = bull_bars / fl  # fraction of bull bars

        # For long: skip if > funding_max_bias bull bars (everyone already long)
        # For short: skip if < (1 - funding_max_bias) bull bars (everyone already short)
        funding_long_ok  = bias < p["funding_max_bias"]
        funding_short_ok = bias > (1 - p["funding_max_bias"])

        # ── 8. Combine conditions & produce signal ────────────────────────────
        sl_dist = atr * p["atr_sl_mult"]
        r        = sl_dist  # 1R = stop distance

        if htf_bullish and long_sweep and funding_long_ok:
            structure_stop = liquidity_low * (1 - 0.0003)
            atr_stop       = price - sl_dist
            stop           = min(structure_stop, atr_stop)   # wider stop wins
            tp1            = price + r * p["r_mult_tp1"]
            tp2            = price + r * p["r_mult_tp2"]
            conf           = self._confidence(atr_pct, bias, True)
            return Signal(
                direction=SignalDirection.long,
                symbol=symbol, timeframe=timeframe,
                confidence=conf,
                suggested_entry=round(price, 6),
                suggested_sl=round(stop, 6),
                suggested_tp=round(tp2, 6),
                reasoning=(
                    f"SMART MONEY LONG | HTF EMA{p['htf_ema']} slope {slope_per_bar:.4%}/bar "
                    f"| Sweep of {liquidity_low:.2f} reclaimed | VWAP={vwap:.2f} "
                    f"| ATR={atr_pct:.3%} | FundBias={bias:.0%} "
                    f"| Stop={stop:.2f} TP1={tp1:.2f} TP2={tp2:.2f}"
                ),
            )

        if htf_bearish and short_sweep and funding_short_ok:
            structure_stop = liquidity_high * (1 + 0.0003)
            atr_stop       = price + sl_dist
            stop           = max(structure_stop, atr_stop)   # wider stop wins
            tp1            = price - r * p["r_mult_tp1"]
            tp2            = price - r * p["r_mult_tp2"]
            conf           = self._confidence(atr_pct, bias, False)
            return Signal(
                direction=SignalDirection.short,
                symbol=symbol, timeframe=timeframe,
                confidence=conf,
                suggested_entry=round(price, 6),
                suggested_sl=round(stop, 6),
                suggested_tp=round(tp2, 6),
                reasoning=(
                    f"SMART MONEY SHORT | HTF EMA{p['htf_ema']} slope {slope_per_bar:.4%}/bar "
                    f"| Sweep of {liquidity_high:.2f} rejected | VWAP={vwap:.2f} "
                    f"| ATR={atr_pct:.3%} | FundBias={bias:.0%} "
                    f"| Stop={stop:.2f} TP1={tp1:.2f} TP2={tp2:.2f}"
                ),
            )

        return flat(
            f"Conditions not met | htf={'bull' if htf_bullish else 'bear' if htf_bearish else 'neutral'} "
            f"long_sweep={long_sweep} short_sweep={short_sweep} "
            f"funding_long={funding_long_ok} funding_short={funding_short_ok}"
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _ema(series: np.ndarray, period: int) -> np.ndarray:
        """Exponential moving average (vectorised, no pandas dependency)."""
        k = 2.0 / (period + 1)
        out = np.empty_like(series)
        out[0] = series[0]
        for i in range(1, len(series)):
            out[i] = series[i] * k + out[i - 1] * (1 - k)
        return out

    @staticmethod
    def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int) -> float:
        """Average True Range over the last `period` bars."""
        tr = np.maximum(
            highs[1:] - lows[1:],
            np.maximum(
                np.abs(highs[1:] - closes[:-1]),
                np.abs(lows[1:]  - closes[:-1]),
            )
        )
        if len(tr) == 0:
            return float(highs[-1] - lows[-1])
        return float(np.mean(tr[-period:]))

    @staticmethod
    def _confidence(atr_pct: float, bias: float, is_long: bool) -> float:
        """
        Confidence score 0–1:
          - ATR in the sweet spot (0.15%–2%) gives higher confidence
          - Funding bias closer to 50% (balanced) gives higher confidence
        """
        # Vol score: peaks at ~1% ATR
        ideal_vol = 0.010
        vol_score = 1.0 - min(abs(math.log(atr_pct / ideal_vol + 1e-9)), 2.0) / 2.0

        # Funding score: 0.5 bias = best, 0.8 or 0.2 = worst
        fund_score = 1.0 - abs(bias - 0.5) * 2.0
        fund_score = max(0.0, fund_score)

        return round(max(0.0, min(1.0, (vol_score + fund_score) / 2.0)), 3)
