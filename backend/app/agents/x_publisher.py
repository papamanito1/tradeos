"""
XPublisher — Master Agent Twitter/X Integration
================================================
Posts filtered trade signals, trade results, and market analysis to X.

Post types:
  1. Trade signal   — when Brain approves a LIVE trade with conviction >= threshold
  2. Trade result   — when a LIVE position closes (shows win/loss + P&L)
  3. Hourly update  — regime + open positions + daily P&L (only if live positions are open)
  4. Daily summary  — midnight UTC digest (always posted)
  5. Weekly recap   — Sunday 20:00 UTC 7-day performance

Filter rules (avoids spam):
  - Signals:  is_live=True AND conviction >= SIGNAL_MIN_CONVICTION (default 0.70)
  - Results:  is_live=True only
  - Hourly:   only when at least 1 live position is open; max 1 post / 60 min
  - Daily:    always at midnight UTC
  - Weekly:   Sunday 20:00 UTC

Requires env vars:
  X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET

All methods are fire-and-forget safe — any tweepy or network error is caught
and logged without crashing the agent.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Tunable constants ──────────────────────────────────────────────────────────
SIGNAL_MIN_CONVICTION = 0.70   # post signal only when conviction ≥ 70%
SIGNAL_COOLDOWN_SEC   = 900    # 15-minute gap between any two signal posts
HOURLY_COOLDOWN_SEC   = 3300   # ~55 min gap so we don't double-post


class XPublisher:
    """
    Wraps tweepy to post agent activity to X.
    Instantiated once in PersistentAgent; does nothing if env vars are absent.
    """

    def __init__(self) -> None:
        self._client = None
        self._enabled = False
        self._last_signal_ts: float = 0
        self._last_hourly_ts: float = 0
        self._init_client()

    def _init_client(self) -> None:
        api_key    = os.environ.get("X_API_KEY", "")
        api_secret = os.environ.get("X_API_SECRET", "")
        acc_token  = os.environ.get("X_ACCESS_TOKEN", "")
        acc_secret = os.environ.get("X_ACCESS_TOKEN_SECRET", "")

        if not all([api_key, api_secret, acc_token, acc_secret]):
            logger.info("[XPublisher] X env vars not set — posting disabled")
            return

        try:
            import tweepy
            self._client = tweepy.Client(
                consumer_key=api_key,
                consumer_secret=api_secret,
                access_token=acc_token,
                access_token_secret=acc_secret,
            )
            self._enabled = True
            logger.info("[XPublisher] X client initialised — posting enabled")
        except ImportError:
            logger.warning("[XPublisher] tweepy not installed — posting disabled")
        except Exception as e:
            logger.warning(f"[XPublisher] Failed to init X client: {e}")

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── Internal post helper ───────────────────────────────────────────────────

    def _post(self, text: str) -> bool:
        """Send a tweet. Returns True on success, False on any error."""
        if not self._enabled or not self._client:
            return False
        try:
            self._client.create_tweet(text=text[:280])
            logger.info(f"[XPublisher] Posted: {text[:60]}…")
            return True
        except Exception as e:
            logger.warning(f"[XPublisher] Post failed: {e}")
            return False

    def _post_async(self, text: str) -> None:
        """Fire-and-forget wrapper safe to call from sync code."""
        async def _send():
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._post, text)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(_send())
        except Exception as e:
            logger.debug(f"[XPublisher] _post_async scheduling error: {e}")

    # ── 1. Trade Signal ────────────────────────────────────────────────────────

    def post_signal(
        self,
        strategy_name: str,
        direction: str,
        entry_price: float,
        sl_price: float,
        tp_price: float,
        conviction: float,
        size_usdc: float,
        regime: str,
    ) -> None:
        """Post a new live trade signal. Filtered by conviction and cooldown."""
        if not self._enabled:
            return
        if conviction < SIGNAL_MIN_CONVICTION:
            logger.debug(f"[XPublisher] Signal skipped — conviction {conviction:.0%} < {SIGNAL_MIN_CONVICTION:.0%}")
            return
        now = time.time()
        if now - self._last_signal_ts < SIGNAL_COOLDOWN_SEC:
            logger.debug("[XPublisher] Signal skipped — cooldown active")
            return

        dir_emoji = "🟢 LONG" if direction == "long" else "🔴 SHORT"
        regime_label = regime.replace("_", " ").title()
        rr = round((tp_price - entry_price) / (entry_price - sl_price), 2) if sl_price and tp_price else 0

        text = (
            f"TRADE SIGNAL — BTC/USDT\n"
            f"{dir_emoji}\n"
            f"Entry:  ${entry_price:,.0f}\n"
            f"SL:     ${sl_price:,.0f}\n"
            f"TP:     ${tp_price:,.0f}\n"
            f"R:R     1:{rr:.1f}\n"
            f"Conviction: {conviction:.0%}\n"
            f"Strategy: {strategy_name}\n"
            f"Regime: {regime_label}\n"
            f"\n"
            f"Live AI trading on BingX\n"
            f"#Bitcoin #BTC #CryptoTrading #AlgoTrading"
        )
        self._post_async(text)
        self._last_signal_ts = now

    # ── 2. Trade Result ────────────────────────────────────────────────────────

    def post_result(
        self,
        strategy_name: str,
        direction: str,
        entry_price: float,
        exit_price: float,
        pnl_usd: float,
        reason: str,
        duration_min: Optional[float] = None,
    ) -> None:
        """Post when a live position closes."""
        if not self._enabled:
            return

        won = pnl_usd >= 0
        result_emoji = "✅ WIN" if won else "❌ LOSS"
        dir_label = direction.upper()
        pnl_str = f"+${pnl_usd:.2f}" if won else f"-${abs(pnl_usd):.2f}"
        reason_label = {"tp": "Take Profit hit", "sl": "Stop Loss hit",
                        "manual": "Manually closed"}.get(reason, reason.replace("_", " ").title())
        dur_str = f"\nDuration: {duration_min:.0f}m" if duration_min else ""

        text = (
            f"TRADE RESULT — BTC/USDT {result_emoji}\n"
            f"{dir_label} @ ${entry_price:,.0f} → ${exit_price:,.0f}\n"
            f"P&L: {pnl_str}\n"
            f"Exit: {reason_label}\n"
            f"Strategy: {strategy_name}{dur_str}\n"
            f"\n"
            f"Live AI trading on BingX\n"
            f"#Bitcoin #BTC #CryptoTrading"
        )
        self._post_async(text)

    # ── 3. Hourly Update ───────────────────────────────────────────────────────

    def post_hourly(
        self,
        open_positions: list[dict],
        daily_pnl: float,
        regime: str,
        regime_stability: str,
    ) -> None:
        """Post an hourly market snapshot. Only if live positions are open."""
        if not self._enabled:
            return

        live_positions = [p for p in open_positions if p and p.get("mode") == "live"]
        if not live_positions:
            logger.debug("[XPublisher] Hourly skipped — no live positions open")
            return

        now = time.time()
        if now - self._last_hourly_ts < HOURLY_COOLDOWN_SEC:
            logger.debug("[XPublisher] Hourly skipped — cooldown active")
            return

        regime_label = regime.replace("_", " ").title()
        pnl_str = f"+${daily_pnl:.2f}" if daily_pnl >= 0 else f"-${abs(daily_pnl):.2f}"
        pos_lines = []
        for p in live_positions[:3]:  # show at most 3
            d = p.get("direction", "?").upper()
            entry = p.get("entry", 0)
            cur = p.get("current_price", entry)
            unr = p.get("unrealized_pnl", 0)
            unr_str = f"+${unr:.2f}" if unr >= 0 else f"-${abs(unr):.2f}"
            pos_lines.append(f"  {d} ${entry:,.0f} (unrealised {unr_str})")

        positions_block = "\n".join(pos_lines) if pos_lines else "  —"
        utc_hour = datetime.now(timezone.utc).strftime("%H:%M UTC")

        text = (
            f"HOURLY UPDATE — {utc_hour}\n"
            f"Regime: {regime_label} ({regime_stability})\n"
            f"Daily P&L: {pnl_str}\n"
            f"Live positions ({len(live_positions)}):\n"
            f"{positions_block}\n"
            f"\n"
            f"#Bitcoin #BTC #CryptoTrading"
        )
        self._post_async(text)
        self._last_hourly_ts = now

    # ── 4. Daily Summary ───────────────────────────────────────────────────────

    def post_daily(
        self,
        stats: dict,
        strategy_stats: dict,
        regime: str,
        live_pnl: float,
    ) -> None:
        """Post a midnight UTC daily digest."""
        if not self._enabled:
            return

        date_str = datetime.now(timezone.utc).strftime("%b %-d")
        total_trades = stats.get("total_trades", 0)
        wins = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        win_rate = stats.get("win_rate", 0)
        pnl_str = f"+${live_pnl:.2f}" if live_pnl >= 0 else f"-${abs(live_pnl):.2f}"

        # Find best performing strategy today
        best_strat = ""
        best_pnl = None
        for key, s in strategy_stats.items():
            spnl = s.get("live_pnl", 0) or 0
            if best_pnl is None or spnl > best_pnl:
                best_pnl = spnl
                t = s.get("live_trades", 0) or 0
                w = s.get("live_wins", 0) or 0
                l = t - w
                best_strat = f"{key.upper()} ({w}W/{l}L)"

        regime_label = regime.replace("_", " ").title()

        text = (
            f"TradeOS Daily Report — {date_str}\n"
            f"\n"
            f"Trades: {total_trades}  |  {wins}W / {losses}L\n"
            f"Win Rate: {win_rate:.1f}%\n"
            f"Live P&L: {pnl_str}\n"
        )
        if best_strat:
            text += f"Top Strategy: {best_strat}\n"
        text += (
            f"Regime: {regime_label}\n"
            f"\n"
            f"AI-driven algo trading on BingX\n"
            f"#Bitcoin #BTC #Trading #AlgoTrading #Crypto"
        )
        self._post_async(text)

    # ── 5. Weekly Recap ────────────────────────────────────────────────────────

    def post_weekly(
        self,
        stats: dict,
        strategy_stats: dict,
        account_balance: float,
        start_balance: Optional[float] = None,
    ) -> None:
        """Post a Sunday 20:00 UTC weekly performance recap."""
        if not self._enabled:
            return

        total_trades = stats.get("total_trades", 0)
        wins = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        win_rate = stats.get("win_rate", 0)
        total_pnl = stats.get("total_pnl", 0)
        pnl_str = f"+${total_pnl:.2f}" if total_pnl >= 0 else f"-${abs(total_pnl):.2f}"
        best = stats.get("best_trade", 0)
        worst = stats.get("worst_trade", 0)

        # Balance change
        bal_line = ""
        if start_balance and account_balance:
            change = account_balance - start_balance
            change_str = f"+${change:.2f}" if change >= 0 else f"-${abs(change):.2f}"
            bal_line = f"Balance: ${account_balance:.2f} ({change_str} week)\n"

        # Strategy breakdown (live only)
        strat_lines = []
        for key, s in strategy_stats.items():
            lt = s.get("live_trades", 0) or 0
            lw = s.get("live_wins", 0) or 0
            if lt > 0:
                strat_lines.append(f"  {key.upper()}: {lw}W/{lt-lw}L")

        strat_block = "\n".join(strat_lines[:4]) if strat_lines else "  No live trades this week"

        week_str = datetime.now(timezone.utc).strftime("Week of %b %-d")
        text = (
            f"WEEKLY RECAP — {week_str}\n"
            f"\n"
            f"Trades: {total_trades}  |  {wins}W / {losses}L\n"
            f"Win Rate: {win_rate:.1f}%\n"
            f"Total P&L: {pnl_str}\n"
            f"Best trade: +${best:.2f}  |  Worst: -${abs(worst):.2f}\n"
            f"{bal_line}"
            f"\n"
            f"Strategy breakdown:\n"
            f"{strat_block}\n"
            f"\n"
            f"AI algo trading · powered by TradeOS on BingX\n"
            f"#Bitcoin #BTC #Crypto #AlgoTrading #TradingResults"
        )
        self._post_async(text)
