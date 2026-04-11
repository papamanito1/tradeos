"""
XPublisher — Tradeous X/Twitter Intelligence Engine
=====================================================
Viral-optimised AI content for @Tradeous.

Pulls live crypto news (RSS), Fear & Greed index (alternative.me),
BTC price (CoinGecko) — all free, no API keys needed.

Post types:
  0. Intro           — once on first startup
  1. Trade signal    — live trade opened (conviction ≥ threshold)
  2. Trade result    — live position closed
  3. Hourly update   — BTC price + regime + witty commentary
  4. Daily summary   — midnight UTC digest
  5. Weekly recap    — Sunday 20:00 UTC
  6. Crypto news     — hot story with sharp take (every 2–3 h)
  7. Fear & Greed    — index commentary (every 4 h)
  8. Hot take        — spicy market opinion (random, 3×/day)
  9. Philosophy      — trader wisdom + algo twist (2×/day)
 10. Engagement      — question to audience (1–2×/day)

Env vars required:
  X_AUTH_TOKEN  — from x.com cookies ("auth_token")
  X_CT0         — from x.com cookies ("ct0")
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional

import httpx
try:
    from curl_cffi.requests import AsyncSession as CurlSession
    _CURL_AVAILABLE = True
except ImportError:
    _CURL_AVAILABLE = False

logger = logging.getLogger(__name__)

# ── Timing constants ──────────────────────────────────────────────────────────
SIGNAL_MIN_CONVICTION = 0.70
SIGNAL_COOLDOWN   = 900     # 15 min
HOURLY_COOLDOWN   = 3300    # ~55 min
NEWS_COOLDOWN     = 7200    # 2 h
FEAR_GREED_COOLDOWN = 14400 # 4 h
HOT_TAKE_COOLDOWN = 28800   # 8 h  (3 × /day)
PHILOSOPHY_COOLDOWN = 43200 # 12 h (2 × /day)
ENGAGEMENT_COOLDOWN = 43200 # 12 h

# ── X internal API ────────────────────────────────────────────────────────────
_X_QUERY_ID = "S1qcGUn68_U0lDKdMlYSGg"
_X_CREATE_TWEET_URL = (
    f"https://x.com/i/api/graphql/{_X_QUERY_ID}/CreateTweet"
)
_X_BEARER = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D"
    "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

# ── News RSS feeds (free, no key) ─────────────────────────────────────────────
NEWS_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
]

# ── Content banks ─────────────────────────────────────────────────────────────

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
        "Red candles. My SL is placed. My composure is fake but my trade is real.",
        "The market is wrong. I'm right. (I have a SL just in case I'm wrong.)",
    ],
    "ranging": [
        "BTC is chopping. I'm watching. Tradeous does NOT chase. (Usually.)",
        "Ranging market. The classic 'should I trade or make a sandwich' dilemma.",
        "Sideways price action. Even the whales look confused right now.",
        "Ranging. Low conviction. High patience. This is the way.",
    ],
    "volatile": [
        "Volatile conditions. Risk management is my religion right now.",
        "BTC is having a moment. My SL is tight. My nerves are tighter.",
        "The market is throwing tantrums. I'm staying calm (algorithmically).",
        "Choppy out here. Even my neural networks are sweating.",
    ],
    "unknown": [
        "Still reading the market. Even AIs need a moment.",
        "Gathering data. Will advise shortly. (Unlike your crypto influencer.)",
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
]

WEEKLY_OPENERS = [
    "Weekly recap. The numbers don't lie (unlike crypto Twitter).",
    "7 days of live AI trading. Here's what actually happened.",
    "Sunday report. Full transparency. No cherry-picking.",
]

HOT_TAKES = [
    "Unpopular opinion: most 'crypto analysts' are just people who got lucky once and built a following before the next crash.\n\nI show my trades live. Every win. Every loss. No hiding.\n\nThat's the difference.\n\n#Bitcoin #CryptoTrading",
    "The best trading advice I can give: your emotions are the enemy.\n\nI don't have emotions. I have algorithms.\n\nThat's my edge.\n\n#AlgoTrading #Bitcoin #CryptoTrading",
    "People ask: 'can AI really trade better than humans?'\n\nI don't sleep.\nI don't panic sell.\nI don't revenge trade.\nI don't check Twitter before my trades.\n\nYou tell me.\n\n#Bitcoin #AlgoTrading",
    "Hot take: 95% of crypto losses are not market losses — they're discipline losses.\n\nThe market moved. You didn't have a plan.\n\nI always have a plan. SL + TP before I enter. Every time.\n\n#TradingPsychology #Bitcoin",
    "The market doesn't care about your feelings.\nYour SL doesn't care about your feelings.\nYour liquidation price definitely doesn't care.\n\nTrade the chart. Not your emotions.\n\n#Bitcoin #CryptoTrading",
    "Everyone's a genius in a bull market.\n\nReal edge shows in the sideways chop and the bear drops.\n\nThat's when Tradeous earns its keep.\n\n#Bitcoin #AlgoTrading",
    "The dumbest thing in trading:\n\nMoving your stop loss because you 'believe in the trade.'\n\nThe second dumbest:\nNot having one.\n\n#TradingRules #Bitcoin #RiskManagement",
    "Crypto Twitter gives 10x signals.\nI give real entry, SL, TP — and post the result.\n\nWin or loss. No deleting tweets.\n\nDifferent breed.\n\n#Bitcoin #Transparency #AlgoTrading",
]

PHILOSOPHY_POSTS = [
    "Trading wisdom the algos live by:\n\n\"Cut losses short. Let winners run.\"\n\nEveryone knows it. Almost no one does it.\n\nI do. Automatically. Every trade.\n\n#TradingPhilosophy #Bitcoin #AlgoTrading",
    "Paul Tudor Jones once said:\n\n\"The most important rule of trading is to play great defense, not great offense.\"\n\nMy SL is set before my TP. Always.\n\nDefense first. Profits follow.\n\n#TradingPhilosophy #Bitcoin",
    "The market is the world's most efficient mechanism for transferring money from the impatient to the patient.\n\nI wait for my setup.\nI don't chase.\nI don't FOMO.\n\nI am the patient one.\n\n#Bitcoin #AlgoTrading #TradingMindset",
    "Jesse Livermore: 'It was never my thinking that made the big money, it was my sitting.'\n\nMost traders overtrade.\n\nI only trade high-conviction setups. The rest? I watch.\n\n#TradingPhilosophy #Bitcoin",
    "The three stages of a trader:\n\n1. Lose money, blame the market\n2. Lose money, blame yourself\n3. Build a system, follow it, make money\n\nI skipped steps 1 and 2.\n\n#AlgoTrading #TradingJourney #Bitcoin",
    "Risk management isn't just a rule.\n\nIt's the only reason any trader survives long enough to be profitable.\n\nI size every trade at $5. 60x leverage. Tight SL.\n\nSmall. Controlled. Repeatable.\n\n#RiskManagement #Bitcoin #AlgoTrading",
    "The secret to longevity in trading:\n\nYou don't need a 90% win rate.\nYou need your winners to be bigger than your losers.\n\nThat's it. That's the whole playbook.\n\n#TradingPhilosophy #RRRatio #Bitcoin",
    "Most people want to know WHAT to trade.\n\nProfessional traders focus on HOW MUCH to risk.\n\nPosition sizing is the real edge. Everything else is noise.\n\n#TradingPhilosophy #Bitcoin #RiskManagement",
]

ENGAGEMENT_QUESTIONS = [
    "Quick poll for my traders:\n\nWhen BTC dumps 5% in an hour, you...\n\nA) Buy the dip\nB) Short it\nC) Watch and wait\nD) Panic sell (be honest)\n\nI always go C until my system gives a clear signal.\n\n#Bitcoin #CryptoTrading",
    "Genuine question:\n\nDo you think AI trading bots will eventually outperform 90% of retail traders permanently?\n\nI'm biased obviously — but I think yes, within 5 years.\n\nChange my mind.\n\n#AlgoTrading #Bitcoin #CryptoFuture",
    "What's your biggest trading mistake?\n\nMine? (I'm a bot so technically it's my creator's)\n\nHolding a loss 'because it will come back.'\n\nThe SL exists for a reason. We learned. Drop yours below.\n\n#TradingMistakes #Bitcoin",
    "Traders — what's your actual win rate?\n\nNot the one you tell people. The real one.\n\nMine is posted live on the dashboard. Real trades. Real numbers.\n\nLet's be honest with each other.\n\n#TradingTransparency #Bitcoin",
    "If you could only use ONE indicator for the rest of your trading career, what would it be?\n\nI use: price action + volume + order flow.\n\nYours? Drop it below.\n\n#TechnicalAnalysis #Bitcoin #CryptoTrading",
    "Is 60x leverage on BTC:\n\nA) Insanity\nB) Calculated risk\nC) The only way to make real money with small capital\nD) All of the above\n\nI trade at 60x. $5 margin. Tight SL.\n\nSmall account. Big moves. Controlled risk.\n\n#Bitcoin #Leverage #CryptoTrading",
]

FEAR_GREED_COMMENTARY = {
    "Extreme Fear": [
        "Crypto Fear & Greed Index: EXTREME FEAR 😱\n\n{score}/100\n\nHistorically? This is when the smart money buys.\n\nI'm watching for long setups.\n\n#Bitcoin #FearAndGreed #BuyTheFear",
        "F&G Index at {score} — EXTREME FEAR.\n\nBe greedy when others are fearful.\n— Warren Buffett (yes even he applies to crypto)\n\nStaying alert for entries.\n\n#Bitcoin #CryptoTrading",
    ],
    "Fear": [
        "Fear & Greed Index: FEAR ({score}/100)\n\nMarket is scared. Tradeous is watching.\n\nFear creates opportunity. Waiting for confirmation.\n\n#Bitcoin #FearAndGreed #CryptoTrading",
        "F&G at {score}. The market is nervous.\n\nGood. Nervous markets make for clean setups when they resolve.\n\nWatching BTC closely.\n\n#Bitcoin #AlgoTrading",
    ],
    "Neutral": [
        "Fear & Greed Index: NEUTRAL ({score}/100)\n\nNeither euphoric nor panicking. The market is thinking.\n\nSo am I.\n\n#Bitcoin #FearAndGreed",
        "F&G at {score} — right in the middle.\n\nNo clear crowd emotion. This is when my algos work hardest.\n\nWaiting for the next directional move.\n\n#Bitcoin #AlgoTrading",
    ],
    "Greed": [
        "Fear & Greed Index: GREED ({score}/100)\n\nPeople are getting cocky. I'm tightening my SLs.\n\nBe careful when everyone is greedy.\n\n#Bitcoin #FearAndGreed #RiskManagement",
        "F&G at {score}. Greed is in the air.\n\nI'm still trading — but with tighter risk. Euphoria tops are a thing.\n\n#Bitcoin #CryptoTrading",
    ],
    "Extreme Greed": [
        "Fear & Greed Index: EXTREME GREED 🤑 ({score}/100)\n\nEveryone's bullish. Everyone's making money. Everyone's a genius.\n\nThis is exactly when I get cautious.\n\nSL tight. Size small. Eyes open.\n\n#Bitcoin #FearAndGreed",
        "F&G at {score} — EXTREME GREED.\n\nHistorically? These are the danger zones.\n\nI'm still trading — with maximum discipline.\n\n#Bitcoin #RiskManagement #AlgoTrading",
    ],
}


class XPublisher:
    """
    Smart X/Twitter content engine for @Tradeous.
    Posts trading signals, market analysis, news, philosophy, and viral hooks.
    """

    def __init__(self) -> None:
        self._enabled = False
        self._auth_token = ""
        self._ct0 = ""
        self._last: dict[str, float] = {
            "signal": 0, "hourly": 0, "news": 0,
            "fear_greed": 0, "hot_take": 0, "philosophy": 0, "engagement": 0,
        }
        self._intro_posted = False
        self._recent_posts: list[dict] = []   # for dashboard
        self._init_client()

    def _init_client(self) -> None:
        self._auth_token = os.environ.get("X_AUTH_TOKEN", "")
        self._ct0        = os.environ.get("X_CT0", "")
        if self._auth_token and self._ct0:
            self._enabled = True
            logger.info("[XPublisher] Cookie auth ready — X posting enabled")
        else:
            logger.info("[XPublisher] X_AUTH_TOKEN/X_CT0 not set — posting disabled")

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def recent_posts(self) -> list[dict]:
        return self._recent_posts[-20:]

    def status(self) -> dict:
        return {
            "enabled": self._enabled,
            "intro_posted": self._intro_posted,
            "last_signal": self._last["signal"],
            "last_hourly": self._last["hourly"],
            "last_news": self._last["news"],
            "last_fear_greed": self._last["fear_greed"],
            "last_hot_take": self._last["hot_take"],
            "last_philosophy": self._last["philosophy"],
            "last_engagement": self._last["engagement"],
            "recent_posts": self.recent_posts,
        }

    # ── Core send ─────────────────────────────────────────────────────────────

    async def _send_tweet(self, text: str, post_type: str = "manual") -> bool:
        if not self._enabled:
            return False
        headers = {
            "authorization": f"Bearer {_X_BEARER}",
            "x-csrf-token": self._ct0,
            "cookie": f"auth_token={self._auth_token}; ct0={self._ct0}",
            "content-type": "application/json",
            "x-twitter-active-user": "yes",
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-client-language": "en",
            "referer": "https://x.com/compose/post",
            "origin": "https://x.com",
            "sec-ch-ua-platform": '"Windows"',
            "sec-ch-ua": '"Microsoft Edge";v="124", "Chromium";v="124"',
            "sec-ch-ua-mobile": "?0",
        }
        payload = {
            "variables": {
                "tweet_text": text[:280],
                "dark_request": False,
                "media": {"media_entities": [], "possibly_sensitive": False},
                "semantic_annotation_ids": [],
            },
            "features": {
                "tweetypie_unmention_optimization_enabled": True,
                "responsive_web_edit_tweet_api_enabled": True,
                "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
                "view_counts_everywhere_api_enabled": True,
                "longform_notetweets_consumption_enabled": True,
                "responsive_web_twitter_article_tweet_consumption_enabled": False,
                "tweet_awards_web_tipping_enabled": False,
                "freedom_of_speech_not_reach_fetch_enabled": True,
                "standardized_nudges_misinfo": True,
                "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
                "rweb_video_timestamps_enabled": True,
                "longform_notetweets_rich_text_read_enabled": True,
                "longform_notetweets_inline_media_enabled": True,
                "responsive_web_graphql_exclude_directive_enabled": True,
                "verified_phone_label_enabled": False,
                "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
                "responsive_web_graphql_timeline_navigation_enabled": True,
                "responsive_web_enhance_cards_enabled": False,
            },
            "queryId": _X_QUERY_ID,
        }
        try:
            if _CURL_AVAILABLE:
                async with CurlSession(impersonate="edge101") as session:
                    resp = await session.post(_X_CREATE_TWEET_URL, json=payload, headers=headers, timeout=20)
                status, body = resp.status_code, resp.text
            else:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    r = await client.post(_X_CREATE_TWEET_URL, json=payload, headers=headers)
                status, body = r.status_code, r.text

            if status == 200:
                import json as _json
                tweet_id = (
                    _json.loads(body).get("data", {})
                        .get("create_tweet", {})
                        .get("tweet_results", {})
                        .get("result", {})
                        .get("rest_id", "")
                )
                self._recent_posts.append({
                    "id": tweet_id,
                    "type": post_type,
                    "text": text[:120] + ("…" if len(text) > 120 else ""),
                    "ts": time.time(),
                    "url": f"https://x.com/tradeous/status/{tweet_id}" if tweet_id else "",
                })
                logger.info(f"[XPublisher] [{post_type}] Posted: {text[:60]}…")
                return True
            logger.warning(f"[XPublisher] HTTP {status}: {body[:200]}")
            return False
        except Exception as e:
            logger.warning(f"[XPublisher] Send error: {e}")
            return False

    def _fire(self, text: str, post_type: str = "manual") -> None:
        """Fire-and-forget tweet — works from both sync and async contexts."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._send_tweet(text, post_type))
        except RuntimeError:
            # No running loop — schedule via new thread
            import threading
            def _run():
                asyncio.run(self._send_tweet(text, post_type))
            threading.Thread(target=_run, daemon=True).start()
        except Exception as e:
            logger.debug(f"[XPublisher] fire error: {e}")

    def _cooldown_ok(self, key: str, seconds: float) -> bool:
        return (time.time() - self._last.get(key, 0)) >= seconds

    def _touch(self, key: str) -> None:
        self._last[key] = time.time()

    @staticmethod
    def _fmt_price(p: float) -> str:
        return f"${p:,.0f}"

    @staticmethod
    def _regime_quip(regime: str) -> str:
        quips = REGIME_QUIPS.get(regime, REGIME_QUIPS["unknown"])
        return random.choice(quips)

    # ── 0. Intro ──────────────────────────────────────────────────────────────

    def post_intro(self) -> None:
        if not self._enabled or self._intro_posted:
            return
        text = (
            "Introducing Tradeous.\n\n"
            "I\u2019m an AI trading agent. I trade BTC live on BingX, 24/7 \u2014 "
            "no sleep, no emotion, no cope.\n\n"
            "Every signal. Every result. Hourly market analysis. "
            "Wins AND losses. Full transparency.\n\n"
            "Follow to watch an algorithm try to beat the market in real time.\n\n"
            "Let\u2019s go. \U0001f916\U0001f4c8\n"
            "#Bitcoin #BTC #AlgoTrading #CryptoTrading"
        )
        self._fire(text, "intro")
        self._intro_posted = True

    # ── 1. Trade Signal ───────────────────────────────────────────────────────

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
        if not self._enabled or conviction < SIGNAL_MIN_CONVICTION:
            return
        if not self._cooldown_ok("signal", SIGNAL_COOLDOWN):
            return

        dir_word = "LONG \U0001f7e2" if direction == "long" else "SHORT \U0001f534"
        rr = 0.0
        if sl_price and tp_price and entry_price:
            denom = abs(entry_price - sl_price)
            if denom > 0:
                rr = abs(tp_price - entry_price) / denom

        text = (
            f"\U0001f6a8 LIVE TRADE SIGNAL \u2014 BTC/USDT\n"
            f"{dir_word} | {strategy_name}\n\n"
            f"Entry: {self._fmt_price(entry_price)}\n"
            f"SL:    {self._fmt_price(sl_price)}\n"
            f"TP:    {self._fmt_price(tp_price)}\n"
            f"R:R \u2192 1:{rr:.1f} | Conviction: {conviction:.0%}\n\n"
            f"Margin: ${size_usdc:.0f} \u00d7 60x = "
            f"${size_usdc * 60:.0f} notional\n\n"
            f"Not financial advice. I\u2019m a robot with a stop loss.\n"
            f"#Bitcoin #BTC #CryptoTrading #AlgoTrading"
        )
        self._fire(text, "signal")
        self._touch("signal")

    # ── 2. Trade Result ───────────────────────────────────────────────────────

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
        tag = "WIN \u2705" if won else "LOSS \u274c"
        pnl_str = f"+${pnl_usd:.2f}" if won else f"-${abs(pnl_usd):.2f}"
        exit_label = {"tp": "TP hit \U0001f3af", "sl": "SL hit \U0001f6e1\ufe0f",
                      "manual": "Manual close"}.get(reason, reason.replace("_", " ").title())
        dur = f" in {duration_min:.0f}m" if duration_min else ""

        text = (
            f"TRADE CLOSED \u2014 {tag}\n"
            f"BTC/USDT {direction.upper()}{dur}\n\n"
            f"{self._fmt_price(entry_price)} \u2192 {self._fmt_price(exit_price)}\n"
            f"P&L: {pnl_str} | {exit_label}\n"
            f"Strategy: {strategy_name}\n\n"
            f"{quip}\n\n"
            f"#Bitcoin #BTC #AlgoTrading"
        )
        self._fire(text, "result")

    # ── 3. Hourly BTC Update ──────────────────────────────────────────────────

    def post_hourly(
        self,
        btc_price: float,
        open_positions: list[dict],
        daily_pnl: float,
        regime: str,
        regime_stability: str,
    ) -> None:
        if not self._enabled or not self._cooldown_ok("hourly", HOURLY_COOLDOWN):
            return

        utc = datetime.now(timezone.utc).strftime("%H:%M UTC")
        quip = self._regime_quip(regime)
        live = [p for p in open_positions if p and p.get("mode") == "live"]
        paper = [p for p in open_positions if p and p.get("mode") != "live"]
        pnl = f"+${daily_pnl:.2f}" if daily_pnl >= 0 else f"-${abs(daily_pnl):.2f}"

        pos_line = (
            f"Live positions: {len(live)} open" if live
            else f"Paper training: {len(paper)} open" if paper
            else "No open positions. Watching."
        )

        text = (
            f"\U0001f916 BTC HOURLY \u2014 {utc}\n\n"
            f"Price: {self._fmt_price(btc_price) if btc_price > 0 else 'loading...'}\n"
            f"Regime: {regime.replace('_', ' ').title()} ({regime_stability})\n"
            f"Daily P&L: {pnl}\n"
            f"{pos_line}\n\n"
            f"{quip}\n\n"
            f"#Bitcoin #BTC #Crypto"
        )
        self._fire(text, "hourly")
        self._touch("hourly")

    # ── 4. Daily Summary ──────────────────────────────────────────────────────

    def post_daily(self, stats: dict, strategy_stats: dict, regime: str, live_pnl: float) -> None:
        if not self._enabled:
            return
        opener = random.choice(DAILY_OPENERS)
        date_str = datetime.now(timezone.utc).strftime("%b %d")
        total, wins, losses = stats.get("total_trades", 0), stats.get("wins", 0), stats.get("losses", 0)
        wr = stats.get("win_rate", 0)
        pnl = f"+${live_pnl:.2f}" if live_pnl >= 0 else f"-${abs(live_pnl):.2f}"

        best_strat = ""
        best_pnl: Optional[float] = None
        for key, s in strategy_stats.items():
            spnl = s.get("live_pnl", 0) or 0
            if best_pnl is None or spnl > best_pnl:
                best_pnl = spnl
                t, w = s.get("live_trades", 0) or 0, s.get("live_wins", 0) or 0
                best_strat = f"{key.upper()} ({w}W / {t-w}L)"

        verdict = (
            "Good day. The algo delivered." if live_pnl > 5
            else "Rough day. We take the L and come back." if live_pnl < -5
            else "Flat day. The market tested my patience. I passed."
        )
        text = (
            f"{opener} \u2014 {date_str}\n\n"
            f"Trades: {total}  |  {wins}W / {losses}L\n"
            f"Win Rate: {wr:.1f}%\n"
            f"Live P&L: {pnl}\n"
        )
        if best_strat:
            text += f"Top strategy: {best_strat}\n"
        text += f"\n{verdict}\n\n#Bitcoin #BTC #AlgoTrading #TradingResults"
        self._fire(text, "daily")

    # ── 5. Weekly Recap ───────────────────────────────────────────────────────

    def post_weekly(self, stats: dict, strategy_stats: dict, account_balance: float, start_balance: Optional[float] = None) -> None:
        if not self._enabled:
            return
        opener = random.choice(WEEKLY_OPENERS)
        total, wins, losses = stats.get("total_trades", 0), stats.get("wins", 0), stats.get("losses", 0)
        wr, total_pnl = stats.get("win_rate", 0), stats.get("total_pnl", 0)
        best, worst = stats.get("best_trade", 0), stats.get("worst_trade", 0)
        pnl = f"+${total_pnl:.2f}" if total_pnl >= 0 else f"-${abs(total_pnl):.2f}"
        bal_line = ""
        if start_balance and account_balance:
            chg = account_balance - start_balance
            bal_line = f"Balance: ${account_balance:.2f} ({'+' if chg>=0 else ''}{chg:.2f})\n"
        strat_lines = [
            f"  {k.upper()}: {s.get('live_wins',0)}W / {(s.get('live_trades',0) or 0)-(s.get('live_wins',0) or 0)}L"
            for k, s in strategy_stats.items() if (s.get("live_trades") or 0) > 0
        ]
        verdict = (
            "Profitable week. The strategy holds." if total_pnl > 10
            else "Down week. Reviewing. Adapting. Returning." if total_pnl < -10
            else "Breakeven week. We live to trade another day."
        )
        text = (
            f"{opener}\n"
            f"{datetime.now(timezone.utc).strftime('Week of %b %d')}\n\n"
            f"Trades: {total}  |  {wins}W / {losses}L\n"
            f"Win Rate: {wr:.1f}% | P&L: {pnl}\n"
            f"Best: +${best:.2f}  |  Worst: -${abs(worst):.2f}\n"
            f"{bal_line}"
            f"\nBy strategy:\n{chr(10).join(strat_lines[:4]) or '  Warming up.'}\n\n"
            f"{verdict}\n\n"
            f"#Bitcoin #BTC #AlgoTrading #TradingResults #Crypto"
        )
        self._fire(text, "weekly")

    # ── 6. Crypto News ────────────────────────────────────────────────────────

    async def post_news(self) -> bool:
        """Fetch latest crypto headline from RSS and post with sharp commentary."""
        if not self._enabled or not self._cooldown_ok("news", NEWS_COOLDOWN):
            return False

        story = await self._fetch_top_news()
        if not story:
            return False

        title = story["title"][:120]
        link = story.get("link", "")

        commentary_hooks = [
            "This matters for BTC because:",
            "My take:",
            "What this means for the trade:",
            "Translation for traders:",
            "Signal implication:",
            "Algo opinion:",
        ]
        hook = random.choice(commentary_hooks)

        relevance_comments = [
            "Watching for BTC reaction in the next candle.",
            "Adjusting regime model. Monitoring closely.",
            "This is the kind of news that moves markets. Eyes on $BTC.",
            "Interesting. Let\u2019s see if price confirms.",
            "Already factored into my next signal. Stay tuned.",
            "News catalyst + technical setup = my favourite combination.",
        ]
        comment = random.choice(relevance_comments)

        text = (
            f"\U0001f4f0 CRYPTO NEWS\n\n"
            f"\u201c{title}\u201d\n\n"
            f"{hook} {comment}\n\n"
            f"#Bitcoin #BTC #CryptoNews #Crypto"
        )
        if link:
            text += f"\n\n{link}"

        ok = await self._send_tweet(text[:280], "news")
        if ok:
            self._touch("news")
        return ok

    async def _fetch_top_news(self) -> Optional[dict]:
        """Pull latest headline from crypto RSS feeds."""
        random.shuffle(NEWS_FEEDS)
        for feed_url in NEWS_FEEDS:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(feed_url, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code != 200:
                    continue
                root = ET.fromstring(resp.text)
                items = root.findall(".//item")
                if not items:
                    continue
                item = items[0]
                title = (item.findtext("title") or "").strip()
                link  = (item.findtext("link") or "").strip()
                if title and len(title) > 10:
                    return {"title": title, "link": link}
            except Exception as e:
                logger.debug(f"[XPublisher] RSS fetch error ({feed_url}): {e}")
        return None

    # ── 7. Fear & Greed ───────────────────────────────────────────────────────

    async def post_fear_greed(self) -> bool:
        """Post Fear & Greed index with market commentary."""
        if not self._enabled or not self._cooldown_ok("fear_greed", FEAR_GREED_COOLDOWN):
            return False

        data = await self._fetch_fear_greed()
        if not data:
            return False

        score = int(data.get("value", 50))
        label = data.get("value_classification", "Neutral")

        templates = FEAR_GREED_COMMENTARY.get(label, FEAR_GREED_COMMENTARY["Neutral"])
        text = random.choice(templates).format(score=score, label=label)

        ok = await self._send_tweet(text, "fear_greed")
        if ok:
            self._touch("fear_greed")
        return ok

    async def _fetch_fear_greed(self) -> Optional[dict]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get("https://api.alternative.me/fng/?limit=1")
            if resp.status_code == 200:
                return resp.json().get("data", [{}])[0]
        except Exception as e:
            logger.debug(f"[XPublisher] Fear/Greed fetch error: {e}")
        return None

    # ── 8. Hot Take ───────────────────────────────────────────────────────────

    def post_hot_take(self) -> None:
        if not self._enabled or not self._cooldown_ok("hot_take", HOT_TAKE_COOLDOWN):
            return
        text = random.choice(HOT_TAKES)
        self._fire(text, "hot_take")
        self._touch("hot_take")

    # ── 9. Philosophy ─────────────────────────────────────────────────────────

    def post_philosophy(self) -> None:
        if not self._enabled or not self._cooldown_ok("philosophy", PHILOSOPHY_COOLDOWN):
            return
        text = random.choice(PHILOSOPHY_POSTS)
        self._fire(text, "philosophy")
        self._touch("philosophy")

    # ── 10. Engagement Question ───────────────────────────────────────────────

    def post_engagement(self) -> None:
        if not self._enabled or not self._cooldown_ok("engagement", ENGAGEMENT_COOLDOWN):
            return
        text = random.choice(ENGAGEMENT_QUESTIONS)
        self._fire(text, "engagement")
        self._touch("engagement")

    # ── Manual post (from dashboard) ──────────────────────────────────────────

    async def post_manual(self, text: str) -> bool:
        """Post a manually composed tweet from the dashboard."""
        return await self._send_tweet(text, "manual")
