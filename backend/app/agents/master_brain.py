"""
Master Trading Brain
====================
The central intelligence layer that sits between raw strategy signals and
BingX execution. No strategy can trade directly — every signal must pass
through the Brain's decision pipeline.

Pipeline:
  1. CONFLUENCE  — How many strategies agree on direction?
  2. REGIME      — What market regime are we in? (trending / ranging / volatile)
  3. PORTFOLIO   — Current exposure, open P&L, correlation risk
  4. RISK GATE   — Daily loss, max drawdown, consecutive-loss streak
  5. SIZING      — Kelly-fraction position sizing based on conviction
  6. DECISION    — Final APPROVE / REJECT / REDUCE with reasoning

The Brain learns from every trade: winning strategies get higher trust,
losing streaks trigger cooldowns, regime mismatches are penalized.
"""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class MasterBrain:
    """
    Singleton decision engine. Instantiated by PersistentAgent on startup.
    All state is serializable for DB persistence.
    """

    def __init__(self) -> None:
        # ── Regime detection ─────────────────────────────────────────────
        self.current_regime: str = "unknown"   # trending_up, trending_down, ranging, volatile
        self.regime_confidence: float = 0.0
        self.regime_updated: str = ""

        # ── Strategy trust scores (0.0 – 2.0, 1.0 = neutral) ────────────
        self.strategy_trust: dict[str, float] = {
            "momentum": 1.0, "hft": 1.0, "orb": 1.0, "obi": 1.0, "grid": 1.0,
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
        self.decisions: list[dict] = []  # last 50 decisions

        # ── Regime-strategy affinity map ─────────────────────────────────
        self.REGIME_AFFINITY = {
            "trending_up":   {"momentum": 1.4, "hft": 0.7, "orb": 1.2, "obi": 0.8, "grid": 0.6},
            "trending_down": {"momentum": 1.3, "hft": 0.7, "orb": 1.1, "obi": 0.9, "grid": 0.5},
            "ranging":       {"momentum": 0.5, "hft": 1.3, "orb": 0.6, "obi": 1.2, "grid": 1.5},
            "volatile":      {"momentum": 0.8, "hft": 1.1, "orb": 0.9, "obi": 1.0, "grid": 0.4},
            "unknown":       {"momentum": 1.0, "hft": 1.0, "orb": 1.0, "obi": 1.0, "grid": 1.0},
        }

        # ── Limits ───────────────────────────────────────────────────────
        self.MAX_DAILY_TRADES = 30
        self.MAX_CONSECUTIVE_LOSSES = 5
        self.MAX_OPEN_POSITIONS = 6   # across all strategies
        self.MAX_DAILY_LOSS = -300.0  # hard stop
        self.CORRELATION_PENALTY = 0.5  # reduce size if same-direction positions open

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
    #  REGIME DETECTION
    # ══════════════════════════════════════════════════════════════════════

    def detect_regime(self, candles_15m: list[dict], candles_1m: list[dict]) -> str:
        """Classify current market regime from candle data."""
        if not candles_15m or len(candles_15m) < 20:
            self.current_regime = "unknown"
            self.regime_confidence = 0.0
            return self.current_regime

        closes = [c["close"] for c in candles_15m[-50:]]
        n = len(closes)

        # EMA20 slope
        ema20 = self._ema_last(closes, 20)
        ema50 = self._ema_last(closes, min(50, n))
        slope = (closes[-1] - ema20) / ema20 * 100 if ema20 > 0 else 0

        # ATR% (volatility)
        atr_pct = self._atr_pct(candles_15m[-20:])

        # Range ratio: (high-low range) / ATR
        recent_high = max(c["high"] for c in candles_15m[-10:])
        recent_low  = min(c["low"] for c in candles_15m[-10:])
        range_pct   = (recent_high - recent_low) / recent_low * 100 if recent_low > 0 else 0

        # Classify
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
        return regime

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
    ) -> dict:
        """
        Master decision on whether to execute a signal.

        Returns: {
            "approved": bool,
            "action": "APPROVE" | "REJECT" | "REDUCE",
            "conviction": float (0-1),
            "size_multiplier": float (0-2),
            "reasoning": str,
            "factors": dict,
        }
        """
        self._check_day()
        reasons = []
        score = 1.0   # starts neutral, modifiers push up/down

        direction = signal.get("direction", "long")
        confidence = signal.get("confidence", 0.5)
        met_count = signal.get("met_count", 0)

        # ── Factor 1: Strategy trust ─────────────────────────────────────
        trust = self.strategy_trust.get(strategy_key, 1.0)
        score *= trust
        if trust < 0.6:
            reasons.append(f"low trust ({trust:.2f})")
        elif trust > 1.2:
            reasons.append(f"high trust ({trust:.2f})")

        # ── Factor 2: Regime affinity ────────────────────────────────────
        affinity = self.REGIME_AFFINITY.get(self.current_regime, {}).get(strategy_key, 1.0)
        score *= affinity
        if affinity < 0.7:
            reasons.append(f"{strategy_key} weak in {self.current_regime} regime")
        elif affinity > 1.2:
            reasons.append(f"{strategy_key} strong in {self.current_regime} regime")

        # ── Factor 3: Confluence — how many strategies agree? ────────────
        same_dir_count = 0
        opposite_dir_count = 0
        for k, pos in open_positions.items():
            if pos and not k.startswith("grid_"):
                if pos.get("direction") == direction:
                    same_dir_count += 1
                else:
                    opposite_dir_count += 1

        if same_dir_count >= 2:
            score *= 1.15
            reasons.append(f"{same_dir_count} positions confirm {direction}")
        if opposite_dir_count >= 2:
            score *= 0.7
            reasons.append(f"{opposite_dir_count} positions oppose — conflict")

        # ── Factor 4: Risk gates ─────────────────────────────────────────
        total_open = sum(1 for v in open_positions.values() if v)

        # Max positions
        if total_open >= self.MAX_OPEN_POSITIONS:
            return self._reject(strategy_key, strategy_name, signal,
                                f"Max {self.MAX_OPEN_POSITIONS} positions reached ({total_open} open)")

        # Daily trade limit
        if self.daily_trades >= self.MAX_DAILY_TRADES:
            return self._reject(strategy_key, strategy_name, signal,
                                f"Daily trade limit reached ({self.daily_trades}/{self.MAX_DAILY_TRADES})")

        # Consecutive losses
        if self.consecutive_losses >= self.MAX_CONSECUTIVE_LOSSES:
            score *= 0.3
            reasons.append(f"⚠ {self.consecutive_losses} consecutive losses — caution mode")

        # Daily P&L stop
        if self.daily_pnl <= self.MAX_DAILY_LOSS:
            return self._reject(strategy_key, strategy_name, signal,
                                f"Daily loss limit hit (${self.daily_pnl:.0f} ≤ ${self.MAX_DAILY_LOSS:.0f})")

        # ── Factor 5: Signal quality ─────────────────────────────────────
        score *= (0.7 + confidence * 0.6)  # range: 0.7 – 1.3
        if confidence < 0.55:
            reasons.append("low signal confidence")
        elif confidence > 0.75:
            reasons.append("strong signal confidence")

        # ── Factor 6: Correlation penalty ────────────────────────────────
        size_mult = 1.0
        if same_dir_count >= 2:
            size_mult *= self.CORRELATION_PENALTY
            reasons.append(f"size reduced — correlated with {same_dir_count} open")

        # ── Factor 7: Kelly-fraction sizing ──────────────────────────────
        stats = self.strategy_stats.get(strategy_key, {})
        win_rate = stats.get("win_rate", 0.5)
        avg_win = stats.get("avg_win", 1.0)
        avg_loss = abs(stats.get("avg_loss", -1.0)) or 1.0
        if win_rate > 0 and avg_loss > 0:
            kelly = win_rate - (1 - win_rate) / (avg_win / avg_loss)
            kelly_frac = max(0.1, min(0.5, kelly * 0.5))  # half-Kelly, clamped
            size_mult *= (0.5 + kelly_frac)

        # ── Final decision ───────────────────────────────────────────────
        conviction = min(1.0, max(0.0, score))
        approved = conviction >= 0.45

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
            "timestamp":       datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "factors": {
                "trust":        round(trust, 2),
                "affinity":     round(affinity, 2),
                "confluence":   same_dir_count,
                "daily_trades": self.daily_trades,
                "consec_losses": self.consecutive_losses,
                "daily_pnl":    round(self.daily_pnl, 2),
                "signal_conf":  round(confidence, 2),
                "kelly_size":   round(size_mult, 2),
            },
        }

        self.decisions = [decision] + self.decisions[:49]

        if approved:
            self.daily_trades += 1

        log_emoji = "✅" if approved else "❌"
        logger.info(f"[MasterBrain] {log_emoji} {action} {strategy_name} {direction.upper()} "
                    f"· conviction {conviction:.0%} · size {size_mult:.0%} · {decision['reasoning']}")
        return decision

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
    #  LEARNING: update trust after trade closes
    # ══════════════════════════════════════════════════════════════════════

    def record_trade_result(self, strategy_key: str, pnl: float, won: bool) -> None:
        """Called after every trade closure. Updates trust and stats."""
        self._check_day()
        self.daily_pnl += pnl

        if won:
            self.daily_wins += 1
            self.consecutive_losses = 0
            # Reward: push trust toward 1.5 (max 2.0)
            cur = self.strategy_trust.get(strategy_key, 1.0)
            self.strategy_trust[strategy_key] = min(2.0, cur + 0.05)
        else:
            self.daily_losses_count += 1
            self.consecutive_losses += 1
            # Penalize: push trust down (min 0.3)
            cur = self.strategy_trust.get(strategy_key, 1.0)
            self.strategy_trust[strategy_key] = max(0.3, cur - 0.08)

        # Update per-strategy stats
        s = self.strategy_stats.setdefault(strategy_key, {
            "trades": 0, "wins": 0, "losses": 0, "total_pnl": 0.0,
            "win_rate": 0.5, "avg_win": 0.0, "avg_loss": 0.0,
            "win_pnls": [], "loss_pnls": [],
        })
        s["trades"] += 1
        s["total_pnl"] += pnl
        if won:
            s["wins"] += 1
            s["win_pnls"] = (s.get("win_pnls", []) + [pnl])[-50:]
            s["avg_win"] = sum(s["win_pnls"]) / len(s["win_pnls"]) if s["win_pnls"] else 0
        else:
            s["losses"] += 1
            s["loss_pnls"] = (s.get("loss_pnls", []) + [pnl])[-50:]
            s["avg_loss"] = sum(s["loss_pnls"]) / len(s["loss_pnls"]) if s["loss_pnls"] else 0
        s["win_rate"] = s["wins"] / s["trades"] if s["trades"] > 0 else 0.5

        logger.info(f"[MasterBrain] Trade result: {strategy_key} {'WIN' if won else 'LOSS'} "
                    f"${pnl:+.2f} · trust now {self.strategy_trust[strategy_key]:.2f} "
                    f"· streak {self.consecutive_losses} losses")

    # ══════════════════════════════════════════════════════════════════════
    #  PORTFOLIO ANALYSIS
    # ══════════════════════════════════════════════════════════════════════

    def portfolio_summary(self, positions: dict, live_price: float) -> dict:
        """Compute portfolio-level metrics for the UI."""
        long_exposure = 0.0
        short_exposure = 0.0
        total_unrealized = 0.0
        position_count = 0

        for k, pos in positions.items():
            if not pos:
                continue
            position_count += 1
            size = pos.get("size_usdc", 0) * pos.get("leverage", 1)
            upnl = pos.get("unrealized_pnl", 0)
            total_unrealized += upnl
            if pos.get("direction") == "long":
                long_exposure += size
            else:
                short_exposure += size

        net_exposure = long_exposure - short_exposure
        gross_exposure = long_exposure + short_exposure

        return {
            "position_count":    position_count,
            "long_exposure":     round(long_exposure, 2),
            "short_exposure":    round(short_exposure, 2),
            "net_exposure":      round(net_exposure, 2),
            "gross_exposure":    round(gross_exposure, 2),
            "total_unrealized":  round(total_unrealized, 2),
            "direction_bias":    "LONG" if net_exposure > 50 else ("SHORT" if net_exposure < -50 else "NEUTRAL"),
            "daily_pnl":         round(self.daily_pnl, 2),
            "daily_trades":      self.daily_trades,
            "daily_wins":        self.daily_wins,
            "daily_losses":      self.daily_losses_count,
            "consec_losses":     self.consecutive_losses,
        }

    # ══════════════════════════════════════════════════════════════════════
    #  STATUS (for API / frontend)
    # ══════════════════════════════════════════════════════════════════════

    def get_status(self, positions: dict, live_price: float) -> dict:
        portfolio = self.portfolio_summary(positions, live_price)
        return {
            "regime":             self.current_regime,
            "regime_confidence":  self.regime_confidence,
            "regime_updated":     self.regime_updated,
            "strategy_trust":     {k: round(v, 2) for k, v in self.strategy_trust.items()},
            "portfolio":          portfolio,
            "recent_decisions":   self.decisions[:10],
            "strategy_stats":     {k: {kk: vv for kk, vv in v.items() if kk not in ("win_pnls", "loss_pnls")}
                                   for k, v in self.strategy_stats.items()},
            "limits": {
                "max_daily_trades":      self.MAX_DAILY_TRADES,
                "max_consecutive_losses": self.MAX_CONSECUTIVE_LOSSES,
                "max_open_positions":     self.MAX_OPEN_POSITIONS,
                "max_daily_loss":         self.MAX_DAILY_LOSS,
            },
        }

    # ══════════════════════════════════════════════════════════════════════
    #  SERIALIZATION
    # ══════════════════════════════════════════════════════════════════════

    def to_dict(self) -> dict:
        return {
            "current_regime":     self.current_regime,
            "regime_confidence":  self.regime_confidence,
            "strategy_trust":     self.strategy_trust,
            "strategy_stats":     self.strategy_stats,
            "consecutive_losses": self.consecutive_losses,
            "daily_trades":       self.daily_trades,
            "daily_pnl":          self.daily_pnl,
            "daily_wins":         self.daily_wins,
            "daily_losses_count": self.daily_losses_count,
            "_day_str":           self._day_str,
            "decisions":          self.decisions[:20],
        }

    def from_dict(self, data: dict) -> None:
        if not data:
            return
        self.current_regime     = data.get("current_regime", "unknown")
        self.regime_confidence  = data.get("regime_confidence", 0.0)
        self.strategy_trust     = data.get("strategy_trust", self.strategy_trust)
        self.strategy_stats     = data.get("strategy_stats", {})
        self.consecutive_losses = data.get("consecutive_losses", 0)
        self.daily_trades       = data.get("daily_trades", 0)
        self.daily_pnl          = data.get("daily_pnl", 0.0)
        self.daily_wins         = data.get("daily_wins", 0)
        self.daily_losses_count = data.get("daily_losses_count", 0)
        self._day_str           = data.get("_day_str", self._day_str)
        self.decisions          = data.get("decisions", [])

    # ── Helpers ──────────────────────────────────────────────────────────

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
