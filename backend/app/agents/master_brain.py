"""
Master Trading Brain
====================
The central intelligence layer that sits between raw strategy signals and
BingX execution. No strategy can trade directly — every signal must pass
through the Brain's decision pipeline.

Pipeline:
  1. MACRO TREND    — 1h/4h multi-timeframe bias (new)
  2. SESSION GATE   — time-of-day affinity (new)
  3. CONFLUENCE     — how many strategies agree?
  4. REGIME         — 15m market regime
  5. PORTFOLIO      — exposure / correlation risk
  6. RISK GATE      — daily loss, consecutive losses, drawdown (new)
  7. MARKET CONTEXT — funding rate + Fear & Greed bias (new)
  8. SIZING         — Kelly + ATR-adjusted + drawdown-scaled (new)
  9. DECISION       — final APPROVE / REJECT / REDUCE with reasoning

Learning from every trade:
  • Strategy trust via exponential EMA (recent results dominate)  [new]
  • Regime affinity (what works in each regime)
  • Session affinity (what works at each UTC hour)                [new]
  • Profit factor tracking per strategy                           [new]
  • Max drawdown tracking per strategy                            [new]
  • Trade duration intelligence                                   [new]
"""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Trading session windows (UTC hours, inclusive) ─────────────────────────────
SESSIONS = {
    "Asia":    (0,  8),   # 00:00–08:59 UTC
    "London":  (8, 13),   # 08:00–12:59 UTC
    "NY":      (13, 22),  # 13:00–21:59 UTC
    "OffHours":(22, 24),  # 22:00–23:59 UTC (low liquidity)
}
# Dead hours — low volume, high slippage
DEAD_HOURS = {3, 4, 5, 6, 7}


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
        self.MIN_PAPER_TRADES_FOR_LIVE = 5
        self.MIN_WIN_RATE_FOR_LIVE = 0.40
        self.MIN_PROFIT_FACTOR_FOR_LIVE = 1.20  # NEW: gross_win / gross_loss
        self.LIVE_CONVICTION_THRESHOLD = 0.55
        self.PAPER_CONVICTION_THRESHOLD = 0.40

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
    ) -> dict:
        self._check_day()
        reasons = []
        score = 1.0

        direction  = signal.get("direction", "long")
        confidence = signal.get("confidence", 0.5)
        now_hour   = datetime.now(timezone.utc).hour
        session    = _current_session()

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
        # Dead hours: low volume, high slippage — penalise all signals
        if now_hour in DEAD_HOURS:
            score *= 0.55
            reasons.append(f"dead hour {now_hour}:00 UTC (low volume)")
            if is_live:
                return self._reject(strategy_key, strategy_name, signal,
                                    f"Dead hour {now_hour}:00 UTC — no live trades (low liquidity)")

        # Session affinity: learned win rate by UTC hour
        h_stats = self._hour_stats.get(now_hour, {"trades": 0, "win_rate": 0.5})
        if h_stats["trades"] >= 8:
            session_wr = h_stats["win_rate"]
            if session_wr < 0.35:
                score *= 0.65
                reasons.append(f"poor hourly win rate ({session_wr:.0%} at {now_hour}:00 UTC)")
            elif session_wr > 0.60:
                score *= 1.15
                reasons.append(f"strong hourly win rate ({session_wr:.0%} at {now_hour}:00 UTC)")

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
        if abs(self.funding_rate) >= 0.0008:  # ≥ 0.08% per 8h (very crowded)
            if self.funding_rate > 0 and direction == "long":
                score *= 0.80
                reasons.append(f"high positive funding ({self.funding_rate:.4%}) — longs crowded")
            elif self.funding_rate < 0 and direction == "short":
                score *= 0.80
                reasons.append(f"high negative funding ({self.funding_rate:.4%}) — shorts crowded")
            # Contrarian bonus: trading against the crowd
            if self.funding_rate > 0.001 and direction == "short":
                score *= 1.10
                reasons.append("contrarian short vs extreme long funding")
            elif self.funding_rate < -0.001 and direction == "long":
                score *= 1.10
                reasons.append("contrarian long vs extreme short funding")

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

        # ── Final decision ────────────────────────────────────────────────
        conviction = min(1.0, max(0.0, score))
        threshold  = self.LIVE_CONVICTION_THRESHOLD if is_live else self.PAPER_CONVICTION_THRESHOLD
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
        if consensus < 0.50 or len(signals) < 2:
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
        # Live losses have a slightly higher learning rate (more impactful)
        alpha = self._TRUST_EMA_ALPHA * (1.3 if (was_live and not won) else 1.0)
        updated = current * (1 - alpha) + target * alpha
        self.strategy_trust[strategy_key] = round(max(0.20, min(2.0, updated)), 3)

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
        else:
            self.daily_losses_count += 1
            if was_live:
                self.consecutive_losses += 1

        # ── Strategy stats ────────────────────────────────────────────────
        s = self.strategy_stats.setdefault(strategy_key, {
            "trades": 0, "wins": 0, "losses": 0, "total_pnl": 0.0,
            "win_rate": 0.5, "avg_win": 0.0, "avg_loss": 0.0,
            "win_pnls": [], "loss_pnls": [],
            "gross_wins": 0.0, "gross_losses": 0.0, "profit_factor": 1.0,
            "live_trades": 0, "live_wins": 0, "live_pnl": 0.0,
            # FEATURE 8: drawdown tracking
            "peak_pnl": 0.0, "current_drawdown": 0.0, "max_drawdown": 0.0,
            # FEATURE 10: duration tracking
            "win_durations": [], "loss_durations": [],
            "avg_win_duration_min": 0.0, "avg_loss_duration_min": 0.0,
        })

        s["trades"]    += 1
        s["total_pnl"] += pnl
        if was_live:
            s["live_trades"] = s.get("live_trades", 0) + 1
            s["live_pnl"]    = s.get("live_pnl", 0.0) + pnl
            if won:
                s["live_wins"] = s.get("live_wins", 0) + 1

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
            "daily_wins":       self.daily_wins,
            "daily_losses":     self.daily_losses_count,
            "consec_losses":    self.consecutive_losses,
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
            "_day_str":            self._day_str,
            "decisions":           self.decisions[:20],
            "_learned_affinity":   self._learned_affinity,
            "_hour_stats":         self._hour_stats,
            "_session_stats":      self._session_stats,
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
