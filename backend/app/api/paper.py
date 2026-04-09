"""
Paper Trading API
Endpoints for resetting balance, querying state, and fetching live activity.
"""
from fastapi import APIRouter, Depends
from app.core.security import get_current_user
from app.core.redis_client import redis_set, redis_get, redis_publish

router = APIRouter(prefix="/api/paper", tags=["paper"])

PAPER_BALANCE_KEY  = "paper:balance"
PAPER_ORDERS_KEY   = "paper:orders"
INITIAL_BALANCE    = 10_000.0


@router.post("/reset")
async def reset_paper_account(current_user: dict = Depends(get_current_user)):
    """Reset paper trading account to $10,000 and clear all open positions/orders."""
    fresh_state = {
        "balance_usd": INITIAL_BALANCE,
        "positions":   {},
        "equity":      INITIAL_BALANCE,
    }
    await redis_set(PAPER_BALANCE_KEY, fresh_state, ex=86400 * 365)
    await redis_set(PAPER_ORDERS_KEY,  [],           ex=86400 * 365)
    await redis_publish("execution:order_placed", {
        "event":   "paper_reset",
        "message": f"Paper account reset to ${INITIAL_BALANCE:,.0f}",
    })
    return {"ok": True, "balance": INITIAL_BALANCE, "message": "Paper account reset to $10,000"}


@router.get("/state")
async def get_paper_state(current_user: dict = Depends(get_current_user)):
    """Current paper balance + positions."""
    state  = await redis_get(PAPER_BALANCE_KEY)
    orders = await redis_get(PAPER_ORDERS_KEY) or []
    if not state:
        state = {"balance_usd": INITIAL_BALANCE, "positions": {}, "equity": INITIAL_BALANCE}
    return {
        "balance_usd": state.get("balance_usd", INITIAL_BALANCE),
        "equity":      state.get("equity",      INITIAL_BALANCE),
        "positions":   state.get("positions",   {}),
        "order_count": len(orders),
        "filled_orders": [o for o in orders if o.get("status") == "filled"][-20:],
    }


@router.get("/activity")
async def get_activity(
    limit: int = 40,
    current_user: dict = Depends(get_current_user),
):
    """
    Merged feed of recent signals + paper orders, newest first.
    Used by the dashboard live activity panel.
    """
    from app.core.signal_cache import get_all as get_all_signals

    events: list[dict] = []

    # ── Signals ───────────────────────────────────────────────────────────────
    all_signals = get_all_signals()
    for sym, sigs in all_signals.items():
        for s in sigs:
            events.append({
                "kind":          "signal",
                "timestamp":     s["timestamp"],
                "symbol":        s["symbol"],
                "direction":     s["direction"],
                "entry":         s.get("entry", 0),
                "sl":            s.get("sl"),
                "tp":            s.get("tp"),
                "confidence":    s.get("confidence", 0),
                "strategy_name": s.get("strategy_name", ""),
                "reasoning":     s.get("reasoning", ""),
                "timeframe":     s.get("timeframe", ""),
            })

    # ── Paper orders ──────────────────────────────────────────────────────────
    orders = await redis_get(PAPER_ORDERS_KEY) or []
    for o in orders:
        if o.get("status") == "filled":
            events.append({
                "kind":          "trade",
                "timestamp":     o.get("created_at", ""),
                "symbol":        o.get("symbol", ""),
                "direction":     "long" if o.get("side") == "buy" else "short",
                "side":          o.get("side", ""),
                "amount":        o.get("amount", 0),
                "fill_price":    o.get("average_fill_price", 0),
                "fee":           o.get("fee", 0),
                "strategy_name": "Paper Trade",
            })

    events.sort(key=lambda e: e["timestamp"], reverse=True)
    return events[:limit]


@router.get("/analysis")
async def get_analysis(current_user: dict = Depends(get_current_user)):
    """
    Run the BTC Momentum Velocity indicator calculations on the latest candles
    and return the current condition checklist + indicator values.
    Updated every call — used by the dashboard analysis panel.
    """
    import math
    import httpx
    from datetime import datetime, timezone

    symbol    = "BTC/USDT"
    timeframe = "15m"

    # ── Fetch candles (live cache → Bybit REST) ───────────────────────────────
    candles_raw: list[dict] = []
    try:
        from app.agents.live_market_stream import LIVE_CANDLES
        candles_raw = list(LIVE_CANDLES.get(f"{symbol}:{timeframe}", []))
    except Exception:
        pass

    if len(candles_raw) < 60:
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.get(
                    "https://api.bybit.com/v5/market/kline",
                    params={"category": "spot", "symbol": "BTCUSDT", "interval": "15", "limit": 120},
                )
                resp.raise_for_status()
                rows = list(reversed(resp.json().get("result", {}).get("list", [])))
                candles_raw = [
                    {
                        "timestamp": datetime.fromtimestamp(int(r[0]) / 1000, tz=timezone.utc).isoformat(),
                        "open": float(r[1]), "high": float(r[2]),
                        "low": float(r[3]),  "close": float(r[4]), "volume": float(r[5]),
                    }
                    for r in rows
                ]
        except Exception as e:
            return {"error": str(e), "conditions": [], "indicators": {}}

    if len(candles_raw) < 60:
        return {"error": "Not enough candles", "conditions": [], "indicators": {}}

    # ── Indicator helpers (inline) ────────────────────────────────────────────
    closes  = [c["close"]  for c in candles_raw]
    highs   = [c["high"]   for c in candles_raw]
    lows    = [c["low"]    for c in candles_raw]
    volumes = [c["volume"] for c in candles_raw]

    def ema(vals, period):
        k = 2.0 / (period + 1)
        result = [float("nan")] * (period - 1)
        seed = sum(vals[:period]) / period
        result.append(seed)
        prev = seed
        for v in vals[period:]:
            prev = v * k + prev * (1 - k)
            result.append(prev)
        return result

    def rsi(vals, period=14):
        n = len(vals)
        if n <= period:
            return [float("nan")] * n
        deltas = [vals[i] - vals[i-1] for i in range(1, n)]
        gains  = [max(d, 0.0) for d in deltas]
        losses = [abs(min(d, 0.0)) for d in deltas]
        ag = sum(gains[:period]) / period
        al = sum(losses[:period]) / period
        out = [float("nan")] * (period + 1)
        for i in range(period, len(deltas)):
            ag = (ag * (period-1) + gains[i])  / period
            al = (al * (period-1) + losses[i]) / period
            out.append(100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al))
        return out

    def atr_val(period=14):
        trs = [float("nan")]
        for i in range(1, len(candles_raw)):
            tr = max(highs[i] - lows[i],
                     abs(highs[i] - closes[i-1]),
                     abs(lows[i]  - closes[i-1]))
            trs.append(tr)
        if len(trs) < period + 1:
            return float("nan")
        seed = sum(trs[1:period+1]) / period
        prev = seed
        for t in trs[period+1:]:
            prev = (prev * (period-1) + t) / period
        return prev

    def vwap_val(window=50):
        seg = candles_raw[-window:]
        tv = sum(c["volume"] for c in seg)
        if tv == 0:
            return closes[-1]
        return sum((c["high"]+c["low"]+c["close"])/3 * c["volume"] for c in seg) / tv

    def sma_vol(period=20):
        if len(volumes) < period:
            return float("nan")
        return sum(volumes[-period:]) / period

    # ── Calculate ─────────────────────────────────────────────────────────────
    ema50_arr = ema(closes, 50)
    ema21_arr = ema(closes, 21)
    rsi_arr   = rsi(closes, 14)

    cur_close  = closes[-1]
    cur_high   = highs[-1]
    cur_low    = lows[-1]
    cur_open   = candles_raw[-1]["open"]
    cur_ema50  = ema50_arr[-1]
    prev_ema50 = ema50_arr[-6] if len(ema50_arr) >= 6 else cur_ema50
    cur_ema21  = ema21_arr[-1]
    cur_rsi    = rsi_arr[-1]
    cur_atr    = atr_val(14)
    cur_vwap   = vwap_val(50)
    cur_vol    = volumes[-1]
    avg_vol    = sma_vol(20)
    vol_ratio  = cur_vol / avg_vol if avg_vol else 0

    ema50_slope   = (cur_ema50 - prev_ema50) / prev_ema50 if prev_ema50 else 0
    atr_pct       = cur_atr / cur_close if cur_close else 0
    bar_range     = cur_high - cur_low
    body          = abs(cur_close - cur_open)
    body_ratio    = body / bar_range if bar_range > 0 else 0
    rsi_window    = rsi_arr[-4:-1]
    rsi_was_below = any(not math.isnan(r) and r < 50 for r in rsi_window)
    rsi_was_above = any(not math.isnan(r) and r > 50 for r in rsi_window)
    near_ema21    = abs(cur_low - cur_ema21) <= 1.2 * cur_atr or abs(cur_high - cur_ema21) <= 1.2 * cur_atr
    near_vwap     = abs(cur_low - cur_vwap)  <= 1.2 * cur_atr or abs(cur_high - cur_vwap)  <= 1.2 * cur_atr

    is_nan = math.isnan

    # ── Condition checklist ───────────────────────────────────────────────────
    long_bias  = cur_close > cur_ema50 and ema50_slope > 0
    short_bias = cur_close < cur_ema50 and ema50_slope < 0
    bias = "long" if long_bias else ("short" if short_bias else "neutral")

    conditions = [
        {
            "name":    "EMA50 Trend",
            "met":     not is_nan(cur_ema50) and abs(ema50_slope) > 0.0002,
            "value":   f"{'▲' if ema50_slope > 0 else '▼'} {ema50_slope*100:.4f}%/bar",
            "target":  ">0.02%/bar",
            "detail":  "Slope must be directional",
        },
        {
            "name":    "Price vs EMA50",
            "met":     not is_nan(cur_ema50) and (cur_close > cur_ema50 if long_bias else cur_close < cur_ema50),
            "value":   f"{'Above' if cur_close > cur_ema50 else 'Below'} EMA50 ({cur_ema50:,.0f})",
            "target":  "Price on right side of trend",
            "detail":  "Must be above EMA50 for longs",
        },
        {
            "name":    "RSI(14) Momentum",
            "met":     not is_nan(cur_rsi) and (
                (cur_rsi >= 52 and rsi_was_below) or (cur_rsi <= 48 and rsi_was_above)
            ),
            "value":   f"{cur_rsi:.1f}" if not is_nan(cur_rsi) else "n/a",
            "target":  "≥52 (long) or ≤48 (short) after crossing 50",
            "detail":  "RSI 50 cross triggers momentum entry",
        },
        {
            "name":    "Volume Surge",
            "met":     vol_ratio >= 1.4,
            "value":   f"{vol_ratio:.2f}× average",
            "target":  "≥1.4× 20-bar average",
            "detail":  "Institutional participation required",
        },
        {
            "name":    "Pullback to Value",
            "met":     near_ema21 or near_vwap,
            "value":   f"EMA21 {cur_ema21:,.0f} | VWAP {cur_vwap:,.0f}",
            "target":  "Within 1.2 ATR of EMA21 or VWAP",
            "detail":  "Enter at discounted price, not the top",
        },
        {
            "name":    "Conviction Candle",
            "met":     body_ratio >= 0.50,
            "value":   f"{body_ratio:.0%} body/range",
            "target":  "≥50% body ratio",
            "detail":  "No indecision/doji candles",
        },
        {
            "name":    "ATR Filter",
            "met":     0.001 <= atr_pct <= 0.012,
            "value":   f"{atr_pct*100:.3f}% of price",
            "target":  "0.1%–1.2% of price",
            "detail":  "Filters flat & panic-spike markets",
        },
    ]

    met_count = sum(1 for c in conditions if c["met"])
    all_met   = met_count == len(conditions)

    # Recent signals from cache
    from app.core.signal_cache import get_signals
    recent = get_signals(symbol, 5)
    last_signal = recent[-1] if recent else None

    return {
        "symbol":     symbol,
        "timeframe":  timeframe,
        "bias":       bias,
        "all_met":    all_met,
        "met_count":  met_count,
        "total":      len(conditions),
        "conditions": conditions,
        "indicators": {
            "price":      cur_close,
            "ema50":      round(cur_ema50, 2) if not is_nan(cur_ema50) else None,
            "ema21":      round(cur_ema21, 2) if not is_nan(cur_ema21) else None,
            "vwap":       round(cur_vwap,  2),
            "rsi":        round(cur_rsi,   2) if not is_nan(cur_rsi)  else None,
            "atr":        round(cur_atr,   2) if not is_nan(cur_atr)  else None,
            "atr_pct":    round(atr_pct * 100, 4),
            "vol_ratio":  round(vol_ratio, 3),
            "ema50_slope": round(ema50_slope * 100, 5),
            "body_ratio": round(body_ratio, 3),
        },
        "last_signal": last_signal,
    }
