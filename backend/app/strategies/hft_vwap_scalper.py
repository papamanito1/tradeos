"""
HFT VWAP Scalper — 1-minute bar strategy
─────────────────────────────────────────
Bias  : EMA9 > EMA21 on 5m aggregated bars
Entry : 1m close above/below rolling VWAP, near VWAP (≤ 0.20 × ATR),
        OBI > 0.18, TFI > 0.12, microprice edge > 0.15 tick, spread ≤ 2 ticks
Exits : SL = max(0.35 × ATR, swing dist), TP1 = 0.6R (50%), TP2 = 1.2R (30%), trail rest
        Time: flatten > 45 s if PnL small, hard flatten > 90 s
        Flow: flatten if spread blows out, TFI flips, or data stale
"""

from __future__ import annotations

import math
from typing import Any

from .base import BaseStrategy, Signal, Candle


TICK_SIZE   = 0.10      # BTC/USDT
OBI_LEVELS  = 5
TFI_WINDOW  = 60        # seconds

DEFAULT_PARAMS: dict[str, Any] = {
    "obi_threshold":      0.18,
    "tfi_threshold":      0.12,
    "micro_edge_ticks":   0.15,
    "near_vwap_atr_mult": 0.20,
    "max_spread_ticks":   2,
    "sl_atr_mult":        0.35,
    "tp1_r":              0.6,
    "tp2_r":              1.2,
    "ema9_period":        9,
    "ema21_period":       21,
    "atr_period":         14,
    "swing_lookback":     10,
    "cooldown_bars":      3,
}


# ─── Indicator helpers ────────────────────────────────────────────────────────

def _ema(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return [float("nan")] * len(values)
    k = 2 / (period + 1)
    out: list[float] = [float("nan")] * (period - 1)
    seed = sum(values[:period]) / period
    out.append(seed)
    prev = seed
    for v in values[period:]:
        prev = v * k + prev * (1 - k)
        out.append(prev)
    return out


def _atr(candles: list[Candle], period: int = 14) -> list[float]:
    n = len(candles)
    if n < period + 1:
        return [float("nan")] * n
    trs: list[float] = [float("nan")]
    for i in range(1, n):
        trs.append(max(
            candles[i].high - candles[i].low,
            abs(candles[i].high - candles[i - 1].close),
            abs(candles[i].low  - candles[i - 1].close),
        ))
    seed = sum(trs[1:period + 1]) / period
    out: list[float] = [float("nan")] * period
    out.append(seed)
    prev = seed
    for i in range(period + 1, n):
        val = (prev * (period - 1) + trs[i]) / period
        out.append(val)
        prev = val
    return out


def _vwap(candles: list[Candle]) -> list[float]:
    cum_tpv = 0.0
    cum_vol = 0.0
    out: list[float] = []
    for c in candles:
        tp = (c.high + c.low + c.close) / 3
        cum_tpv += tp * c.volume
        cum_vol  += c.volume
        out.append(cum_tpv / cum_vol if cum_vol else tp)
    return out


def _to_5m(candles: list[Candle]) -> list[Candle]:
    """Aggregate 1m candles into 5m bars aligned to 5-minute UTC slots."""
    from collections import defaultdict
    buckets: dict[int, list[Candle]] = defaultdict(list)
    for c in candles:
        ts_ms  = int(c.timestamp.timestamp() * 1000)
        bucket = (ts_ms // 300_000) * 300_000
        buckets[bucket].append(c)
    result: list[Candle] = []
    for bucket in sorted(buckets):
        bars = buckets[bucket]
        result.append(Candle(
            timestamp = bars[0].timestamp,
            open  = bars[0].open,
            high  = max(b.high for b in bars),
            low   = min(b.low  for b in bars),
            close = bars[-1].close,
            volume= sum(b.volume for b in bars),
        ))
    return result


# ─── Strategy ─────────────────────────────────────────────────────────────────

class HftVwapScalper(BaseStrategy):
    name        = "HFT VWAP Scalper 1m"
    description = (
        "1-minute VWAP scalper. 5m EMA9/21 trend bias, entry near VWAP with "
        "order-book imbalance + trade-flow imbalance confirmation. "
        "0.6R / 1.2R dual target, trailing stop on remainder."
    )
    default_parameters = DEFAULT_PARAMS

    def generate_signal(
        self,
        candles: list[Candle],
        symbol: str,
        timeframe: str,
        order_book: dict | None = None,
        recent_trades: list[dict] | None = None,
    ) -> Signal | None:
        p = {**DEFAULT_PARAMS, **(self.parameters or {})}
        min_bars = max(p["ema21_period"] * 2, p["atr_period"] + 5)
        if len(candles) < min_bars:
            return None

        # ── 5m EMA bias ───────────────────────────────────────────────────────
        bars5m    = _to_5m(candles)
        closes5m  = [c.close for c in bars5m]
        ema9arr   = _ema(closes5m, p["ema9_period"])
        ema21arr  = _ema(closes5m, p["ema21_period"])
        ema9_5m   = ema9arr[-1]
        ema21_5m  = ema21arr[-1]
        if math.isnan(ema9_5m) or math.isnan(ema21_5m):
            return None
        long_bias  = ema9_5m > ema21_5m
        short_bias = ema9_5m < ema21_5m

        # ── 1m VWAP & ATR ────────────────────────────────────────────────────
        vwap_arr = _vwap(candles)
        atr_arr  = _atr(candles, p["atr_period"])
        vwap1m   = vwap_arr[-1]
        atr1m    = atr_arr[-1]
        cur      = candles[-1]
        if math.isnan(atr1m):
            return None

        near_vwap = abs(cur.close - vwap1m) <= p["near_vwap_atr_mult"] * atr1m

        # ── Order-book metrics ────────────────────────────────────────────────
        obi = 0.0
        microprice = cur.close
        micro_edge = 0.0
        spread_ticks = 0.0
        best_bid = cur.close
        best_ask = cur.close

        if order_book:
            bids = order_book.get("bids", [])[:OBI_LEVELS]
            asks = order_book.get("asks", [])[:OBI_LEVELS]
            bid_vol = sum(b[1] for b in bids)
            ask_vol = sum(a[1] for a in asks)
            if bid_vol + ask_vol:
                obi = (bid_vol - ask_vol) / (bid_vol + ask_vol)
            if bids and asks:
                best_bid = bids[0][0]
                best_ask = asks[0][0]
                bid_sz1  = bids[0][1]
                ask_sz1  = asks[0][1]
                if bid_sz1 + ask_sz1:
                    microprice = (best_ask * bid_sz1 + best_bid * ask_sz1) / (bid_sz1 + ask_sz1)
                micro_edge  = microprice - (best_bid + best_ask) / 2
                spread_ticks = (best_ask - best_bid) / TICK_SIZE

        clean_spread = spread_ticks <= p["max_spread_ticks"]

        # ── TFI (trade-flow imbalance from recent_trades list) ────────────────
        tfi = 0.0
        if recent_trades:
            buy_vol = sum(t["qty"] for t in recent_trades if not t.get("is_buyer_maker"))
            sell_vol= sum(t["qty"] for t in recent_trades if     t.get("is_buyer_maker"))
            if buy_vol + sell_vol:
                tfi = (buy_vol - sell_vol) / (buy_vol + sell_vol)

        # ── Signal conditions ─────────────────────────────────────────────────
        obi_ok_long   = obi         >  p["obi_threshold"]
        obi_ok_short  = obi         < -p["obi_threshold"]
        tfi_ok_long   = tfi         >  p["tfi_threshold"]
        tfi_ok_short  = tfi         < -p["tfi_threshold"]
        micro_ok_long = micro_edge  >  p["micro_edge_ticks"] * TICK_SIZE
        micro_ok_short= micro_edge  < -p["micro_edge_ticks"] * TICK_SIZE

        long_signal = (
            long_bias and cur.close > vwap1m and near_vwap
            and obi_ok_long and tfi_ok_long and micro_ok_long and clean_spread
        )
        short_signal = (
            short_bias and cur.close < vwap1m and near_vwap
            and obi_ok_short and tfi_ok_short and micro_ok_short and clean_spread
        )

        if not long_signal and not short_signal:
            return None

        direction = "long" if long_signal else "short"
        entry     = best_bid if direction == "long" else best_ask

        # SL = max(0.35 × ATR, swing distance)
        swing_low  = min(c.low  for c in candles[-p["swing_lookback"]:])
        swing_high = max(c.high for c in candles[-p["swing_lookback"]:])
        swing_dist = (entry - swing_low) if direction == "long" else (swing_high - entry)
        sl_dist    = max(p["sl_atr_mult"] * atr1m, swing_dist)
        sl         = entry - sl_dist if direction == "long" else entry + sl_dist
        R          = sl_dist
        tp1        = entry + p["tp1_r"] * R if direction == "long" else entry - p["tp1_r"] * R
        tp2        = entry + p["tp2_r"] * R if direction == "long" else entry - p["tp2_r"] * R

        # Confidence: weighted OBI + TFI + micro
        vol_score  = min(abs(obi) / 0.5, 1.0)
        flow_score = min(abs(tfi) / 0.4, 1.0)
        micro_score= min(abs(micro_edge) / (TICK_SIZE * 0.5), 1.0)
        confidence = 0.40 * vol_score + 0.35 * flow_score + 0.25 * micro_score

        reasoning = (
            f"{direction.upper()}: 5m EMA9={ema9_5m:.0f} {'>' if long_signal else '<'} EMA21={ema21_5m:.0f}, "
            f"close {cur.close:.0f} {'>' if long_signal else '<'} VWAP {vwap1m:.0f}, "
            f"OBI={obi:.3f}, TFI={tfi:.3f}, spread={spread_ticks:.1f}t. "
            f"Entry={entry:.1f} SL={sl:.1f} TP1={tp1:.1f}(50%) TP2={tp2:.1f}(30%) trail rest."
        )

        return Signal(
            symbol    = symbol,
            direction = direction,
            entry     = entry,
            stop_loss = sl,
            take_profit = tp2,
            confidence  = round(confidence, 3),
            reasoning   = reasoning,
            metadata    = {
                "tp1": tp1, "tp2": tp2, "r_distance": R,
                "obi": obi, "tfi": tfi, "vwap": vwap1m,
                "atr": atr1m, "spread_ticks": spread_ticks,
                "ema9_5m": ema9_5m, "ema21_5m": ema21_5m,
            },
        )
