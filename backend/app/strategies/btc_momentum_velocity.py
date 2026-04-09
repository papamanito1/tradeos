"""
BTC Momentum Velocity Scalper  (15m)
======================================
Edge:  Captures explosive momentum moves on BTC right after a confirmed
       trend resumption following a VWAP / EMA(21) pullback.

Why it wins
-----------
* 1 : 2  risk-reward — only needs a 34 % win rate to be profitable.
* Entries are taken AFTER momentum is confirmed, not before — avoids
  chasing breakouts that fail.
* Volume surge filter (1.4× avg) ensures institutional participation and
  rules out low-conviction moves.
* ATR-gated SL adapts to the current volatility regime; no fixed pip stops
  that get hunted.
* RSI 50 cross as a trigger: captures the exact moment momentum flips from
  neutral to directional.

Signal logic  (LONG mirror for SHORT)
--------------------------------------
1. EMA(50) slope is positive over last 5 bars   →  macro trend is up
2. Price is above EMA(50)                        →  price on right side of trend
3. Price pulled back to within 1 ATR of EMA(21) or VWAP  →  entry at value
4. RSI(14) was below 50 within last 3 bars and is now ≥ 52   →  momentum cross
5. Current bar volume  ≥  1.4 × 20-bar avg volume            →  participation
6. Current bar body ratio  ≥  0.55                            →  conviction candle
7. ATR(14) / close  ∈  [0.001, 0.012]                        →  tradeable volatility

Risk management
---------------
SL  = entry − 1.5 × ATR     (hard stop)
TP1 = entry + 2.0 × ATR     (partial, single target for backtesting)
Confidence scales with volume strength and RSI distance from 50.
"""

from __future__ import annotations

import math
from typing import Optional

from app.exchange.base import Candle
from app.strategies.base import BaseStrategy, Signal, SignalDirection


# ── Indicator helpers ─────────────────────────────────────────────────────────

def _ema(values: list[float], period: int) -> list[float]:
    """Exponential moving average (returns same-length list, NaN-padded)."""
    if len(values) < period:
        return [float("nan")] * len(values)
    k = 2.0 / (period + 1)
    result: list[float] = [float("nan")] * (period - 1)
    seed = sum(values[:period]) / period
    result.append(seed)
    prev = seed
    for v in values[period:]:
        cur = v * k + prev * (1 - k)
        result.append(cur)
        prev = cur
    return result


def _rsi(closes: list[float], period: int = 14) -> list[float]:
    """Wilder RSI."""
    n = len(closes)
    if n <= period:
        return [float("nan")] * n
    deltas = [closes[i] - closes[i - 1] for i in range(1, n)]
    gains = [max(d, 0.0) for d in deltas]
    losses = [abs(min(d, 0.0)) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsi_vals: list[float] = [float("nan")] * (period + 1)
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi_vals.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_vals.append(100.0 - 100.0 / (1.0 + rs))
    return rsi_vals


def _atr(candles: list[Candle], period: int = 14) -> list[float]:
    """Average True Range (Wilder smoothing)."""
    n = len(candles)
    if n < period + 1:
        return [float("nan")] * n
    trs: list[float] = [float("nan")]
    for i in range(1, n):
        tr = max(
            candles[i].high - candles[i].low,
            abs(candles[i].high - candles[i - 1].close),
            abs(candles[i].low  - candles[i - 1].close),
        )
        trs.append(tr)
    valid_trs = [t for t in trs[1:period + 1] if not math.isnan(t)]
    if len(valid_trs) < period:
        return [float("nan")] * n
    seed = sum(valid_trs) / period
    atr_vals: list[float] = [float("nan")] * period
    atr_vals.append(seed)
    prev = seed
    for t in trs[period + 1:]:
        cur = (prev * (period - 1) + t) / period
        atr_vals.append(cur)
        prev = cur
    return atr_vals


def _vwap(candles: list[Candle], window: int = 50) -> list[float]:
    """Rolling VWAP over `window` bars."""
    result: list[float] = []
    for i, c in enumerate(candles):
        start = max(0, i - window + 1)
        seg = candles[start: i + 1]
        total_vol = sum(x.volume for x in seg)
        if total_vol == 0:
            result.append((c.high + c.low + c.close) / 3)
        else:
            tp_vol = sum((x.high + x.low + x.close) / 3 * x.volume for x in seg)
            result.append(tp_vol / total_vol)
    return result


def _sma_volume(candles: list[Candle], period: int = 20) -> list[float]:
    result: list[float] = [float("nan")] * (period - 1)
    for i in range(period - 1, len(candles)):
        avg = sum(c.volume for c in candles[i - period + 1: i + 1]) / period
        result.append(avg)
    return result


# ── Strategy ─────────────────────────────────────────────────────────────────

class BtcMomentumVelocity(BaseStrategy):
    """
    BTC Momentum Velocity Scalper — optimised for 15m, works on 5m / 30m too.
    """

    name = "BTC Momentum Velocity Scalper"
    description = (
        "VWAP / EMA(21) pullback entry triggered by RSI(14) momentum cross above/below 50 "
        "with volume surge confirmation. 1:2 R:R, ATR-adaptive stops."
    )
    default_parameters = {
        "ema_trend_period":  50,    # macro trend filter
        "ema_pullback_period": 21,  # value zone
        "rsi_period":        14,
        "atr_period":        14,
        "vwap_window":       50,
        "vol_avg_period":    20,
        "rsi_cross_lookback": 3,    # bars ago RSI was below 50
        "rsi_trigger_long":  52,    # RSI must be ≥ this after crossing 50
        "rsi_trigger_short": 48,    # RSI must be ≤ this after crossing 50
        "vol_ratio_min":     1.4,   # volume must be X× average
        "body_ratio_min":    0.50,  # body / total range
        "pullback_atr_mult": 1.2,   # price within N×ATR of EMA21/VWAP
        "sl_atr_mult":       1.5,   # stop loss distance
        "tp_atr_mult":       3.0,   # take profit distance  →  1:2 R:R
        "atr_min_pct":       0.001, # min ATR/price (filter flat markets)
        "atr_max_pct":       0.012, # max ATR/price (filter panic spikes)
        "ema_slope_bars":    5,     # bars to measure EMA50 slope over
    }

    def generate_signal(self, candles: list[Candle], symbol: str, timeframe: str) -> Signal:
        p = self.parameters
        no_signal = Signal(
            direction=SignalDirection.none,
            symbol=symbol, timeframe=timeframe,
            confidence=0.0, suggested_entry=0.0,
        )

        min_bars = max(
            p["ema_trend_period"] + p["ema_slope_bars"] + 5,
            p["rsi_period"] + p["rsi_cross_lookback"] + 5,
            60,
        )
        if len(candles) < min_bars:
            return no_signal

        closes  = [c.close for c in candles]
        highs   = [c.high  for c in candles]
        lows    = [c.low   for c in candles]

        # ── Indicators ────────────────────────────────────────────────────────
        ema50  = _ema(closes, p["ema_trend_period"])
        ema21  = _ema(closes, p["ema_pullback_period"])
        rsi    = _rsi(closes, p["rsi_period"])
        atr    = _atr(candles, p["atr_period"])
        vwap   = _vwap(candles, p["vwap_window"])
        vol_ma = _sma_volume(candles, p["vol_avg_period"])

        # Current bar (index -1) and previous bars
        i = len(candles) - 1

        cur_close  = closes[i]
        cur_high   = highs[i]
        cur_low    = lows[i]
        cur_open   = candles[i].open
        cur_vol    = candles[i].volume
        cur_atr    = atr[i]
        cur_ema50  = ema50[i]
        cur_ema21  = ema21[i]
        cur_vwap   = vwap[i]
        cur_rsi    = rsi[i]
        cur_volma  = vol_ma[i]
        prev_ema50 = ema50[i - p["ema_slope_bars"]]

        # Guard: all indicators must be valid
        for v in [cur_atr, cur_ema50, cur_ema21, cur_vwap, cur_rsi,
                  cur_volma, prev_ema50]:
            if math.isnan(v) or v == 0:
                return no_signal

        # ── Gate 1: Tradeable volatility ──────────────────────────────────────
        atr_pct = cur_atr / cur_close
        if not (p["atr_min_pct"] <= atr_pct <= p["atr_max_pct"]):
            return no_signal

        # ── Gate 2: Volume participation ─────────────────────────────────────
        vol_ratio = cur_vol / cur_volma if cur_volma > 0 else 0
        if vol_ratio < p["vol_ratio_min"]:
            return no_signal

        # ── Gate 3: Conviction candle (body ≥ 50 % of range) ─────────────────
        bar_range = cur_high - cur_low
        body = abs(cur_close - cur_open)
        body_ratio = body / bar_range if bar_range > 0 else 0
        if body_ratio < p["body_ratio_min"]:
            return no_signal

        # ── EMA50 slope ───────────────────────────────────────────────────────
        ema50_slope = (cur_ema50 - prev_ema50) / prev_ema50  # fractional

        # ── RSI cross detection ───────────────────────────────────────────────
        lb = p["rsi_cross_lookback"]
        rsi_window = rsi[i - lb: i + 1]
        # All values valid?
        if any(math.isnan(r) for r in rsi_window):
            return no_signal

        # ── LONG setup ────────────────────────────────────────────────────────
        long_conditions = (
            ema50_slope > 0.0002                                # EMA50 trending up
            and cur_close > cur_ema50                           # price above trend
            and cur_rsi >= p["rsi_trigger_long"]                # RSI crossed above 50
            and min(rsi_window[:-1]) < 50                       # was below 50 recently
            and cur_close > cur_open                            # bullish candle
            and (
                abs(cur_low - cur_ema21) <= p["pullback_atr_mult"] * cur_atr
                or abs(cur_low - cur_vwap) <= p["pullback_atr_mult"] * cur_atr
            )                                                   # pulled back to value
        )

        # ── SHORT setup ───────────────────────────────────────────────────────
        short_conditions = (
            ema50_slope < -0.0002                               # EMA50 trending down
            and cur_close < cur_ema50                           # price below trend
            and cur_rsi <= p["rsi_trigger_short"]               # RSI crossed below 50
            and max(rsi_window[:-1]) > 50                       # was above 50 recently
            and cur_close < cur_open                            # bearish candle
            and (
                abs(cur_high - cur_ema21) <= p["pullback_atr_mult"] * cur_atr
                or abs(cur_high - cur_vwap) <= p["pullback_atr_mult"] * cur_atr
            )                                                   # pulled back to value
        )

        if not long_conditions and not short_conditions:
            return no_signal

        direction = SignalDirection.long if long_conditions else SignalDirection.short

        # ── Risk levels ───────────────────────────────────────────────────────
        sl_dist = p["sl_atr_mult"] * cur_atr
        tp_dist = p["tp_atr_mult"] * cur_atr

        if direction == SignalDirection.long:
            entry = cur_close
            sl    = entry - sl_dist
            tp    = entry + tp_dist
        else:
            entry = cur_close
            sl    = entry + sl_dist
            tp    = entry - tp_dist

        # ── Confidence score ──────────────────────────────────────────────────
        # Scale with: volume strength, RSI distance from 50, EMA slope strength
        vol_score  = min(vol_ratio / 3.0, 1.0)           # caps at 3× avg
        rsi_score  = min(abs(cur_rsi - 50) / 20.0, 1.0)  # max when RSI is 70/30
        slope_score = min(abs(ema50_slope) / 0.003, 1.0)
        body_score  = min(body_ratio / 0.85, 1.0)
        confidence  = round(
            0.35 * vol_score + 0.30 * rsi_score + 0.20 * slope_score + 0.15 * body_score,
            3,
        )

        # ── Reasoning ─────────────────────────────────────────────────────────
        if direction == SignalDirection.long:
            reason = (
                f"LONG: EMA50 slope +{ema50_slope * 100:.3f}%/bar, "
                f"RSI {cur_rsi:.1f} crossed above 50, "
                f"vol {vol_ratio:.1f}× avg, "
                f"body ratio {body_ratio:.0%}, "
                f"entry near EMA21 ({cur_ema21:.2f}) / VWAP ({cur_vwap:.2f}). "
                f"SL -{sl_dist:.2f}  TP +{tp_dist:.2f}  (1:{p['tp_atr_mult'] / p['sl_atr_mult']:.1f} R:R)"
            )
        else:
            reason = (
                f"SHORT: EMA50 slope {ema50_slope * 100:.3f}%/bar, "
                f"RSI {cur_rsi:.1f} crossed below 50, "
                f"vol {vol_ratio:.1f}× avg, "
                f"body ratio {body_ratio:.0%}, "
                f"entry near EMA21 ({cur_ema21:.2f}) / VWAP ({cur_vwap:.2f}). "
                f"SL +{sl_dist:.2f}  TP -{tp_dist:.2f}  (1:{p['tp_atr_mult'] / p['sl_atr_mult']:.1f} R:R)"
            )

        return Signal(
            direction=direction,
            symbol=symbol,
            timeframe=timeframe,
            confidence=confidence,
            suggested_entry=round(entry, 2),
            suggested_sl=round(sl, 2),
            suggested_tp=round(tp, 2),
            reasoning=reason,
        )
