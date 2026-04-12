"""
Persistent 24/7 Trading Agent
==============================
Runs all strategies on the backend server continuously.
Positions survive browser close / page refreshes.
State is persisted to a JSON file and reloaded on startup.

Strategies:
  - Momentum 15m   (EMA50 slope + RSI + VWAP + volume)
  - HFT Scalper 1m (EMA9/21 + OBI + TFI + microprice)
  - ORB-30 1m      (Opening Range Breakout, first 30 bars)
  - OBI Scalper 1m (Order Book Imbalance + EMA9/21 + RSI)
  - Fusion          (Master Brain signal aggregation across all strategies)
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── State file (survives restarts) ────────────────────────────────────────────
STATE_FILE = Path(os.environ.get("AGENT_STATE_FILE", "/tmp/tradeos_agent_state.json"))

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_STRATEGY_CFG = {
    "enabled":        True,
    "size_usdc":      5,
    "leverage":       60,
    "min_confidence": 0.50,
    "min_conditions": 2,
}

DEFAULT_CONFIG = {
    "enabled":        True,   # auto-start on server boot
    "size_usdc":      5,
    "min_confidence": 0.50,
    "min_conditions": 2,
    "mode":           "live",
    "auto_execute":   True,
    "leverage":       60,
    "daily_loss_limit": 50.0,   # halt trading if daily loss exceeds $50
    "max_position_usdc": 50.0,  # max total exposure
    "strategy_overrides": {
        "momentum": {**DEFAULT_STRATEGY_CFG},
        "hft":      {**DEFAULT_STRATEGY_CFG},
        "orb":      {**DEFAULT_STRATEGY_CFG},
        "obi":      {**DEFAULT_STRATEGY_CFG},
    },
}

STRATEGY_KEYS = ["momentum", "hft", "orb", "obi", "fusion"]

# Max hold time (minutes) for always-on shadow paper positions
SHADOW_MAX_HOLD = {"momentum": 90, "hft": 20, "orb": 60, "obi": 10}

# ── Indicator helpers ─────────────────────────────────────────────────────────

def _ema(vals: list[float], period: int) -> list[float]:
    if len(vals) < period:
        return [float("nan")] * len(vals)
    k = 2 / (period + 1)
    out: list[float] = [float("nan")] * (period - 1)
    prev = sum(vals[:period]) / period
    out.append(prev)
    for i in range(period, len(vals)):
        prev = vals[i] * k + prev * (1 - k)
        out.append(prev)
    return out


def _rsi(closes: list[float], period: int = 14) -> list[float]:
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
        out.append(0.0 if al == 0 else 100 - 100 / (1 + ag / al))
    return out


def _atr(candles: list[dict], period: int = 14) -> list[float]:
    n = len(candles)
    if n < period + 1:
        return [float("nan")] * n
    trs: list[float] = [float("nan")]
    for i in range(1, n):
        tr = max(
            candles[i]["high"] - candles[i]["low"],
            abs(candles[i]["high"] - candles[i - 1]["close"]),
            abs(candles[i]["low"]  - candles[i - 1]["close"]),
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


def _vwap(candles: list[dict], window: int = 50) -> list[float]:
    result = []
    for i, c in enumerate(candles):
        seg = candles[max(0, i - window + 1):i + 1]
        tv = sum(s["volume"] for s in seg)
        if not tv:
            result.append((c["high"] + c["low"] + c["close"]) / 3)
        else:
            result.append(
                sum((s["high"] + s["low"] + s["close"]) / 3 * s["volume"] for s in seg) / tv
            )
    return result


def _sma_vol(candles: list[dict], period: int = 20) -> list[float]:
    out = []
    for i in range(len(candles)):
        if i < period - 1:
            out.append(float("nan"))
        else:
            out.append(sum(c["volume"] for c in candles[i - period + 1:i + 1]) / period)
    return out


def _nan(v) -> bool:
    try:
        return math.isnan(float(v))
    except (TypeError, ValueError):
        return True


# ── Strategy engines ─────────────────────────────────────────────────────────

def _run_momentum(candles15m: list[dict]) -> dict:
    """BTC Momentum Velocity Scalper (15m)."""
    null = {"bias": "neutral", "signal": None, "met_count": 0, "total": 7, "name": "Momentum 15m"}
    if len(candles15m) < 65:
        return null

    closes = [c["close"] for c in candles15m]
    ema50  = _ema(closes, 50)
    ema21  = _ema(closes, 21)
    rsi_a  = _rsi(closes, 14)
    atr_a  = _atr(candles15m, 14)
    vwap_a = _vwap(candles15m, 50)
    vol_ma = _sma_vol(candles15m, 20)

    i = len(candles15m) - 1
    cur = candles15m[i]
    for v in [atr_a[i], ema50[i], ema21[i], vwap_a[i], rsi_a[i], vol_ma[i], ema50[i - 5]]:
        if _nan(v) or v == 0:
            return null

    atr_pct    = atr_a[i] / cur["close"]
    vol_ratio  = cur["volume"] / vol_ma[i]
    bar_range  = cur["high"] - cur["low"]
    body       = abs(cur["close"] - cur["open"])
    body_ratio = body / bar_range if bar_range > 0 else 0
    ema50_slope = (ema50[i] - ema50[i - 5]) / ema50[i - 5]

    # EMA21 slope for extra trend confirmation
    ema21_slope = (ema21[i] - ema21[i - 3]) / ema21[i - 3] if ema21[i - 3] > 0 else 0

    long_conds = [
        ema50_slope > 0.0001,
        cur["close"] > ema50[i],
        rsi_a[i] >= 50,
        cur["close"] > cur["open"],
        abs(cur["low"] - ema21[i]) <= 2.5 * atr_a[i] or abs(cur["low"] - vwap_a[i]) <= 2.5 * atr_a[i],
        vol_ratio >= 0.7,
        0.0003 <= atr_pct <= 0.040,
    ]
    short_conds = [
        ema50_slope < -0.0003,                       # stricter: need clear downtrend
        cur["close"] < ema50[i],
        rsi_a[i] <= 45,                              # stricter: need proper bearish RSI
        cur["close"] < cur["open"],
        abs(cur["high"] - ema21[i]) <= 2.0 * atr_a[i] or abs(cur["high"] - vwap_a[i]) <= 2.0 * atr_a[i],
        vol_ratio >= 0.8,                            # stricter: need stronger volume confirmation
        ema21_slope < -0.0001,                       # EMA21 also declining
    ]

    long_met  = sum(1 for c in long_conds if c)
    short_met = sum(1 for c in short_conds if c)
    is_long_bias  = ema50_slope > 0
    is_short_bias = ema50_slope < -0.0002
    bias = "long" if long_met >= 3 else "short" if short_met >= 3 else "neutral"
    met_count = long_met if is_long_bias else short_met

    full_long  = long_met  >= 4 and is_long_bias
    full_short = short_met >= 5 and is_short_bias    # stricter: need 5/7 for shorts
    signal = None

    if full_long or full_short:
        d      = "long" if full_long else "short"
        entry  = cur["close"]
        sl_d   = 2.0 * atr_a[i] if d == "long" else 2.5 * atr_a[i]   # wider SL for shorts
        tp_d   = 3.5 * atr_a[i] if d == "long" else 4.0 * atr_a[i]   # higher R:R target
        sl     = entry - sl_d if d == "long" else entry + sl_d
        tp     = entry + tp_d if d == "long" else entry - tp_d
        mc     = long_met if d == "long" else short_met

        vol_s   = min(vol_ratio / 2, 1)
        rsi_s   = min(abs(rsi_a[i] - 50) / 15, 1)
        slope_s = min(abs(ema50_slope) / 0.002, 1)
        body_s  = min(body_ratio / 0.6, 1)
        cond_s  = mc / 7
        raw     = 0.25 * vol_s + 0.25 * rsi_s + 0.20 * slope_s + 0.15 * body_s + 0.15 * cond_s
        conf    = max(0.52, min(raw, 0.99))

        rr_ratio = round(tp_d / sl_d, 1)
        signal = {
            "direction": d,
            "entry": round(entry, 2),
            "sl":    round(sl, 2),
            "tp":    round(tp, 2),
            "confidence": round(conf, 3),
            "rr": f"1:{rr_ratio}",
            "reasoning": f"Momentum [{mc}/7] {d.upper()}: slope {ema50_slope * 100:.3f}%, RSI {rsi_a[i]:.1f}, vol {vol_ratio:.1f}×, SL ${sl_d:.0f}",
        }

    return {"bias": bias, "signal": signal, "met_count": met_count, "total": 7, "name": "Momentum 15m"}


def _run_obi(candles1m: list[dict], orderbook: Optional[dict]) -> dict:
    """1-Minute OBI Scalper (Order Book Imbalance + EMA9/21 + RSI)."""
    null = {"bias": "neutral", "signal": None, "met_count": 0, "total": 3, "name": "OBI Scalper"}
    if len(candles1m) < 30:
        return null

    closes = [c["close"] for c in candles1m]
    ema9   = _ema(closes, 9)
    ema21  = _ema(closes, 21)
    rsi_a  = _rsi(closes, 14)

    i = len(candles1m) - 1
    cur_e9, cur_e21, cur_rsi = ema9[i], ema21[i], rsi_a[i]
    if any(_nan(v) for v in [cur_e9, cur_e21, cur_rsi]):
        return null

    obi = bid_vol = ask_vol = 0.0
    if orderbook:
        bids = orderbook.get("bids", [])[:10]
        asks = orderbook.get("asks", [])[:10]
        for b in bids:
            bid_vol += b["amount"] if isinstance(b, dict) else float(b[1])
        for a in asks:
            ask_vol += a["amount"] if isinstance(a, dict) else float(a[1])
        total = bid_vol + ask_vol
        obi   = (bid_vol - ask_vol) / total if total > 0 else 0.0

    long_obi  = obi  >  0.12;  short_obi = obi  < -0.12
    long_ema  = cur_e9 > cur_e21; short_ema = cur_e9 < cur_e21
    long_rsi  = cur_rsi > 48;    short_rsi = cur_rsi < 52

    long_met  = sum([long_obi,  long_ema,  long_rsi])
    short_met = sum([short_obi, short_ema, short_rsi])
    bias = "long" if long_met == 3 else "short" if short_met == 3 else "neutral"
    met_count = long_met if cur_e9 >= cur_e21 else short_met

    signal = None
    if long_met == 3 or short_met == 3:
        d    = "long" if long_met == 3 else "short"
        e    = candles1m[i]["close"]
        sl   = e * (1 - 0.004) if d == "long" else e * (1 + 0.004)
        tp   = e * (1 + 0.008) if d == "long" else e * (1 - 0.008)
        obi_s = min(abs(obi) / 0.5, 1)
        rsi_s = min(abs(cur_rsi - 50) / 20, 1)
        ema_s = min(abs(cur_e9 - cur_e21) / max(cur_e21 * 0.002, 1e-9), 1)
        conf  = max(0.52, min(0.55 * obi_s + 0.25 * rsi_s + 0.20 * ema_s, 0.99))
        signal = {
            "direction": d, "entry": round(e, 2), "sl": round(sl, 2), "tp": round(tp, 2),
            "confidence": round(conf, 3), "rr": "1:2.0",
            "reasoning": f"OBI Scalper {d.upper()}: OBI {obi:+.3f}, EMA9/21 {cur_e9:.0f}/{cur_e21:.0f}, RSI {cur_rsi:.1f}",
        }
    return {"bias": bias, "signal": signal, "met_count": met_count, "total": 3, "name": "OBI Scalper"}


def _run_hft(candles1m: list[dict], orderbook: Optional[dict]) -> dict:
    """HFT VWAP Scalper (EMA9/21 on 5m derived + OBI + near VWAP)."""
    null = {"bias": "neutral", "signal": None, "met_count": 0, "total": 5, "name": "HFT Scalper"}
    if len(candles1m) < 30:
        return null

    # Derive 5m bars by grouping 1m bars in groups of 5
    def to_5m(bars):
        out = []
        for j in range(0, len(bars) - len(bars) % 5, 5):
            grp = bars[j:j + 5]
            out.append({
                "open": grp[0]["open"], "high": max(c["high"] for c in grp),
                "low": min(c["low"] for c in grp), "close": grp[-1]["close"],
                "volume": sum(c["volume"] for c in grp),
            })
        return out

    bars5m = to_5m(candles1m)
    if len(bars5m) < 5:
        return null

    closes5 = [c["close"] for c in bars5m]
    ema9_5  = _ema(closes5, min(9, len(closes5)))
    ema21_5 = _ema(closes5, min(21, len(closes5)))

    cur1m = candles1m[-1]
    vwap_a = _vwap(candles1m, 20)
    atr_a  = _atr(candles1m, 14)
    i = len(candles1m) - 1
    vwap1m = vwap_a[i]
    atr1m  = atr_a[i]

    j = len(bars5m) - 1
    e9 = ema9_5[j];  e21 = ema21_5[j]
    if _nan(e9) or _nan(e21) or _nan(vwap1m) or _nan(atr1m):
        return null

    obi = 0.0
    if orderbook:
        bids = orderbook.get("bids", [])[:10]
        asks = orderbook.get("asks", [])[:10]
        bv = sum(b["amount"] if isinstance(b, dict) else float(b[1]) for b in bids)
        av = sum(a["amount"] if isinstance(a, dict) else float(a[1]) for a in asks)
        total = bv + av
        obi = (bv - av) / total if total > 0 else 0.0

    long_bias  = e9 > e21
    short_bias = e9 < e21
    near_vwap  = abs(cur1m["close"] - vwap1m) <= 2.0 * atr1m

    long_conds = [
        long_bias,
        cur1m["close"] > vwap1m,
        near_vwap,
        obi > 0.05,
        abs(obi) > 0.03,
    ]
    short_conds = [
        short_bias,
        cur1m["close"] < vwap1m,
        near_vwap,
        obi < -0.05,
        abs(obi) > 0.03,
    ]

    long_met  = sum(1 for c in long_conds if c)
    short_met = sum(1 for c in short_conds if c)
    bias = "long" if long_met >= 3 else "short" if short_met >= 3 else "neutral"
    met_count = max(long_met, short_met)   # always show the strongest side in logs

    signal = None
    if long_met >= 3 or short_met >= 3:
        # On a tie use OBI to break: positive OBI → long, negative → short, else skip
        if long_met == short_met:
            d = "long" if obi > 0 else "short" if obi < 0 else None
            if d is None:
                return null  # genuine indecision — no trade, return standard dict shape
        else:
            d = "long" if long_met > short_met else "short"
        e    = cur1m["close"]
        sl_d = max(1.5 * atr1m, abs(e - vwap1m))
        sl   = e - sl_d if d == "long" else e + sl_d
        tp   = e + 2.5 * sl_d if d == "long" else e - 2.5 * sl_d
        conf = max(0.52, min(0.45 * min(abs(obi) / 0.3, 1) + 0.55, 0.92))
        signal = {
            "direction": d, "entry": round(e, 2), "sl": round(sl, 2), "tp": round(tp, 2),
            "confidence": round(conf, 3), "rr": "1:2.5",
            "reasoning": f"HFT {d.upper()}: EMA9/21 {e9:.0f}/{e21:.0f}, VWAP {vwap1m:.0f}, OBI {obi:+.3f}",
        }
    return {"bias": bias, "signal": signal, "met_count": met_count, "total": 5, "name": "HFT Scalper"}


def _run_orb(candles1m: list[dict]) -> dict:
    """ORB-30 — Opening Range Breakout on the first 30 minutes of each session."""
    null = {"bias": "neutral", "signal": None, "met_count": 0, "total": 4, "name": "ORB-30"}
    if len(candles1m) < 35:
        return null

    now = datetime.now(timezone.utc)
    # Session start: 16:30 UTC (NYSE open proxy) or 00:00 UTC (Asia)
    session_starts = [
        now.replace(hour=16, minute=30, second=0, microsecond=0),
        now.replace(hour=0,  minute=0,  second=0, microsecond=0),
        now.replace(hour=8,  minute=0,  second=0, microsecond=0),
    ]
    # Find the most recent session start that's in the past
    session_start = None
    for ss in sorted(session_starts, reverse=True):
        if ss <= now:
            session_start = ss
            break
    if not session_start:
        return null

    session_start_ts = session_start.timestamp() * 1000

    # Classify candles as "in ORB" (first 30m) or "post-ORB"
    orb_candles   = []
    post_candles  = []
    for c in candles1m:
        try:
            ts = datetime.fromisoformat(c["timestamp"].replace("Z", "+00:00")).timestamp() * 1000
        except Exception:
            continue
        elapsed = ts - session_start_ts
        if 0 <= elapsed < 30 * 60 * 1000:
            orb_candles.append(c)
        elif elapsed >= 30 * 60 * 1000:
            post_candles.append(c)

    if len(orb_candles) < 20:
        return null

    or_high = max(c["high"]  for c in orb_candles)
    or_low  = min(c["low"]   for c in orb_candles)
    or_range_pct = (or_high - or_low) / or_low * 100 if or_low > 0 else 0

    if or_range_pct < 0.05 or or_range_pct > 5.0 or not post_candles:
        return null

    cur = post_candles[-1]
    bars_since = len(post_candles)

    if bars_since > 210:
        return null  # Trade window expired

    # EMA20 on 15m (derived from 1m)
    all_closes = [c["close"] for c in candles1m]
    ema20_all = _ema(all_closes, 20)
    ema20 = ema20_all[-1]

    long_break  = cur["close"] > or_high * 1.001
    short_break = cur["close"] < or_low  * 0.999
    long_ema    = not _nan(ema20) and cur["close"] > ema20
    short_ema   = not _nan(ema20) and cur["close"] < ema20
    in_window   = bars_since <= 210
    vol_ok      = True  # simplified

    long_conds  = [long_break,  long_ema,  in_window, vol_ok]
    short_conds = [short_break, short_ema, in_window, vol_ok]

    long_met  = sum(1 for c in long_conds  if c)
    short_met = sum(1 for c in short_conds if c)
    bias = "long" if long_met >= 3 else "short" if short_met >= 3 else "neutral"

    signal = None
    if long_met >= 3 or short_met >= 3:
        d   = "long" if long_met >= 3 else "short"
        e   = cur["close"]
        ref = or_high if d == "long" else or_low
        sl  = or_low  if d == "long" else or_high
        tp  = e + 2.0 * abs(e - sl) if d == "long" else e - 2.0 * abs(sl - e)
        conf = max(0.55, min(0.65 + (bars_since / 210) * -0.10, 0.90))
        signal = {
            "direction": d, "entry": round(e, 2), "sl": round(sl, 2), "tp": round(tp, 2),
            "confidence": round(conf, 3), "rr": "1:2.0",
            "reasoning": f"ORB-30 {d.upper()}: {'above' if d=='long' else 'below'} OR {'high' if d=='long' else 'low'} ${ref:.0f}, bar {bars_since}/210",
        }

    return {"bias": bias, "signal": signal, "met_count": long_met if bias == "long" else short_met, "total": 4, "name": "ORB-30"}


# ── Persistent Agent ──────────────────────────────────────────────────────────

class PersistentAgent:
    """
    The 24/7 brain. Runs all 5 strategies every SCAN_INTERVAL seconds.
    Each strategy gets its own independent position slot.
    """

    SCAN_INTERVAL = 20  # seconds

    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self.config: dict      = {}
        self.positions: dict   = {}   # key → position dict | None
        self.trades: list      = []   # closed/confirmed trades
        self.stats:  dict      = {}
        self.log:    list      = []   # last 200 lines
        self.training_index: dict = {}  # per-strategy performance index (self-learning)
        self.scan_count        = 0
        self.last_scan: Optional[str] = None
        self._live: Optional[object] = None   # LiveExecutor instance when mode == "live"
        # Master Brain — central intelligence for trade decisions
        from app.agents.master_brain import MasterBrain
        self.brain = MasterBrain()
        # Sync daily loss limit from config into brain (single source of truth)
        self.brain.MAX_DAILY_LOSS = -abs(float(self.config.get("daily_loss_limit", 50.0)))
        # Ensure fusion strategy slot exists in brain trust scores
        self.brain.strategy_trust.setdefault("fusion", 1.0)
        # X/Twitter publisher — gracefully disabled when env vars are absent
        from app.agents.x_publisher import XPublisher
        self.x_publisher = XPublisher()
        from app.agents.paper_trader import PaperTrader
        self.paper_trader = PaperTrader()
        self._load_state()

    def _get_live_executor(self):
        """Return a LiveExecutor if BingX keys are configured, else None."""
        from app.core.config import settings
        if not settings.bingx_api_key or not settings.bingx_api_secret:
            return None
        if self._live is None:
            from app.agents.live_executor import LiveExecutor
            ddl = float(self.config.get("daily_loss_limit", 50.0))
            max_pos = float(self.config.get("max_position_usdc", 500.0))
            self._live = LiveExecutor(
                api_key=settings.bingx_api_key,
                api_secret=settings.bingx_api_secret,
                testnet=settings.bingx_testnet,
                daily_loss_limit=ddl,
                max_position_usdc=max_pos,
            )
            self._log(f"[LIVE] BingX executor initialised · daily_loss_limit=${ddl}")
            asyncio.ensure_future(self._live.fetch_balance(force=True))
        return self._live

    def _is_live_mode(self) -> bool:
        return self.config.get("mode") == "live"

    # ── Persistence ──────────────────────────────────────────────────────────

    def _state_dict(self) -> dict:
        return {
            "config":          self.config,
            "positions":       self.positions,
            "trades":          self.trades[-500:],
            "brain":           self.brain.to_dict(),
            "stats":           self.stats,
            "log":             self.log[-100:],
            "training_index":  self.training_index,
            "saved_at":        datetime.now(timezone.utc).isoformat(),
            # Shadow positions survive restarts so training data is continuous
            "shadow_positions": {k: v for k, v in self.positions.items()
                                 if k.startswith("shadow_") and v},
            # X posts survive restarts — last 50 posts persisted
            "x_recent_posts":   self.x_publisher._recent_posts[-50:],
            "x_last_times":     self.x_publisher._last,
            "x_intro_posted":   self.x_publisher._intro_posted,
            # Paper trader state survives restarts
            "paper_trader":    self.paper_trader.to_dict(),
        }

    def _apply_state(self, data: dict) -> None:
        loaded_cfg      = data.get("config", {})
        self.config     = {**DEFAULT_CONFIG.copy(), **loaded_cfg}   # merge so new keys always exist
        self.positions  = data.get("positions",  {})
        # Restore shadow positions into the main positions dict
        for k, v in data.get("shadow_positions", {}).items():
            if k not in self.positions:
                self.positions[k] = v
        self.trades     = data.get("trades",     [])[-500:]   # keep up to 500 trades in memory
        self.log        = data.get("log",        [])[-100:]
        self.training_index  = data.get("training_index", {})
        # Always rebuild stats + training index from trade history
        self._rebuild_stats()
        # Merge stored stats for fields not derivable from trades (best/worst may be correct)
        stored = data.get("stats", {})
        if stored and self.stats["total_trades"] > 0:
            self.stats["best_trade"]  = max(self.stats.get("best_trade",  stored.get("best_trade",  0)), stored.get("best_trade",  0))
            self.stats["worst_trade"] = min(self.stats.get("worst_trade", stored.get("worst_trade", 0)), stored.get("worst_trade", 0))
        # Restore MasterBrain state
        self.brain.from_dict(data.get("brain", {}))
        # Restore X publisher recent posts and cooldown times
        if data.get("x_recent_posts"):
            self.x_publisher._recent_posts = data["x_recent_posts"]
        if data.get("x_last_times"):
            self.x_publisher._last.update(data["x_last_times"])
        if data.get("x_intro_posted"):
            self.x_publisher._intro_posted = True
        # Restore paper trader
        if data.get("paper_trader"):
            self.paper_trader.from_dict(data["paper_trader"])
        # Re-sync brain limits from the loaded config — single source of truth
        self.brain.MAX_DAILY_LOSS = -abs(float(self.config.get("daily_loss_limit", 50.0)))
        # If Brain's stats were wiped (restart/deploy), reconstruct from trade history
        self._reconstruct_brain_stats()

    def _reconstruct_brain_stats(self) -> None:
        """Rebuild Brain strategy_stats from trade history if they were wiped."""
        try:
            brain_total = sum(s.get("trades", 0) for s in self.brain.strategy_stats.values())
            agent_total = len(self.trades)
            if brain_total >= agent_total or agent_total == 0:
                return
            logger.info(f"[Agent] Reconstructing Brain stats from {agent_total} trades "
                        f"(Brain had {brain_total})")
            for t in reversed(self.trades):
                sk = t.get("strategy_key", "")
                if sk.startswith("shadow_"):
                    sk = sk.replace("shadow_", "")
                if not sk:
                    continue
                pnl = float(t.get("pnl_usd", 0) or 0)
                won = pnl > 0 or t.get("exit_reason") == "tp"
                was_live = bool(t.get("is_live", False) or t.get("mode") == "live")
                self.brain.record_trade_result(sk, pnl, won, was_live=was_live)
            summary = ", ".join(f"{k}:{v.get('trades',0)}t" for k,v in self.brain.strategy_stats.items())
            logger.info(f"[Agent] Brain reconstruction complete — {summary}")
        except Exception as e:
            logger.warning(f"[Agent] Brain reconstruction failed: {type(e).__name__}: {e}")

    # ── File fallback (local dev / fast cache) ────────────────────────────────

    def _load_state(self) -> None:
        """Load from JSON file — fallback when DB is unavailable."""
        try:
            if STATE_FILE.exists():
                data = json.loads(STATE_FILE.read_text())
                self._apply_state(data)
                logger.info(f"Agent state loaded from file: {len(self.trades)} trades")
            else:
                self._apply_state({})
        except Exception as e:
            logger.warning(f"File state load failed: {e}")
            self._apply_state({})

    def _save_state(self) -> None:
        """Save to JSON file — fast sync cache."""
        try:
            STATE_FILE.write_text(json.dumps(self._state_dict(), indent=2))
        except Exception as e:
            logger.debug(f"File state save failed: {e}")

    # ── Database persistence (survives Railway restarts / redeploys) ──────────

    _DB_DDL = """
        CREATE TABLE IF NOT EXISTS agent_state (
            id         INTEGER PRIMARY KEY,
            state      TEXT    NOT NULL,
            updated_at TEXT
        )
    """

    async def _load_state_db(self) -> bool:
        """Load state from PostgreSQL/SQLite. Returns True if state was found."""
        try:
            from sqlalchemy import text as sa_text
            from app.core.database import engine
            async with engine.begin() as conn:
                await conn.execute(sa_text(self._DB_DDL))
            async with engine.connect() as conn:
                result = await conn.execute(sa_text(
                    "SELECT state FROM agent_state WHERE id = 1"
                ))
                row = result.fetchone()
                if row and row[0]:
                    data = json.loads(row[0])
                    self._apply_state(data)
                    n_open = sum(1 for p in self.positions.values() if p)
                    logger.info(
                        f"[Agent] DB state loaded — {len(self.trades)} trades, "
                        f"{n_open} open position(s)"
                    )
                    return True
        except Exception as e:
            logger.warning(f"[Agent] DB state load FAILED: {type(e).__name__}: {e}")
        return False

    async def _save_state_db(self) -> None:
        """Persist state to PostgreSQL/SQLite — survives server restarts."""
        try:
            from sqlalchemy import text as sa_text
            from app.core.database import engine
            from app.core.config import settings
            payload = json.dumps(self._state_dict())
            ts      = datetime.now(timezone.utc).isoformat()
            async with engine.begin() as conn:
                await conn.execute(sa_text(self._DB_DDL))
                if settings.database_url.startswith("sqlite"):
                    await conn.execute(sa_text(
                        "INSERT OR REPLACE INTO agent_state (id, state, updated_at) "
                        "VALUES (1, :s, :t)"
                    ), {"s": payload, "t": ts})
                else:
                    await conn.execute(sa_text(
                        "INSERT INTO agent_state (id, state, updated_at) VALUES (1, :s, :t) "
                        "ON CONFLICT (id) DO UPDATE SET state = :s, updated_at = :t"
                    ), {"s": payload, "t": ts})
        except Exception as e:
            logger.warning(f"[Agent] DB state save FAILED: {type(e).__name__}: {e}")

    def _schedule_db_save(self) -> None:
        """Fire-and-forget DB save from a sync context."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._save_state_db())
        except RuntimeError:
            pass  # not in async context — skip; next scheduled save will catch it

    def _empty_stats(self) -> dict:
        return {"total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
                "total_pnl": 0.0, "best_trade": 0.0, "worst_trade": 0.0}

    def _rebuild_stats(self) -> None:
        """Recompute stats from trade history — ensures P&L is always correct after load."""
        s = self._empty_stats()
        for t in self.trades:
            pnl = t.get("pnl_usd", 0) or 0
            reason = t.get("exit_reason", "")
            s["total_trades"] += 1
            if reason == "tp" or pnl > 0:
                s["wins"] += 1
            else:
                s["losses"] += 1   # sl hits, manual closes, and breakeven all counted as losses
            s["total_pnl"]   = round(s["total_pnl"] + pnl, 2)
            s["best_trade"]  = max(s["best_trade"],  pnl)
            s["worst_trade"] = min(s["worst_trade"], pnl)
        s["win_rate"] = round(s["wins"] / s["total_trades"] * 100, 1) if s["total_trades"] > 0 else 0.0
        self.stats = s
        self._rebuild_training_index()

    # ── Training data index — self-learning from trade history ────────────────

    def _rebuild_training_index(self) -> None:
        """
        Build per-strategy performance metrics from closed trade history.
        Used during scans to dynamically adjust confidence requirements,
        apply loss cooldowns, and weight strategies by recent performance.
        """
        KEY_TO_NAME = {"momentum": "momentum", "hft": "hft", "orb": "orb", "obi": "obi"}
        by_strat: dict[str, list] = {"momentum": [], "hft": [], "orb": [], "obi": []}

        for t in self.trades:
            sk = t.get("strategy_key", "")
            if sk in by_strat:
                by_strat[sk].append(t)

        idx: dict[str, dict] = {}
        now = datetime.now(timezone.utc)

        for key, trades in by_strat.items():
            trades_sorted = sorted(trades, key=lambda t: t.get("closed_at", ""), reverse=True)
            wins   = [t for t in trades_sorted if t.get("exit_reason") == "tp" or (t.get("pnl_usd", 0) or 0) > 0]
            losses = [t for t in trades_sorted if t.get("exit_reason") == "sl" or (t.get("pnl_usd", 0) or 0) < 0]
            total  = len(wins) + len(losses)
            win_rate = wins.__len__() / total if total >= 3 else 0.50

            # Streak from most recent
            streak = 0
            for t in trades_sorted:
                is_win = t.get("exit_reason") == "tp" or (t.get("pnl_usd", 0) or 0) > 0
                if streak == 0:
                    streak = 1 if is_win else -1
                elif streak > 0 and is_win:
                    streak += 1
                elif streak < 0 and not is_win:
                    streak -= 1
                else:
                    break

            # Time since last SL (use large int sentinel instead of inf — JSON safe)
            NO_SL = 999_999_999
            last_sl = next((t for t in trades_sorted if t.get("exit_reason") == "sl"), None)
            ms_since_sl = NO_SL
            if last_sl and last_sl.get("closed_at"):
                try:
                    sl_time = datetime.fromisoformat(last_sl["closed_at"].replace("Z", "+00:00"))
                    ms_since_sl = int((now - sl_time).total_seconds() * 1000)
                except Exception:
                    pass

            total_pnl = sum(t.get("pnl_usd", 0) or 0 for t in trades_sorted)

            # Composite trust score (mirrors frontend logic)
            wr_mult     = 0.5 + win_rate
            cooldown    = 0.70 if ms_since_sl < 45 * 60 * 1000 else 1.0   # 45-min cooldown after SL
            streak_mult = (1.25 if streak >= 3 else 1.10 if streak >= 2
                           else 0.70 if streak <= -3 else 0.82 if streak <= -2 else 1.0)
            trust = max(0.30, min(2.0, wr_mult * cooldown * streak_mult))

            # Dynamic confidence adjustment: cold strategies need higher confidence
            conf_adj = 0.0
            if streak <= -3:
                conf_adj = 0.10       # require 10% more confidence when on cold streak
            elif streak <= -2:
                conf_adj = 0.05
            elif streak >= 3:
                conf_adj = -0.05      # reward hot streak with lower threshold

            label = "HOT" if streak >= 2 else "COLD" if streak <= -2 else "NORMAL"

            idx[key] = {
                "win_rate":      round(win_rate, 3),
                "total_trades":  total,
                "wins":          len(wins),
                "losses":        len(losses),
                "streak":        streak,
                "total_pnl":     round(total_pnl, 2),
                "ms_since_sl":   ms_since_sl,
                "trust_score":   round(trust, 3),
                "conf_adj":      round(conf_adj, 3),
                "label":         label,
                "last_updated":  now.isoformat(),
            }

        self.training_index = idx
        total_indexed = sum(v["total_trades"] for v in idx.values())
        if total_indexed > 0 and self.scan_count % 20 == 0:
            summary = " · ".join(f"{k}:{v['label']}({v['trust_score']:.2f})" for k, v in idx.items() if v["total_trades"] > 0)
            self._log(f"[TRAINING] Indexed {total_indexed} trades → {summary}")

    def _log(self, msg: str) -> None:
        ts   = datetime.now(timezone.utc).strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self.log.insert(0, line)
        if len(self.log) > 200:
            self.log = self.log[:200]
        logger.info(f"[Agent] {msg}")

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._running:
            # Already running — ensure trading flags are active
            self.config["enabled"]      = True
            self.config["auto_execute"] = True
            self._schedule_db_save()
            return
        # Load from DB first (persistent) — overrides the file loaded in __init__
        loaded_from_db = await self._load_state_db()
        if not loaded_from_db:
            self._load_state()  # file fallback (local dev)
        # Load paper trader's own persistent DB (separate from main agent state)
        await self.paper_trader.load_from_db()
        # On first run (no saved state), enable trading by default.
        # If state was loaded from DB, preserve whatever the user had set.
        if not loaded_from_db:
            self.config.setdefault("enabled", True)
            self.config.setdefault("auto_execute", True)
        self._running = True
        self._task = asyncio.create_task(self._loop())
        asyncio.create_task(self._x_scheduler())
        asyncio.create_task(self._market_context_loop())
        asyncio.create_task(self._grok_warmup())
        # Fire intro post once on first startup
        self.x_publisher.post_intro()
        mode = self.config.get("mode", "paper")
        if mode == "live":
            self._log("Agent STARTED — DUAL MODE: paper shadow training + live BingX (qualified strategies only)")
        else:
            self._log("Agent STARTED — PAPER MODE: all 5 strategies training every 20s (server-side, 24/7)")
        logger.info(f"PersistentAgent started in {mode} mode")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        if self._live:
            await self._live.close()
            self._live = None
        self._log("Agent STOPPED")
        self._save_state()
        await self._save_state_db()

    # ── X / Twitter scheduled posts ───────────────────────────────────────────

    async def _x_scheduler(self) -> None:
        """
        Single-post-at-a-time X scheduler.

        Rules:
          • Trade signals / results → fired instantly by the trading engine (not here).
          • Trade signals/results → posted immediately (priority 1-2).
          • Daily / Weekly       → fired once at the right UTC hour.
          • Other content        → one post chosen at random from cooldown-ready types,
                                   only during peak hours, max 5 posts/day.
        """
        if not self.x_publisher.enabled:
            return

        # Wait for market data to initialise before first post
        await asyncio.sleep(30)

        # Single intro post on startup — then wait a random 2–5 min before anything else
        self.x_publisher.post_intro()
        await asyncio.sleep(random.uniform(120, 300))

        last_day_posted  = -1
        last_week_posted = -1

        # Content candidates (key, async_fn or sync_fn) — excluding news (handled separately)
        def _get_btc_price() -> float:
            try:
                from app.agents.live_market_stream import LIVE_PRICES
                return LIVE_PRICES.get("BTC/USDT", {}).get("last", 0.0)
            except Exception:
                return 0.0

        def _get_regime() -> tuple[str, str]:
            r  = self.brain.current_regime if hasattr(self.brain, "current_regime") else "unknown"
            rh = getattr(self.brain, "_regime_history", [])
            if len(rh) >= 3 and len(set(rh[-3:])) == 1:
                rs = "stable"
            elif len(rh) >= 3:
                rs = "shifting"
            else:
                rs = "unknown"
            return r, rs

        MAX_SILENCE_SEC   = 3600   # 60 min — longer silence OK (max 5 posts/day)

        while self._running:
            try:
                now     = datetime.now(timezone.utc)
                regime, regime_stability = _get_regime()
                btc_price = _get_btc_price()
                open_pos  = [p for p in self.positions.values() if p]

                # Update BTC price in memory for context
                if btc_price > 0:
                    self.x_publisher.memory.set_btc_price(btc_price)

                # ── Daily summary at midnight UTC ─────────────────────────────
                if now.hour == 0 and now.day != last_day_posted:
                    live_pnl = sum(t.get("pnl_usd", 0) for t in self.trades if t.get("is_live"))
                    self.x_publisher.post_daily(
                        stats=self.stats,
                        strategy_stats=self.brain.strategy_stats,
                        regime=regime,
                        live_pnl=live_pnl,
                    )
                    last_day_posted = now.day
                    await asyncio.sleep(random.uniform(60, 300))
                    continue

                # ── Weekly recap on Sunday 20:00 UTC ─────────────────────────
                iso_week = now.isocalendar()[1]
                if now.weekday() == 6 and now.hour == 20 and iso_week != last_week_posted:
                    acc_balance = 0.0
                    executor = self._get_live_executor()
                    if executor:
                        try:
                            bal = await executor.fetch_balance()
                            acc_balance = bal.get("total", 0)
                        except Exception:
                            pass
                    self.x_publisher.post_weekly(
                        stats=self.stats,
                        strategy_stats=self.brain.strategy_stats,
                        account_balance=acc_balance,
                    )
                    last_week_posted = iso_week
                    await asyncio.sleep(random.uniform(60, 300))
                    continue

                # ── Background Grok trend refresh (non-blocking) ───────────
                await self.x_publisher.refresh_grok_trends()

                # ── Regular content: one post at a time, randomly chosen ───────
                # Only high-value content types; daily budget enforced by XPublisher
                grok_enabled = (
                    self.x_publisher.grok is not None
                    and getattr(self.x_publisher.grok, "enabled", False)
                )
                content_map: dict[str, any] = {
                    "contrarian":        self.x_publisher.post_contrarian,
                    "psychology_thread": self.x_publisher.post_psychology_thread,
                    "poll":              self.x_publisher.post_poll,
                    "trade_breakdown":   self.x_publisher.post_trade_breakdown,
                    **({"trending_hook":    self.x_publisher.post_trending_hook,
                        "viral_commentary": self.x_publisher.post_viral_commentary,
                        "bold_prediction":  lambda: self.x_publisher.post_bold_prediction(
                            macro_trend=self.brain.macro_trend,
                            fear_greed=self.brain.fear_greed_score,
                        ),
                        "reply_hook":       self.x_publisher.post_reply_hook,
                        } if grok_enabled else {}),
                }
                available = self.x_publisher.available_post_types()
                candidates = [k for k in available if k in content_map]

                if candidates:
                    random.shuffle(candidates)
                    chosen_key = candidates[0]
                    fn = content_map[chosen_key]
                    result = fn()
                    if asyncio.iscoroutine(result):
                        await result
                    logger.debug(f"[XScheduler] Posted: {chosen_key}")

                # Sleep 20-60 min between checks (targets 3-5 posts/day)
                sleep_sec = random.uniform(1200, 3600)
                await asyncio.sleep(sleep_sec)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[XScheduler] Error: {e}")
                await asyncio.sleep(60)

    async def _market_context_loop(self) -> None:
        """
        Background loop that refreshes market-wide context for MasterBrain:
        - BTC perpetual funding rate (every 5 minutes)
        - Fear & Greed Index (every 30 minutes)
        - HTF Pivot Points (every 60 minutes)
        All feed into evaluate_signal biasing logic.
        """
        import httpx
        _fear_greed_interval = 30 * 60   # 30 minutes
        _funding_interval    = 5  * 60   # 5 minutes
        _pivot_interval      = 60 * 60   # 60 minutes
        _last_fg = 0.0
        _last_fr = 0.0
        _last_piv = 0.0

        while self._running:
            now = time.time()

            # ── Funding rate (BingX perp) ─────────────────────────────────
            if now - _last_fr >= _funding_interval:
                try:
                    async with httpx.AsyncClient(timeout=6.0) as c:
                        r = await c.get(
                            "https://open-api.bingx.com/openApi/swap/v2/quote/premiumIndex",
                            params={"symbol": "BTC-USDT"},
                        )
                        if r.status_code == 200:
                            j = r.json()
                            fr = float(j.get("data", {}).get("lastFundingRate", 0) or 0)
                            self.brain.update_market_context(funding_rate=fr)
                            _last_fr = now
                            logger.debug(f"[ContextLoop] Funding rate updated: {fr:+.5f}")
                except Exception as e:
                    logger.debug(f"[ContextLoop] Funding rate fetch failed: {e}")

            # ── Fear & Greed Index (alternative.me) ───────────────────────
            if now - _last_fg >= _fear_greed_interval:
                try:
                    async with httpx.AsyncClient(timeout=6.0) as c:
                        r = await c.get("https://api.alternative.me/fng/?limit=1")
                        if r.status_code == 200:
                            j = r.json()
                            fg = int(j["data"][0]["value"])
                            self.brain.update_market_context(fear_greed_score=fg)
                            _last_fg = now
                            logger.debug(f"[ContextLoop] Fear & Greed updated: {fg}")
                except Exception as e:
                    logger.debug(f"[ContextLoop] Fear & Greed fetch failed: {e}")

            # ── HTF Pivot Points (1h + 4h candles for weekly/monthly pivots) ─
            if now - _last_piv >= _pivot_interval:
                try:
                    from app.agents.live_market_stream import LIVE_CANDLES
                    c1h = LIVE_CANDLES.get("BTC/USDT:1h", [])
                    c4h = LIVE_CANDLES.get("BTC/USDT:4h", [])
                    if len(c1h) >= 100 or len(c4h) >= 10:
                        self.brain.compute_htf_pivots(c1h, c4h)
                        _last_piv = now
                        wp  = self.brain._weekly_pivots.get("PP", 0)
                        mp  = self.brain._monthly_pivots.get("PP", 0)
                        logger.debug(f"[ContextLoop] HTF Pivots — W.PP=${wp:,.0f} M.PP=${mp:,.0f}")
                except Exception as e:
                    logger.debug(f"[ContextLoop] HTF pivot compute failed: {e}")

            await asyncio.sleep(60)  # check every minute, act based on intervals above

    async def _grok_warmup(self) -> None:
        """
        Fetch initial Grok trend intelligence on startup so the first
        trending_hook post is immediately data-rich.
        """
        try:
            if self.x_publisher.grok and getattr(self.x_publisher.grok, "enabled", False):
                await self.x_publisher.grok.fetch_btc_trends()
                await self.x_publisher.grok.fetch_viral_formats()
                logger.info("[Agent] Grok intelligence warmed up on startup")
        except Exception as e:
            logger.debug(f"[Agent] Grok warmup failed: {e}")

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._scan()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Agent scan error: {e}", exc_info=True)
            await asyncio.sleep(self.SCAN_INTERVAL)

    # ── Scan ──────────────────────────────────────────────────────────────────

    # ── Direct price fetch (bypasses market stream) ───────────────────────────
    async def _direct_price(self) -> float:
        """Fetch BTC price directly — multi-source, no market stream dependency."""
        import httpx
        sources = [
            ("CoinGecko",    "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
             lambda d: float(d["bitcoin"]["usd"])),
            ("Kraken",       "https://api.kraken.com/0/public/Ticker?pair=XBTUSD",
             lambda d: float(list(d["result"].values())[0]["c"][0])),
            ("Bybit",        "https://api.bybit.com/v5/market/tickers?category=spot&symbol=BTCUSDT",
             lambda d: float(d["result"]["list"][0]["lastPrice"])),
            ("blockchain",   "https://blockchain.info/ticker",
             lambda d: float(d["USD"]["last"])),
            ("BinanceUS",    "https://api.binance.us/api/v3/ticker/price?symbol=BTCUSDT",
             lambda d: float(d["price"])),
            ("Binance",      "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT",
             lambda d: float(d["price"])),
        ]
        async with httpx.AsyncClient(timeout=7.0, follow_redirects=True) as client:
            for name, url, parse in sources:
                try:
                    r = await client.get(url)
                    if r.status_code == 200:
                        price = parse(r.json())
                        if price > 0:
                            logger.info(f"[DirectPrice] {name} → ${price:,.2f}")
                            return price
                except Exception as e:
                    logger.warning(f"[DirectPrice] {name} failed: {type(e).__name__}: {e}")
        return 0.0

    async def _scan(self) -> None:
        from app.agents.live_market_stream import LIVE_CANDLES, LIVE_PRICES, LIVE_ORDERBOOK

        self.scan_count += 1
        self.last_scan = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        cfg = self.config

        candles1m  = LIVE_CANDLES.get("BTC/USDT:1m",  [])
        candles15m = LIVE_CANDLES.get("BTC/USDT:15m", [])
        candles1h  = LIVE_CANDLES.get("BTC/USDT:1h",  [])
        candles4h  = LIVE_CANDLES.get("BTC/USDT:4h",  [])
        orderbook  = LIVE_ORDERBOOK.get("BTC/USDT")
        price_data = LIVE_PRICES.get("BTC/USDT", {})
        live_price = price_data.get("last", 0.0)

        # If market stream hasn't populated price yet, fall back through candles then direct fetch
        if live_price <= 0 and candles1m:
            live_price = candles1m[-1]["close"]
            if self.scan_count % 5 == 1:
                self._log(f"⚠ Ticker missing — using last 1m candle close ${live_price:,.0f}")

        if live_price <= 0:
            # Last resort: hit a public API directly
            if self.scan_count % 3 == 1:   # throttle to every 3rd scan (~30s)
                self._log("⚠ Stream empty — fetching price directly…")
                live_price = await self._direct_price()
                if live_price > 0:
                    # Seed the market stream cache so future scans are fast
                    LIVE_PRICES["BTC/USDT"] = {"last": live_price, "bid": live_price, "ask": live_price}
                    self._log(f"✓ Direct fetch OK → ${live_price:,.0f}")

        if live_price <= 0:
            self._log("⚠ No price data from any source — skipping scan")
            return

        # Diagnostic log every 10 scans
        if self.scan_count % 10 == 1:
            ob_bids = len(orderbook.get("bids", [])) if orderbook else 0
            self._log(
                f"DATA: 1m={len(candles1m)} · 15m={len(candles15m)} · "
                f"1h={len(candles1h)} · 4h={len(candles4h)} bars · "
                f"OB={ob_bids} levels · price=${live_price:,.0f}"
            )

        # Update open position P&L
        self._update_positions(live_price)

        # Paper trader price update happens above the enabled gate (always-on)

        # Keep X publisher context fresh every scan
        regime_now = self.brain.current_regime if hasattr(self.brain, "current_regime") else "unknown"
        regime_conf = getattr(self.brain, "_regime_confidence", 0.5)
        consec_losses = getattr(self.brain, "consecutive_losses", 0)
        daily_pnl_now = getattr(self.brain, "daily_pnl", 0.0)
        open_pos_count = sum(1 for v in self.positions.values() if v)
        wr_all = self.stats.get("win_rate", 0) / 100.0
        last_trade_ts = max(
            (t.get("ts", 0) or 0 for t in (self.x_publisher._recent_posts or [])), default=0
        )
        self.x_publisher.update_context(
            price=live_price,
            regime=regime_now,
            regime_confidence=regime_conf,
            daily_pnl=daily_pnl_now,
            consecutive_losses=consec_losses,
            last_trade_ago_sec=time.time() - last_trade_ts if last_trade_ts else 999999,
            win_rate=wr_all,
            open_positions=open_pos_count,
            scan_count=self.scan_count,
        )

        # Sync with BingX exchange — detect positions closed by SL/TP
        if self._is_live_mode() and self._live:
            await self._sync_exchange_positions(live_price)
            # Software SL/TP safety-net: fires if exchange orders somehow didn't close the position
            async def _async_close(key: str, price: float, reason: str) -> None:
                self._close_position(key, price, reason)
            await self._live.check_sl_tp(live_price, _async_close)

        # ── Paper Trader price update + SL/TP always runs — never blocked ──────
        # Paper trader and shadow training run 24/7 regardless of circuit breaker,
        # kill switch, or enabled flag. Their only job is to feed MasterBrain data.
        self.paper_trader.update_prices(live_price)
        paper_closed = self.paper_trader.check_sl_tp(live_price)
        for pt in paper_closed:
            strat_key = pt.get("strat_key_ref", pt.get("strategy_key", ""))
            pnl = pt.get("pnl_usd", 0)
            won = pnl > 0
            pt_dur = 0.0
            pt_opened = pt.get("opened_at") or pt.get("timestamp", "")
            if pt_opened:
                try:
                    pt_dt = datetime.fromisoformat(pt_opened.replace("Z", "+00:00"))
                    pt_dur = (datetime.now(timezone.utc) - pt_dt).total_seconds() / 60
                except Exception:
                    pass
            self.brain.record_trade_result(strat_key, pnl, won, was_live=False, duration_min=pt_dur)
            self._log(f"📊 [PAPER_TRADER] {pt['strategy_name']} {pt['exit_reason'].upper()} "
                      f"· P&L {'+' if pnl>=0 else ''}${pnl:.2f} · bal ${self.paper_trader.balance:.2f}")

        if not cfg.get("enabled", True):
            return

        # Candle freshness check — don't trade on stale data
        def _candle_age_sec(candles: list) -> float:
            if not candles:
                return 99999.0
            try:
                ts = datetime.fromisoformat(candles[-1]["timestamp"].replace("Z", "+00:00"))
                return (datetime.now(timezone.utc) - ts).total_seconds()
            except Exception:
                return 99999.0

        age_1m  = _candle_age_sec(candles1m)
        age_15m = _candle_age_sec(candles15m)

        use_momentum  = age_15m < 20 * 60  # allow up to 20 min (candle is 15m long)
        use_1m_strats = age_1m  < 3 * 60   # 1m candles must be within 3 minutes

        if age_15m >= 20 * 60 and self.scan_count % 5 == 0:
            self._log(f"⚠ 15m candles stale ({age_15m/60:.0f}m old) — Momentum 15m paused")
        if age_1m >= 3 * 60 and self.scan_count % 5 == 0:
            self._log(f"⚠ 1m candles stale ({age_1m/60:.1f}m old) — 1m strategies paused")

        def _safe_run(key: str, fn, *args) -> dict:
            """Run a strategy function and return null result on any error."""
            null_map = {"momentum": {"bias": "neutral", "signal": None, "met_count": 0, "total": 7, "name": "Momentum 15m"},
                        "hft":      {"bias": "neutral", "signal": None, "met_count": 0, "total": 5, "name": "HFT Scalper"},
                        "orb":      {"bias": "neutral", "signal": None, "met_count": 0, "total": 4, "name": "ORB-30"},
                        "obi":      {"bias": "neutral", "signal": None, "met_count": 0, "total": 3, "name": "OBI Scalper"}}
            try:
                return fn(*args)
            except Exception as exc:
                logger.error(f"[Agent] Strategy '{key}' crashed: {type(exc).__name__}: {exc}", exc_info=True)
                if self.scan_count % 5 == 0:
                    self._log(f"⚠ [{key}] strategy error: {type(exc).__name__}: {exc}")
                return null_map.get(key, {"bias": "neutral", "signal": None, "met_count": 0, "total": 0, "name": key})

        strategies = [
            ("momentum", _safe_run("momentum", _run_momentum, candles15m) if use_momentum  else {"bias": "neutral", "signal": None, "met_count": 0, "total": 7, "name": "Momentum 15m"}),
            ("hft",      _safe_run("hft",      _run_hft,      candles1m, orderbook) if use_1m_strats else {"bias": "neutral", "signal": None, "met_count": 0, "total": 5, "name": "HFT Scalper"}),
            ("orb",      _safe_run("orb",      _run_orb,      candles1m) if use_1m_strats else {"bias": "neutral", "signal": None, "met_count": 0, "total": 4, "name": "ORB-30"}),
            ("obi",      _safe_run("obi",      _run_obi,      candles1m, orderbook) if use_1m_strats else {"bias": "neutral", "signal": None, "met_count": 0, "total": 3, "name": "OBI Scalper"}),
        ]

        # Rebuild training index periodically (every 10 scans or after trades)
        if self.scan_count % 10 == 0:
            self._rebuild_training_index()

        # ── MasterBrain: detect regimes + macro trend ────────────────────
        self.brain.detect_regime(candles15m, candles1m)
        if candles1h or candles4h:
            self.brain.detect_macro_trend(candles1h, candles4h)

        # ── MasterBrain: Fibonacci + advanced intelligence (every 15 scans ≈ 5 min) ─
        if self.scan_count % 15 == 0:
            if len(candles15m) >= 20:
                self.brain.compute_fib_levels(candles15m, lookback=100)
                self.brain.detect_liquidity_sweep(candles15m, lookback=50)
                self.brain.detect_market_structure(candles15m, candles1h)
                self.brain.detect_volume_anomaly(candles15m)
                self.brain.detect_rsi_divergence(candles15m)

        if self.scan_count % 10 == 1:
            fib_info = ""
            if self.brain._fib_levels:
                fib_618 = self.brain._fib_levels.get("61.8", 0)
                fib_info = f" · Fib61.8%=${fib_618:,.0f}"
            mss_info = ""
            if self.brain._mss:
                mss_info = f" · {self.brain._mss.get('type','')} {self.brain._mss.get('direction','')[:3].upper()}"
            sweep_info = f" · sweep={self.brain._liq_sweep.get('direction','none')[:3]}" if self.brain._liq_sweep else ""
            div_info   = f" · RSIDiv={self.brain._rsi_divergence}" if self.brain._rsi_divergence else ""
            stability  = self.brain._regime_stability()
            self._log(
                f"🧠 Regime: {self.brain.current_regime.upper()} "
                f"({self.brain.regime_confidence:.0%} conf · {stability}) · "
                f"Macro: {self.brain.macro_trend.upper()} ({self.brain.macro_confidence:.0%}) · "
                f"ATR {self.brain.current_atr_pct:.3%} · "
                f"F&G {self.brain.fear_greed_score} · "
                f"Funding {self.brain.funding_rate:+.4%}"
                f"{fib_info}{mss_info}{sweep_info}{div_info}"
            )

            # Log live readiness summary
            if self._is_live_mode():
                ready_strats = []
                training_strats = []
                for sk in list(self.brain.strategy_trust.keys()):
                    r = self.brain.is_strategy_live_ready(sk)
                    if r["ready"]:
                        ready_strats.append(f"{sk}({r['win_rate']:.0%})")
                    else:
                        training_strats.append(f"{sk}({r['trades']}/{self.brain.MIN_PAPER_TRADES_FOR_LIVE})")
                if ready_strats:
                    self._log(f"🟢 Live-ready: {', '.join(ready_strats)}")
                if training_strats:
                    self._log(f"📋 Training: {', '.join(training_strats)}")

        any_signal = False
        for key, result in strategies:
            s_cfg = self._strategy_cfg(key)
            if not s_cfg.get("enabled", True):
                continue

            sig = result.get("signal")
            met = result.get("met_count", 0)
            total = result.get("total", 7)
            bias = result.get("bias", "neutral")
            name = result.get("name", key)
            open_pos = self.positions.get(key)

            # Training-adjusted thresholds
            ti = self.training_index.get(key, {})
            conf_adj = ti.get("conf_adj", 0)
            trust    = ti.get("trust_score", 1.0)
            label    = ti.get("label", "NORMAL")
            cooldown_ms = ti.get("ms_since_sl", 999_999_999)
            in_cooldown = cooldown_ms < 30 * 60 * 1000   # 30-min cooldown after SL

            if sig:
                conf = sig.get("confidence", 0)
                adj_min_conf = min(0.95, s_cfg.get("min_confidence", 0.50) + conf_adj)
                cond_ok = met >= s_cfg.get("min_conditions", 2)
                conf_ok = conf >= adj_min_conf
                block   = ("POS OPEN" if open_pos
                           else "COOLDOWN" if in_cooldown
                           else "conf_fail" if not conf_ok
                           else "cond_fail" if not cond_ok else "")

                ti_str = f" · trust {trust:.2f} {label}" if ti.get("total_trades", 0) > 0 else ""
                cd_str = f" · ⏸ {int(cooldown_ms/60000)}m since SL" if in_cooldown else ""
                self._log(f"[{name}] SIGNAL {sig['direction'].upper()} · {met}/{total} conds · "
                          f"conf {conf*100:.0f}% (min {adj_min_conf*100:.0f}%){ti_str}{cd_str} · {block or 'EXECUTING'}")

                if cond_ok and conf_ok and not open_pos and not in_cooldown and s_cfg.get("auto_execute", True):
                    # In live mode: individual strategies NEVER execute on BingX.
                    # They run as paper/shadow to feed Brain learning.
                    # Only the Fusion signal (below) places real BingX trades.
                    self._brain_gate_execute(key, name, sig, s_cfg, live_price,
                                             live_allowed=False, orderbook=orderbook)
                    any_signal = True
            else:
                if self.scan_count % 5 == 0:
                    ti_str = f" · trust {trust:.2f} {label}" if ti.get("total_trades", 0) > 0 else ""
                    self._log(f"[{name}] {met}/{total} conds · no signal · {bias}{ti_str}")

        # ── Always-on shadow paper training ──────────────────────────────────
        # Runs regardless of strategy enabled flag — shadow trains the brain
        # even if the strategy is disabled for live/paper execution.
        for key, result in strategies:
            s_cfg = self._strategy_cfg(key)
            sig = result.get("signal")
            if not sig:
                continue
            shadow_key = f"shadow_{key}"
            if self.positions.get(shadow_key):
                continue  # shadow already open for this strategy
            # Lower bar for shadow — we want maximum training data
            min_conf = max(0.40, s_cfg.get("min_confidence", 0.50) - 0.08)
            if sig.get("confidence", 0) >= min_conf:
                self._open_shadow(shadow_key, result.get("name", key), sig, s_cfg, live_price)

        # ── Paper Trader — takes every signal with realistic sizing ────────
        # Also always-on — paper trader feeds MasterBrain regardless of live gates.
        for key, result in strategies:
            s_cfg = self._strategy_cfg(key)
            sig = result.get("signal")
            if not sig:
                continue
            min_conf = max(0.35, s_cfg.get("min_confidence", 0.50) - 0.12)
            if sig.get("confidence", 0) >= min_conf:
                sig_entry = sig.get("entry") or live_price
                entry = live_price if live_price > 0 else sig_entry
                sl_dist = abs(sig_entry - (sig.get("sl") or sig_entry)) if sig.get("sl") else entry * 0.004
                tp_dist = abs((sig.get("tp") or sig_entry) - sig_entry) if sig.get("tp") else entry * 0.008
                d = sig["direction"]
                sl = (entry - sl_dist) if d == "long" else (entry + sl_dist)
                tp = (entry + tp_dist) if d == "long" else (entry - tp_dist)
                self.paper_trader.open_position(
                    strategy_key=key,
                    strategy_name=result.get("name", key),
                    direction=d,
                    entry_price=entry,
                    sl_price=round(sl, 2),
                    tp_price=round(tp, 2),
                    confidence=sig.get("confidence", 0),
                    leverage=int(s_cfg.get("leverage", 10)),
                    reasoning=sig.get("reasoning", ""),
                )

        # ── Fusion strategy — ONE unified meta-signal from all sub-strategies ─
        # The MasterBrain acts as an ensemble learner: it weights each strategy's
        # signal by trust × learned_regime_affinity × signal_confidence, then
        # fuses them into a single directional conviction.  Only ONE fusion
        # position is open at a time; it uses the same SL/TP/leverage config as
        # individual strategies.
        results_dict = {key: result for key, result in strategies}
        fusion_pos = self.positions.get("fusion")

        if not fusion_pos and cfg.get("enabled", True) and cfg.get("auto_execute", True):
            fusion_sig = self.brain.fuse_signals(results_dict, live_price)
            if fusion_sig:
                fusion_cfg = {
                    "enabled":        True,
                    "size_usdc":      cfg.get("size_usdc", 5),
                    "leverage":       cfg.get("leverage", 60),
                    "min_confidence": 0.40,
                    "min_conditions": 1,
                    "mode":           cfg.get("mode", "paper"),
                    "auto_execute":   True,
                }
                f_decision = self.brain.evaluate_signal(
                    strategy_key="fusion",
                    strategy_name="Fusion Strategy",
                    signal=fusion_sig,
                    open_positions=self.positions,
                    live_price=live_price,
                    portfolio_pnl=self.stats.get("total_pnl", 0),
                    is_live=self._is_live_mode(),
                    orderbook=orderbook,
                )
                contributors = fusion_sig.get("contributors", [])
                n_strats     = len(results_dict)
                consensus    = fusion_sig.get("consensus", 0)
                self._log(
                    f"🎯 FUSION {fusion_sig['direction'].upper()} · "
                    f"{len(contributors)}/{n_strats} agree · "
                    f"consensus {consensus:.0%} · "
                    f"conf {fusion_sig['confidence']:.0%} · "
                    f"[{', '.join(contributors)}] · "
                    f"{'✅ EXECUTING' if f_decision['approved'] else '❌ ' + f_decision['reasoning'][:50]}"
                )
                if f_decision["approved"]:
                    adj_cfg = dict(fusion_cfg)
                    adj_cfg["size_usdc"] = round(
                        fusion_cfg["size_usdc"] * f_decision["size_multiplier"], 2
                    )
                    # Post signal to X (Fusion is the only live signal)
                    if self._is_live_mode():
                        self.x_publisher.post_signal(
                            strategy_name="Fusion (MasterBrain)",
                            direction=fusion_sig.get("direction", "long"),
                            entry_price=live_price,
                            sl_price=fusion_sig.get("sl", 0),
                            tp_price=fusion_sig.get("tp", 0),
                            conviction=f_decision["conviction"],
                            size_usdc=adj_cfg["size_usdc"],
                            regime=self.brain.current_regime,
                        )
                    self._open_position("fusion", "Fusion Strategy", fusion_sig, adj_cfg, live_price)
                    any_signal = True
            elif self.scan_count % 5 == 0:
                self._log(f"🎯 FUSION: no consensus · regime={self.brain.current_regime}")

        self._save_state()           # fast file cache
        await self._save_state_db()  # durable DB persist

    # ── Brain-gated execution (dual-mode: paper shadow + live) ─────────────

    def _brain_gate_execute(
        self,
        key: str,
        name: str,
        sig: dict,
        cfg: dict,
        live_price: float,
        live_allowed: bool = True,
        orderbook: Optional[dict] = None,
    ) -> None:
        """
        Dual-mode execution pipeline.

        live_allowed=True  → normal flow: live approval path can open BingX trade.
        live_allowed=False → individual strategy in live mode: paper/shadow ONLY.
                             Used to feed Brain training without hitting BingX.
                             Only Fusion calls this with live_allowed=True.
        """
        strat_key    = key
        is_live_mode = self._is_live_mode()

        # ── LIVE MODE ──────────────────────────────────────────────────────────
        if is_live_mode:
            if live_allowed:
                # Full live path — evaluate with live thresholds, may open BingX trade
                live_decision = self.brain.evaluate_signal(
                    strategy_key=strat_key, strategy_name=name, signal=sig,
                    open_positions=self.positions, live_price=live_price,
                    portfolio_pnl=self.stats.get("total_pnl", 0),
                    is_live=True, orderbook=orderbook,
                )
                if live_decision["approved"]:
                    adjusted_cfg = {**cfg}
                    adjusted_cfg["size_usdc"] = round(
                        cfg["size_usdc"] * live_decision["size_multiplier"], 2
                    )
                    readiness = self.brain.is_strategy_live_ready(strat_key)
                    self._log(
                        f"🧠 LIVE APPROVED {name} · conviction {live_decision['conviction']:.0%} "
                        f"· size {live_decision['size_multiplier']:.0%} "
                        f"· win rate {readiness['win_rate']:.0%} ({readiness['trades']} trades) "
                        f"· {live_decision['reasoning']}"
                    )
                    self.x_publisher.post_signal(
                        strategy_name=name,
                        direction=sig.get("direction", "long"),
                        entry_price=live_price,
                        sl_price=sig.get("sl", 0),
                        tp_price=sig.get("tp", 0),
                        conviction=live_decision["conviction"],
                        size_usdc=adjusted_cfg["size_usdc"],
                        regime=getattr(self.brain, "current_regime", "unknown"),
                    )
                    self._open_position(key, name, sig, adjusted_cfg, live_price)
                    return

                rejection_reason = live_decision["reasoning"]
            else:
                # Individual strategy in live mode — BingX execution is disabled.
                # Route straight to paper shadow to keep training data flowing.
                rejection_reason = "Fusion-only live mode — individual strategies are paper/shadow"

            # Paper shadow path (for both: live-rejected + live_allowed=False)
            paper_decision = self.brain.evaluate_signal(
                strategy_key=strat_key, strategy_name=name, signal=sig,
                open_positions=self.positions, live_price=live_price,
                portfolio_pnl=self.stats.get("total_pnl", 0),
                is_live=False, orderbook=orderbook,
            )
            if paper_decision["approved"]:
                adjusted_cfg = {**cfg}
                adjusted_cfg["size_usdc"] = round(
                    cfg["size_usdc"] * paper_decision["size_multiplier"], 2
                )
                readiness = self.brain.is_strategy_live_ready(strat_key)
                label = "SHADOW PAPER" if live_allowed else "TRAINING SHADOW"
                self._log(
                    f"🧠 {label} {name} (live blocked: {rejection_reason[:60]}) "
                    f"· {readiness['trades']}/{self.brain.MIN_PAPER_TRADES_FOR_LIVE} trades "
                    f"· win rate {readiness['win_rate']:.0%}"
                )
                self._open_position_paper_shadow(key, name, sig, adjusted_cfg, live_price)
            else:
                self._log(f"🧠 BLOCKED {name} — {rejection_reason[:80]}")

        # ── PAPER MODE (agent not in live mode) ───────────────────────────────
        else:
            decision = self.brain.evaluate_signal(
                strategy_key=strat_key, strategy_name=name, signal=sig,
                open_positions=self.positions, live_price=live_price,
                portfolio_pnl=self.stats.get("total_pnl", 0),
                is_live=False, orderbook=orderbook,
            )
            if decision["approved"]:
                adjusted_cfg = {**cfg}
                adjusted_cfg["size_usdc"] = round(
                    cfg["size_usdc"] * decision["size_multiplier"], 2
                )
                self._log(
                    f"🧠 APPROVED {name} · conviction {decision['conviction']:.0%} "
                    f"· size {decision['size_multiplier']:.0%} · {decision['reasoning']}"
                )
                self._open_position(key, name, sig, adjusted_cfg, live_price)
            else:
                self._log(f"🧠 BLOCKED {name} — {decision['reasoning']}")

    def _open_position_paper_shadow(self, key: str, name: str, sig: dict, cfg: dict, price: float) -> None:
        """Open a paper position even in live mode — shadow training for the Brain."""
        sig_entry = sig["entry"] if sig.get("entry", 0) > 0 else price
        d = sig["direction"]
        entry = price if price > 0 else sig_entry

        sig_sl = sig.get("sl") or 0
        sig_tp = sig.get("tp") or 0
        sl_dist = abs(sig_entry - sig_sl) if sig_sl else 0
        tp_dist = abs(sig_tp - sig_entry) if sig_tp else 0
        sl = ((entry - sl_dist) if d == "long" else (entry + sl_dist)) if sl_dist > 0 else sig_sl
        tp = ((entry + tp_dist) if d == "long" else (entry - tp_dist)) if tp_dist > 0 else sig_tp

        leverage = max(1, int(cfg.get("leverage", 1)))
        btc_size = (cfg["size_usdc"] * leverage) / entry if entry > 0 else 0

        pos = {
            "id":             f"{key}-shadow-{int(time.time()*1000)}",
            "strategy_key":   key,
            "strategy_name":  name,
            "direction":      d,
            "entry":          round(entry, 2),
            "sl":             round(sl, 2),
            "tp":             round(tp, 2),
            "size_usdc":      cfg["size_usdc"],
            "leverage":       leverage,
            "confidence":     sig["confidence"],
            "reasoning":      sig.get("reasoning", ""),
            "rr":             sig.get("rr", "1:2"),
            "timestamp":      datetime.now(timezone.utc).isoformat(),
            "current_price":  entry,
            "unrealized_pnl": 0.0,
            "unrealized_pct": 0.0,
            "btc_size":       btc_size,
            "is_paper":       True,
            "is_shadow":      True,
            "mode":           "shadow",
        }
        self.positions[key] = pos
        self._log(f"📋 [SHADOW] [{name}] {d.upper()} @ ${entry:.0f} · paper training while building live confidence")

    # ── Always-on shadow training position ───────────────────────────────────

    def _open_shadow(self, shadow_key: str, name: str, sig: dict, cfg: dict, price: float) -> None:
        """
        Open a lightweight paper shadow position for continuous training.
        Shadow positions NEVER go live — they exist purely to generate training data
        for the MasterBrain regardless of the current trading mode.
        """
        if self.positions.get(shadow_key):
            return
        entry  = price if price > 0 else sig.get("entry", price)
        d      = sig["direction"]
        sig_e  = sig.get("entry", entry) or entry

        # Preserve SL/TP distances from signal, shift to actual fill price
        sl_dist = abs(sig_e - (sig.get("sl") or sig_e)) if sig.get("sl") else entry * 0.004
        tp_dist = abs((sig.get("tp") or sig_e) - sig_e) if sig.get("tp") else entry * 0.008
        sl = (entry - sl_dist) if d == "long" else (entry + sl_dist)
        tp = (entry + tp_dist) if d == "long" else (entry - tp_dist)

        strat_key = shadow_key.replace("shadow_", "")
        size_usd  = min(cfg.get("size_usdc", 5), 5.0)  # shadow uses small fixed size
        btc_size  = size_usd / entry if entry > 0 else 0

        pos = {
            "id":             f"{shadow_key}-{int(time.time()*1000)}",
            "strategy_key":   shadow_key,
            "strategy_name":  f"[SHADOW] {name}",
            "direction":      d,
            "entry":          round(entry, 2),
            "sl":             round(sl, 2),
            "tp":             round(tp, 2),
            "size_usdc":      size_usd,
            "leverage":       1,
            "confidence":     sig.get("confidence", 0.5),
            "reasoning":      sig.get("reasoning", ""),
            "rr":             sig.get("rr", "1:2"),
            "timestamp":      datetime.now(timezone.utc).isoformat(),
            "current_price":  entry,
            "unrealized_pnl": 0.0,
            "unrealized_pct": 0.0,
            "btc_size":       btc_size,
            "is_paper":       True,
            "is_shadow":      True,
            "mode":           "shadow",
            "strat_key_ref":  strat_key,   # which strategy this shadow tracks
        }
        self.positions[shadow_key] = pos
        self._log(f"📋 [SHADOW] {name} {d.upper()} @ ${entry:.0f} · training always on")

    # ── Position management ───────────────────────────────────────────────────

    def _open_position(self, key: str, name: str, sig: dict, cfg: dict, price: float) -> None:
        sig_entry = sig["entry"] if sig.get("entry", 0) > 0 else price
        d         = sig["direction"]

        # Always fill at live market price — never at the stale candle close
        entry = price if price > 0 else sig_entry

        # Preserve the ATR/dollar distances from the signal, shift SL/TP to actual fill
        sig_sl  = sig.get("sl") or 0
        sig_tp  = sig.get("tp") or 0
        sl_dist = abs(sig_entry - sig_sl) if sig_sl else 0
        tp_dist = abs(sig_tp - sig_entry) if sig_tp else 0

        sl = ((entry - sl_dist) if d == "long" else (entry + sl_dist)) if sl_dist > 0 else sig_sl
        tp = ((entry + tp_dist) if d == "long" else (entry - tp_dist)) if tp_dist > 0 else sig_tp

        # Slippage guard — skip if price moved >0.3% from signal (chasing)
        if sig_entry > 0:
            slip_pct = abs(entry - sig_entry) / sig_entry * 100
            if slip_pct > 0.30:
                self._log(f"⚠ [{name}] SKIPPED — price moved {slip_pct:.2f}% from signal "
                          f"(${sig_entry:.0f} → ${entry:.0f}) · max 0.3%")
                return

        leverage = max(1, int(cfg.get("leverage", 1)))
        btc_size = (cfg["size_usdc"] * leverage) / entry if entry > 0 else 0
        lev_str  = f" · {leverage}×" if leverage > 1 else ""
        slip_str = f" · filled ${sig_entry:.0f}→${entry:.0f}" if abs(entry - sig_entry) > 1 else ""

        # ── LIVE MODE: execute on BingX ───────────────────────────────────────
        if self._is_live_mode():
            executor = self._get_live_executor()
            if not executor:
                self._log(f"⚠ [{name}] Live mode but no BingX keys — falling back to paper")
            elif executor.halted:
                self._log(f"⛔ [{name}] CIRCUIT BREAKER active — skipping trade (daily loss limit hit)")
                return
            else:
                live_leverage = min(leverage, 60)  # hard cap 60x

                # Set a placeholder immediately to prevent race conditions
                # (next scan seeing positions[key] as empty and opening duplicates)
                self.positions[key] = {
                    "id": f"{key}-pending-{int(time.time()*1000)}",
                    "strategy_key": key, "strategy_name": name,
                    "direction": d, "entry": entry, "mode": "live",
                    "is_live": True, "_pending": True,
                    "sl": round(sl, 2), "tp": round(tp, 2),
                    "current_price": entry, "unrealized_pnl": 0,
                    "unrealized_pct": 0, "btc_size": btc_size,
                    "size_usdc": cfg["size_usdc"], "leverage": live_leverage,
                    "confidence": sig.get("confidence", 0),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

                async def _do_live_open():
                    pos = await executor.open_position(
                        strategy_key=key,
                        strategy_name=name,
                        direction=d,
                        size_usdc=cfg["size_usdc"],
                        leverage=live_leverage,
                        sl_price=round(sl, 2),
                        tp_price=round(tp, 2),
                        entry_price=entry,
                    )
                    if pos:
                        self.positions[key] = pos
                        self._log(f"★ [LIVE] [{name}] {d.upper()} @ ${pos['entry']:.0f} · "
                                  f"margin ${pos['size_usdc']:.2f} (2% of capital) · "
                                  f"{pos['leverage']}× · SL ${sl:.0f} · TP ${tp:.0f}")
                        self._save_state()
                        self._schedule_db_save()
                    else:
                        err_msg = getattr(executor, "last_error", None) or "unknown error"
                        self._log(f"✗ [LIVE] [{name}] BingX FAILED — {err_msg}")
                        # Remove placeholder — trade never executed
                        self.positions.pop(key, None)
                        self.brain.rollback_daily_trade()

                asyncio.create_task(_do_live_open())
                return

        # ── PAPER MODE: simulated fill ────────────────────────────────────────
        pos = {
            "id":             f"{key}-{int(time.time()*1000)}",
            "strategy_key":   key,
            "strategy_name":  name,
            "direction":      d,
            "entry":          round(entry, 2),
            "sl":             round(sl, 2),
            "tp":             round(tp, 2),
            "size_usdc":      cfg["size_usdc"],
            "leverage":       leverage,
            "confidence":     sig["confidence"],
            "reasoning":      sig.get("reasoning", ""),
            "rr":             sig.get("rr", "1:2"),
            "timestamp":      datetime.now(timezone.utc).isoformat(),
            "current_price":  entry,
            "unrealized_pnl": 0.0,
            "unrealized_pct": 0.0,
            "btc_size":       btc_size,
            "is_paper":       True,
            "mode":           "paper",
        }
        self.positions[key] = pos
        self._log(f"★ [{name}] OPENED {d.upper()} @ ${entry:.0f} · SL ${sl:.0f} · TP ${tp:.0f} · "
                  f"conf {sig['confidence']*100:.0f}%{lev_str}{slip_str}")

    # Max hold time in minutes per strategy before auto-close at market
    MAX_HOLD_MINUTES = {
        "momentum": 240, "hft": 45, "orb": 180, "obi": 15, "fusion": 120,
    }

    def _max_hold_for_key(self, key: str) -> int:
        if key.startswith("shadow_"):
            # Shadow positions have shorter hold so training cycles faster
            strat = key.replace("shadow_", "")
            return SHADOW_MAX_HOLD.get(strat, 30)
        return self.MAX_HOLD_MINUTES.get(key, 120)

    async def _sync_exchange_positions(self, live_price: float) -> None:
        """Check BingX for positions closed by exchange SL/TP orders."""
        try:
            closed_keys = await self._live.sync_positions(live_price)
            for key in closed_keys:
                pos = self.positions.get(key)
                if not pos or pos.get("mode") != "live":
                    continue
                # Position was closed on exchange by SL/TP
                entry = pos["entry"]
                d = pos["direction"]
                sl = pos.get("sl") or 0
                tp = pos.get("tp") or 0

                # Try to get actual fill price from exchange order history
                actual_fill: Optional[float] = None
                try:
                    recent_trades = await self._live._exchange.fetch_my_trades(
                        "BTC/USDT:USDT", limit=5
                    )
                    if recent_trades:
                        actual_fill = float(recent_trades[-1].get("price") or 0) or None
                except Exception:
                    pass

                # Determine which stop fired based on price proximity to SL/TP
                sl_dist = abs(live_price - sl) if sl else float("inf")
                tp_dist = abs(live_price - tp) if tp else float("inf")
                if tp_dist < sl_dist:
                    reason = "tp"
                    exit_price = actual_fill or (tp if tp else live_price)
                else:
                    reason = "sl"
                    exit_price = actual_fill or (sl if sl else live_price)

                diff = (exit_price - entry) if d == "long" else (entry - exit_price)
                pnl = round(diff * pos.get("btc_size", 0), 2)

                self._log(f"🔄 [LIVE] [{pos.get('strategy_name', key)}] Exchange SL/TP fired — "
                          f"{reason.upper()} @ ${exit_price:.0f} · P&L {'+' if pnl >= 0 else ''}${pnl:.2f}")

                # Clean up executor's local tracking
                if key in self._live.live_positions:
                    self._live.record_pnl(pnl)
                    del self._live.live_positions[key]

                self._record_trade_closure(key, pos, pnl, exit_price, reason, is_live=True)
        except Exception as e:
            if self.scan_count % 10 == 0:
                logger.warning(f"[Agent] Exchange sync failed: {type(e).__name__}: {e}")

    def _update_positions(self, price: float) -> None:
        now_utc = datetime.now(timezone.utc)
        for key in list(self.positions.keys()):
            pos = self.positions.get(key)
            if not pos:
                continue

            d   = pos["direction"]
            sl  = pos.get("sl")
            tp  = pos.get("tp")

            hit_tp = (d == "long"  and tp and price >= tp) or (d == "short" and tp and price <= tp)
            hit_sl = (d == "long"  and sl and price <= sl) or (d == "short" and sl and price >= sl)

            # Auto-close if held past max hold time
            timed_out = False
            try:
                opened_at = datetime.fromisoformat(pos["timestamp"].replace("Z", "+00:00"))
                held_min  = (now_utc - opened_at).total_seconds() / 60
                max_hold  = self._max_hold_for_key(key)
                timed_out = held_min > max_hold
            except Exception:
                pass

            if hit_tp or hit_sl or timed_out:
                reason     = "tp" if hit_tp else ("sl" if hit_sl else "timeout")
                exit_price = (tp if hit_tp else (sl if hit_sl else price)) or price
                self._close_position(key, exit_price, reason)
            else:
                d_   = pos["direction"]
                diff = (price - pos["entry"]) if d_ == "long" else (pos["entry"] - price)
                # btc_size already encodes notional (size_usdc * leverage / entry),
                # so pnl = diff * btc_size — no extra *leverage needed
                pnl  = diff * pos["btc_size"]
                pct  = diff / pos["entry"] * 100 if pos["entry"] > 0 else 0
                self.positions[key] = {**pos, "current_price": price, "unrealized_pnl": round(pnl, 2), "unrealized_pct": round(pct, 4)}

    def _close_position(self, key: str, exit_price: float, reason: str) -> None:
        pos = self.positions.get(key)
        if not pos:
            return

        # Shadow positions are always paper — never touch the exchange
        if pos.get("is_shadow") or key.startswith("shadow_"):
            d    = pos["direction"]
            diff = (exit_price - pos["entry"]) if d == "long" else (pos["entry"] - exit_price)
            pnl  = round(diff * pos["btc_size"], 2)
            self._record_trade_closure(key, pos, pnl, exit_price, reason, is_live=False)
            return

        # ── LIVE MODE: close on BingX exchange ────────────────────────────────
        if pos.get("mode") == "live" and self._live:
            async def _do_live_close():
                trade = await self._live.close_position(key, exit_price, reason)
                if trade:
                    actual_reason = trade.get("exit_reason", reason)
                    if "_exchange_closed" in actual_reason:
                        self._log(f"🔄 [LIVE] [{pos.get('strategy_name', key)}] Already closed on exchange "
                                  f"(SL/TP fired) — recorded P&L {'+' if trade['pnl_usd'] >= 0 else ''}${trade['pnl_usd']:.2f}")
                    self._record_trade_closure(key, pos, trade["pnl_usd"], trade.get("exit_price", exit_price),
                                               reason, is_live=True)
                else:
                    self._log(f"⚠ [LIVE] close FAILED for {key} — forcing local cleanup")
                    # Force close locally to prevent stuck positions
                    d = pos["direction"]
                    diff = (exit_price - pos["entry"]) if d == "long" else (pos["entry"] - exit_price)
                    pnl = round(diff * pos.get("btc_size", 0), 2)
                    self._record_trade_closure(key, pos, pnl, exit_price, f"{reason}_forced", is_live=True)
            asyncio.create_task(_do_live_close())
            return

        # ── PAPER MODE: simulate closure ──────────────────────────────────────
        d    = pos["direction"]
        diff = (exit_price - pos["entry"]) if d == "long" else (pos["entry"] - exit_price)
        pnl  = round(diff * pos["btc_size"], 2)
        self._record_trade_closure(key, pos, pnl, exit_price, reason, is_live=False)

    def _record_trade_closure(self, key: str, pos: dict, pnl: float, exit_price: float, reason: str, is_live: bool) -> None:
        entry  = pos.get("entry") or 0
        d      = pos.get("direction", "long")
        diff   = (exit_price - entry) if d == "long" else (entry - exit_price)
        pct    = round(diff / entry * 100, 4) if entry > 0 else 0
        trade = {**pos, "exit_price": exit_price, "exit_reason": reason,
                 "pnl_usd": pnl, "pnl_pct": pct,
                 "closed_at": datetime.now(timezone.utc).isoformat(),
                 "status": "confirmed", "is_live": is_live}
        self.trades.insert(0, trade)
        if len(self.trades) > 500:
            self.trades = self.trades[:500]

        s = self.stats
        s["total_trades"] += 1
        # Count win/loss by actual PnL — covers tp, sl, manual, timeout, and all other reasons
        if pnl > 0:
            s["wins"]   += 1
        else:
            s["losses"] += 1
        s["total_pnl"]   = round(s["total_pnl"] + pnl, 2)
        s["win_rate"]    = round(s["wins"] / s["total_trades"] * 100, 1) if s["total_trades"] > 0 else 0
        s["best_trade"]  = max(s.get("best_trade",  pnl), pnl)
        s["worst_trade"] = min(s.get("worst_trade", pnl), pnl)

        self.positions[key] = None
        prefix = "[LIVE]" if is_live else ""
        self._log(f"{prefix} [{pos['strategy_name']}] {reason.upper()} @ ${exit_price:.0f} · "
                  f"P&L {'+' if pnl>=0 else ''}${pnl:.2f}")

        # Resolve canonical strategy key for brain learning
        if key.startswith("shadow_"):
            strat_key = key.replace("shadow_", "")
        else:
            strat_key = key
        won = pnl > 0

        # Compute trade duration for brain learning (Feature 10)
        opened_at = pos.get("opened_at") or pos.get("timestamp", "")
        duration_min: float = 0.0
        if opened_at:
            try:
                opened_dt = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
                duration_min = (datetime.now(timezone.utc) - opened_dt).total_seconds() / 60
            except Exception:
                pass

        self.brain.record_trade_result(strat_key, pnl, won, was_live=is_live, duration_min=duration_min)

        # Post trade result to X for live closes
        if is_live:
            self.x_publisher.post_result(
                strategy_name=pos.get("strategy_name", ""),
                direction=pos.get("direction", "long"),
                entry_price=entry,
                exit_price=exit_price,
                pnl_usd=pnl,
                reason=reason,
                duration_min=duration_min,
            )

        self._rebuild_training_index()
        self._save_state()
        self._schedule_db_save()

    def close_position(self, key: str) -> bool:
        pos = self.positions.get(key)
        if not pos:
            return False
        self._close_position(key, pos["current_price"], "manual")
        self._save_state()
        self._schedule_db_save()
        return True

    def reset(self) -> None:
        self.positions  = {}
        self.trades     = []
        self.stats      = self._empty_stats()
        self.log        = []
        self.training_index  = {}
        self.scan_count = 0
        # Also reset brain daily counters so the trading lock is fully cleared
        self.brain.daily_trades       = 0
        self.brain.consecutive_losses = 0
        self.brain.daily_pnl          = 0.0
        self.brain.daily_wins         = 0
        self.brain.daily_losses_count = 0
        self._log("Account reset — positions, trades, training index, and brain daily counters cleared")
        self._save_state()
        self._schedule_db_save()

    def _strategy_cfg(self, strategy_key: str) -> dict:
        """Resolve effective config for a strategy: per-strategy override merged over global."""
        overrides = self.config.get("strategy_overrides", {})
        s_cfg = overrides.get(strategy_key, {})
        return {
            "enabled":        s_cfg.get("enabled",        self.config.get("enabled", True)),
            "size_usdc":      s_cfg.get("size_usdc",      self.config.get("size_usdc", 100)),
            "leverage":       s_cfg.get("leverage",       self.config.get("leverage", 1)),
            "min_confidence": s_cfg.get("min_confidence",  self.config.get("min_confidence", 0.50)),
            "min_conditions": s_cfg.get("min_conditions",  self.config.get("min_conditions", 2)),
            "mode":           self.config.get("mode", "paper"),
            "auto_execute":   self.config.get("auto_execute", True),
        }

    def update_config(self, patch: dict) -> None:
        # Handle strategy_overrides merge separately to preserve per-key data
        if "strategy_overrides" in patch:
            existing = self.config.get("strategy_overrides", {})
            for strat_key, strat_patch in patch["strategy_overrides"].items():
                if strat_key not in existing:
                    existing[strat_key] = {**DEFAULT_STRATEGY_CFG}
                existing[strat_key].update(strat_patch)
            self.config["strategy_overrides"] = existing
            patch = {k: v for k, v in patch.items() if k != "strategy_overrides"}
        self.config.update(patch)
        # Keep brain daily loss limit in sync when config changes
        if "daily_loss_limit" in patch:
            self.brain.MAX_DAILY_LOSS = -abs(float(patch["daily_loss_limit"]))
        if patch.get("enabled") is True and not self._running:
            asyncio.create_task(self.start())
        elif patch.get("enabled") is False and self._running:
            asyncio.create_task(self.stop())
        self._save_state()
        self._schedule_db_save()

    # ── State snapshot for API ────────────────────────────────────────────────

    def get_status(self) -> dict:
        from app.agents.live_market_stream import LIVE_PRICES
        price = LIVE_PRICES.get("BTC/USDT", {}).get("last", 0)
        # Separate live/paper positions from shadow training positions
        open_positions = [p for k, p in self.positions.items()
                          if p and not k.startswith("shadow_")]
        shadow_positions = [p for k, p in self.positions.items()
                            if p and k.startswith("shadow_")]

        from app.core.config import settings as _cfg
        keys_set = bool(_cfg.bingx_api_key and _cfg.bingx_api_secret)
        live_executor_status: dict = {"connected": False, "keys_set": keys_set}
        # Eagerly init executor if BingX keys are configured, so status always shows
        if keys_set:
            executor = self._get_live_executor()
            if executor:
                live_executor_status = {**executor.status(), "keys_set": True}
        elif self._live:
            live_executor_status = {**self._live.status(), "keys_set": False}

        return {
            "running":              self._running,
            "mode":                 self.config.get("mode", "paper"),
            "config":               self.config,
            "scan_count":           self.scan_count,
            "last_scan":            self.last_scan,
            "live_price":           price,
            "open_positions":       open_positions,
            "shadow_positions":     shadow_positions,
            "trades":               self.trades[:200],
            "stats":                self.stats,
            "log":                  self.log[:100],
            "training_index":       self.training_index,
            "live_executor":        live_executor_status,
            "master_brain":         self.brain.get_status(self.positions, price),
            "paper_trader":         self.paper_trader.get_status(),
        }


# ── Module-level singleton ────────────────────────────────────────────────────
_agent: Optional[PersistentAgent] = None


def get_agent() -> PersistentAgent:
    global _agent
    if _agent is None:
        _agent = PersistentAgent()
    return _agent


async def start_agent() -> None:
    agent = get_agent()
    if agent.config.get("enabled", True):
        await agent.start()
