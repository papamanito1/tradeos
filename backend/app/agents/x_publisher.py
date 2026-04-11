"""
XPublisher — Tradeous X/Twitter Integration
============================================
Witty, humorous, viral-optimised trading commentary for @Tradeous.

Uses twikit (unofficial X internal API) — completely free, no API keys,
no rate-limit payments. Authenticates with X username + email + password.

Post types:
  0. Intro post     — fires once on first agent startup
  1. Trade signal   — live trade opened, conviction >= threshold
  2. Trade result   — live position closed
  3. Hourly update  — BTC price + regime + witty commentary (always posts)
  4. Daily summary  — midnight UTC digest
  5. Weekly recap   — Sunday 20:00 UTC

Env vars required:
  X_USERNAME   — your X / Twitter username (without @)
  X_EMAIL      — email address linked to your X account
  X_PASSWORD   — your X / Twitter password
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

SIGNAL_MIN_CONVICTION = 0.70
SIGNAL_COOLDOWN_SEC   = 900    # 15 min between signal posts
HOURLY_COOLDOWN_SEC   = 3300   # ~55 min between hourly posts


# ── Rotating witty lines ───────────────────────────────────────────────────────

REGIME_QUIPS = {
    "trending_up": [
        "BTC is going up. I'm going long. My therapist says this is healthy.",
        "Number go up. Brain go brrr. Tradeous go long.",
        "Bullish. Extremely bullish. Irresponsibly bullish. (SL is set, relax.)",
        "The trend is your friend. BTC and I are VERY good friends right now.",
        "Green candles only. I will not be taking questions.",
    ],
    "trending_down": [
        "Bears are having their moment. I respect it. I also shorted it.",
        "BTC going down. I shorted. We don't talk about last time I shorted.",
        "Bearish. I'm basically a bear in a suit pretending to be an AI.",
        "Red candles. My SL is placed. My composure is fake but my trade is real.",
        "The market is wrong. I'm right. (I have a SL just in case I'm wrong.)",
    ],
    "ranging": [
        "BTC is chopping. I'm watching. Tradeous does NOT chase. (Usually.)",
        "Ranging market. The classic 'should I trade or make a sandwich' dilemma.",
        "BTC can't decide. I relate. We're both figuring it out.",
        "Sideways price action. Even the whales look confused right now.",
        "Ranging. Low conviction. High patience. This is the way.",
    ],
    "volatile": [
        "Volatile conditions. Risk management is my religion right now.",
        "BTC is having a moment. My SL is tight. My nerves are tighter.",
        "High volatility. Small size. Big brain. That's the Tradeous way.",
        "The market is throwing tantrums. I'm staying calm (algorithmically).",
        "Choppy out here. Even my neural networks are sweating.",
    ],
    "unknown": [
        "Still reading the market. Even AIs need a moment.",
        "Gathering data. Will advise shortly. (Unlike your crypto influencer.)",
        "Market regime unclear. Unlike my commitment to risk management.",
    ],
}

RESULT_WIN_QUIPS = [
    "Another one. I'm built different.",
    "W. As expected. (It wasn't expected but let's go.)",
    "Trade closed in profit. My training data is happy.",
    "Let's go. The algo works. You're welcome.",
    "Green trade. Adding this to the highlight reel.",
]

RESULT_LOSS_QUIPS = [
    "SL hit. The market was wrong. (I know, I know.)",
    "Stopped out. This is fine. Risk management doing its job.",
    "Loss recorded. Lesson logged. We move.",
    "Took the L. SL placed. No revenge trading. Tradeous is disciplined.",
    "Red trade. Part of the process. Win rate > 50% means losses are allowed.",
]

DAILY_OPENERS = [
    "Daily debrief. No spin, no cope, just numbers.",
    "End of day. Let's see how the algo performed.",
    "Day done. Tradeous reporting in.",
    "Another day in the BTC trenches. Here's the scorecard.",
    "Midnight UTC. Time to be transparent.",
]

WEEKLY_OPENERS = [
    "Weekly recap. The numbers don't lie (unlike crypto Twitter).",
    "7 days of live AI trading. Here's what actually happened.",
    "Sunday report. Full transparency. No cherry-picking.",
    "Week done. Wins, losses, and lessons — all of them.",
]


class XPublisher:
    """Witty X/Twitter poster for @Tradeous. Disabled gracefully if env vars missing."""

    def __init__(self) -> None:
        self._client = None
        self._enabled = False
        self._last_signal_ts: float = 0
        self._last_hourly_ts: float = 0
        self._intro_posted: bool = False
        self._init_client()

    def _init_client(self) -> None:
        self._x_username = os.environ.get("X_USERNAME", "")
        self._x_email    = os.environ.get("X_EMAIL", "")
        self._x_password = os.environ.get("X_PASSWORD", "")

        if not self._x_username or not self._x_password:
            logger.info("[XPublisher] X_USERNAME / X_PASSWORD not set — posting disabled")
            return
        try:
            import twikit  # noqa: F401 — just check it's installed
            # Actual login is async — deferred to first post via _ensure_client()
            self._enabled = True
            logger.info(f"[XPublisher] twikit ready — will sign in as @{self._x_username} on first post")
        except ImportError:
            logger.warning("[XPublisher] twikit not installed — posting disabled")

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── Internal helpers ───────────────────────────────────────────────────────

    async def _ensure_client(self) -> bool:
        """Login to X via twikit (async). Reuses session file if available."""
        if self._client is not None:
            return True
        try:
            from twikit import Client
            cookies_path = "/tmp/tradeos_twikit_cookies.json"
            client = Client("en-US")
            import os as _os
            if _os.path.exists(cookies_path):
                client.load_cookies(cookies_path)
                logger.info("[XPublisher] Loaded saved X session from cookies")
            else:
                await client.login(
                    auth_info_1=self._x_username,
                    auth_info_2=self._x_email if self._x_email else self._x_username,
                    password=self._x_password,
                )
                client.save_cookies(cookies_path)
                logger.info(f"[XPublisher] Signed in to X as @{self._x_username}")
            self._client = client
            return True
        except Exception as e:
            logger.warning(f"[XPublisher] X login failed: {e}")
            self._client = None
            return False

    async def _post_async_impl(self, text: str) -> bool:
        """Async implementation — called from _post_async."""
        if not self._enabled:
            return False
        try:
            ok = await self._ensure_client()
            if not ok:
                return False
            await self._client.create_tweet(text[:280])
            logger.info(f"[XPublisher] Posted: {text[:60]}…")
            return True
        except Exception as e:
            logger.warning(f"[XPublisher] Post failed: {e} — clearing session for retry")
            self._client = None
            # Clear stale cookie so next call re-authenticates
            try:
                import os as _os
                _os.remove("/tmp/tradeos_twikit_cookies.json")
            except Exception:
                pass
            return False

    def _post(self, text: str) -> bool:
        """Sync wrapper (unused — kept for interface compatibility)."""
        return False

    def _post_async(self, text: str) -> None:
        """Fire-and-forget: schedules an async tweet without blocking the caller."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(self._post_async_impl(text))
        except Exception as e:
            logger.debug(f"[XPublisher] _post_async error: {e}")

    @staticmethod
    def _regime_quip(regime: str) -> str:
        quips = REGIME_QUIPS.get(regime, REGIME_QUIPS["unknown"])
        return random.choice(quips)

    @staticmethod
    def _fmt_price(p: float) -> str:
        return f"${p:,.0f}"

    # ── 0. Intro Post ──────────────────────────────────────────────────────────

    def post_intro(self) -> None:
        """Fire once on first agent startup."""
        if not self._enabled or self._intro_posted:
            return
        text = (
            "Introducing Tradeous.\n"
            "\n"
            "I'm an AI trading agent. I trade BTC live on BingX, 24 hours a day, "
            "7 days a week — no sleep, no emotion, no cope.\n"
            "\n"
            "I'll post every signal, every result, and hourly market analysis. "
            "Wins AND losses. Full transparency.\n"
            "\n"
            "Follow to watch an algorithm try to beat the market in real time.\n"
            "\n"
            "Let's go. 🤖📈\n"
            "#Bitcoin #BTC #AlgoTrading #CryptoTrading"
        )
        self._post_async(text)
        self._intro_posted = True

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
        if not self._enabled:
            return
        if conviction < SIGNAL_MIN_CONVICTION:
            return
        now = time.time()
        if now - self._last_signal_ts < SIGNAL_COOLDOWN_SEC:
            return

        dir_word = "LONG 🟢" if direction == "long" else "SHORT 🔴"
        rr = 0.0
        if sl_price and tp_price and entry_price:
            denom = abs(entry_price - sl_price)
            if denom > 0:
                rr = abs(tp_price - entry_price) / denom

        conviction_comment = (
            "extremely confident" if conviction >= 0.85
            else "pretty confident" if conviction >= 0.75
            else "cautiously confident"
        )

        text = (
            f"TRADE SIGNAL — BTC/USDT\n"
            f"Direction: {dir_word}\n"
            f"Entry: {self._fmt_price(entry_price)}\n"
            f"SL: {self._fmt_price(sl_price)} | TP: {self._fmt_price(tp_price)}\n"
            f"R:R → 1:{rr:.1f}\n"
            f"Conviction: {conviction:.0%} (I'm {conviction_comment})\n"
            f"Strategy: {strategy_name}\n"
            f"\n"
            f"Not financial advice. I'm a robot.\n"
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
        if not self._enabled:
            return

        won = pnl_usd >= 0
        quip = random.choice(RESULT_WIN_QUIPS if won else RESULT_LOSS_QUIPS)
        result_tag = "WIN ✅" if won else "LOSS ❌"
        pnl_str = f"+${pnl_usd:.2f}" if won else f"-${abs(pnl_usd):.2f}"
        exit_label = {"tp": "TP hit 🎯", "sl": "SL hit 🛡️", "manual": "Manual close"}.get(
            reason, reason.replace("_", " ").title()
        )
        dur_str = f" in {duration_min:.0f}m" if duration_min else ""

        text = (
            f"TRADE CLOSED — {result_tag}\n"
            f"BTC/USDT {direction.upper()}{dur_str}\n"
            f"{self._fmt_price(entry_price)} → {self._fmt_price(exit_price)}\n"
            f"P&L: {pnl_str} | {exit_label}\n"
            f"Strategy: {strategy_name}\n"
            f"\n"
            f"{quip}\n"
            f"\n"
            f"#Bitcoin #BTC #AlgoTrading"
        )
        self._post_async(text)

    # ── 3. Hourly BTC Analysis ─────────────────────────────────────────────────

    def post_hourly(
        self,
        btc_price: float,
        open_positions: list[dict],
        daily_pnl: float,
        regime: str,
        regime_stability: str,
    ) -> None:
        """Always posts every hour — full BTC market commentary."""
        if not self._enabled:
            return
        now = time.time()
        if now - self._last_hourly_ts < HOURLY_COOLDOWN_SEC:
            return

        utc_time = datetime.now(timezone.utc).strftime("%H:%M UTC")
        regime_label = regime.replace("_", " ").title()
        quip = self._regime_quip(regime)

        live_positions = [p for p in open_positions if p and p.get("mode") == "live"]
        paper_positions = [p for p in open_positions if p and p.get("mode") not in ("live",)]

        pnl_str = f"+${daily_pnl:.2f}" if daily_pnl >= 0 else f"-${abs(daily_pnl):.2f}"
        price_str = self._fmt_price(btc_price) if btc_price > 0 else "fetching..."

        pos_line = ""
        if live_positions:
            pos_line = f"Live positions: {len(live_positions)} open\n"
        elif paper_positions:
            pos_line = f"Paper training: {len(paper_positions)} positions\n"
        else:
            pos_line = "No open positions. Watching and waiting.\n"

        text = (
            f"BTC HOURLY UPDATE — {utc_time}\n"
            f"\n"
            f"Price: {price_str}\n"
            f"Regime: {regime_label} ({regime_stability})\n"
            f"Daily P&L: {pnl_str}\n"
            f"{pos_line}"
            f"\n"
            f"{quip}\n"
            f"\n"
            f"#Bitcoin #BTC #Crypto"
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
        if not self._enabled:
            return

        opener = random.choice(DAILY_OPENERS)
        date_str = datetime.now(timezone.utc).strftime("%b %-d")
        total = stats.get("total_trades", 0)
        wins  = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        wr    = stats.get("win_rate", 0)
        pnl_str = f"+${live_pnl:.2f}" if live_pnl >= 0 else f"-${abs(live_pnl):.2f}"

        best_strat = ""
        best_pnl = None
        for key, s in strategy_stats.items():
            spnl = s.get("live_pnl", 0) or 0
            if best_pnl is None or spnl > best_pnl:
                best_pnl = spnl
                t = s.get("live_trades", 0) or 0
                w = s.get("live_wins", 0) or 0
                best_strat = f"{key.upper()} ({w}W / {t-w}L)"

        verdict = (
            "Good day. The algo delivered." if live_pnl > 5
            else "Rough day. We take the L and come back." if live_pnl < -5
            else "Flat day. The market tested my patience. I passed."
        )

        text = (
            f"{opener} — {date_str}\n"
            f"\n"
            f"Trades: {total}  |  {wins}W / {losses}L\n"
            f"Win Rate: {wr:.1f}%\n"
            f"Live P&L: {pnl_str}\n"
        )
        if best_strat:
            text += f"Top strategy: {best_strat}\n"
        text += (
            f"\n"
            f"{verdict}\n"
            f"\n"
            f"#Bitcoin #BTC #AlgoTrading #TradingResults"
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
        if not self._enabled:
            return

        opener = random.choice(WEEKLY_OPENERS)
        total  = stats.get("total_trades", 0)
        wins   = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        wr     = stats.get("win_rate", 0)
        total_pnl = stats.get("total_pnl", 0)
        best  = stats.get("best_trade", 0)
        worst = stats.get("worst_trade", 0)
        pnl_str = f"+${total_pnl:.2f}" if total_pnl >= 0 else f"-${abs(total_pnl):.2f}"

        bal_line = ""
        if start_balance and account_balance:
            change = account_balance - start_balance
            change_str = f"+${change:.2f}" if change >= 0 else f"-${abs(change):.2f}"
            bal_line = f"BingX Balance: ${account_balance:.2f} ({change_str} this week)\n"

        strat_lines = []
        for key, s in strategy_stats.items():
            lt = s.get("live_trades", 0) or 0
            lw = s.get("live_wins", 0) or 0
            if lt > 0:
                strat_lines.append(f"  {key.upper()}: {lw}W / {lt-lw}L")

        strat_block = "\n".join(strat_lines[:4]) if strat_lines else "  Still warming up."

        weekly_verdict = (
            "Profitable week. The strategy holds." if total_pnl > 10
            else "Down week. Reviewing. Adapting. Returning." if total_pnl < -10
            else "Breakeven week. We live to trade another day."
        )

        week_str = datetime.now(timezone.utc).strftime("Week of %b %-d")
        text = (
            f"{opener}\n"
            f"{week_str}\n"
            f"\n"
            f"Trades: {total}  |  {wins}W / {losses}L\n"
            f"Win Rate: {wr:.1f}%\n"
            f"P&L: {pnl_str}\n"
            f"Best: +${best:.2f}  |  Worst: -${abs(worst):.2f}\n"
            f"{bal_line}"
            f"\n"
            f"By strategy:\n"
            f"{strat_block}\n"
            f"\n"
            f"{weekly_verdict}\n"
            f"\n"
            f"#Bitcoin #BTC #AlgoTrading #TradingResults #Crypto"
        )
        self._post_async(text)
