"""
ORB-30 Scalper — Opening Range Breakout (adapted for BTC 24/7)
──────────────────────────────────────────────────────────────
Based on the 5-Year NASDAQ Backtest (2020-2024):
  Total Return: +94.3%  |  Win Rate: 55.3%  |  Profit Factor: 1.81
  Sharpe: 1.74  |  Avg R:R: 2.25:1  |  Max DD: -13.8%

Adaptation for BTC/USDT 24/7:
  Sessions: 4-hour UTC blocks (00,04,08,12,16,20)
  ORB window: first 30 × 1m bars of each session
  Entry: breakout of OR_HIGH (long) or OR_LOW (short)
  Filter: 15m EMA20 trend filter (from PDF recommendation)
  Stop: opposite ORB boundary ± buffer
  Target: 2.25R (matching backtest Avg R:R)
  Volume: breakout bar volume ≥ 1.3× ORB session avg
  Time exit: max 90 bars (90 min) per session
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, List, Optional

from .base import BaseStrategy, Signal, Candle

# ── Parameters ────────────────────────────────────────────────────────────────
SESSION_HOURS = [0, 4, 8, 12, 16, 20]  # UTC session start hours

DEFAULT_PARAMS: dict[str, Any] = {
    "orb_bars":        30,      # 30 × 1m = 30-minute opening range
    "ema_period":      20,      # EMA20 on 15m bars for trend filter
    "rr_target":       2.25,    # R:R target from 5-yr backtest
    "sl_buffer_pct":   0.0003,  # 0.03% buffer beyond range boundary
    "vol_ratio":       1.3,     # breakout bar volume ≥ 1.3× ORB avg
    "max_hold_bars":   90,      # max 90 bars (90 min) per session
    "or_range_min":    0.08,    # min OR range %  (not too tight)
    "or_range_max":    3.5,     # max OR range % (not blown gap)
}


# ── Helpers ───────────────────────────────────────────────────────────────────
def _ema(values: List[float], period: int) -> List[float]:
    if len(values) < period:
        return [float("nan")] * len(values)
    k = 2 / (period + 1)
    out: List[float] = [float("nan")] * (period - 1)
    prev = sum(values[:period]) / period
    out.append(prev)
    for v in values[period:]:
        prev = v * k + prev * (1 - k)
        out.append(prev)
    return out


def _to_15m(candles: List[Candle]) -> List[Candle]:
    """Aggregate 1m candles to 15m."""
    from collections import defaultdict
    buckets: dict[int, List[Candle]] = defaultdict(list)
    for c in candles:
        try:
            ts = datetime.fromisoformat(c["timestamp"].replace("Z", "+00:00"))
        except (ValueError, KeyError):
            continue
        epoch_s = int(ts.timestamp())
        bucket  = (epoch_s // 900) * 900
        buckets[bucket].append(c)

    result: List[Candle] = []
    for bucket, bars in sorted(buckets.items()):
        result.append({
            "timestamp": datetime.fromtimestamp(bucket, tz=timezone.utc).isoformat(),
            "open":   bars[0]["open"],
            "high":   max(b["high"] for b in bars),
            "low":    min(b["low"]  for b in bars),
            "close":  bars[-1]["close"],
            "volume": sum(b.get("volume", 0) for b in bars),
        })
    return result


def _get_session_start(ts_str: str) -> int:
    """Return UTC epoch (seconds) for the start of the current 4h session."""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.now(tz=timezone.utc)
    epoch_s = int(dt.timestamp())
    day_start = (epoch_s // 86400) * 86400
    hour = dt.hour
    session_hour = max((h for h in SESSION_HOURS if h <= hour), default=0)
    return day_start + session_hour * 3600


# ── Strategy class ────────────────────────────────────────────────────────────
class ORBScalper(BaseStrategy):
    """
    ORB-30 Opening Range Breakout — adapted for BTC/USDT 24/7 trading.

    Uses 4-hour UTC session blocks. The first 30 1m bars define the Opening
    Range. After the ORB window, breakouts from OR_HIGH (long) or OR_LOW
    (short) are traded with a 15m EMA20 trend filter and volume confirmation.
    """

    name: str = "ORB-30 Scalper"
    timeframe: str = "1m"

    def generate_signal(
        self,
        candles: List[Candle],
        symbol: str = "BTC/USDT",
        timeframe: str = "1m",
        order_book: Optional[dict] = None,
        recent_trades: Optional[List[dict]] = None,
    ) -> Signal:
        p = {**DEFAULT_PARAMS, **(self.params or {})}

        no_signal = Signal(
            symbol=symbol, direction="neutral", confidence=0.0,
            entry=0.0, sl=None, tp=None,
            reasoning="Insufficient data or no setup.",
            conditions={},
        )

        if not candles or len(candles) < p["orb_bars"] + 5:
            no_signal["reasoning"] = f"Need ≥ {p['orb_bars'] + 5} 1m bars, got {len(candles)}."
            return no_signal

        # ── Identify current session ──────────────────────────────────────────
        last_ts       = candles[-1]["timestamp"]
        session_start = _get_session_start(last_ts)
        orb_end       = session_start + p["orb_bars"] * 60

        orb_candles  = [c for c in candles if _ts_to_epoch(c["timestamp"]) >= session_start
                        and _ts_to_epoch(c["timestamp"]) <  orb_end]
        post_candles = [c for c in candles if _ts_to_epoch(c["timestamp"]) >= orb_end]

        orb_established = len(orb_candles) >= p["orb_bars"]
        if not orb_established or not post_candles:
            no_signal["reasoning"] = (
                f"ORB window building: {len(orb_candles)}/{p['orb_bars']} bars. "
                f"Post-ORB bars: {len(post_candles)}."
            )
            no_signal["conditions"] = {
                "orb_established": False,
                "bars_in_orb": len(orb_candles),
                "bars_after_orb": len(post_candles),
            }
            return no_signal

        # ── Opening Range ─────────────────────────────────────────────────────
        OR_HIGH = max(c["high"]   for c in orb_candles)
        OR_LOW  = min(c["low"]    for c in orb_candles)
        or_mid  = (OR_HIGH + OR_LOW) / 2
        or_range = OR_HIGH - OR_LOW
        or_range_pct = or_range / or_mid * 100 if or_mid else 0

        range_valid = p["or_range_min"] <= or_range_pct <= p["or_range_max"]

        # ── 15m EMA20 trend filter ────────────────────────────────────────────
        bars15m     = _to_15m(candles)
        closes15m   = [float(c["close"]) for c in bars15m]
        ema20_arr   = _ema(closes15m, int(p["ema_period"]))
        ema20       = ema20_arr[-1] if ema20_arr else float("nan")

        cur          = post_candles[-1]
        cur_close    = float(cur["close"])
        cur_vol      = float(cur.get("volume", 0))
        bars_after   = len(post_candles)

        is_above_ema  = not math.isnan(ema20) and cur_close > ema20
        is_below_ema  = not math.isnan(ema20) and cur_close < ema20
        bias          = "long" if is_above_ema else "short" if is_below_ema else "neutral"

        # ── Volume filter ─────────────────────────────────────────────────────
        sess_vol_avg = (
            sum(float(c.get("volume", 0)) for c in orb_candles) / len(orb_candles)
        ) if orb_candles else 1
        vol_ratio     = cur_vol / sess_vol_avg if sess_vol_avg > 0 else 0
        vol_ok        = vol_ratio >= p["vol_ratio"]

        # ── Breakout detection ────────────────────────────────────────────────
        breakout_long  = cur_close > OR_HIGH
        breakout_short = cur_close < OR_LOW
        within_session = 0 < bars_after <= p["max_hold_bars"]

        long_signal  = breakout_long  and is_above_ema and vol_ok and within_session and range_valid
        short_signal = breakout_short and is_below_ema and vol_ok and within_session and range_valid

        # ── Conditions dict ───────────────────────────────────────────────────
        conditions = {
            "ema20_trend":     bias,
            "or_high":         round(OR_HIGH, 2),
            "or_low":          round(OR_LOW,  2),
            "or_range_pct":    round(or_range_pct, 3),
            "range_valid":     range_valid,
            "breakout_long":   breakout_long,
            "breakout_short":  breakout_short,
            "vol_ratio":       round(vol_ratio, 3),
            "vol_ok":          vol_ok,
            "within_session":  within_session,
            "bars_after_orb":  bars_after,
            "ema20_15m":       round(ema20, 2) if not math.isnan(ema20) else None,
        }

        if not (long_signal or short_signal):
            conds_met = sum([
                bias != "neutral",
                True,                 # OR always established here
                breakout_long or breakout_short,
                vol_ok,
                within_session,
                range_valid,
            ])
            return Signal(
                symbol=symbol, direction="neutral",
                confidence=round(conds_met / 6, 3),
                entry=cur_close, sl=None, tp=None,
                reasoning=(
                    f"No breakout. Bias: {bias}. "
                    f"Close {cur_close:.0f} vs OR {OR_HIGH:.0f}/{OR_LOW:.0f}. "
                    f"Vol ratio {vol_ratio:.2f}×. Range {or_range_pct:.2f}%."
                ),
                conditions=conditions,
            )

        # ── Entry / SL / TP calculation ───────────────────────────────────────
        direction  = "long" if long_signal else "short"
        entry      = cur_close
        sl_base    = OR_LOW if direction == "long" else OR_HIGH
        sl_buf     = sl_base * p["sl_buffer_pct"]
        sl         = sl_base - sl_buf if direction == "long" else sl_base + sl_buf
        risk       = abs(entry - sl)
        tp         = entry + p["rr_target"] * risk if direction == "long" \
                     else entry - p["rr_target"] * risk
        tp_ib      = entry + risk if direction == "long" else entry - risk  # 1R partial

        # Confidence: breakout strength + vol bonus
        break_str  = (cur_close - OR_HIGH) / or_range if direction == "long" \
                     else (OR_LOW - cur_close) / or_range
        vol_bonus  = min((vol_ratio - p["vol_ratio"]) / p["vol_ratio"], 1.0)
        confidence = min(0.48 + max(0, break_str) * 0.25 + vol_bonus * 0.27, 0.95)

        return Signal(
            symbol=symbol,
            direction=direction,
            confidence=round(confidence, 3),
            entry=round(entry, 2),
            sl=round(sl, 2),
            tp=round(tp, 2),
            reasoning=(
                f"{'▲ LONG' if direction == 'long' else '▼ SHORT'} ORB-30 breakout: "
                f"close {entry:.0f} {'>' if direction == 'long' else '<'} "
                f"OR {'High' if direction == 'long' else 'Low'} {OR_HIGH if direction == 'long' else OR_LOW:.0f}. "
                f"Range {or_range_pct:.2f}%, 15m EMA20 {ema20:.0f} ({bias}), "
                f"vol {vol_ratio:.2f}× avg. "
                f"SL {sl:.0f}, TP1 {tp_ib:.0f} (1R), TP2 {tp:.0f} ({p['rr_target']}R). "
                f"5yr backtest: +94.3% return, 55.3% WR, 2.25:1 R:R."
            ),
            conditions={
                **conditions,
                "direction":    direction,
                "tp_ib":        round(tp_ib, 2),
                "risk_usd":     round(risk, 2),
                "rr_target":    p["rr_target"],
            },
        )


def _ts_to_epoch(ts_str: str) -> int:
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except (ValueError, AttributeError):
        return 0
