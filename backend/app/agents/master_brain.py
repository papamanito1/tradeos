"""
Master Trading Brain
====================
The central intelligence layer that sits between raw strategy signals and
BingX execution. No strategy can trade directly — every signal must pass
through the Brain's decision pipeline.

Pipeline:
  1. MACRO TREND    — 1h/4h multi-timeframe bias
  2. SESSION GATE   — time-of-day affinity
  3. CONFLUENCE     — how many strategies agree?
  4. REGIME         — 15m market regime
  5. PORTFOLIO      — exposure / correlation risk
  6. RISK GATE      — daily loss, consecutive losses, drawdown
  7. MARKET CONTEXT — funding rate + Fear & Greed bias
  8. FIB LEVELS     — Fibonacci retracement bias (new)
  9. SIZING         — Kelly + ATR-adjusted + drawdown-scaled
 10. DECISION       — final APPROVE / REJECT / REDUCE with reasoning

Learning from every trade:
  • Strategy trust via exponential EMA (recent results dominate)
  • Regime affinity (what works in each regime)
  • Session affinity (what works at each UTC hour)
  • Profit factor tracking per strategy
  • Max drawdown tracking per strategy
  • Trade duration intelligence
  • Fibonacci level hit-rate per level (new)
"""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone
from typing import Optional

# ── Known high-impact macro events (UTC month/day/hour windows) ────────────────
# Format: (month, day_of_month_approx, hour_utc, label)
# These are updated manually each quarter — FOMC 8x/year, CPI ~2nd Tue each month
_MACRO_BLACKOUT_WINDOWS: list[tuple[int, int, int, str]] = [
    # FOMC 2026 approximate dates (Tue/Wed, decision ~19:00 UTC Wed)
    (1, 28, 18, "FOMC"), (1, 29, 18, "FOMC"),
    (3, 18, 18, "FOMC"), (3, 19, 18, "FOMC"),
    (5, 6,  18, "FOMC"), (5, 7,  18, "FOMC"),
    (6, 17, 18, "FOMC"), (6, 18, 18, "FOMC"),
    (7, 29, 18, "FOMC"), (7, 30, 18, "FOMC"),
    (9, 16, 18, "FOMC"), (9, 17, 18, "FOMC"),
    (11, 4, 18, "FOMC"), (11, 5, 18, "FOMC"),
    (12, 9, 18, "FOMC"), (12, 10, 18, "FOMC"),
]
# CPI releases typically 2nd Tuesday each month ~12:30 UTC — blackout ±90 min
_CPI_HOUR_UTC = 12   # 12:00–14:00 UTC on CPI day

logger = logging.getLogger(__name__)

# ── Trading session windows (UTC hours, inclusive) ─────────────────────────────
SESSIONS = {
    "Asia":    (0,  8),   # 00:00–08:59 UTC
    "London":  (8, 13),   # 08:00–12:59 UTC
    "NY":      (13, 22),  # 13:00–21:59 UTC
    "OffHours":(22, 24),  # 22:00–23:59 UTC (low liquidity)
}
# Dead hours — truly minimal volume, hard-reject live trades
DEAD_HOURS = {4, 5}
# Low-volume hours — penalised but not blocked
LOW_VOLUME_HOURS = {3, 6, 7, 22, 23}


def _current_session() -> str:
    h = datetime.now(timezone.utc).hour
    for name, (start, end) in SESSIONS.items():
        if start <= h < end:
            return name
    return "OffHours"


class MasterBrain:
    """
    Singleton decision engine. Instantiated by PersistentAgent on startup.
    All state is serializable for DB persistence.
    """

    def __init__(self) -> None:
        # ── Regime detection ─────────────────────────────────────────────
        self.current_regime: str = "unknown"
        self.regime_confidence: float = 0.0
        self.regime_updated: str = ""

        # ── Macro trend (multi-timeframe: 1h + 4h) ───────────────────────
        self.macro_trend: str = "neutral"       # "bullish" | "bearish" | "neutral"
        self.macro_confidence: float = 0.0

        # ── Market context ────────────────────────────────────────────────
        self.current_atr_pct: float = 0.5       # current ATR as % of price
        self._baseline_atr_pct: float = 0.5     # rolling 50-bar average
        self.funding_rate: float = 0.0          # BTC perp 8h funding rate
        self.fear_greed_score: int = 50         # 0-100 (alternative.me)

        # ── Fibonacci levels (computed from swing high/low on 15m candles) ─
        # Key retracement levels: 23.6%, 38.2%, 50%, 61.8%, 78.6%
        self._fib_levels: dict[str, float] = {}   # e.g. {"23.6": 84210.5, ...}
        self._fib_swing_high: float = 0.0
        self._fib_swing_low: float = 0.0
        self._fib_trend: str = "up"              # "up" (measuring retracement from high) or "down"
        self._fib_updated_at: float = 0.0

        # ── Liquidity sweep detection ─────────────────────────────────────
        # Recent sweep: price wicked beyond a swing level then closed back
        self._liq_sweep: dict = {}          # {"direction": "up"|"down", "level": float, "bars_ago": int}

        # ── Market structure (BOS / CHOCH) ────────────────────────────────
        # BOS = Break of Structure, CHOCH = Change of Character
        self._mss: dict = {}                # {"type": "BOS"|"CHOCH", "direction": str, "level": float}
        self._swing_highs: list[float] = []  # recent swing highs on 15m
        self._swing_lows:  list[float] = []  # recent swing lows  on 15m

        # ── Volume anomaly ────────────────────────────────────────────────
        self._vol_spike: bool = False        # True if last closed candle had 2× avg vol
        self._vol_spike_dir: str = ""        # "bullish" | "bearish" (candle close vs open)

        # ── HTF Pivot Points (weekly + monthly) ───────────────────────────
        self._weekly_pivots: dict[str, float] = {}   # PP, R1-R3, S1-S3
        self._monthly_pivots: dict[str, float] = {}
        self._pivots_updated: float = 0.0

        # ── RSI divergence ────────────────────────────────────────────────
        self._rsi_divergence: str = ""       # "bullish" | "bearish" | ""

        # ── Bayesian trust (Beta distribution: alpha=wins, beta=losses) ───
        # These run alongside the EMA trust for ensemble decision
        self._bayes_trust: dict[str, dict] = {}   # {strat: {"alpha": float, "beta": float}}

        # ── Strategy trust scores (0.0 – 2.0, 1.0 = neutral) ────────────
        # Updated via exponential EMA — recent results dominate old history
        self.strategy_trust: dict[str, float] = {
            "momentum": 1.0, "hft": 1.0, "orb": 1.0, "obi": 1.0,
        }

        # ── Performance tracking ─────────────────────────────────────────
        self.strategy_stats: dict[str, dict] = {}
        self.consecutive_losses: int = 0
        self.daily_trades: int = 0
        self.daily_pnl: float = 0.0
        self.daily_wins: int = 0
        self.daily_losses_count: int = 0
        self.daily_wins_live: int = 0        # live-trade wins only (for risk reporting)
        self.daily_losses_live: int = 0      # live-trade losses only
        self._day_str: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # ── Decision log ─────────────────────────────────────────────────
        self.decisions: list[dict] = []

        # ── Regime-strategy affinity map (base priors) ───────────────────
        self.REGIME_AFFINITY = {
            "trending_up":   {"momentum": 1.4, "hft": 0.7, "orb": 1.2, "obi": 0.8, "fusion": 1.0},
            "trending_down": {"momentum": 1.3, "hft": 0.7, "orb": 1.1, "obi": 0.9, "fusion": 1.0},
            "ranging":       {"momentum": 0.5, "hft": 1.3, "orb": 0.6, "obi": 1.2, "fusion": 1.0},
            "volatile":      {"momentum": 0.8, "hft": 1.1, "orb": 0.9, "obi": 1.0, "fusion": 1.0},
            "unknown":       {"momentum": 1.0, "hft": 1.0, "orb": 1.0, "obi": 1.0, "fusion": 1.0},
        }

        # ── Learned regime affinity — drifts toward what actually works ──
        self._learned_affinity: dict[str, dict[str, float]] = {
            regime: dict(vals) for regime, vals in self.REGIME_AFFINITY.items()
        }

        # ── Session affinity — win rate by UTC hour (0-23) ───────────────
        # Each key: {"trades": int, "wins": int, "win_rate": float}
        self._hour_stats: dict[int, dict] = {
            h: {"trades": 0, "wins": 0, "win_rate": 0.5} for h in range(24)
        }

        # ── Session name → aggregate stats ────────────────────────────────
        self._session_stats: dict[str, dict] = {
            s: {"trades": 0, "wins": 0, "win_rate": 0.5}
            for s in SESSIONS
        }

        # ── Live readiness thresholds ─────────────────────────────────────
        self.MIN_PAPER_TRADES_FOR_LIVE = 50      # was 3 — need real sample before going live
        self.MIN_WIN_RATE_FOR_LIVE = 0.45        # was 0.40
        self.MIN_PROFIT_FACTOR_FOR_LIVE = 1.30   # was 1.20 — gross_win / gross_loss
        self.LIVE_CONVICTION_THRESHOLD = 0.45
        self.PAPER_CONVICTION_THRESHOLD = 0.35

        # ── Limits ───────────────────────────────────────────────────────
        self.MAX_DAILY_TRADES = 50
        self.MAX_CONSECUTIVE_LOSSES = 5
        self.MAX_OPEN_POSITIONS = 8
        self.MAX_DAILY_LOSS = -50.0
        self.CORRELATION_PENALTY = 0.5

        # ── Regime history ────────────────────────────────────────────────
        self._regime_history: list[str] = []

    # ── Day reset ────────────────────────────────────────────────────────

    def _check_day(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._day_str:
            self._day_str = today
            self.daily_trades = 0
            self.daily_pnl = 0.0
            self.daily_wins = 0
            self.daily_losses_count = 0
            self.daily_wins_live = 0
            self.daily_losses_live = 0
            self.consecutive_losses = 0
            logger.info("[MasterBrain] New day — daily counters reset")

    # ══════════════════════════════════════════════════════════════════════
    #  FEATURE 1: MULTI-TIMEFRAME MACRO TREND (1h + 4h)
    # ══════════════════════════════════════════════════════════════════════

    def detect_macro_trend(
        self,
        candles_1h: list[dict],
        candles_4h: list[dict],
    ) -> str:
        """
        Determine the higher-timeframe trend from 1h and 4h candles.
        Sets self.macro_trend ("bullish" | "bearish" | "neutral") and
        self.macro_confidence (0–1).
        """
        bullish_votes = 0
        bearish_votes = 0
        total_votes = 0

        for candles, weight in [(candles_4h, 2), (candles_1h, 1)]:
            if not candles or len(candles) < 20:
                continue
            closes = [c["close"] for c in candles[-50:]]
            ema20 = self._ema_last(closes, 20)
            ema50 = self._ema_last(closes, min(50, len(closes)))
            last  = closes[-1]
            slope = (closes[-1] - closes[-5]) / closes[-5] * 100 if closes[-5] > 0 else 0

            if last > ema20 > ema50 and slope > 0.1:
                bullish_votes += weight
            elif last < ema20 < ema50 and slope < -0.1:
                bearish_votes += weight
            total_votes += weight

        if total_votes == 0:
            self.macro_trend = "neutral"
            self.macro_confidence = 0.0
            return self.macro_trend

        bull_pct = bullish_votes / total_votes
        bear_pct = bearish_votes / total_votes

        if bull_pct >= 0.6:
            self.macro_trend = "bullish"
            self.macro_confidence = round(bull_pct, 2)
        elif bear_pct >= 0.6:
            self.macro_trend = "bearish"
            self.macro_confidence = round(bear_pct, 2)
        else:
            self.macro_trend = "neutral"
            self.macro_confidence = round(max(bull_pct, bear_pct), 2)

        return self.macro_trend

    # ══════════════════════════════════════════════════════════════════════
    #  REGIME DETECTION (15m)
    # ══════════════════════════════════════════════════════════════════════

    def detect_regime(self, candles_15m: list[dict], candles_1m: list[dict]) -> str:
        """Classify current market regime from 15m candle data. Also updates ATR baseline."""
        if not candles_15m or len(candles_15m) < 20:
            self.current_regime = "unknown"
            self.regime_confidence = 0.0
            return self.current_regime

        closes = [c["close"] for c in candles_15m[-50:]]
        n = len(closes)

        ema20 = self._ema_last(closes, 20)
        ema50 = self._ema_last(closes, min(50, n))
        slope = (closes[-1] - ema20) / ema20 * 100 if ema20 > 0 else 0

        atr_pct = self._atr_pct(candles_15m[-20:])
        # FEATURE 3: maintain rolling ATR baseline for volatility-adjusted sizing
        self.current_atr_pct = round(atr_pct, 4)
        # Baseline: exponential average of ATR (slow, 50-bar effect)
        if self._baseline_atr_pct <= 0:
            self._baseline_atr_pct = atr_pct
        else:
            self._baseline_atr_pct = round(
                0.97 * self._baseline_atr_pct + 0.03 * atr_pct, 4
            )

        recent_high = max(c["high"] for c in candles_15m[-10:])
        recent_low  = min(c["low"]  for c in candles_15m[-10:])
        range_pct   = (recent_high - recent_low) / recent_low * 100 if recent_low > 0 else 0

        if atr_pct > 0.5:
            regime = "volatile"
            conf = min(1.0, atr_pct / 0.8)
        elif abs(slope) > 0.15 and closes[-1] > ema20:
            regime = "trending_up"
            conf = min(1.0, abs(slope) / 0.4)
        elif abs(slope) > 0.15 and closes[-1] < ema20:
            regime = "trending_down"
            conf = min(1.0, abs(slope) / 0.4)
        elif range_pct < 1.0:
            regime = "ranging"
            conf = min(1.0, (1.0 - range_pct) * 2)
        else:
            regime = "unknown"
            conf = 0.3

        self.current_regime = regime
        self.regime_confidence = round(conf, 2)
        self.regime_updated = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self._regime_history = (self._regime_history + [regime])[-10:]
        return regime

    # ══════════════════════════════════════════════════════════════════════
    #  FEATURES 7 & 9: MARKET CONTEXT UPDATE (funding rate + Fear & Greed)
    # ══════════════════════════════════════════════════════════════════════

    def update_market_context(
        self,
        funding_rate: Optional[float] = None,
        fear_greed_score: Optional[int] = None,
    ) -> None:
        """
        Update market-wide bias signals. Called by PersistentAgent periodically.
        funding_rate: BTC perp 8h funding rate (e.g. 0.001 = 0.1%)
        fear_greed_score: 0-100 (alternative.me)
        """
        if funding_rate is not None:
            self.funding_rate = round(funding_rate, 5)
        if fear_greed_score is not None:
            self.fear_greed_score = int(max(0, min(100, fear_greed_score)))

    # ══════════════════════════════════════════════════════════════════════
    #  FIBONACCI LEVEL DETECTION
    # ══════════════════════════════════════════════════════════════════════

    FIB_RATIOS = {
        "23.6": 0.236,
        "38.2": 0.382,
        "50.0": 0.500,
        "61.8": 0.618,   # "golden ratio" — highest-probability reversal
        "78.6": 0.786,
    }
    FIB_TOLERANCE_ATR_MULT = 0.8  # within 0.8× ATR = "near" a level

    def compute_fib_levels(self, candles: list[dict], lookback: int = 100) -> None:
        """
        Detect swing high/low over the last `lookback` candles and compute
        Fibonacci retracement levels. Called by PersistentAgent every 15 min.

        Trend direction is set by whether the most recent candle is closer
        to the swing high (uptrend in progress, measuring pullback levels)
        or the swing low (downtrend, measuring bounce levels).
        """
        if len(candles) < 20:
            return

        bars = candles[-lookback:] if len(candles) >= lookback else candles
        highs  = [c["high"]  for c in bars]
        lows   = [c["low"]   for c in bars]
        closes = [c["close"] for c in bars]

        swing_high = max(highs)
        swing_low  = min(lows)
        rng = swing_high - swing_low

        if rng <= 0:
            return

        current = closes[-1]

        # Determine trend: if price is in the upper half → uptrend, measure pullbacks FROM high
        # If in the lower half → downtrend, measure bounces FROM low
        upper_half = current > (swing_low + rng * 0.5)
        self._fib_trend = "up" if upper_half else "down"

        if self._fib_trend == "up":
            # Retracement levels below swing high (support on pullback)
            self._fib_levels = {
                k: round(swing_high - r * rng, 2)
                for k, r in self.FIB_RATIOS.items()
            }
        else:
            # Retracement levels above swing low (resistance on bounce)
            self._fib_levels = {
                k: round(swing_low + r * rng, 2)
                for k, r in self.FIB_RATIOS.items()
            }

        self._fib_swing_high = swing_high
        self._fib_swing_low  = swing_low
        self._fib_updated_at = time.time()

        logger.debug(
            f"[MasterBrain] Fib levels ({self._fib_trend}trend) — "
            f"high ${swing_high:,.0f} / low ${swing_low:,.0f} / range ${rng:,.0f} — "
            f"61.8%: ${self._fib_levels.get('61.8', 0):,.0f}"
        )

    def _fib_bias(self, live_price: float, direction: str, atr_usd: float) -> tuple[float, str]:
        """
        Return (score_multiplier, reason_string) based on proximity to Fib levels.

        Logic:
        • Uptrend: price near 38.2/50/61.8 support → long bias ✅ | short bias ❌
        • Downtrend: price near 38.2/50/61.8 resistance → short bias ✅ | long bias ❌
        • 61.8% (golden ratio) gets the strongest weight
        • 78.6% is a deep retracement — extra caution on both sides
        • 23.6% is shallow — mild bonus if trading with trend
        """
        if not self._fib_levels or live_price <= 0 or atr_usd <= 0:
            return 1.0, ""

        tol = atr_usd * self.FIB_TOLERANCE_ATR_MULT

        # Level weights: golden ratio strongest
        weights = {"23.6": 0.5, "38.2": 1.0, "50.0": 1.0, "61.8": 1.5, "78.6": 0.7}
        nearest_level: Optional[str] = None
        nearest_dist  = float("inf")

        for lvl_name, lvl_price in self._fib_levels.items():
            dist = abs(live_price - lvl_price)
            if dist < nearest_dist:
                nearest_dist  = dist
                nearest_level = lvl_name

        if nearest_level is None or nearest_dist > tol * 3:
            return 1.0, ""   # too far from any level

        w = weights.get(nearest_level, 1.0)
        proximity = max(0.0, 1.0 - (nearest_dist / (tol * 3)))  # 1.0 = exact, 0 = at 3×ATR
        effect_mag = proximity * w * 0.15   # max ±15% per level at exact hit, weighted

        trend_up = self._fib_trend == "up"

        # Uptrend: Fib levels are support → favour longs near levels, penalise shorts
        # Downtrend: Fib levels are resistance → favour shorts near levels, penalise longs
        if nearest_dist <= tol:  # within tolerance — strong effect
            if trend_up and direction == "long":
                mult = 1.0 + effect_mag
                reason = f"Fib {nearest_level}% support ${self._fib_levels[nearest_level]:,.0f} — long confluence ✅"
                return round(mult, 3), reason
            elif trend_up and direction == "short":
                mult = 1.0 - effect_mag
                reason = f"shorting at Fib {nearest_level}% support — counter-trend ⚠️"
                return round(mult, 3), reason
            elif not trend_up and direction == "short":
                mult = 1.0 + effect_mag
                reason = f"Fib {nearest_level}% resistance ${self._fib_levels[nearest_level]:,.0f} — short confluence ✅"
                return round(mult, 3), reason
            elif not trend_up and direction == "long":
                mult = 1.0 - effect_mag
                reason = f"longing at Fib {nearest_level}% resistance — counter-trend ⚠️"
                return round(mult, 3), reason

        # Near but not within tight tolerance — mild proximity effect
        if trend_up and direction == "long" and nearest_dist <= tol * 2:
            return round(1.0 + effect_mag * 0.5, 3), f"approaching Fib {nearest_level}% support"
        if not trend_up and direction == "short" and nearest_dist <= tol * 2:
            return round(1.0 + effect_mag * 0.5, 3), f"approaching Fib {nearest_level}% resistance"

        return 1.0, ""

    def fib_status(self) -> dict:
        """Return current Fibonacci levels for API/dashboard display."""
        age_min = round((time.time() - self._fib_updated_at) / 60, 1) if self._fib_updated_at else None
        return {
            "levels":      self._fib_levels,
            "swing_high":  self._fib_swing_high,
            "swing_low":   self._fib_swing_low,
            "trend":       self._fib_trend,
            "updated_min_ago": age_min,
        }

    # ══════════════════════════════════════════════════════════════════════
    #  FEATURE A: LIQUIDITY SWEEP DETECTION
    # ══════════════════════════════════════════════════════════════════════

    def detect_liquidity_sweep(self, candles: list[dict], lookback: int = 50) -> None:
        """
        Detect if price recently swept a significant swing high/low (stop hunt)
        then closed back inside the range — classic Smart Money reversal setup.

        A sweep is defined as: wick beyond the previous swing level + candle
        closes back inside the range. High-probability reversal entry AFTER
        the sweep in the opposite direction of the sweep.
        """
        self._liq_sweep = {}
        bars = candles[-lookback:] if len(candles) >= lookback else candles
        if len(bars) < 10:
            return

        # Find the most significant swing high/low in bars[:-3]
        body = bars[:-3]
        if not body:
            return
        swing_h = max(c["high"]  for c in body)
        swing_l = min(c["low"]   for c in body)

        # Check last 3 candles for a sweep
        for i, c in enumerate(bars[-3:], 1):
            high, low, close = c["high"], c["low"], c["close"]
            # Bullish sweep: wick below swing low then closes back above it
            if low < swing_l and close > swing_l:
                self._liq_sweep = {
                    "direction": "bullish",   # sweep was down, expect reversal UP
                    "level":     round(swing_l, 2),
                    "bars_ago":  3 - i + 1,
                    "close":     round(close, 2),
                }
                break
            # Bearish sweep: wick above swing high then closes back below it
            if high > swing_h and close < swing_h:
                self._liq_sweep = {
                    "direction": "bearish",   # sweep was up, expect reversal DOWN
                    "level":     round(swing_h, 2),
                    "bars_ago":  3 - i + 1,
                    "close":     round(close, 2),
                }
                break

        if self._liq_sweep:
            d = self._liq_sweep
            logger.debug(
                f"[MasterBrain] Liquidity sweep {d['direction'].upper()} at "
                f"${d['level']:,.0f} ({d['bars_ago']} bars ago)"
            )

    # ══════════════════════════════════════════════════════════════════════
    #  FEATURE B: MARKET STRUCTURE (BOS / CHOCH)
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _find_swings(highs: list[float], lows: list[float], pivot_len: int = 3) -> tuple[list[float], list[float]]:
        """Return lists of significant swing highs and lows using pivot detection."""
        sh: list[float] = []
        sl: list[float] = []
        n = len(highs)
        for i in range(pivot_len, n - pivot_len):
            if all(highs[i] >= highs[i - j] and highs[i] >= highs[i + j] for j in range(1, pivot_len + 1)):
                sh.append(highs[i])
            if all(lows[i] <= lows[i - j] and lows[i] <= lows[i + j] for j in range(1, pivot_len + 1)):
                sl.append(lows[i])
        return sh[-6:], sl[-6:]   # keep last 6 each

    def detect_market_structure(self, candles15m: list[dict], candles1h: list[dict]) -> None:
        """
        Detect Break of Structure (BOS) and Change of Character (CHOCH) on 15m.
        BOS  = continuation: price closes above last swing high (bullish) or below low (bearish)
        CHOCH = reversal signal: first BOS against prior structure direction.
        """
        self._mss = {}
        bars = candles15m[-60:] if len(candles15m) >= 60 else candles15m
        if len(bars) < 15:
            return

        highs  = [c["high"]  for c in bars]
        lows   = [c["low"]   for c in bars]
        closes = [c["close"] for c in bars]

        sh, sl = self._find_swings(highs, lows)
        self._swing_highs = sh
        self._swing_lows  = sl

        if not sh or not sl:
            return

        last_close = closes[-1]
        prev_sh    = sh[-1]
        prev_sl    = sl[-1]

        # Determine prior structure direction from last 2 swing points
        if len(sh) >= 2 and len(sl) >= 2:
            prior_bull = sh[-1] > sh[-2] and sl[-1] > sl[-2]
            prior_bear = sh[-1] < sh[-2] and sl[-1] < sl[-2]
        else:
            prior_bull = prior_bear = False

        # BOS / CHOCH detection
        if last_close > prev_sh:
            stype = "BOS" if prior_bull else "CHOCH"
            self._mss = {"type": stype, "direction": "bullish", "level": round(prev_sh, 2)}
        elif last_close < prev_sl:
            stype = "BOS" if prior_bear else "CHOCH"
            self._mss = {"type": stype, "direction": "bearish", "level": round(prev_sl, 2)}

        if self._mss:
            m = self._mss
            logger.debug(f"[MasterBrain] Market structure: {m['type']} {m['direction'].upper()} at ${m['level']:,.0f}")

    # ══════════════════════════════════════════════════════════════════════
    #  FEATURE C: VOLUME ANOMALY DETECTOR
    # ══════════════════════════════════════════════════════════════════════

    def detect_volume_anomaly(self, candles: list[dict], ma_period: int = 20, spike_mult: float = 2.0) -> None:
        """
        Flag when the most recent closed candle has volume ≥ 2× the rolling
        20-bar average. Direction determined by whether close > open (bullish bar).
        """
        self._vol_spike     = False
        self._vol_spike_dir = ""
        if len(candles) < ma_period + 1:
            return

        bars   = candles[-(ma_period + 1):]
        closed = bars[-1]   # most recently closed candle
        avg_vol = sum(c.get("volume", 0) for c in bars[:-1]) / ma_period

        if avg_vol <= 0:
            return

        vol = closed.get("volume", 0)
        if vol >= avg_vol * spike_mult:
            self._vol_spike     = True
            self._vol_spike_dir = "bullish" if closed["close"] >= closed["open"] else "bearish"
            logger.debug(
                f"[MasterBrain] Volume spike {vol/avg_vol:.1f}× avg — {self._vol_spike_dir.upper()}"
            )

    # ══════════════════════════════════════════════════════════════════════
    #  FEATURE D: HTF PIVOT POINTS (weekly + monthly)
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _calc_pivots(high: float, low: float, close: float) -> dict[str, float]:
        pp = (high + low + close) / 3
        rng = high - low
        return {
            "PP": round(pp, 2),
            "R1": round(2 * pp - low, 2),   "S1": round(2 * pp - high, 2),
            "R2": round(pp + rng, 2),        "S2": round(pp - rng, 2),
            "R3": round(high + 2*(pp-low), 2), "S3": round(low - 2*(high-pp), 2),
        }

    def compute_htf_pivots(self, candles1h: list[dict], candles4h: list[dict]) -> None:
        """
        Compute weekly (from 4h bars) and monthly (from 1h bars) classic pivot points.
        Called once per hour by the market context loop.
        """
        now = time.time()

        # Weekly pivots: last 7 × 4h candles ≈ past week
        if candles4h and len(candles4h) >= 42:
            week_bars = candles4h[-42:]
            wh = max(c["high"]  for c in week_bars)
            wl = min(c["low"]   for c in week_bars)
            wc = week_bars[-1]["close"]
            self._weekly_pivots = self._calc_pivots(wh, wl, wc)

        # Monthly pivots: last 30 × 1h candles ≈ past 5 weeks
        if candles1h and len(candles1h) >= 720:
            month_bars = candles1h[-720:]
            mh = max(c["high"]  for c in month_bars)
            ml = min(c["low"]   for c in month_bars)
            mc = month_bars[-1]["close"]
            self._monthly_pivots = self._calc_pivots(mh, ml, mc)

        self._pivots_updated = now

    def _pivot_bias(self, live_price: float, direction: str, atr_usd: float) -> tuple[float, str]:
        """
        Score signal vs proximity to weekly/monthly pivot points.
        Approaching R-levels on a long → penalty; approaching S-levels on short → penalty.
        """
        if live_price <= 0 or atr_usd <= 0:
            return 1.0, ""

        tol = atr_usd * 1.5   # within 1.5 ATR = "near" a pivot
        best_mult = 1.0
        best_reason = ""

        all_pivots: list[tuple[str, str, float]] = []
        for label, price in self._weekly_pivots.items():
            all_pivots.append(("W", label, price))
        for label, price in self._monthly_pivots.items():
            all_pivots.append(("M", label, price))

        for tf, label, piv_price in all_pivots:
            dist = abs(live_price - piv_price)
            if dist > tol * 2:
                continue
            proximity = max(0.0, 1.0 - dist / (tol * 2))

            if label == "PP":
                continue   # neutral at PP

            is_resistance = label.startswith("R")
            is_support    = label.startswith("S")

            if is_resistance and direction == "long":
                mult   = 1.0 - 0.12 * proximity
                reason = f"approaching {tf} {label} pivot ${piv_price:,.0f} — resistance"
            elif is_support and direction == "short":
                mult   = 1.0 - 0.12 * proximity
                reason = f"approaching {tf} {label} pivot ${piv_price:,.0f} — support"
            elif is_support and direction == "long":
                mult   = 1.0 + 0.10 * proximity
                reason = f"{tf} {label} pivot ${piv_price:,.0f} — long confluence"
            elif is_resistance and direction == "short":
                mult   = 1.0 + 0.10 * proximity
                reason = f"{tf} {label} pivot ${piv_price:,.0f} — short confluence"
            else:
                continue

            # Keep only the strongest effect
            if abs(mult - 1.0) > abs(best_mult - 1.0):
                best_mult   = round(mult, 3)
                best_reason = reason

        return best_mult, best_reason

    # ══════════════════════════════════════════════════════════════════════
    #  FEATURE E: RSI DIVERGENCE FILTER
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _rsi(closes: list[float], period: int = 14) -> list[float]:
        """Compute RSI series from close prices."""
        if len(closes) < period + 1:
            return []
        gains, losses = [], []
        for i in range(1, len(closes)):
            delta = closes[i] - closes[i - 1]
            gains.append(max(0.0, delta))
            losses.append(max(0.0, -delta))
        if len(gains) < period:
            return []
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        rsi_vals: list[float] = []
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            rs  = avg_gain / avg_loss if avg_loss > 0 else 100
            rsi = 100 - 100 / (1 + rs)
            rsi_vals.append(round(rsi, 2))
        return rsi_vals

    def detect_rsi_divergence(self, candles: list[dict], period: int = 14, lookback: int = 40) -> None:
        """
        Detect RSI divergence on the last `lookback` candles.
        Bullish div: price making lower low but RSI making higher low → expect UP.
        Bearish div: price making higher high but RSI making lower high → expect DOWN.
        """
        self._rsi_divergence = ""
        bars = candles[-lookback:] if len(candles) >= lookback else candles
        if len(bars) < period + 10:
            return

        closes = [c["close"] for c in bars]
        highs  = [c["high"]  for c in bars]
        lows   = [c["low"]   for c in bars]
        rsi    = self._rsi(closes, period)

        if len(rsi) < 10:
            return

        # Compare last two comparable extremes (simple 5-bar split)
        mid = len(rsi) // 2
        # Bearish divergence: higher price high, lower RSI high
        price_h1 = max(highs[:mid])
        price_h2 = max(highs[mid:])
        rsi_h1   = max(rsi[:mid])
        rsi_h2   = max(rsi[mid:])
        if price_h2 > price_h1 * 1.001 and rsi_h2 < rsi_h1 - 2:
            self._rsi_divergence = "bearish"
            logger.debug(f"[MasterBrain] RSI bearish divergence: price ↑${price_h2:,.0f} RSI ↓{rsi_h2:.1f}")
            return

        # Bullish divergence: lower price low, higher RSI low
        price_l1 = min(lows[:mid])
        price_l2 = min(lows[mid:])
        rsi_l1   = min(rsi[:mid])
        rsi_l2   = min(rsi[mid:])
        if price_l2 < price_l1 * 0.999 and rsi_l2 > rsi_l1 + 2:
            self._rsi_divergence = "bullish"
            logger.debug(f"[MasterBrain] RSI bullish divergence: price ↓${price_l2:,.0f} RSI ↑{rsi_l2:.1f}")

    # ══════════════════════════════════════════════════════════════════════
    #  FEATURE F: NEWS EVENT BLACKOUT
    # ══════════════════════════════════════════════════════════════════════

    def _is_macro_blackout(self) -> tuple[bool, str]:
        """
        Return (True, reason) if current time falls within ±90 minutes of a
        known high-impact macro event (FOMC decision, CPI release).
        """
        now = datetime.now(timezone.utc)
        # FOMC check (±90 min of hardcoded dates/hours)
        for month, day, hour, label in _MACRO_BLACKOUT_WINDOWS:
            if now.month == month and abs(now.day - day) <= 1:
                diff_h = abs(now.hour - hour)
                if diff_h <= 1:   # within 1 hour on each side
                    return True, f"{label} blackout ({now.strftime('%b %d %H:%M')} UTC)"

        # CPI: ~2nd Tuesday each month, 12:30 UTC → blackout 12:00–14:00
        if now.weekday() == 1 and 7 <= now.day <= 14:   # Tuesday, 2nd week
            if _CPI_HOUR_UTC <= now.hour < _CPI_HOUR_UTC + 2:
                return True, f"CPI release blackout ({now.strftime('%H:%M')} UTC)"

        return False, ""

    # ══════════════════════════════════════════════════════════════════════
    #  FEATURE G: BAYESIAN WIN RATE (Beta distribution)
    # ══════════════════════════════════════════════════════════════════════

    def _bayes_trust_score(self, strategy_key: str) -> float:
        """
        Compute Bayesian trust from Beta(alpha, beta) where alpha = wins+prior,
        beta = losses+prior. Returns posterior mean = alpha / (alpha + beta).
        Scaled to [0.2, 2.0] to match EMA trust range.
        Blended 40% Bayes / 60% EMA for stability with small samples.
        """
        b = self._bayes_trust.get(strategy_key)
        if not b:
            return self.strategy_trust.get(strategy_key, 1.0)

        alpha = b.get("alpha", 2.0)   # prior: 2 pseudo-wins
        beta  = b.get("beta",  2.0)   # prior: 2 pseudo-losses
        posterior_mean = alpha / (alpha + beta)   # 0.0 – 1.0

        # Map posterior_mean [0,1] → trust [0.2, 2.0]
        bayes_t = 0.2 + posterior_mean * 1.8
        ema_t   = self.strategy_trust.get(strategy_key, 1.0)

        # Blend: weight Bayesian more as sample grows
        n       = alpha + beta - 4   # subtract priors
        w_bayes = min(0.6, n / 50)   # ramps to 60% at 50 trades
        blended = ema_t * (1 - w_bayes) + bayes_t * w_bayes
        return round(max(0.20, min(2.0, blended)), 3)

    def _update_bayes_trust(self, strategy_key: str, won: bool) -> None:
        if strategy_key not in self._bayes_trust:
            self._bayes_trust[strategy_key] = {"alpha": 2.0, "beta": 2.0}
        b = self._bayes_trust[strategy_key]
        if won:
            b["alpha"] = round(b["alpha"] + 1.0, 3)
        else:
            b["beta"]  = round(b["beta"]  + 1.0, 3)

    # ══════════════════════════════════════════════════════════════════════
    #  FEATURE H: ORDER FLOW IMBALANCE AT FIB / PIVOT LEVELS
    # ══════════════════════════════════════════════════════════════════════

    def _orderflow_fib_bias(
        self, live_price: float, direction: str, orderbook: Optional[dict], atr_usd: float
    ) -> tuple[float, str]:
        """
        When price is near a key Fib or pivot level, check the order book for
        bid/ask imbalance.  Strong bid wall at support → bullish OFI confirmation.
        Strong ask wall at resistance → bearish OFI confirmation.
        """
        if not orderbook or live_price <= 0 or atr_usd <= 0:
            return 1.0, ""

        tol = atr_usd * 1.2   # near = within 1.2 ATR

        # Determine if price is near a key Fib level or HTF pivot
        near_support = near_resistance = False
        for lvl_name, lvl_price in self._fib_levels.items():
            if abs(live_price - lvl_price) > tol:
                continue
            if self._fib_trend == "up":
                near_support    = True
            else:
                near_resistance = True
            break

        if not near_support and not near_resistance:
            for _, label, piv_price in [
                ("W", lbl, p) for lbl, p in self._weekly_pivots.items()
            ] + [
                ("M", lbl, p) for lbl, p in self._monthly_pivots.items()
            ]:
                if abs(live_price - piv_price) > tol:
                    continue
                if label.startswith("S"):
                    near_support    = True
                elif label.startswith("R"):
                    near_resistance = True
                break

        if not near_support and not near_resistance:
            return 1.0, ""

        # Aggregate bid/ask volume within 0.5% of price
        bids: list[list] = orderbook.get("bids", [])[:20]
        asks: list[list] = orderbook.get("asks", [])[:20]
        price_window = live_price * 0.005

        bid_vol = sum(float(v) for p, v in bids if live_price - price_window <= float(p) <= live_price)
        ask_vol = sum(float(v) for p, v in asks if live_price <= float(p) <= live_price + price_window)
        total   = bid_vol + ask_vol

        if total < 0.01:
            return 1.0, ""

        bid_ratio = bid_vol / total    # > 0.6 = bid wall, < 0.4 = ask wall

        if near_support and bid_ratio >= 0.60 and direction == "long":
            mult   = 1.0 + 0.12 * ((bid_ratio - 0.60) / 0.40)
            return round(min(1.12, mult), 3), f"OFI bid wall at support ({bid_ratio:.0%} bids) — long"
        if near_resistance and bid_ratio <= 0.40 and direction == "short":
            ask_ratio = 1 - bid_ratio
            mult      = 1.0 + 0.12 * ((ask_ratio - 0.60) / 0.40)
            return round(min(1.12, mult), 3), f"OFI ask wall at resistance ({ask_ratio:.0%} asks) — short"
        if near_support and bid_ratio <= 0.35 and direction == "long":
            return 0.88, "weak OFI at support (ask-dominated) — caution long"
        if near_resistance and bid_ratio >= 0.65 and direction == "short":
            return 0.88, "weak OFI at resistance (bid-dominated) — caution short"

        return 1.0, ""

    # ══════════════════════════════════════════════════════════════════════
    #  CORE DECISION: should we take this trade?
    # ══════════════════════════════════════════════════════════════════════

    def evaluate_signal(
        self,
        strategy_key: str,
        strategy_name: str,
        signal: dict,
        open_positions: dict,
        live_price: float,
        portfolio_pnl: float,
        is_live: bool = False,
        orderbook: Optional[dict] = None,
    ) -> dict:
        self._check_day()
        reasons = []
        score = 1.0

        direction  = signal.get("direction", "long")
        confidence = signal.get("confidence", 0.5)
        now_hour   = datetime.now(timezone.utc).hour
        session    = _current_session()

        # ── FEATURE F: News event blackout ───────────────────────────────
        blackout, blackout_reason = self._is_macro_blackout()
        if blackout and is_live:
            return self._reject(strategy_key, strategy_name, signal,
                                f"Macro event blackout — {blackout_reason}")
        if blackout:
            score *= 0.55
            reasons.append(f"macro event window ({blackout_reason[:40]})")

        # ── FEATURE 1: Macro trend filter ────────────────────────────────
        # Hard gate for live: never fight the higher-timeframe trend
        if is_live and self.macro_confidence >= 0.6:
            if self.macro_trend == "bullish" and direction == "short":
                return self._reject(strategy_key, strategy_name, signal,
                                    f"Macro trend BULLISH ({self.macro_confidence:.0%}) — no shorts")
            if self.macro_trend == "bearish" and direction == "long":
                return self._reject(strategy_key, strategy_name, signal,
                                    f"Macro trend BEARISH ({self.macro_confidence:.0%}) — no longs")

        # Macro alignment bonus / penalty for all modes
        if self.macro_trend == "bullish" and direction == "long":
            score *= 1.20
            reasons.append(f"macro bullish ({self.macro_confidence:.0%})")
        elif self.macro_trend == "bearish" and direction == "short":
            score *= 1.20
            reasons.append(f"macro bearish ({self.macro_confidence:.0%})")
        elif self.macro_trend != "neutral" and self.macro_confidence >= 0.5:
            score *= 0.75
            reasons.append(f"against macro trend ({self.macro_trend})")

        # ── FEATURE 4: Time-of-day session penalty ───────────────────────
        if now_hour in DEAD_HOURS:
            score *= 0.50
            reasons.append(f"dead hour {now_hour}:00 UTC (minimal liquidity)")
            if is_live:
                return self._reject(strategy_key, strategy_name, signal,
                                    f"Dead hour {now_hour}:00 UTC — no live trades (minimal liquidity)")
        elif now_hour in LOW_VOLUME_HOURS:
            score *= 0.80
            reasons.append(f"low-volume hour {now_hour}:00 UTC")

        # Session affinity: learned win rate by UTC hour
        h_stats = self._hour_stats.get(now_hour, {"trades": 0, "win_rate": 0.5})
        if h_stats["trades"] >= 30:  # require 30 trades for reliable signal (was 8)
            session_wr = h_stats["win_rate"]
            if session_wr < 0.35:
                # Hard block for live after 30+ trades with poor edge
                if is_live:
                    return self._reject(strategy_key, strategy_name, signal,
                                        f"Hour {now_hour}:00 UTC blocked — {session_wr:.0%} win rate "
                                        f"over {h_stats['trades']} trades (need ≥35%)")
                score *= 0.60
                reasons.append(f"poor hourly win rate ({session_wr:.0%} at {now_hour}:00 UTC)")
            elif session_wr > 0.60:
                score *= 1.15
                reasons.append(f"strong hourly win rate ({session_wr:.0%} at {now_hour}:00 UTC)")
        elif h_stats["trades"] >= 8:
            session_wr = h_stats["win_rate"]
            if session_wr < 0.35:
                score *= 0.65
                reasons.append(f"poor hourly win rate ({session_wr:.0%} at {now_hour}:00 UTC — {h_stats['trades']} trades)")

        # ── Factor 1: Strategy trust (exponential EMA) ───────────────────
        trust = self.strategy_trust.get(strategy_key, 1.0)
        score *= trust
        if trust < 0.6:
            reasons.append(f"low trust ({trust:.2f})")
        elif trust > 1.2:
            reasons.append(f"high trust ({trust:.2f})")

        # ── Factor 2: Regime affinity (static + learned blend) ──────────
        base_affinity    = self.REGIME_AFFINITY.get(self.current_regime, {}).get(strategy_key, 1.0)
        learned_affinity = self._learned_affinity.get(self.current_regime, {}).get(strategy_key, 1.0)
        affinity = round(0.5 * base_affinity + 0.5 * learned_affinity, 4)
        score *= affinity
        if affinity < 0.7:
            reasons.append(f"{strategy_key} weak in {self.current_regime} regime")
        elif affinity > 1.2:
            reasons.append(f"{strategy_key} strong in {self.current_regime} regime")

        # ── Factor 3: Regime stability ────────────────────────────────────
        if len(self._regime_history) >= 5:
            unique_recent = len(set(self._regime_history[-5:]))
            if unique_recent >= 3:
                score *= 0.7
                reasons.append("regime unstable (3+ changes in 5 readings)")

        # ── Factor 4: Directional lock + confluence ───────────────────────
        same_dir_count = opposite_dir_count = live_dir_count = live_opposite_count = 0
        for k, pos in open_positions.items():
            if not pos:
                continue
            if k.startswith("shadow_") or k.startswith("paper_"):
                continue
            if isinstance(pos, dict) and (pos.get("is_shadow") or pos.get("mode") == "paper_trader"):
                continue
            if pos.get("direction") == direction:
                same_dir_count += 1
                if pos.get("mode") == "live" or pos.get("is_live"):
                    live_dir_count += 1
            else:
                opposite_dir_count += 1
                if pos.get("mode") == "live" or pos.get("is_live"):
                    live_opposite_count += 1

        if is_live and live_opposite_count > 0:
            return self._reject(strategy_key, strategy_name, signal,
                                f"Direction conflict — {live_opposite_count} live pos in opposite direction")
        if opposite_dir_count >= 1 and is_live:
            return self._reject(strategy_key, strategy_name, signal,
                                f"Directional lock — {opposite_dir_count} pos in opposite direction")

        if same_dir_count >= 2:
            score *= 1.15
            reasons.append(f"{same_dir_count} positions confirm {direction}")
        if opposite_dir_count >= 2:
            score *= 0.5
            reasons.append(f"{opposite_dir_count} positions oppose — strong conflict")

        # ── Factor 5: Risk gates ──────────────────────────────────────────
        total_open = sum(
            1 for k, v in open_positions.items()
            if v and not k.startswith("shadow_") and not (isinstance(v, dict) and v.get("is_shadow"))
        )
        if total_open >= self.MAX_OPEN_POSITIONS:
            return self._reject(strategy_key, strategy_name, signal,
                                f"Max {self.MAX_OPEN_POSITIONS} positions reached")
        if is_live and self.daily_trades >= self.MAX_DAILY_TRADES:
            return self._reject(strategy_key, strategy_name, signal,
                                f"Daily live trade limit ({self.daily_trades}/{self.MAX_DAILY_TRADES})")
        if self.consecutive_losses >= self.MAX_CONSECUTIVE_LOSSES:
            score *= 0.3
            reasons.append(f"⚠ {self.consecutive_losses} consecutive losses — caution mode")
        if is_live and self.daily_pnl <= self.MAX_DAILY_LOSS:
            return self._reject(strategy_key, strategy_name, signal,
                                f"Daily loss limit (${self.daily_pnl:.0f} ≤ ${self.MAX_DAILY_LOSS:.0f})")

        # ── Factor 6: Signal quality ──────────────────────────────────────
        score *= (0.7 + confidence * 0.6)
        if confidence < 0.55:
            reasons.append("low signal confidence")
        elif confidence > 0.75:
            reasons.append("strong signal confidence")

        # ── Factor 6b: R:R ratio ──────────────────────────────────────────
        sig_entry = signal.get("entry", live_price) or live_price
        sig_sl    = signal.get("sl", 0) or 0
        sig_tp    = signal.get("tp", 0) or 0
        rr_ratio  = 0.0
        if sig_sl and sig_tp and sig_entry > 0:
            sl_dist  = abs(sig_entry - sig_sl)
            tp_dist  = abs(sig_tp - sig_entry)
            rr_ratio = tp_dist / sl_dist if sl_dist > 0 else 0
            if is_live and rr_ratio < 1.5:
                return self._reject(strategy_key, strategy_name, signal,
                                    f"Poor R:R ({rr_ratio:.1f}:1 < 1.5:1)")
            elif rr_ratio < 1.0:
                score *= 0.6
                reasons.append(f"weak R:R ({rr_ratio:.1f}:1)")
            elif rr_ratio >= 2.0:
                score *= 1.1
                reasons.append(f"excellent R:R ({rr_ratio:.1f}:1)")

        # ── Factor 6c: Regime-direction alignment ─────────────────────────
        if is_live:
            if self.current_regime == "trending_up" and direction == "short" and self.regime_confidence > 0.5:
                return self._reject(strategy_key, strategy_name, signal,
                                    f"Shorting against uptrend ({self.regime_confidence:.0%})")
            elif self.current_regime == "trending_down" and direction == "long" and self.regime_confidence > 0.5:
                return self._reject(strategy_key, strategy_name, signal,
                                    f"Longing against downtrend ({self.regime_confidence:.0%})")

        if self.current_regime == "trending_up" and direction == "long":
            score *= 1.15; reasons.append("with uptrend")
        elif self.current_regime == "trending_down" and direction == "short":
            score *= 1.15; reasons.append("with downtrend")
        elif self.current_regime in ("trending_up", "trending_down"):
            score *= 0.75; reasons.append("against 15m trend")

        # ── FEATURE 7: Funding rate bias ──────────────────────────────────
        # Extreme funding = crowded positioning, near reversal territory
        # Hard reject for live when funding is extreme and trade goes with crowd
        if is_live and self.funding_rate > 0.0015 and direction == "long":
            return self._reject(strategy_key, strategy_name, signal,
                                f"Funding rate extreme LONG ({self.funding_rate:.4%}) — longs dangerously crowded")
        if is_live and self.funding_rate < -0.0015 and direction == "short":
            return self._reject(strategy_key, strategy_name, signal,
                                f"Funding rate extreme SHORT ({self.funding_rate:.4%}) — shorts dangerously crowded")
        if abs(self.funding_rate) >= 0.0008:  # ≥ 0.08% per 8h (crowded)
            if self.funding_rate > 0 and direction == "long":
                score *= 0.78
                reasons.append(f"high positive funding ({self.funding_rate:.4%}) — longs crowded")
            elif self.funding_rate < 0 and direction == "short":
                score *= 0.78
                reasons.append(f"high negative funding ({self.funding_rate:.4%}) — shorts crowded")
            # Contrarian bonus: trading against the crowd
            if self.funding_rate > 0.001 and direction == "short":
                score *= 1.12
                reasons.append("contrarian short vs extreme long funding ✅")
            elif self.funding_rate < -0.001 and direction == "long":
                score *= 1.12
                reasons.append("contrarian long vs extreme short funding ✅")

        # ── FEATURE 9: Fear & Greed bias ─────────────────────────────────
        fg = self.fear_greed_score
        if fg <= 20:   # Extreme Fear — historical buy zone
            if direction == "long":
                score *= 1.15
                reasons.append(f"extreme fear ({fg}) — contrarian long bonus")
            if is_live and direction == "short":
                score *= 0.75
                reasons.append(f"shorting in extreme fear zone ({fg}) — risky")
        elif fg >= 80:  # Extreme Greed — historical sell zone
            if direction == "short":
                score *= 1.10
                reasons.append(f"extreme greed ({fg}) — contrarian short bonus")
            if is_live and direction == "long":
                score *= 0.80
                reasons.append(f"longing in extreme greed zone ({fg}) — risky")

        # ── FEATURE 2: Profit factor gate ────────────────────────────────
        stats = self.strategy_stats.get(strategy_key, {})
        total_trades = stats.get("trades", 0)
        win_rate  = stats.get("win_rate", 0.5)
        avg_win   = stats.get("avg_win", 1.0)
        avg_loss  = abs(stats.get("avg_loss", -1.0)) or 1.0
        gross_win  = stats.get("gross_wins", 0.0)
        gross_loss = abs(stats.get("gross_losses", 0.0))
        profit_factor = (gross_win / gross_loss) if gross_loss > 0 else 1.5

        if is_live and total_trades >= self.MIN_PAPER_TRADES_FOR_LIVE:
            if profit_factor < self.MIN_PROFIT_FACTOR_FOR_LIVE:
                return self._reject(strategy_key, strategy_name, signal,
                                    f"Profit factor too low ({profit_factor:.2f} < {self.MIN_PROFIT_FACTOR_FOR_LIVE})")

        # ── Factor 7: Correlation penalty ────────────────────────────────
        size_mult = 1.0
        if same_dir_count >= 2:
            size_mult *= self.CORRELATION_PENALTY
            reasons.append(f"size reduced — {same_dir_count} correlated positions")

        # ── Factor 8: Kelly-fraction sizing ──────────────────────────────
        if total_trades >= 5 and win_rate > 0 and avg_loss > 0:
            kelly = win_rate - (1 - win_rate) / (avg_win / avg_loss) if avg_win > 0 else 0
            kelly_frac = max(0.1, min(0.5, kelly * 0.5))
            size_mult *= (0.5 + kelly_frac)
            if win_rate > 0.6:
                reasons.append(f"high win rate ({win_rate:.0%}) → size boost")
            elif win_rate < 0.4:
                reasons.append(f"low win rate ({win_rate:.0%}) → size cut")

        # ── FEATURE 3: Volatility-adjusted sizing ────────────────────────
        if self._baseline_atr_pct > 0 and self.current_atr_pct > 0:
            atr_ratio = self._baseline_atr_pct / self.current_atr_pct
            # Cap: don't size up more than 1.5× or down less than 0.4× of base
            atr_scale = max(0.40, min(1.50, atr_ratio))
            if abs(atr_scale - 1.0) > 0.1:
                size_mult *= atr_scale
                if atr_scale < 0.8:
                    reasons.append(f"size reduced — ATR {self.current_atr_pct:.3%} > baseline {self._baseline_atr_pct:.3%}")
                elif atr_scale > 1.2:
                    reasons.append(f"size boosted — low volatility (ATR {self.current_atr_pct:.3%})")

        # ── FEATURE 8: Drawdown-based size reduction ──────────────────────
        current_dd = stats.get("current_drawdown", 0.0)
        max_dd     = stats.get("max_drawdown", 0.0)
        if max_dd > 0 and current_dd >= 0.5 * max_dd:
            dd_scale = max(0.4, 1.0 - (current_dd / max_dd) * 0.6)
            size_mult *= dd_scale
            reasons.append(f"in drawdown (${current_dd:.2f}/{max_dd:.2f} max) → size {dd_scale:.0%}")

        # ── Factor 9: LIVE-ONLY adjustments ──────────────────────────────
        if is_live:
            if self.consecutive_losses >= 3:
                return self._reject(strategy_key, strategy_name, signal,
                                    f"Live paused — {self.consecutive_losses} consecutive losses")
            if trust < 0.5:
                return self._reject(strategy_key, strategy_name, signal,
                                    f"Trust too low for live ({trust:.2f} < 0.50)")
            if total_trades < self.MIN_PAPER_TRADES_FOR_LIVE:
                history_scale = max(0.3, total_trades / self.MIN_PAPER_TRADES_FOR_LIVE)
                size_mult *= history_scale
                reasons.append(f"new strategy ({total_trades} trades) → size {history_scale:.0%}")
            elif win_rate < self.MIN_WIN_RATE_FOR_LIVE and total_trades >= self.MIN_PAPER_TRADES_FOR_LIVE:
                size_mult *= 0.5
                reasons.append(f"low win rate ({win_rate:.0%}) → size halved for live")
            else:
                reasons.append("✓ live-qualified")

        # ── FEATURE 10: Trade duration warning ───────────────────────────
        avg_loss_dur = stats.get("avg_loss_duration_min", 0.0)
        avg_win_dur  = stats.get("avg_win_duration_min", 0.0)
        if avg_loss_dur > 0 and avg_win_dur > 0 and avg_loss_dur > avg_win_dur * 2.5:
            # Losses held much longer than wins — probable discipline issue
            size_mult *= 0.80
            reasons.append(f"losses held {avg_loss_dur:.0f}m vs wins {avg_win_dur:.0f}m — size trimmed")

        # ── FEATURE B: Market structure (BOS / CHOCH) ────────────────────
        if self._mss:
            mss_dir = self._mss.get("direction", "")
            mss_type = self._mss.get("type", "")
            if mss_dir == direction:
                mult = 1.18 if mss_type == "CHOCH" else 1.10
                reasons.append(f"{mss_type} {mss_dir.upper()} — structure aligned ✅")
                score *= mult
            elif mss_dir and mss_dir != direction:
                if is_live and mss_type == "CHOCH":
                    return self._reject(strategy_key, strategy_name, signal,
                                        f"CHOCH {mss_dir.upper()} — structure reversal against trade")
                score *= 0.80
                reasons.append(f"against {mss_type} {mss_dir.upper()} structure")

        # ── FEATURE A: Liquidity sweep bias ──────────────────────────────
        if self._liq_sweep and self._liq_sweep.get("bars_ago", 99) <= 3:
            sweep_dir = self._liq_sweep.get("direction", "")  # "bullish" = expect UP
            if sweep_dir == "bullish" and direction == "long":
                score *= 1.15
                reasons.append(f"liq sweep bullish @ ${self._liq_sweep['level']:,.0f} — long edge ✅")
            elif sweep_dir == "bearish" and direction == "short":
                score *= 1.15
                reasons.append(f"liq sweep bearish @ ${self._liq_sweep['level']:,.0f} — short edge ✅")
            elif sweep_dir == "bullish" and direction == "short":
                if is_live:
                    return self._reject(strategy_key, strategy_name, signal,
                                        f"Shorting after bullish liquidity sweep — bad timing")
                score *= 0.75
                reasons.append("shorting after bullish liq sweep — counter")
            elif sweep_dir == "bearish" and direction == "long":
                if is_live:
                    return self._reject(strategy_key, strategy_name, signal,
                                        f"Longing after bearish liquidity sweep — bad timing")
                score *= 0.75
                reasons.append("longing after bearish liq sweep — counter")

        # ── FEATURE C: Volume anomaly ─────────────────────────────────────
        if self._vol_spike:
            if self._vol_spike_dir == "bullish" and direction == "long":
                score *= 1.12
                reasons.append("vol spike bullish confirmation ✅")
            elif self._vol_spike_dir == "bearish" and direction == "short":
                score *= 1.12
                reasons.append("vol spike bearish confirmation ✅")
            elif self._vol_spike_dir == "bullish" and direction == "short":
                score *= 0.80
                reasons.append("vol spike bullish — fading it short (risky)")
            elif self._vol_spike_dir == "bearish" and direction == "long":
                score *= 0.80
                reasons.append("vol spike bearish — fading it long (risky)")

        # ── FEATURE D: HTF Pivot Points ───────────────────────────────────
        atr_usd = live_price * (self.current_atr_pct / 100) if live_price > 0 else 0
        piv_mult, piv_reason = self._pivot_bias(live_price, direction, atr_usd)
        if piv_reason:
            score *= piv_mult
            reasons.append(piv_reason)

        # ── FEATURE E: RSI divergence filter ─────────────────────────────
        if self._rsi_divergence:
            if self._rsi_divergence == "bullish" and direction == "long":
                score *= 1.10
                reasons.append("RSI bullish divergence — momentum shift ✅")
            elif self._rsi_divergence == "bearish" and direction == "short":
                score *= 1.10
                reasons.append("RSI bearish divergence — momentum shift ✅")
            elif self._rsi_divergence == "bullish" and direction == "short":
                score *= 0.82
                reasons.append("RSI bullish divergence — shorting against it")
            elif self._rsi_divergence == "bearish" and direction == "long":
                score *= 0.82
                reasons.append("RSI bearish divergence — longing against it")

        # ── FEATURE G: Bayesian trust blend ──────────────────────────────
        bayes_t = self._bayes_trust_score(strategy_key)
        if abs(bayes_t - trust) > 0.15:
            # Bayesian and EMA disagree — use the lower (more cautious)
            blended_trust = min(trust, bayes_t)
            delta = trust - blended_trust
            if delta > 0.05:
                score *= (1.0 - delta * 0.3)   # soft penalty
                reasons.append(f"Bayes trust {bayes_t:.2f} < EMA {trust:.2f} — caution")

        # ── FEATURE 11: Fibonacci level bias ─────────────────────────────
        # Use ATR in USD to define "near" tolerance for each Fib level
        atr_usd = live_price * (self.current_atr_pct / 100) if live_price > 0 else 0
        fib_mult, fib_reason = self._fib_bias(live_price, direction, atr_usd)
        if fib_reason:
            score *= fib_mult
            reasons.append(fib_reason)
            # Hard reject for live: don't short at key Fib support or long at key resistance
            if is_live and fib_mult < 0.88 and "counter-trend" in fib_reason:
                return self._reject(
                    strategy_key, strategy_name, signal,
                    f"Fib counter-trend: {fib_reason}",
                )

        # ── FEATURE H: Order flow imbalance at Fib/Pivot levels ──────────
        ofi_mult, ofi_reason = self._orderflow_fib_bias(live_price, direction, orderbook, atr_usd)
        if ofi_reason:
            score *= ofi_mult
            reasons.append(ofi_reason)

        # ── FEATURE: Backtest gate — statistical quality check ────────────
        # Before allowing live promotion, require Sharpe-like quality
        if is_live and total_trades >= self.MIN_PAPER_TRADES_FOR_LIVE:
            avg_pnl     = stats.get("avg_pnl", 0.0)
            pnl_std     = stats.get("pnl_std", 1.0) or 1.0
            sharpe_est  = avg_pnl / pnl_std  # simplified Sharpe per trade
            max_dd_pct  = stats.get("max_drawdown_pct", 0.0)
            if sharpe_est < 0.05:  # negative or flat expectancy
                return self._reject(strategy_key, strategy_name, signal,
                                    f"Backtest gate: negative Sharpe ({sharpe_est:.3f}) — no edge")
            if max_dd_pct > 25.0:  # drawdown exceeded 25% of peak balance
                return self._reject(strategy_key, strategy_name, signal,
                                    f"Backtest gate: max drawdown {max_dd_pct:.1f}% exceeds 25% limit")

        # ── FEATURE: Adaptive conviction threshold ────────────────────────
        # After consecutive losses, raise the bar — system must be more certain
        conviction = min(1.0, max(0.0, score))
        threshold  = self.LIVE_CONVICTION_THRESHOLD if is_live else self.PAPER_CONVICTION_THRESHOLD
        if is_live and self.consecutive_losses >= 2:
            # Raise threshold 5% per additional consecutive loss (max +20%)
            extra = min(0.20, 0.05 * (self.consecutive_losses - 1))
            threshold = min(0.80, threshold + extra)
            if extra > 0.02:
                reasons.append(f"⚠ raised threshold +{extra:.0%} — {self.consecutive_losses} consec losses")
        approved   = conviction >= threshold

        action = "APPROVE" if approved else "REJECT"
        if approved and size_mult < 0.6:
            action = "REDUCE"

        decision = {
            "approved":        approved,
            "action":          action,
            "conviction":      round(conviction, 3),
            "size_multiplier": round(size_mult, 2),
            "reasoning":       " · ".join(reasons) if reasons else "standard pass",
            "strategy_key":    strategy_key,
            "strategy_name":   strategy_name,
            "direction":       direction,
            "regime":          self.current_regime,
            "macro_trend":     self.macro_trend,
            "session":         session,
            "is_live":         is_live,
            "timestamp":       datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "factors": {
                "trust":          round(trust, 2),
                "affinity":       round(affinity, 2),
                "macro_trend":    self.macro_trend,
                "macro_conf":     round(self.macro_confidence, 2),
                "session":        session,
                "atr_pct":        round(self.current_atr_pct, 4),
                "funding_rate":   round(self.funding_rate, 5),
                "fear_greed":     self.fear_greed_score,
                "profit_factor":  round(profit_factor, 2),
                "confluence":     same_dir_count,
                "daily_trades":   self.daily_trades,
                "consec_losses":  self.consecutive_losses,
                "daily_pnl":      round(self.daily_pnl, 2),
                "signal_conf":    round(confidence, 2),
                "kelly_size":     round(size_mult, 2),
                "total_trades":   total_trades,
                "win_rate":       round(win_rate, 3),
                "current_dd":     round(current_dd, 2),
                "fib_mult":       round(fib_mult, 3),
                "fib_level":      fib_reason[:60] if fib_reason else "none",
                "pivot_mult":     round(piv_mult, 3),
                "pivot_level":    piv_reason[:60] if piv_reason else "none",
                "ofi_mult":       round(ofi_mult, 3),
                "market_structure": self._mss.get("type", "") + " " + self._mss.get("direction", "") if self._mss else "none",
                "liq_sweep":      self._liq_sweep.get("direction", "") if self._liq_sweep else "none",
                "vol_spike":      self._vol_spike_dir if self._vol_spike else "none",
                "rsi_div":        self._rsi_divergence or "none",
                "bayes_trust":    round(bayes_t, 3),
                "blackout":       blackout_reason[:40] if blackout else "none",
            },
        }

        self.decisions = [decision] + self.decisions[:49]

        if approved and is_live:
            self.daily_trades += 1
            decision["_counted_daily_trade"] = True

        mode_tag = "LIVE" if is_live else "PAPER"
        log_emoji = "✅" if approved else "❌"
        logger.info(
            f"[MasterBrain] [{mode_tag}] {log_emoji} {action} {strategy_name} {direction.upper()} "
            f"· conviction {conviction:.0%} · size {size_mult:.0%} · {decision['reasoning']}"
        )
        return decision

    def rollback_daily_trade(self) -> None:
        """Call when a live open that was approved fails at the exchange."""
        if self.daily_trades > 0:
            self.daily_trades -= 1

    def _reject(self, key: str, name: str, signal: dict, reason: str) -> dict:
        decision = {
            "approved":        False,
            "action":          "REJECT",
            "conviction":      0.0,
            "size_multiplier": 0.0,
            "reasoning":       reason,
            "strategy_key":    key,
            "strategy_name":   name,
            "direction":       signal.get("direction", "?"),
            "regime":          self.current_regime,
            "timestamp":       datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "factors":         {},
        }
        self.decisions = [decision] + self.decisions[:49]
        logger.info(f"[MasterBrain] ❌ REJECT {name} — {reason}")
        return decision

    # ══════════════════════════════════════════════════════════════════════
    #  FUSION: combine all strategy signals into one meta-signal
    # ══════════════════════════════════════════════════════════════════════

    def fuse_signals(self, strategy_results: dict, live_price: float) -> Optional[dict]:
        if live_price <= 0:
            return None

        long_signals:  list[tuple[str, dict, float]] = []
        short_signals: list[tuple[str, dict, float]] = []
        long_w = short_w = 0.0

        for key, result in strategy_results.items():
            sig = result.get("signal")
            if not sig:
                continue
            direction = sig.get("direction", "")
            if direction not in ("long", "short"):
                continue

            confidence = sig.get("confidence", 0.5)
            trust      = self.strategy_trust.get(key, 1.0)
            base_aff   = self.REGIME_AFFINITY.get(self.current_regime, {}).get(key, 1.0)
            learn_aff  = self._learned_affinity.get(self.current_regime, {}).get(key, 1.0)
            affinity   = (base_aff + learn_aff) / 2
            weight     = confidence * trust * affinity

            if direction == "long":
                long_w += weight; long_signals.append((key, sig, weight))
            else:
                short_w += weight; short_signals.append((key, sig, weight))

        total_w = long_w + short_w
        if total_w < 0.05 or (not long_signals and not short_signals):
            return None

        if long_w >= short_w:
            direction, signals, win_w = "long",  long_signals,  long_w
        else:
            direction, signals, win_w = "short", short_signals, short_w

        consensus = win_w / total_w
        # Require at least 2 agreeing strategies AND 60% consensus (was 50%)
        if consensus < 0.60 or len(signals) < 2:
            return None

        sl_sum = tp_sum = w_sum = 0.0
        contributors: list[str] = []
        for key, sig, w in signals:
            sl = sig.get("sl") or 0.0
            tp = sig.get("tp") or 0.0
            if sl > 0 and tp > 0:
                sl_sum += sl * w; tp_sum += tp * w; w_sum += w
            contributors.append(key)

        if w_sum > 0:
            fused_sl = sl_sum / w_sum
            fused_tp = tp_sum / w_sum
        else:
            sl_dist  = live_price * 0.003
            tp_dist  = live_price * 0.006
            fused_sl = (live_price - sl_dist) if direction == "long" else (live_price + sl_dist)
            fused_tp = (live_price + tp_dist) if direction == "long" else (live_price - tp_dist)

        n = max(len(strategy_results), 1)
        confidence = min(0.97, (win_w / n) * (0.5 + consensus * 0.5))
        rr = abs(fused_tp - live_price) / max(abs(live_price - fused_sl), 1)

        return {
            "direction":    direction,
            "entry":        round(live_price, 2),
            "sl":           round(fused_sl, 2),
            "tp":           round(fused_tp, 2),
            "confidence":   round(confidence, 3),
            "rr":           f"1:{rr:.1f}",
            "reasoning":    (
                f"FUSION {direction.upper()} · {len(contributors)}/{n} agree · "
                f"consensus {consensus:.0%} · macro {self.macro_trend} · "
                f"regime {self.current_regime} · [{', '.join(contributors)}]"
            ),
            "contributors": contributors,
            "long_weight":  round(long_w, 3),
            "short_weight": round(short_w, 3),
            "consensus":    round(consensus, 3),
        }

    # ══════════════════════════════════════════════════════════════════════
    #  ADAPTIVE LEARNING
    # ══════════════════════════════════════════════════════════════════════

    def _adapt_affinity(self, strategy_key: str, regime: str, won: bool) -> None:
        if not regime or regime not in self._learned_affinity:
            self._learned_affinity[regime] = {
                k: 1.0 for k in list(self.strategy_trust.keys()) + ["fusion"]
            }
        current = self._learned_affinity[regime].get(strategy_key, 1.0)
        delta   = +0.04 if won else -0.06
        self._learned_affinity[regime][strategy_key] = round(
            max(0.20, min(2.50, current + delta)), 3
        )

    def analyze_journal_patterns(self) -> dict[str, str]:
        """
        Mine strategy_stats for patterns and aggressively auto-adjust
        REGIME_AFFINITY based on observed win rates per strategy.

        Called periodically from the scan loop (every ~50 scans ≈ ~8 min).
        Returns a summary of adjustments made.
        """
        adjustments: dict[str, str] = {}
        min_trades_for_pattern = 15  # need at least 15 trades to draw conclusions

        for strat_key, s in self.strategy_stats.items():
            n = s.get("trades", 0)
            if n < min_trades_for_pattern:
                continue

            wr     = s.get("win_rate", 0.5)
            pf     = s.get("profit_factor", 1.0)
            avg_p  = s.get("avg_pnl", 0.0)
            pnl_std = s.get("pnl_std", 1.0) or 1.0
            sharpe = avg_p / pnl_std

            for regime in list(self._learned_affinity.keys()):
                current_aff = self._learned_affinity[regime].get(strat_key, 1.0)
                # Determine target affinity from journal data
                # Win rate + profit factor both strong → high affinity
                if wr >= 0.55 and pf >= 1.40 and sharpe > 0.10:
                    target_aff = min(2.0, current_aff + 0.08)
                    tag = f"↑ {strat_key}@{regime} WR={wr:.0%} PF={pf:.2f}"
                elif wr <= 0.35 or pf <= 0.80 or sharpe < -0.05:
                    # Poor edge — penalise this strategy in this regime
                    target_aff = max(0.25, current_aff - 0.10)
                    tag = f"↓ {strat_key}@{regime} WR={wr:.0%} PF={pf:.2f}"
                else:
                    continue  # neutral — leave affinity alone

                if abs(target_aff - current_aff) > 0.02:
                    self._learned_affinity[regime][strat_key] = round(target_aff, 3)
                    adjustments[f"{strat_key}/{regime}"] = tag

        if adjustments:
            logger.info(
                f"[MasterBrain] Journal mining — {len(adjustments)} affinity adjustments: "
                + " | ".join(list(adjustments.values())[:5])
            )
        return adjustments

    def _adapt_session(self, hour: int, session: str, won: bool) -> None:
        """Update per-hour and per-session win rate stats."""
        # Hour-level stats
        h = self._hour_stats.setdefault(hour, {"trades": 0, "wins": 0, "win_rate": 0.5})
        h["trades"] += 1
        if won:
            h["wins"] += 1
        h["win_rate"] = round(h["wins"] / h["trades"], 3)

        # Session-level stats
        s = self._session_stats.setdefault(session, {"trades": 0, "wins": 0, "win_rate": 0.5})
        s["trades"] += 1
        if won:
            s["wins"] += 1
        s["win_rate"] = round(s["wins"] / s["trades"], 3)

    # ── FEATURE 5: Exponential trust EMA ─────────────────────────────────
    # trust = 0.88 × old_trust + 0.12 × result  (recent results dominate)
    # result = 1.2 for win, 0.0 for loss (asymmetric — losses penalised harder)
    _TRUST_EMA_ALPHA = 0.12
    _TRUST_WIN_TARGET = 1.20
    _TRUST_LOSS_TARGET = 0.00

    def _update_trust_ema(self, strategy_key: str, won: bool, was_live: bool) -> None:
        current = self.strategy_trust.get(strategy_key, 1.0)
        target  = self._TRUST_WIN_TARGET if won else self._TRUST_LOSS_TARGET
        alpha = self._TRUST_EMA_ALPHA * (1.3 if (was_live and not won) else 1.0)
        updated = current * (1 - alpha) + target * alpha
        self.strategy_trust[strategy_key] = round(max(0.20, min(2.0, updated)), 3)
        # Also update Bayesian counter
        self._update_bayes_trust(strategy_key, won)

    # ══════════════════════════════════════════════════════════════════════
    #  LEARNING: update after every trade closes
    # ══════════════════════════════════════════════════════════════════════

    def record_trade_result(
        self,
        strategy_key: str,
        pnl: float,
        won: bool,
        was_live: bool = False,
        duration_min: float = 0.0,   # FEATURE 10: trade duration
    ) -> None:
        """Called after every trade closure. Updates trust and all learning state."""
        self._check_day()

        # Only live-trade PnL counts toward the daily loss limit gate
        if was_live:
            self.daily_pnl += pnl

        # ── FEATURE 5: Exponential trust EMA ─────────────────────────────
        self._update_trust_ema(strategy_key, won, was_live)

        if won:
            self.daily_wins += 1
            if was_live:
                self.consecutive_losses = 0
                self.daily_wins_live += 1
        else:
            self.daily_losses_count += 1
            if was_live:
                self.consecutive_losses += 1
                self.daily_losses_live += 1

        # ── Strategy stats ────────────────────────────────────────────────
        s = self.strategy_stats.setdefault(strategy_key, {
            "trades": 0, "wins": 0, "losses": 0, "total_pnl": 0.0,
            "win_rate": 0.5, "avg_win": 0.0, "avg_loss": 0.0,
            "win_pnls": [], "loss_pnls": [], "all_pnls": [],
            "gross_wins": 0.0, "gross_losses": 0.0, "profit_factor": 1.0,
            "live_trades": 0, "live_wins": 0, "live_pnl": 0.0,
            # FEATURE 8: drawdown tracking
            "peak_pnl": 0.0, "current_drawdown": 0.0, "max_drawdown": 0.0,
            "max_drawdown_pct": 0.0,
            # FEATURE 10: duration tracking
            "win_durations": [], "loss_durations": [],
            "avg_win_duration_min": 0.0, "avg_loss_duration_min": 0.0,
            # Sharpe estimate
            "avg_pnl": 0.0, "pnl_std": 1.0,
        })

        s["trades"]    += 1
        s["total_pnl"] += pnl
        if was_live:
            s["live_trades"] = s.get("live_trades", 0) + 1
            s["live_pnl"]    = s.get("live_pnl", 0.0) + pnl
            if won:
                s["live_wins"] = s.get("live_wins", 0) + 1

        # Track rolling PnL for Sharpe estimate (last 50 trades)
        all_pnls = s.get("all_pnls", [])
        all_pnls = (all_pnls + [pnl])[-50:]
        s["all_pnls"] = all_pnls
        if len(all_pnls) >= 5:
            avg_p = sum(all_pnls) / len(all_pnls)
            variance = sum((x - avg_p) ** 2 for x in all_pnls) / len(all_pnls)
            s["avg_pnl"]  = round(avg_p, 4)
            s["pnl_std"]  = round(math.sqrt(variance), 4) if variance > 0 else 0.01
        else:
            s["avg_pnl"]  = round(pnl, 4)
            s["pnl_std"]  = 1.0

        if won:
            s["wins"] += 1
            s["win_pnls"]   = (s.get("win_pnls", []) + [pnl])[-50:]
            s["avg_win"]    = sum(s["win_pnls"]) / len(s["win_pnls"])
            s["gross_wins"] = s.get("gross_wins", 0.0) + pnl
        else:
            s["losses"] += 1
            s["loss_pnls"]   = (s.get("loss_pnls", []) + [pnl])[-50:]
            s["avg_loss"]    = sum(s["loss_pnls"]) / len(s["loss_pnls"])
            s["gross_losses"] = s.get("gross_losses", 0.0) + abs(pnl)

        s["win_rate"] = s["wins"] / s["trades"] if s["trades"] > 0 else 0.5

        # ── FEATURE 2: Profit factor ──────────────────────────────────────
        gl = s.get("gross_losses", 0.0)
        s["profit_factor"] = round(s.get("gross_wins", 0.0) / gl, 3) if gl > 0 else 1.5

        # ── FEATURE 8: Drawdown tracking ─────────────────────────────────
        total_pnl = s["total_pnl"]
        if total_pnl > s.get("peak_pnl", 0.0):
            s["peak_pnl"] = total_pnl
            s["current_drawdown"] = 0.0
        else:
            s["current_drawdown"] = round(s.get("peak_pnl", 0.0) - total_pnl, 2)
        s["max_drawdown"] = max(s.get("max_drawdown", 0.0), s["current_drawdown"])
        # Percentage drawdown relative to peak
        peak = s.get("peak_pnl", 0.0)
        if peak > 0:
            s["max_drawdown_pct"] = max(
                s.get("max_drawdown_pct", 0.0),
                round(s["current_drawdown"] / peak * 100, 2),
            )
        else:
            s["max_drawdown_pct"] = s.get("max_drawdown_pct", 0.0)

        # ── FEATURE 10: Trade duration tracking ──────────────────────────
        if duration_min > 0:
            dur_key = "win_durations" if won else "loss_durations"
            avg_key = "avg_win_duration_min" if won else "avg_loss_duration_min"
            durs = (s.get(dur_key, []) + [duration_min])[-50:]
            s[dur_key] = durs
            s[avg_key] = round(sum(durs) / len(durs), 1)

        # ── FEATURE 4 & 6: Session learning ──────────────────────────────
        now_hour = datetime.now(timezone.utc).hour
        session  = _current_session()
        self._adapt_session(now_hour, session, won)

        # ── Regime affinity ───────────────────────────────────────────────
        self._adapt_affinity(strategy_key, self.current_regime, won)

        mode_tag = "LIVE" if was_live else "PAPER"
        logger.info(
            f"[MasterBrain] [{mode_tag}] {strategy_key} {'WIN' if won else 'LOSS'} "
            f"${pnl:+.2f} · trust {self.strategy_trust[strategy_key]:.2f} "
            f"· win_rate {s['win_rate']:.0%} ({s['trades']} trades) "
            f"· PF {s['profit_factor']:.2f} "
            f"· streak {self.consecutive_losses} losses "
            f"· regime {self.current_regime} · session {session}"
        )

    # ══════════════════════════════════════════════════════════════════════
    #  PORTFOLIO ANALYSIS
    # ══════════════════════════════════════════════════════════════════════

    def portfolio_summary(self, positions: dict, live_price: float) -> dict:
        long_exposure = short_exposure = total_unrealized = 0.0
        position_count = 0

        for k, pos in positions.items():
            if not pos:
                continue
            # Exclude shadow/paper positions from live portfolio exposure reporting
            if pos.get("is_shadow") or k.startswith("shadow_") or k.startswith("paper_"):
                continue
            position_count += 1
            size = pos.get("size_usdc", 0) * pos.get("leverage", 1)
            total_unrealized += pos.get("unrealized_pnl", 0)
            if pos.get("direction") == "long":
                long_exposure += size
            else:
                short_exposure += size

        net_exposure   = long_exposure - short_exposure
        gross_exposure = long_exposure + short_exposure

        return {
            "position_count":   position_count,
            "long_exposure":    round(long_exposure, 2),
            "short_exposure":   round(short_exposure, 2),
            "net_exposure":     round(net_exposure, 2),
            "gross_exposure":   round(gross_exposure, 2),
            "total_unrealized": round(total_unrealized, 2),
            "direction_bias":   "LONG" if net_exposure > 50 else ("SHORT" if net_exposure < -50 else "NEUTRAL"),
            "daily_pnl":        round(self.daily_pnl, 2),
            "daily_trades":     self.daily_trades,
            "daily_wins":        self.daily_wins,
            "daily_losses":      self.daily_losses_count,
            "daily_wins_live":   self.daily_wins_live,
            "daily_losses_live": self.daily_losses_live,
            "consec_losses":     self.consecutive_losses,
        }

    # ══════════════════════════════════════════════════════════════════════
    #  STATUS (for API / frontend)
    # ══════════════════════════════════════════════════════════════════════

    def is_strategy_live_ready(self, strategy_key: str) -> dict:
        stats    = self.strategy_stats.get(strategy_key, {})
        total    = stats.get("trades", 0)
        wr       = stats.get("win_rate", 0.0)
        pf       = stats.get("profit_factor", 0.0)
        trust    = self.strategy_trust.get(strategy_key, 1.0)
        fully_ready = (
            total >= self.MIN_PAPER_TRADES_FOR_LIVE
            and wr >= self.MIN_WIN_RATE_FOR_LIVE
            and pf >= self.MIN_PROFIT_FACTOR_FOR_LIVE
            and trust >= 0.5
        )
        can_trade = trust >= 0.5 and self.consecutive_losses < 3
        scale = max(0.3, total / self.MIN_PAPER_TRADES_FOR_LIVE) if total < self.MIN_PAPER_TRADES_FOR_LIVE else 1.0
        return {
            "ready":              fully_ready,
            "can_trade":          can_trade,
            "scale":              round(scale, 2),
            "trades":             total,
            "trades_needed":      max(0, self.MIN_PAPER_TRADES_FOR_LIVE - total),
            "win_rate":           round(wr, 3),
            "win_rate_needed":    self.MIN_WIN_RATE_FOR_LIVE,
            "profit_factor":      round(pf, 2),
            "profit_factor_needed": self.MIN_PROFIT_FACTOR_FOR_LIVE,
            "trust":              round(trust, 2),
        }

    def get_status(self, positions: dict, live_price: float) -> dict:
        portfolio = self.portfolio_summary(positions, live_price)
        live_readiness = {k: self.is_strategy_live_ready(k) for k in self.strategy_trust}
        return {
            "regime":              self.current_regime,
            "regime_confidence":   self.regime_confidence,
            "regime_updated":      self.regime_updated,
            "regime_stability":    self._regime_stability(),
            "macro_trend":         self.macro_trend,
            "macro_confidence":    self.macro_confidence,
            "market_context": {
                "atr_pct":         round(self.current_atr_pct, 4),
                "baseline_atr":    round(self._baseline_atr_pct, 4),
                "funding_rate":    round(self.funding_rate, 5),
                "fear_greed":      self.fear_greed_score,
                "fib":             self.fib_status(),
                "pivots": {
                    "weekly":  self._weekly_pivots,
                    "monthly": self._monthly_pivots,
                },
                "market_structure": self._mss,
                "liq_sweep":        self._liq_sweep,
                "vol_spike":        {"active": self._vol_spike, "dir": self._vol_spike_dir},
                "rsi_divergence":   self._rsi_divergence or "none",
                "macro_blackout":   self._is_macro_blackout()[1] or "none",
            },
            "strategy_trust":      {k: round(v, 2) for k, v in self.strategy_trust.items()},
            "live_readiness":      live_readiness,
            "portfolio":           portfolio,
            "recent_decisions":    self.decisions[:10],
            "strategy_stats":      {
                k: {kk: vv for kk, vv in v.items()
                    if kk not in ("win_pnls", "loss_pnls", "win_durations", "loss_durations")}
                for k, v in self.strategy_stats.items()
            },
            "learned_affinity":    {
                r: {k: round(v, 2) for k, v in affs.items()}
                for r, affs in self._learned_affinity.items()
            },
            "session_stats":       self._session_stats,
            "limits": {
                "max_daily_trades":       self.MAX_DAILY_TRADES,
                "max_consecutive_losses": self.MAX_CONSECUTIVE_LOSSES,
                "max_open_positions":     self.MAX_OPEN_POSITIONS,
                "max_daily_loss":         self.MAX_DAILY_LOSS,
                "min_profit_factor":      self.MIN_PROFIT_FACTOR_FOR_LIVE,
            },
        }

    def _regime_stability(self) -> str:
        if len(self._regime_history) < 3:
            return "insufficient_data"
        unique = len(set(self._regime_history[-5:]))
        if unique <= 1:
            return "stable"
        elif unique == 2:
            return "moderate"
        return "unstable"

    # ══════════════════════════════════════════════════════════════════════
    #  SERIALIZATION
    # ══════════════════════════════════════════════════════════════════════

    def to_dict(self) -> dict:
        return {
            "current_regime":      self.current_regime,
            "regime_confidence":   self.regime_confidence,
            "regime_history":      self._regime_history[-10:],
            "macro_trend":         self.macro_trend,
            "macro_confidence":    self.macro_confidence,
            "current_atr_pct":     self.current_atr_pct,
            "baseline_atr_pct":    self._baseline_atr_pct,
            "funding_rate":        self.funding_rate,
            "fear_greed_score":    self.fear_greed_score,
            "strategy_trust":      self.strategy_trust,
            "strategy_stats":      self.strategy_stats,
            "consecutive_losses":  self.consecutive_losses,
            "daily_trades":        self.daily_trades,
            "daily_pnl":           self.daily_pnl,
            "daily_wins":          self.daily_wins,
            "daily_losses_count":  self.daily_losses_count,
            "daily_wins_live":     self.daily_wins_live,
            "daily_losses_live":   self.daily_losses_live,
            "_day_str":            self._day_str,
            "decisions":           self.decisions[:20],
            "_learned_affinity":   self._learned_affinity,
            "_hour_stats":         self._hour_stats,
            "_session_stats":      self._session_stats,
            "_fib_levels":         self._fib_levels,
            "_fib_swing_high":     self._fib_swing_high,
            "_fib_swing_low":      self._fib_swing_low,
            "_fib_trend":          self._fib_trend,
            "_liq_sweep":          self._liq_sweep,
            "_mss":                self._mss,
            "_swing_highs":        self._swing_highs,
            "_swing_lows":         self._swing_lows,
            "_vol_spike":          self._vol_spike,
            "_vol_spike_dir":      self._vol_spike_dir,
            "_weekly_pivots":      self._weekly_pivots,
            "_monthly_pivots":     self._monthly_pivots,
            "_rsi_divergence":     self._rsi_divergence,
            "_bayes_trust":        self._bayes_trust,
        }

    def from_dict(self, data: dict) -> None:
        if not data:
            return
        self.current_regime     = data.get("current_regime", "unknown")
        self.regime_confidence  = data.get("regime_confidence", 0.0)
        self._regime_history    = data.get("regime_history", [])
        self.macro_trend        = data.get("macro_trend", "neutral")
        self.macro_confidence   = data.get("macro_confidence", 0.0)
        self.current_atr_pct    = data.get("current_atr_pct", 0.5)
        self._baseline_atr_pct  = data.get("baseline_atr_pct", 0.5)
        self.funding_rate       = data.get("funding_rate", 0.0)
        self.fear_greed_score   = data.get("fear_greed_score", 50)
        self.strategy_trust     = data.get("strategy_trust", self.strategy_trust)
        self.strategy_stats     = data.get("strategy_stats", {})
        self.consecutive_losses = data.get("consecutive_losses", 0)
        self.daily_trades       = data.get("daily_trades", 0)
        self.daily_pnl          = data.get("daily_pnl", 0.0)
        self.daily_wins         = data.get("daily_wins", 0)
        self.daily_losses_count = data.get("daily_losses_count", 0)
        self.daily_wins_live    = data.get("daily_wins_live", 0)
        self.daily_losses_live  = data.get("daily_losses_live", 0)
        self._day_str           = data.get("_day_str", self._day_str)
        self.decisions          = data.get("decisions", [])

        # Restore learned affinity
        stored_aff = data.get("_learned_affinity", {})
        for regime, affinities in stored_aff.items():
            if regime not in self._learned_affinity:
                self._learned_affinity[regime] = {}
            self._learned_affinity[regime].update(affinities)

        # Restore hour / session stats
        stored_hour = data.get("_hour_stats", {})
        for h, v in stored_hour.items():
            self._hour_stats[int(h)] = v
        stored_sess = data.get("_session_stats", {})
        for s, v in stored_sess.items():
            self._session_stats[s] = v

        # Restore Fibonacci state
        if data.get("_fib_levels"):
            self._fib_levels     = data["_fib_levels"]
            self._fib_swing_high = data.get("_fib_swing_high", 0.0)
            self._fib_swing_low  = data.get("_fib_swing_low",  0.0)
            self._fib_trend      = data.get("_fib_trend",  "up")

        # Restore new intelligence state
        self._liq_sweep      = data.get("_liq_sweep",     {})
        self._mss            = data.get("_mss",           {})
        self._swing_highs    = data.get("_swing_highs",   [])
        self._swing_lows     = data.get("_swing_lows",    [])
        self._vol_spike      = data.get("_vol_spike",     False)
        self._vol_spike_dir  = data.get("_vol_spike_dir", "")
        self._weekly_pivots  = data.get("_weekly_pivots", {})
        self._monthly_pivots = data.get("_monthly_pivots",{})
        self._rsi_divergence = data.get("_rsi_divergence","")
        if data.get("_bayes_trust"):
            self._bayes_trust = data["_bayes_trust"]

    # ── Math helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _ema_last(values: list[float], period: int) -> float:
        if len(values) < period:
            return values[-1] if values else 0
        k = 2 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    @staticmethod
    def _atr_pct(candles: list[dict]) -> float:
        if len(candles) < 2:
            return 0.0
        trs = []
        for i in range(1, len(candles)):
            h, l, pc = candles[i]["high"], candles[i]["low"], candles[i-1]["close"]
            tr = max(h - l, abs(h - pc), abs(l - pc))
            trs.append(tr / pc * 100 if pc > 0 else 0)
        return sum(trs) / len(trs) if trs else 0
