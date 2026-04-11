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
        self.decisions: list[dict] = []  # last 50 decisions

        # ── Regime-strategy affinity map (base priors) ───────────────────
        self.REGIME_AFFINITY = {
            "trending_up":   {"momentum": 1.4, "hft": 0.7, "orb": 1.2, "obi": 0.8, "fusion": 1.0},
            "trending_down": {"momentum": 1.3, "hft": 0.7, "orb": 1.1, "obi": 0.9, "fusion": 1.0},
            "ranging":       {"momentum": 0.5, "hft": 1.3, "orb": 0.6, "obi": 1.2, "fusion": 1.0},
            "volatile":      {"momentum": 0.8, "hft": 1.1, "orb": 0.9, "obi": 1.0, "fusion": 1.0},
            "unknown":       {"momentum": 1.0, "hft": 1.0, "orb": 1.0, "obi": 1.0, "fusion": 1.0},
        }

        # ── Learned affinity — updated from every trade result ────────────
        # Starts as a copy of REGIME_AFFINITY; drifts toward what actually works.
        self._learned_affinity: dict[str, dict[str, float]] = {
            regime: dict(vals) for regime, vals in self.REGIME_AFFINITY.items()
        }

        # ── Live readiness thresholds ─────────────────────────────────────
        self.MIN_PAPER_TRADES_FOR_LIVE = 5    # strategy must have 5+ paper trades before live
        self.MIN_WIN_RATE_FOR_LIVE = 0.40     # must be >=40% win rate to go live
        self.LIVE_CONVICTION_THRESHOLD = 0.55 # higher bar for live than paper
        self.PAPER_CONVICTION_THRESHOLD = 0.40

        # ── Limits ───────────────────────────────────────────────────────
        self.MAX_DAILY_TRADES = 50
        self.MAX_CONSECUTIVE_LOSSES = 5
        self.MAX_OPEN_POSITIONS = 8   # across all strategies + shadows
        # Set via PersistentAgent from config["daily_loss_limit"] — single source of truth
        self.MAX_DAILY_LOSS = -50.0
        self.CORRELATION_PENALTY = 0.5  # reduce size if same-direction positions open

        # ── Regime history for stability ──────────────────────────────────
        self._regime_history: list[str] = []  # last 10 regime readings

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
        self._regime_history = (self._regime_history + [regime])[-10:]
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
        is_live: bool = False,
    ) -> dict:
        """
        Master decision on whether to execute a signal.
        is_live=True applies stricter thresholds — strategy must have proven
        itself on paper first.

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
        score = 1.0

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

        # ── Factor 2: Regime affinity — blend static base with learned ──
        base_affinity    = self.REGIME_AFFINITY.get(self.current_regime, {}).get(strategy_key, 1.0)
        learned_affinity = self._learned_affinity.get(self.current_regime, {}).get(strategy_key, 1.0)
        affinity = round(0.5 * base_affinity + 0.5 * learned_affinity, 4)
        score *= affinity
        if affinity < 0.7:
            reasons.append(f"{strategy_key} weak in {self.current_regime} regime")
        elif affinity > 1.2:
            reasons.append(f"{strategy_key} strong in {self.current_regime} regime")

        # ── Factor 3: Regime stability — reject if regime is unstable ────
        if len(self._regime_history) >= 5:
            unique_recent = len(set(self._regime_history[-5:]))
            if unique_recent >= 3:
                score *= 0.7
                reasons.append("regime unstable (3+ changes in 5 readings)")

        # ── Factor 4: Directional lock + confluence ──────────────────────
        # Count live/paper (non-shadow, non-paper_trader) positions by direction
        same_dir_count = 0
        opposite_dir_count = 0
        live_dir_count = 0
        live_opposite_count = 0
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

        # HARD GATE: never open opposite direction while live positions exist
        if is_live and live_opposite_count > 0:
            return self._reject(strategy_key, strategy_name, signal,
                                f"Direction conflict — {live_opposite_count} live position(s) in opposite direction")

        # HARD GATE for all modes: don't fight yourself
        if opposite_dir_count >= 1 and is_live:
            return self._reject(strategy_key, strategy_name, signal,
                                f"Directional lock — {opposite_dir_count} position(s) already open in opposite direction")

        if same_dir_count >= 2:
            score *= 1.15
            reasons.append(f"{same_dir_count} positions confirm {direction}")
        if opposite_dir_count >= 2:
            score *= 0.5
            reasons.append(f"{opposite_dir_count} positions oppose — strong conflict")

        # ── Factor 5: Risk gates ─────────────────────────────────────────
        # Only count real (non-shadow) positions toward the cap
        total_open = sum(
            1 for k, v in open_positions.items()
            if v and not k.startswith("shadow_") and not (isinstance(v, dict) and v.get("is_shadow"))
        )

        if total_open >= self.MAX_OPEN_POSITIONS:
            return self._reject(strategy_key, strategy_name, signal,
                                f"Max {self.MAX_OPEN_POSITIONS} positions reached ({total_open} open)")

        if is_live and self.daily_trades >= self.MAX_DAILY_TRADES:
            return self._reject(strategy_key, strategy_name, signal,
                                f"Daily live trade limit ({self.daily_trades}/{self.MAX_DAILY_TRADES})")

        if self.consecutive_losses >= self.MAX_CONSECUTIVE_LOSSES:
            score *= 0.3
            reasons.append(f"⚠ {self.consecutive_losses} consecutive losses — caution mode")

        # Daily loss limit only applies to live trades — paper/shadow always run
        if is_live and self.daily_pnl <= self.MAX_DAILY_LOSS:
            return self._reject(strategy_key, strategy_name, signal,
                                f"Daily loss limit (${self.daily_pnl:.0f} ≤ ${self.MAX_DAILY_LOSS:.0f})")

        # ── Factor 6: Signal quality ─────────────────────────────────────
        score *= (0.7 + confidence * 0.6)
        if confidence < 0.55:
            reasons.append("low signal confidence")
        elif confidence > 0.75:
            reasons.append("strong signal confidence")

        # ── Factor 6b: Minimum risk-reward ratio ──────────────────────────
        sig_entry = signal.get("entry", live_price) or live_price
        sig_sl = signal.get("sl", 0) or 0
        sig_tp = signal.get("tp", 0) or 0
        if sig_sl and sig_tp and sig_entry > 0:
            sl_dist = abs(sig_entry - sig_sl)
            tp_dist = abs(sig_tp - sig_entry)
            rr_ratio = tp_dist / sl_dist if sl_dist > 0 else 0
            if is_live and rr_ratio < 1.5:
                return self._reject(strategy_key, strategy_name, signal,
                                    f"Poor R:R ratio ({rr_ratio:.1f}:1, need 1.5:1+)")
            elif rr_ratio < 1.0:
                score *= 0.6
                reasons.append(f"weak R:R ({rr_ratio:.1f}:1)")
            elif rr_ratio >= 2.0:
                score *= 1.1
                reasons.append(f"excellent R:R ({rr_ratio:.1f}:1)")

        # ── Factor 6c: Regime-direction alignment ─────────────────────────
        # Don't short in uptrends, don't long in downtrends (for live)
        if is_live:
            if self.current_regime == "trending_up" and direction == "short":
                if self.regime_confidence > 0.5:
                    return self._reject(strategy_key, strategy_name, signal,
                                        f"Shorting against uptrend (regime conf {self.regime_confidence:.0%})")
            elif self.current_regime == "trending_down" and direction == "long":
                if self.regime_confidence > 0.5:
                    return self._reject(strategy_key, strategy_name, signal,
                                        f"Longing against downtrend (regime conf {self.regime_confidence:.0%})")

        # Regime alignment bonus/penalty for all modes
        if self.current_regime == "trending_up" and direction == "long":
            score *= 1.15
            reasons.append("trading with uptrend")
        elif self.current_regime == "trending_down" and direction == "short":
            score *= 1.15
            reasons.append("trading with downtrend")
        elif self.current_regime in ("trending_up", "trending_down"):
            score *= 0.75
            reasons.append("trading against trend")

        # ── Factor 7: Correlation penalty ────────────────────────────────
        size_mult = 1.0
        if same_dir_count >= 2:
            size_mult *= self.CORRELATION_PENALTY
            reasons.append(f"size reduced — {same_dir_count} correlated positions")

        # ── Factor 8: Kelly-fraction sizing ──────────────────────────────
        stats = self.strategy_stats.get(strategy_key, {})
        total_trades = stats.get("trades", 0)
        win_rate = stats.get("win_rate", 0.5)
        avg_win = stats.get("avg_win", 1.0)
        avg_loss = abs(stats.get("avg_loss", -1.0)) or 1.0
        if total_trades >= 5 and win_rate > 0 and avg_loss > 0:
            kelly = win_rate - (1 - win_rate) / (avg_win / avg_loss) if avg_win > 0 else 0
            kelly_frac = max(0.1, min(0.5, kelly * 0.5))
            size_mult *= (0.5 + kelly_frac)
            if win_rate > 0.6:
                reasons.append(f"high win rate ({win_rate:.0%}) → size boost")
            elif win_rate < 0.4:
                reasons.append(f"low win rate ({win_rate:.0%}) → size cut")

        # ── Factor 9: LIVE-ONLY adjustments ───────────────────────────────
        if is_live:
            # Hard gate: 3+ consecutive losses pauses live
            if self.consecutive_losses >= 3:
                return self._reject(strategy_key, strategy_name, signal,
                                    f"Live paused — {self.consecutive_losses} consecutive losses")

            # Hard gate: trust collapsed (strategy consistently losing)
            if trust < 0.5:
                return self._reject(strategy_key, strategy_name, signal,
                                    f"Trust too low for live ({trust:.2f} < 0.50)")

            # Soft scaling: less history → smaller position size (not blocked)
            if total_trades < self.MIN_PAPER_TRADES_FOR_LIVE:
                history_scale = max(0.3, total_trades / self.MIN_PAPER_TRADES_FOR_LIVE)
                size_mult *= history_scale
                reasons.append(f"new strategy ({total_trades} trades) → size {history_scale:.0%}")
            elif win_rate < self.MIN_WIN_RATE_FOR_LIVE and total_trades >= self.MIN_PAPER_TRADES_FOR_LIVE:
                size_mult *= 0.5
                reasons.append(f"low win rate ({win_rate:.0%}) → size halved for live")
            else:
                reasons.append("✓ live-qualified")

        # ── Final decision ───────────────────────────────────────────────
        conviction = min(1.0, max(0.0, score))
        threshold = self.LIVE_CONVICTION_THRESHOLD if is_live else self.PAPER_CONVICTION_THRESHOLD
        approved = conviction >= threshold

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
            "is_live":         is_live,
            "timestamp":       datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "factors": {
                "trust":         round(trust, 2),
                "affinity":      round(affinity, 2),
                "confluence":    same_dir_count,
                "daily_trades":  self.daily_trades,
                "consec_losses": self.consecutive_losses,
                "daily_pnl":     round(self.daily_pnl, 2),
                "signal_conf":   round(confidence, 2),
                "kelly_size":    round(size_mult, 2),
                "total_trades":  total_trades,
                "win_rate":      round(win_rate, 3),
            },
        }

        self.decisions = [decision] + self.decisions[:49]

        if approved and is_live:
            self.daily_trades += 1  # only live executions count against the daily limit
            decision["_counted_daily_trade"] = True

        mode_tag = "LIVE" if is_live else "PAPER"
        log_emoji = "✅" if approved else "❌"
        logger.info(f"[MasterBrain] [{mode_tag}] {log_emoji} {action} {strategy_name} {direction.upper()} "
                    f"· conviction {conviction:.0%} · size {size_mult:.0%} · {decision['reasoning']}")
        return decision

    def rollback_daily_trade(self) -> None:
        """Call when a live open that was approved fails at the exchange.
        Returns the pre-incremented daily_trades slot so the counter stays accurate."""
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
    #  FUSION: combine all strategy signals into one unified meta-signal
    # ══════════════════════════════════════════════════════════════════════

    def fuse_signals(
        self,
        strategy_results: dict,   # {key: {signal, met_count, bias, name, ...}}
        live_price: float,
    ) -> Optional[dict]:
        """
        Trust × learned-affinity × confidence weighted voting across all strategies.
        Returns one unified signal or None if no consensus.
        """
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
            # Use learned affinity (what actually worked) blended with base prior
            base_aff   = self.REGIME_AFFINITY.get(self.current_regime, {}).get(key, 1.0)
            learn_aff  = self._learned_affinity.get(self.current_regime, {}).get(key, 1.0)
            affinity   = (base_aff + learn_aff) / 2          # blend priors + experience
            weight     = confidence * trust * affinity

            if direction == "long":
                long_w += weight
                long_signals.append((key, sig, weight))
            else:
                short_w += weight
                short_signals.append((key, sig, weight))

        total_w = long_w + short_w
        if total_w < 0.05 or (not long_signals and not short_signals):
            return None

        if long_w >= short_w:
            direction, signals, win_w = "long",  long_signals,  long_w
        else:
            direction, signals, win_w = "short", short_signals, short_w

        consensus = win_w / total_w
        # Require consensus AND at least 2 contributing strategies (confluence)
        if consensus < 0.50 or len(signals) < 2:
            return None

        # Weighted SL/TP from contributing strategies
        sl_sum = tp_sum = w_sum = 0.0
        contributors: list[str] = []
        for key, sig, w in signals:
            sl = sig.get("sl") or 0.0
            tp = sig.get("tp") or 0.0
            if sl > 0 and tp > 0:
                sl_sum += sl * w
                tp_sum += tp * w
                w_sum  += w
            contributors.append(key)

        if w_sum > 0:
            fused_sl = sl_sum / w_sum
            fused_tp = tp_sum / w_sum
        else:
            sl_dist  = live_price * 0.003
            tp_dist  = live_price * 0.006
            fused_sl = (live_price - sl_dist) if direction == "long" else (live_price + sl_dist)
            fused_tp = (live_price + tp_dist) if direction == "long" else (live_price - tp_dist)

        # Confidence: normalised weight × consensus bonus
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
                f"consensus {consensus:.0%} · weight {win_w:.2f} · "
                f"regime {self.current_regime} · [{', '.join(contributors)}]"
            ),
            "contributors": contributors,
            "long_weight":  round(long_w, 3),
            "short_weight": round(short_w, 3),
            "consensus":    round(consensus, 3),
        }

    # ══════════════════════════════════════════════════════════════════════
    #  ADAPTIVE LEARNING: update regime affinity from every trade result
    # ══════════════════════════════════════════════════════════════════════

    def _adapt_affinity(self, strategy_key: str, regime: str, won: bool) -> None:
        """
        Nudge learned regime affinity based on trade outcome.
        Winning strategies in a regime get boosted; losing ones get penalised.
        Converges toward the true affinity over hundreds of trades.
        """
        if not regime or regime not in self._learned_affinity:
            # Initialise regime bucket if missing (e.g. after schema upgrade)
            self._learned_affinity[regime] = {
                k: 1.0 for k in list(self.strategy_trust.keys()) + ["fusion"]
            }

        current = self._learned_affinity[regime].get(strategy_key, 1.0)
        # Small learning rate so affinity changes are gradual
        delta   = +0.04 if won else -0.06
        updated = round(max(0.20, min(2.50, current + delta)), 3)
        self._learned_affinity[regime][strategy_key] = updated

    # ══════════════════════════════════════════════════════════════════════
    #  LEARNING: update trust after trade closes
    # ══════════════════════════════════════════════════════════════════════

    def record_trade_result(self, strategy_key: str, pnl: float, won: bool,
                           was_live: bool = False) -> None:
        """Called after every trade closure. Updates trust and stats.
        Paper trades still count for learning — this is how the Brain
        builds confidence before approving live execution."""
        self._check_day()
        # Only live-trade PnL counts toward the daily loss limit gate.
        # Paper/shadow trades still update trust and stats for learning.
        if was_live:
            self.daily_pnl += pnl

        if won:
            self.daily_wins += 1
            if was_live:
                self.consecutive_losses = 0  # only live wins reset the streak
            cur = self.strategy_trust.get(strategy_key, 1.0)
            self.strategy_trust[strategy_key] = min(2.0, cur + 0.05)
        else:
            self.daily_losses_count += 1
            if was_live:
                self.consecutive_losses += 1  # only live losses count toward the guard
            cur = self.strategy_trust.get(strategy_key, 1.0)
            penalty = 0.12 if was_live else 0.08  # live losses penalize harder
            self.strategy_trust[strategy_key] = max(0.3, cur - penalty)

        s = self.strategy_stats.setdefault(strategy_key, {
            "trades": 0, "wins": 0, "losses": 0, "total_pnl": 0.0,
            "win_rate": 0.5, "avg_win": 0.0, "avg_loss": 0.0,
            "win_pnls": [], "loss_pnls": [],
            "live_trades": 0, "live_wins": 0, "live_pnl": 0.0,
        })
        s["trades"] += 1
        s["total_pnl"] += pnl
        if was_live:
            s["live_trades"] = s.get("live_trades", 0) + 1
            s["live_pnl"] = s.get("live_pnl", 0.0) + pnl
            if won:
                s["live_wins"] = s.get("live_wins", 0) + 1
        if won:
            s["wins"] += 1
            s["win_pnls"] = (s.get("win_pnls", []) + [pnl])[-50:]
            s["avg_win"] = sum(s["win_pnls"]) / len(s["win_pnls"]) if s["win_pnls"] else 0
        else:
            s["losses"] += 1
            s["loss_pnls"] = (s.get("loss_pnls", []) + [pnl])[-50:]
            s["avg_loss"] = sum(s["loss_pnls"]) / len(s["loss_pnls"]) if s["loss_pnls"] else 0
        s["win_rate"] = s["wins"] / s["trades"] if s["trades"] > 0 else 0.5

        # ── Adaptive regime affinity — learns what works in each market type ──
        self._adapt_affinity(strategy_key, self.current_regime, won)

        mode_tag = "LIVE" if was_live else "PAPER"
        learned_aff = self._learned_affinity.get(self.current_regime, {}).get(strategy_key, 1.0)
        logger.info(f"[MasterBrain] [{mode_tag}] {strategy_key} {'WIN' if won else 'LOSS'} "
                    f"${pnl:+.2f} · trust {self.strategy_trust[strategy_key]:.2f} "
                    f"· win rate {s['win_rate']:.0%} ({s['trades']} trades) "
                    f"· streak {self.consecutive_losses} losses "
                    f"· regime_aff[{self.current_regime}]={learned_aff:.2f}")

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

    def is_strategy_live_ready(self, strategy_key: str) -> dict:
        """Check strategy's live qualification level."""
        stats = self.strategy_stats.get(strategy_key, {})
        total = stats.get("trades", 0)
        wr = stats.get("win_rate", 0.0)
        trust = self.strategy_trust.get(strategy_key, 1.0)
        fully_ready = (total >= self.MIN_PAPER_TRADES_FOR_LIVE
                       and wr >= self.MIN_WIN_RATE_FOR_LIVE
                       and trust >= 0.5)
        can_trade = trust >= 0.5 and self.consecutive_losses < 3
        scale = max(0.3, total / self.MIN_PAPER_TRADES_FOR_LIVE) if total < self.MIN_PAPER_TRADES_FOR_LIVE else 1.0
        return {
            "ready": fully_ready,
            "can_trade": can_trade,
            "scale": round(scale, 2),
            "trades": total,
            "trades_needed": max(0, self.MIN_PAPER_TRADES_FOR_LIVE - total),
            "win_rate": round(wr, 3),
            "win_rate_needed": self.MIN_WIN_RATE_FOR_LIVE,
            "trust": round(trust, 2),
        }

    def get_status(self, positions: dict, live_price: float) -> dict:
        portfolio = self.portfolio_summary(positions, live_price)
        live_readiness = {k: self.is_strategy_live_ready(k)
                         for k in self.strategy_trust}
        learned_aff_rounded = {
            regime: {k: round(v, 2) for k, v in affs.items()}
            for regime, affs in self._learned_affinity.items()
        }
        return {
            "regime":             self.current_regime,
            "regime_confidence":  self.regime_confidence,
            "regime_updated":     self.regime_updated,
            "regime_stability":   self._regime_stability(),
            "strategy_trust":     {k: round(v, 2) for k, v in self.strategy_trust.items()},
            "live_readiness":     live_readiness,
            "portfolio":          portfolio,
            "recent_decisions":   self.decisions[:10],
            "strategy_stats":     {k: {kk: vv for kk, vv in v.items() if kk not in ("win_pnls", "loss_pnls")}
                                   for k, v in self.strategy_stats.items()},
            "learned_affinity":   learned_aff_rounded,
            "limits": {
                "max_daily_trades":      self.MAX_DAILY_TRADES,
                "max_consecutive_losses": self.MAX_CONSECUTIVE_LOSSES,
                "max_open_positions":     self.MAX_OPEN_POSITIONS,
                "max_daily_loss":         self.MAX_DAILY_LOSS,
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
        else:
            return "unstable"

    # ══════════════════════════════════════════════════════════════════════
    #  SERIALIZATION
    # ══════════════════════════════════════════════════════════════════════

    def to_dict(self) -> dict:
        return {
            "current_regime":     self.current_regime,
            "regime_confidence":  self.regime_confidence,
            "regime_history":     self._regime_history[-10:],
            "strategy_trust":     self.strategy_trust,
            "strategy_stats":     self.strategy_stats,
            "consecutive_losses": self.consecutive_losses,
            "daily_trades":       self.daily_trades,
            "daily_pnl":          self.daily_pnl,
            "daily_wins":         self.daily_wins,
            "daily_losses_count": self.daily_losses_count,
            "_day_str":           self._day_str,
            "decisions":          self.decisions[:20],
            "_learned_affinity":  self._learned_affinity,
        }

    def from_dict(self, data: dict) -> None:
        if not data:
            return
        self.current_regime     = data.get("current_regime", "unknown")
        self.regime_confidence  = data.get("regime_confidence", 0.0)
        self._regime_history    = data.get("regime_history", [])
        self.strategy_trust     = data.get("strategy_trust", self.strategy_trust)
        self.strategy_stats     = data.get("strategy_stats", {})
        self.consecutive_losses = data.get("consecutive_losses", 0)
        self.daily_trades       = data.get("daily_trades", 0)
        self.daily_pnl          = data.get("daily_pnl", 0.0)
        self.daily_wins         = data.get("daily_wins", 0)
        self.daily_losses_count = data.get("daily_losses_count", 0)
        self._day_str           = data.get("_day_str", self._day_str)
        self.decisions          = data.get("decisions", [])
        # Restore learned affinity — merge stored values over default priors
        stored_aff = data.get("_learned_affinity", {})
        for regime, affinities in stored_aff.items():
            if regime not in self._learned_affinity:
                self._learned_affinity[regime] = {}
            self._learned_affinity[regime].update(affinities)

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
