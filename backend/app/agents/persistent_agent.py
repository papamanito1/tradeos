"""
Persistent 24/7 Trading Agent
==============================
Runs all 4 strategies on the backend server continuously.
Positions survive browser close / page refreshes.
State is persisted to a JSON file and reloaded on startup.

Strategies:
  - Momentum 15m   (EMA50 slope + RSI + VWAP + volume)
  - HFT Scalper 1m (EMA9/21 + OBI + TFI + microprice)
  - ORB-30 1m      (Opening Range Breakout, first 30 bars)
  - OBI Scalper 1m (Order Book Imbalance + EMA9/21 + RSI)
"""

from __future__ import annotations

import asyncio
import json
import logging
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
DEFAULT_CONFIG = {
    "enabled":        True,   # auto-start on server boot
    "size_usdc":      100,
    "min_confidence": 0.50,
    "min_conditions": 3,
    "mode":           "paper",
    "auto_execute":   True,
}

STRATEGY_KEYS = ["momentum", "hft", "orb", "obi"]

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

    rsi_window = rsi_a[max(0, i - 3):i + 1]
    if any(_nan(v) for v in rsi_window):
        return null

    long_conds = [
        ema50_slope > 0.0002,
        cur["close"] > ema50[i],
        rsi_a[i] >= 50 and (not rsi_window[:-1] or min(rsi_window[:-1]) < 50),
        cur["close"] > cur["open"],
        abs(cur["low"] - ema21[i]) <= 2.0 * atr_a[i] or abs(cur["low"] - vwap_a[i]) <= 2.0 * atr_a[i],
        vol_ratio >= 0.8,
        0.0005 <= atr_pct <= 0.030,
    ]
    short_conds = [
        ema50_slope < -0.0002,
        cur["close"] < ema50[i],
        rsi_a[i] <= 50 and (not rsi_window[:-1] or max(rsi_window[:-1]) > 50),
        cur["close"] < cur["open"],
        abs(cur["high"] - ema21[i]) <= 2.0 * atr_a[i] or abs(cur["high"] - vwap_a[i]) <= 2.0 * atr_a[i],
        vol_ratio >= 0.8,
        0.0005 <= atr_pct <= 0.030,
    ]

    long_met  = sum(1 for c in long_conds if c)
    short_met = sum(1 for c in short_conds if c)
    is_long_bias  = ema50_slope > 0
    is_short_bias = ema50_slope < 0
    bias = "long" if long_met >= 4 else "short" if short_met >= 4 else "neutral"
    met_count = long_met if is_long_bias else short_met

    full_long  = long_met  >= 5 and is_long_bias
    full_short = short_met >= 5 and is_short_bias
    signal = None

    if full_long or full_short:
        d      = "long" if full_long else "short"
        entry  = cur["close"]
        sl_d   = 1.5 * atr_a[i]
        tp_d   = 3.0 * atr_a[i]
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

        signal = {
            "direction": d,
            "entry": round(entry, 2),
            "sl":    round(sl, 2),
            "tp":    round(tp, 2),
            "confidence": round(conf, 3),
            "rr": "1:2.0",
            "reasoning": f"Momentum [{mc}/7] {d.upper()}: slope {ema50_slope * 100:.3f}%, RSI {rsi_a[i]:.1f}, vol {vol_ratio:.1f}×",
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

    long_obi  = obi  >  0.20;  short_obi = obi  < -0.20
    long_ema  = cur_e9 > cur_e21; short_ema = cur_e9 < cur_e21
    long_rsi  = cur_rsi > 50;    short_rsi = cur_rsi < 50

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
        obi > 0.08,
        abs(obi) > 0.05,
    ]
    short_conds = [
        short_bias,
        cur1m["close"] < vwap1m,
        near_vwap,
        obi < -0.08,
        abs(obi) > 0.05,
    ]

    long_met  = sum(1 for c in long_conds if c)
    short_met = sum(1 for c in short_conds if c)
    bias = "long" if long_met >= 3 else "short" if short_met >= 3 else "neutral"
    met_count = long_met if long_bias else short_met

    signal = None
    if long_met >= 4 or short_met >= 4:
        d    = "long" if long_met >= 4 else "short"
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
    The 24/7 brain. Runs all 4 strategies every SCAN_INTERVAL seconds.
    Each strategy gets its own independent position slot.
    """

    SCAN_INTERVAL = 20  # seconds

    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self.config: dict    = {}
        self.positions: dict = {}   # key → PaperPosition | None
        self.trades: list    = []   # closed/confirmed trades
        self.stats:  dict    = {}
        self.log:    list    = []   # last 200 lines
        self.scan_count      = 0
        self.last_scan: Optional[str] = None
        self._load_state()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _state_dict(self) -> dict:
        return {
            "config":    self.config,
            "positions": self.positions,
            "trades":    self.trades[-200:],
            "stats":     self.stats,
            "log":       self.log[-100:],
            "saved_at":  datetime.now(timezone.utc).isoformat(),
        }

    def _apply_state(self, data: dict) -> None:
        self.config    = data.get("config",    DEFAULT_CONFIG.copy())
        self.positions = data.get("positions", {})
        self.trades    = data.get("trades",    [])[-200:]
        self.stats     = data.get("stats",     self._empty_stats())
        self.log       = data.get("log",       [])[-100:]

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
            logger.warning(f"DB state load failed: {e}")
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
            logger.debug(f"DB state save failed: {e}")

    def _schedule_db_save(self) -> None:
        """Fire-and-forget DB save from a sync context."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(self._save_state_db())
        except Exception:
            pass

    def _empty_stats(self) -> dict:
        return {"total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
                "total_pnl": 0.0, "best_trade": 0.0, "worst_trade": 0.0}

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
            return
        # Load from DB first (persistent) — overrides the file loaded in __init__
        loaded_from_db = await self._load_state_db()
        if not loaded_from_db:
            self._load_state()  # file fallback (local dev)
        self._running = True
        self._task = asyncio.create_task(self._loop())
        self._log("Agent STARTED — all 4 strategies scanning every 20s (server-side, 24/7)")
        logger.info("PersistentAgent started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        self._log("Agent STOPPED")
        self._save_state()
        await self._save_state_db()

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

    async def _scan(self) -> None:
        from app.agents.live_market_stream import LIVE_CANDLES, LIVE_PRICES, LIVE_ORDERBOOK

        self.scan_count += 1
        self.last_scan = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        cfg = self.config

        candles1m  = LIVE_CANDLES.get("BTC/USDT:1m",  [])
        candles15m = LIVE_CANDLES.get("BTC/USDT:15m", [])
        orderbook  = LIVE_ORDERBOOK.get("BTC/USDT")
        price_data = LIVE_PRICES.get("BTC/USDT", {})
        live_price = price_data.get("last", 0.0)

        # Update open position P&L
        if live_price > 0:
            self._update_positions(live_price)

        if not cfg.get("enabled", True):
            return

        strategies = [
            ("momentum", _run_momentum(candles15m)),
            ("hft",      _run_hft(candles1m, orderbook)),
            ("orb",      _run_orb(candles1m)),
            ("obi",      _run_obi(candles1m, orderbook)),
        ]

        any_signal = False
        for key, result in strategies:
            sig = result.get("signal")
            met = result.get("met_count", 0)
            total = result.get("total", 7)
            bias = result.get("bias", "neutral")
            name = result.get("name", key)
            open_pos = self.positions.get(key)

            if sig:
                conf = sig.get("confidence", 0)
                cond_ok = met >= cfg.get("min_conditions", 3)
                conf_ok = conf >= cfg.get("min_confidence", 0.50)

                self._log(f"[{name}] {met}/{total} conds · {sig['direction'].upper()} · conf {conf*100:.0f}% · {'POS OPEN' if open_pos else bias}")

                if cond_ok and conf_ok and not open_pos and cfg.get("auto_execute", True):
                    self._open_position(key, name, sig, cfg, live_price)
                    any_signal = True
            else:
                self._log(f"[{name}] {met}/{total} conds · no signal · {bias} · {'POS OPEN' if open_pos else 'flat'}")

        self._save_state()           # fast file cache
        await self._save_state_db()  # durable DB persist

    # ── Position management ───────────────────────────────────────────────────

    def _open_position(self, key: str, name: str, sig: dict, cfg: dict, price: float) -> None:
        entry    = sig["entry"] if sig["entry"] > 0 else price
        btc_size = cfg["size_usdc"] / entry if entry > 0 else 0

        pos = {
            "id":             f"{key}-{int(time.time()*1000)}",
            "strategy_key":   key,
            "strategy_name":  name,
            "direction":      sig["direction"],
            "entry":          entry,
            "sl":             sig["sl"],
            "tp":             sig["tp"],
            "size_usdc":      cfg["size_usdc"],
            "confidence":     sig["confidence"],
            "reasoning":      sig.get("reasoning", ""),
            "rr":             sig.get("rr", "1:2"),
            "timestamp":      datetime.now(timezone.utc).isoformat(),
            "current_price":  entry,
            "unrealized_pnl": 0.0,
            "unrealized_pct": 0.0,
            "btc_size":       btc_size,
            "is_paper":       cfg.get("mode", "paper") == "paper",
        }
        self.positions[key] = pos
        self._log(f"★ [{name}] OPENED {sig['direction'].upper()} @ ${entry:.0f} · SL ${sig['sl']:.0f} · TP ${sig['tp']:.0f} · conf {sig['confidence']*100:.0f}%")

    def _update_positions(self, price: float) -> None:
        for key in list(self.positions.keys()):
            pos = self.positions.get(key)
            if not pos:
                continue

            d   = pos["direction"]
            sl  = pos.get("sl")
            tp  = pos.get("tp")

            hit_tp = (d == "long"  and tp and price >= tp) or (d == "short" and tp and price <= tp)
            hit_sl = (d == "long"  and sl and price <= sl) or (d == "short" and sl and price >= sl)

            if hit_tp or hit_sl:
                reason     = "tp" if hit_tp else "sl"
                exit_price = (tp if hit_tp else sl) or price
                self._close_position(key, exit_price, reason)
            else:
                diff = (price - pos["entry"]) if d == "long" else (pos["entry"] - price)
                pnl  = diff * pos["btc_size"]
                pct  = diff / pos["entry"] * 100 if pos["entry"] > 0 else 0
                self.positions[key] = {**pos, "current_price": price, "unrealized_pnl": round(pnl, 2), "unrealized_pct": round(pct, 4)}

    def _close_position(self, key: str, exit_price: float, reason: str) -> None:
        pos = self.positions.get(key)
        if not pos:
            return

        d    = pos["direction"]
        diff = (exit_price - pos["entry"]) if d == "long" else (pos["entry"] - exit_price)
        pnl  = round(diff * pos["btc_size"], 2)
        pct  = round(diff / pos["entry"] * 100, 4) if pos["entry"] > 0 else 0

        trade = {**pos, "exit_price": exit_price, "exit_reason": reason,
                 "pnl_usd": pnl, "pnl_pct": pct,
                 "closed_at": datetime.now(timezone.utc).isoformat(), "status": "confirmed"}
        self.trades.insert(0, trade)
        if len(self.trades) > 100:
            self.trades = self.trades[:100]

        # Update stats
        s = self.stats
        s["total_trades"] += 1
        if reason == "tp":
            s["wins"]   += 1
        elif reason == "sl":
            s["losses"] += 1
        s["total_pnl"]   = round(s["total_pnl"] + pnl, 2)
        s["win_rate"]    = round(s["wins"] / s["total_trades"] * 100, 1) if s["total_trades"] > 0 else 0
        s["best_trade"]  = max(s.get("best_trade",  pnl), pnl)
        s["worst_trade"] = min(s.get("worst_trade", pnl), pnl)

        self.positions[key] = None
        self._log(f"[{pos['strategy_name']}] {reason.upper()} hit @ ${exit_price:.0f} · P&L {'+' if pnl>=0 else ''}${pnl:.2f}")

    def close_position(self, key: str) -> bool:
        pos = self.positions.get(key)
        if not pos:
            return False
        self._close_position(key, pos["current_price"], "manual")
        self._save_state()
        self._schedule_db_save()
        return True

    def reset(self) -> None:
        self.positions = {}
        self.trades    = []
        self.stats     = self._empty_stats()
        self.log       = []
        self.scan_count = 0
        self._log("Paper account reset — all positions cleared")
        self._save_state()
        self._schedule_db_save()

    def update_config(self, patch: dict) -> None:
        self.config.update(patch)
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
        open_positions = [p for p in self.positions.values() if p]
        return {
            "running":        self._running,
            "config":         self.config,
            "scan_count":     self.scan_count,
            "last_scan":      self.last_scan,
            "live_price":     price,
            "open_positions": open_positions,
            "trades":         self.trades[:50],
            "stats":          self.stats,
            "log":            self.log[:100],
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
