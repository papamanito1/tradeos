"""
Server-side strategy engines for the 24/7 living agent.
Ported from the TypeScript frontend hooks — same logic, same parameters.

Strategies implemented:
  1. Momentum 15m  (useStrategyEngine.ts)
  2. OBI Scalper 1m (useOBIScalper.ts)
  3. ORB-30 Scalper  (useORBStrategy.ts — simplified)
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Literal

# ─── Candle & order book types ───────────────────────────────────────────────

@dataclass
class Candle:
    open:   float
    high:   float
    low:    float
    close:  float
    volume: float


@dataclass
class OrderBookLevel:
    price:  float
    amount: float


@dataclass
class OrderBook:
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]


# ─── Signal / Result types ────────────────────────────────────────────────────

@dataclass
class Signal:
    direction:  Literal["long", "short"]
    entry:      float
    sl:         float
    tp:         float
    confidence: float
    reasoning:  str
    rr:         str = "1 : 2.0"


@dataclass
class Condition:
    name: str
    met:  bool
    value: str


@dataclass
class StrategyResult:
    bias:       Literal["long", "short", "neutral"]
    conditions: list[Condition]
    met_count:  int
    total:      int
    all_met:    bool
    signal:     Signal | None
    strategy_name: str
    strategy_key:  str


# ─── Indicator helpers ────────────────────────────────────────────────────────

def ema(vals: list[float], period: int) -> list[float]:
    if len(vals) < period:
        return [float("nan")] * len(vals)
    k = 2.0 / (period + 1)
    out: list[float] = [float("nan")] * (period - 1)
    prev = sum(vals[:period]) / period
    out.append(prev)
    for v in vals[period:]:
        prev = v * k + prev * (1 - k)
        out.append(prev)
    return out


def rsi(closes: list[float], period: int = 14) -> list[float]:
    n = len(closes)
    if n <= period:
        return [float("nan")] * n
    deltas = [closes[i] - closes[i - 1] for i in range(1, n)]
    gains  = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    out: list[float] = [float("nan")] * (period + 1)
    for i in range(period, len(deltas)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
        out.append(100.0 if al == 0 else 100 - 100 / (1 + ag / al))
    return out


def atr(candles: list[Candle], period: int = 14) -> list[float]:
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
    seed = sum(trs[1:period + 1]) / period
    out: list[float] = [float("nan")] * period
    out.append(seed)
    prev = seed
    for i in range(period + 1, n):
        cur = (prev * (period - 1) + trs[i]) / period
        out.append(cur)
        prev = cur
    return out


def vwap(candles: list[Candle], window: int = 50) -> list[float]:
    out: list[float] = []
    for i, c in enumerate(candles):
        seg = candles[max(0, i - window + 1): i + 1]
        tv  = sum(s.volume for s in seg)
        if not tv:
            out.append((c.high + c.low + c.close) / 3)
        else:
            out.append(sum((s.high + s.low + s.close) / 3 * s.volume for s in seg) / tv)
    return out


def sma_vol(candles: list[Candle], period: int = 20) -> list[float]:
    out: list[float] = []
    for i, _ in enumerate(candles):
        if i < period - 1:
            out.append(float("nan"))
        else:
            out.append(sum(c.volume for c in candles[i - period + 1: i + 1]) / period)
    return out


def _nan(*vals) -> bool:
    return any(math.isnan(v) or v == 0 for v in vals)


# ─── Strategy 1: Momentum 15m ─────────────────────────────────────────────────

_M = dict(
    ema_trend_period=50, ema_pullback_period=21, rsi_period=14,
    atr_period=14, vwap_window=50, vol_avg_period=20,
    rsi_trigger_long=50, rsi_trigger_short=50,
    vol_ratio_min=0.8, pullback_atr_mult=2.0,
    sl_atr_mult=1.5, tp_atr_mult=3.0,
    atr_min_pct=0.0005, atr_max_pct=0.030,
    ema_slope_bars=5, signal_min_conds=5,
)


def run_momentum(candles15m: list[Candle]) -> StrategyResult:
    null = StrategyResult("neutral", [], 0, 7, False, None, "Momentum 15m", "momentum")
    if len(candles15m) < 65:
        return null

    closes = [c.close for c in candles15m]
    ema50  = ema(closes, _M["ema_trend_period"])
    ema21  = ema(closes, _M["ema_pullback_period"])
    rsi14  = rsi(closes, _M["rsi_period"])
    atr14  = atr(candles15m, _M["atr_period"])
    vwap50 = vwap(candles15m, _M["vwap_window"])
    vol_ma = sma_vol(candles15m, _M["vol_avg_period"])

    i = len(candles15m) - 1
    c = candles15m[i]
    e50, e21, v, r, a, vm = ema50[i], ema21[i], vwap50[i], rsi14[i], atr14[i], vol_ma[i]
    prev_e50 = ema50[i - _M["ema_slope_bars"]]

    if _nan(e50, e21, v, r, a, vm, prev_e50):
        return null

    atr_pct   = a / c.close
    vol_ratio = c.volume / vm if vm else 0
    bar_range = c.high - c.low
    body      = abs(c.close - c.open)
    body_ratio= body / bar_range if bar_range else 0
    slope     = (e50 - prev_e50) / prev_e50

    long_conds = [
        Condition("EMA50 trending up",     slope > 0.0002,                                             f"slope {slope*100:.4f}%"),
        Condition("Price above EMA50",     c.close > e50,                                              f"{c.close:.0f}>{e50:.0f}"),
        Condition("RSI > 50",              r >= _M["rsi_trigger_long"],                                f"RSI {r:.1f}"),
        Condition("Bullish candle",        c.close > c.open,                                           f"body {body_ratio*100:.0f}%"),
        Condition("Pullback to EMA21/VWAP",abs(c.low-e21) <= _M["pullback_atr_mult"]*a or abs(c.low-v) <= _M["pullback_atr_mult"]*a, f"EMA21 {e21:.0f}"),
        Condition(f"Volume >{_M['vol_ratio_min']}×", vol_ratio >= _M["vol_ratio_min"],                 f"{vol_ratio:.2f}×"),
        Condition("ATR in range",          _M["atr_min_pct"] <= atr_pct <= _M["atr_max_pct"],          f"{atr_pct*100:.3f}%"),
    ]
    short_conds = [
        Condition("EMA50 trending down",   slope < -0.0002,                                            f"slope {slope*100:.4f}%"),
        Condition("Price below EMA50",     c.close < e50,                                              f"{c.close:.0f}<{e50:.0f}"),
        Condition("RSI < 50",              r <= _M["rsi_trigger_short"],                               f"RSI {r:.1f}"),
        Condition("Bearish candle",        c.close < c.open,                                           f"body {body_ratio*100:.0f}%"),
        Condition("Rejection at EMA21/VWAP",abs(c.high-e21) <= _M["pullback_atr_mult"]*a or abs(c.high-v) <= _M["pullback_atr_mult"]*a, f"EMA21 {e21:.0f}"),
        Condition(f"Volume >{_M['vol_ratio_min']}×", vol_ratio >= _M["vol_ratio_min"],                 f"{vol_ratio:.2f}×"),
        Condition("ATR in range",          _M["atr_min_pct"] <= atr_pct <= _M["atr_max_pct"],          f"{atr_pct*100:.3f}%"),
    ]

    long_met  = sum(1 for cd in long_conds  if cd.met)
    short_met = sum(1 for cd in short_conds if cd.met)
    is_long   = slope > 0
    conditions= long_conds if is_long else short_conds
    met_count = long_met if is_long else short_met
    bias      = "long" if long_met >= 4 else "short" if short_met >= 4 else "neutral"

    signal = None
    fire_long  = long_met  >= _M["signal_min_conds"] and slope > 0
    fire_short = short_met >= _M["signal_min_conds"] and slope < 0
    if fire_long or fire_short:
        d  = "long" if fire_long else "short"
        sl = c.close - _M["sl_atr_mult"] * a if d == "long" else c.close + _M["sl_atr_mult"] * a
        tp = c.close + _M["tp_atr_mult"] * a if d == "long" else c.close - _M["tp_atr_mult"] * a
        mc = long_met if fire_long else short_met
        vol_s = min(vol_ratio / 2, 1); rsi_s = min(abs(r - 50) / 15, 1); sl_s = min(abs(slope) / 0.002, 1); bdy_s = min(body_ratio / 0.6, 1)
        raw   = 0.25*vol_s + 0.25*rsi_s + 0.20*sl_s + 0.15*bdy_s + 0.15*(mc/7)
        conf  = max(0.52, min(raw, 0.99))
        signal = Signal(d, round(c.close,2), round(sl,2), round(tp,2), round(conf,3),
                        f"[Momentum] {d.upper()} [{mc}/7]: EMA50 slope {slope*100:.3f}%, RSI {r:.1f}, vol {vol_ratio:.1f}×. SL ${abs(sl-c.close):.0f} TP ${abs(tp-c.close):.0f}",
                        f"1 : {_M['tp_atr_mult']/_M['sl_atr_mult']:.1f}")

    return StrategyResult(bias, conditions, met_count, 7, met_count == 7, signal, "Momentum 15m", "momentum")


# ─── Strategy 2: OBI Scalper 1m ──────────────────────────────────────────────

_O = dict(ema_fast=9, ema_slow=21, rsi_period=14, obi_threshold=0.20, ob_levels=10, sl_pct=0.004, tp_pct=0.008)


def run_obi(candles1m: list[Candle], ob: OrderBook | None) -> StrategyResult:
    null = StrategyResult("neutral", [], 0, 3, False, None, "OBI Scalper", "obi")
    if len(candles1m) < 30:
        return null

    closes  = [c.close for c in candles1m]
    ema9arr = ema(closes, _O["ema_fast"])
    ema21arr= ema(closes, _O["ema_slow"])
    rsi14   = rsi(closes, _O["rsi_period"])
    i       = len(candles1m) - 1
    e9, e21, r = ema9arr[i], ema21arr[i], rsi14[i]
    cur = candles1m[i].close

    if _nan(e9, e21, r):
        return null

    obi = bid_vol = ask_vol = 0.0
    if ob and ob.bids and ob.asks:
        bid_vol = sum(lvl.amount for lvl in ob.bids[:_O["ob_levels"]])
        ask_vol = sum(lvl.amount for lvl in ob.asks[:_O["ob_levels"]])
        total   = bid_vol + ask_vol
        obi     = (bid_vol - ask_vol) / total if total else 0.0

    is_long   = e9 >= e21 or obi >= 0
    long_obi  = obi >  _O["obi_threshold"]
    short_obi = obi < -_O["obi_threshold"]
    long_ema  = e9 > e21
    short_ema = e9 < e21
    long_rsi  = r > 50
    short_rsi = r < 50

    sign  = "+" if obi >= 0 else ""
    conditions = [
        Condition(f"OBI {'>' if is_long else '<'} {'+'if is_long else '-'}{_O['obi_threshold']}",
                  long_obi if is_long else short_obi, f"{sign}{obi:.3f}"),
        Condition("EMA9 vs EMA21",  long_ema  if is_long else short_ema,  f"{e9:.0f} {'>' if is_long else '<'} {e21:.0f}"),
        Condition("RSI(14) vs 50",  long_rsi  if is_long else short_rsi,  f"RSI {r:.1f}"),
    ] if is_long else [
        Condition(f"OBI < -{_O['obi_threshold']}", short_obi, f"{sign}{obi:.3f}"),
        Condition("EMA9 < EMA21",  short_ema,  f"{e9:.0f} < {e21:.0f}"),
        Condition("RSI < 50",      short_rsi,  f"RSI {r:.1f}"),
    ]

    long_met  = sum([long_obi,  long_ema,  long_rsi])
    short_met = sum([short_obi, short_ema, short_rsi])
    met_count = long_met if is_long else short_met
    bias      = "long" if long_met == 3 else "short" if short_met == 3 else "neutral"

    signal = None
    if long_met == 3 or short_met == 3:
        d  = "long" if long_met == 3 else "short"
        sl = cur * (1 - _O["sl_pct"]) if d == "long" else cur * (1 + _O["sl_pct"])
        tp = cur * (1 + _O["tp_pct"]) if d == "long" else cur * (1 - _O["tp_pct"])
        obi_s = min(abs(obi) / 0.5, 1); rsi_s = min(abs(r - 50) / 20, 1)
        ema_s = min(abs(e9 - e21) / (e21 * 0.002), 1) if e21 else 0
        conf  = max(0.52, min(0.55*obi_s + 0.25*rsi_s + 0.20*ema_s, 0.99))
        signal = Signal(d, round(cur,2), round(sl,2), round(tp,2), round(conf,3),
                        f"[OBI] {d.upper()}: OBI {sign}{obi:.3f}, EMA9 {'>' if d=='long' else '<'} EMA21 ({e9:.0f}/{e21:.0f}), RSI {r:.1f}. SL {_O['sl_pct']*100:.1f}% TP {_O['tp_pct']*100:.1f}%",
                        "1 : 2.0")

    return StrategyResult(bias, conditions, met_count, 3, met_count == 3, signal, "OBI Scalper", "obi")


# ─── Strategy 3: ORB-30 Scalper ──────────────────────────────────────────────

_R = dict(
    orb_bars=30, orb_min_bars=22, max_hold_bars=210,
    or_range_min=0.05, or_range_max=5.0,
    atr_period=14, ema_period=20,
    sl_atr_mult=1.5, tp_atr_mult=2.5,
    breakout_buffer_pct=0.0002,
)

_SESSIONS = [
    ("London",    7,  30),  # 07:30 UTC
    ("New York",  13, 30),  # 13:30 UTC
    ("Asia",      0,   0),  # 00:00 UTC
]


def run_orb(candles1m: list[Candle]) -> StrategyResult:
    """Simplified ORB: detects breakout from first 30-min range of nearest session."""
    null = StrategyResult("neutral", [], 0, 5, False, None, "ORB-30", "orb")
    if len(candles1m) < 35:
        return null

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    # Find bars_in_orb and post_orb bars
    # Use last 35 candles — assume they are 1-minute bars ending now
    bars = candles1m[-210:] if len(candles1m) >= 210 else candles1m
    orb_bars = bars[:30]
    post_bars = bars[30:]

    if len(orb_bars) < _R["orb_min_bars"]:
        return StrategyResult("neutral", [Condition("ORB building", False, f"{len(orb_bars)}/30")], 0, 5, False, None, "ORB-30", "orb")

    or_high = max(c.high  for c in orb_bars)
    or_low  = min(c.low   for c in orb_bars)
    or_range_pct = (or_high - or_low) / or_low * 100 if or_low else 0

    if not (_R["or_range_min"] <= or_range_pct <= _R["or_range_max"]):
        return StrategyResult("neutral", [Condition("OR range valid", False, f"{or_range_pct:.2f}%")], 0, 5, False, None, "ORB-30", "orb")

    if not post_bars:
        return StrategyResult("neutral", [Condition("Waiting for breakout", False, "0 bars post-ORB")], 0, 5, False, None, "ORB-30", "orb")

    cur = post_bars[-1]
    closes_all = [c.close for c in bars]
    ema20_arr  = ema(closes_all, _R["ema_period"])
    atr14_arr  = atr(bars, _R["atr_period"])
    e20 = ema20_arr[-1] if not math.isnan(ema20_arr[-1]) else None
    a14 = atr14_arr[-1] if not math.isnan(atr14_arr[-1]) else None

    buf      = or_low * _R["breakout_buffer_pct"]
    long_bo  = cur.close > (or_high + buf)
    short_bo = cur.close < (or_low  - buf)
    ema_long = cur.close > e20  if e20 else True
    ema_short= cur.close < e20  if e20 else True
    bars_post = len(post_bars)
    in_window = bars_post <= _R["max_hold_bars"]
    vol_ok    = True  # simplified

    conditions = [
        Condition("ORB established",    len(orb_bars) >= _R["orb_min_bars"],   f"{len(orb_bars)}/30 bars"),
        Condition("OR range valid",     _R["or_range_min"] <= or_range_pct <= _R["or_range_max"], f"{or_range_pct:.2f}%"),
        Condition("Breakout detected",  long_bo or short_bo,                   f"H:{or_high:.0f} L:{or_low:.0f}"),
        Condition("EMA20 aligned",      (ema_long if long_bo else ema_short) if e20 else True, f"EMA20 {e20:.0f}" if e20 else "n/a"),
        Condition("Trade window",       in_window,                              f"{bars_post}/{_R['max_hold_bars']}"),
    ]
    met_count = sum(1 for cd in conditions if cd.met)
    bias = "long" if long_bo and ema_long else "short" if short_bo and ema_short else "neutral"

    signal = None
    if long_bo and ema_long and in_window and a14 and met_count >= 4:
        sl   = or_low  - _R["sl_atr_mult"]  * a14
        tp   = cur.close + _R["tp_atr_mult"] * a14
        conf = max(0.52, min(0.55 + min(or_range_pct / 2, 0.15) + (0.1 if e20 and cur.close > e20 else 0), 0.90))
        signal = Signal("long", round(cur.close,2), round(sl,2), round(tp,2), round(conf,3),
                        f"[ORB-30] LONG: breakout above ${or_high:.0f}, OR range {or_range_pct:.2f}%, EMA20 aligned. SL ${sl:.0f} TP ${tp:.0f}", "1 : 2.5")
    elif short_bo and ema_short and in_window and a14 and met_count >= 4:
        sl   = or_high + _R["sl_atr_mult"]  * a14
        tp   = cur.close - _R["tp_atr_mult"] * a14
        conf = max(0.52, min(0.55 + min(or_range_pct / 2, 0.15) + (0.1 if e20 and cur.close < e20 else 0), 0.90))
        signal = Signal("short", round(cur.close,2), round(sl,2), round(tp,2), round(conf,3),
                        f"[ORB-30] SHORT: breakdown below ${or_low:.0f}, OR range {or_range_pct:.2f}%, EMA20 aligned. SL ${sl:.0f} TP ${tp:.0f}", "1 : 2.5")

    return StrategyResult(bias, conditions, met_count, 5, met_count == 5, signal, "ORB-30", "orb")
