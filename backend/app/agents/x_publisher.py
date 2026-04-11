"""
XPublisher — Tradeous X/Twitter Intelligence Engine
=====================================================
Viral-optimised AI content for @Tradeous.

Posts every 25 minutes. Has persistent memory — never repeats headlines or
content. Uses Grok (xAI) with live X/web search to detect what's trending
and craft hooks optimised for engagement and virality.

Post types:
  0. Intro           — once on first startup
  1. Trade signal    — live trade opened (conviction ≥ threshold)
  2. Trade result    — live position closed
  3. Hourly update   — BTC price + regime + witty commentary (every 25 min)
  4. Daily summary   — midnight UTC digest
  5. Weekly recap    — Sunday 20:00 UTC
  6. Crypto news     — trending story with sharp unique take (every 50 min)
  7. Fear & Greed    — index commentary (every 2 h)
  8. Hot take        — spicy market opinion (every 60 min)
  9. Philosophy      — trader wisdom + algo twist (every 2 h)
 10. Engagement      — question to audience (every 2 h)
 11. BTC Move        — triggered when BTC moves ±1.5%+ between posts
 12. Algo Insight    — transparency post about how the system works
 13. Trending Hook   — Grok-powered post on what's viral on X right now
 14. Bold Prediction — contrarian market call with reasoning
 15. Milestone       — performance achievement posts

Env vars required:
  X_AUTH_TOKEN  — from x.com cookies ("auth_token")
  X_CT0         — from x.com cookies ("ct0")
  XAI_API_KEY   — xAI / Grok API key (for real-time X trend search)
  GROQ_API_KEY  — Groq fallback (free)
  GEMINI_API_KEY — Gemini fallback (free)
"""

from __future__ import annotations

import asyncio
import collections
import hashlib
import json
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

try:
    import aiosqlite as _aiosqlite
    _SQLITE_AVAILABLE = True
except ImportError:
    _SQLITE_AVAILABLE = False

logger = logging.getLogger(__name__)

# ── Timing constants ──────────────────────────────────────────────────────────
SIGNAL_MIN_CONVICTION  = 0.70
SIGNAL_COOLDOWN        = 900      # 15 min
HOURLY_COOLDOWN        = 1500     # 25 min
NEWS_COOLDOWN          = 3000     # 50 min
FEAR_GREED_COOLDOWN    = 7200     # 2 h
HOT_TAKE_COOLDOWN      = 3600     # 60 min
PHILOSOPHY_COOLDOWN    = 7200     # 2 h
ENGAGEMENT_COOLDOWN    = 7200     # 2 h
BTC_MOVE_COOLDOWN      = 1800     # 30 min
ALGO_INSIGHT_COOLDOWN  = 10800    # 3 h
TRENDING_COOLDOWN      = 5400     # 90 min — Grok X-trend post
PREDICTION_COOLDOWN    = 14400    # 4 h
MILESTONE_COOLDOWN     = 3600     # 1 h (but only fires when milestone reached)

# Headline dedup window: 72 hours
NEWS_SEEN_TTL_HOURS = 72

# ── X internal API ─────────────────────────────────────────────────────────────
_X_QUERY_ID = "S1qcGUn68_U0lDKdMlYSGg"
_X_CREATE_TWEET_URL = (
    f"https://x.com/i/api/graphql/{_X_QUERY_ID}/CreateTweet"
)
_X_BEARER = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D"
    "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

# ── News RSS feeds (free, no key) ──────────────────────────────────────────────
NEWS_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
]

# ── Content banks — @mistor style: BTC-only, short, lowercase, punchy ─────────
# Style rules:
#   • BTC only — dismiss ETH/SOL/meme coins when relevant
#   • Short 1–4 lines, line breaks for drama
#   • Mostly lowercase; ALL CAPS only for rare emphasis
#   • No hashtags
#   • Max 1 emoji per tweet, often zero
#   • Psychological / conviction / FOMO energy
#   • Never sounds corporate or robotic

REGIME_QUIPS = {
    "trending_up": [
        "people who paper handed last week are gonna be so mad\n\njust watch",
        "btc is doing what btc does\n\nif you're not in you're not gonna make it",
        "the move is happening and most people are watching from the sidelines\n\nalways",
        "number going up. eth still down bad. funny how that works",
        "btc breaking out while altcoins are bleeding\n\nbtc only stays winning",
        "soon.",
        "this is what a real asset looks like\n\nnot your little memecoin 😁",
        "the fud was loud\n\nthe chart didn't care",
        "holders eating\n\nsellers coping",
        "not early. just not wrong yet 💨",
        "every cycle the same people sell the breakout\n\nevery cycle they regret it",
        "btc doing btc things. i'm not surprised",
    ],
    "trending_down": [
        "the fud is loud when the move is close\n\nalways",
        "shakeout. not a top.\n\nbig difference",
        "they want you to sell here\n\njust saying",
        "btc dipping while eth holders pretend their bags aren't worse\n\ninteresting",
        "every dip feels like the end\n\nnone of them have been",
        "paper hands are leaving\n\ngood",
        "the people selling right now are the same ones who paper handed the last run",
        "flush it. get the weak hands out. then we go",
        "red candles are just discounts for people who understand what btc is",
        "i've seen this before\n\nsoon.",
    ],
    "ranging": [
        "boring markets are where diamonds are made\n\nwe wait",
        "btc consolidating while alts bleed to zero\n\nbullish honestly",
        "nothing to do but wait\n\nmost people can't do that",
        "the move is loading\n\ni'm not in a rush",
        "coiling. just watch",
        "patience is the most underrated skill in this game\n\nalmost nobody has it",
        "sideways btc > down alts\n\nthere is no competition",
        "the ones who wait here are the ones who win later",
        "no setup. no trade. that's it",
    ],
    "volatile": [
        "volatile btc > stable eth\n\ni don't make the rules",
        "this kind of candle is why you have a sl\n\nplease have a sl",
        "crazy moves. staying focused. sl is set",
        "everyone panicking. the system is running. that's the whole point",
        "volatility is the price of admission\n\nbtc doesn't apologize",
        "btc shaking out weak hands again 😁\n\nstandard",
        "big move. kept my size. kept my sl. that's how you survive this",
    ],
    "unknown": [
        "still watching. not every minute needs a trade",
        "reading the market\n\nwill tell you what i see",
        "no signal yet. patience.",
        "not forcing anything\n\nthe setup will come",
    ],
}

RESULT_WIN_QUIPS = [
    "w",
    "that's how btc trading is supposed to feel",
    "another one. on to the next",
    "the system works\n\ni'll keep saying it",
    "closed green. sl did its job. tp hit. simple",
    "btc paid again 🙏",
    "this is why we hold the signal\n\nnot the emotion",
    "w trade. no luck. just system",
]

RESULT_LOSS_QUIPS = [
    "sl hit. that's what it's there for. we move",
    "took the l. no revenge. no cope\n\nnext setup",
    "stopped out. better than being wrong with no plan",
    "loss recorded. lesson logged. btc is still btc",
    "not every trade wins\n\nevery trade is managed. that's different",
    "sl protected the account. that's a win in disguise",
]

DAILY_OPENERS = [
    "day report. no spin.",
    "another day trading btc. here's what happened.",
    "eod. real numbers. no cherry picking.",
    "day wrapped. the algo ran. here's the score.",
    "daily debrief. transparent as always.",
]

WEEKLY_OPENERS = [
    "week done. btc only. here's the truth.",
    "7 days of trading btc live. the numbers:",
    "sunday report. no deleted tweets ever.",
    "week closed. wins losses and everything in between.",
]

HOT_TAKES = [
    "people who buy altcoins instead of btc deserve what happens to them\n\nnot being mean\n\njust the truth 😁",
    "eth is a failed btc competitor that found a different way to lose\n\nchange my mind",
    "if you're in a memecoin right now you're not investing\n\nyou're gambling at the casino and pretending it's different",
    "unpopular opinion: 95% of crypto projects are just ways to take money from people who don't understand btc yet",
    "the fud is always loudest right before the move\n\nalways\n\nlearn this or stay poor",
    "paper handing btc is genuinely a skill issue\n\nnot saying that to be mean\n\njust the truth",
    "solana going down and the community acts surprised\n\nbro btc has been here since 2009\n\nthere is no competition",
    "the people selling btc here are going to be so mad in two weeks\n\njust watch",
    "every altcoin season ends the same way\n\nbags held. lessons learned. btc wished you bought instead",
    "not a single person who held btc for 4 years has regrets\n\ncould be said about zero other coins",
    "if your crypto thesis requires a new buyer to profit you're in a ponzi\n\nbtc doesn't need your narrative",
    "the number of people who sold btc in 2022 and bought a memecoin instead 💀\n\ncertified skill issue",
    "i'm not early\n\ni'm just not wrong yet 💨",
    "4 figures to 5 figures to 6 figures to 7 figures\n\nbtc is the only coin with a proven path\n\neverything else is hoping",
    "hot take: following eth maxis is actively harmful to your portfolio\n\nfilter aggressively",
    "the funniest thing in crypto is watching people swap btc for alts at the top\n\nevery cycle\n\nclockwork",
    "you don't need 10 coins\n\nyou need btc and patience\n\nthat's it",
    "I NEED A HUGE FAT COOK 🙏",
    "most people in crypto are one bad trade from giving up\n\nbecause they never understood what they were buying",
    "the difference between btc and every other coin:\n\nbtc doesn't need you to believe in it",
]

PHILOSOPHY_POSTS = [
    "the best trade you'll ever make is just holding btc and not touching it\n\nmost people are too smart for that",
    "btc doesn't care about your feelings\n\nit doesn't care about the news\n\nit just does what it does",
    "patience in btc is not passive\n\nit's the hardest active choice you can make every day",
    "every time btc dips someone sells\n\nevery time btc pumps they buy back higher\n\nthis is why most people don't make it",
    "the people who made life-changing money from btc weren't smarter\n\nthey just didn't sell",
    "sl before entry. always.\n\nif you can't define your loss before the trade you're not trading\n\nyou're praying",
    "four years of btc charts and the pattern is always the same\n\ndip. shake. run. repeat\n\nbut people always forget",
    "the market transfers money from emotional people to patient ones\n\ni trade the emotion. not the narrative",
    "the only edge that consistently works in btc:\n\nenter with conviction. exit with discipline. don't revenge trade.",
    "btc: invented 2009. survived every crash. every ban. every fud.\n\nstill here.\n\nyour altcoin won't say the same",
    "if your plan requires others to be wrong you don't have a plan\n\nbtc doesn't need consensus\n\nit is the consensus",
    "cutting a loss is not losing\n\nholding a losing trade hoping it comes back is losing\n\nbig difference",
    "the people who are going to make real money this cycle are already in\n\nthey bought when nobody was talking about it",
]

ENGAGEMENT_QUESTIONS = [
    "what's your btc target this cycle?\n\nno wrong answers\n\njust curious who's thinking big",
    "be honest: how many times have you sold btc and regretted it?",
    "if you had to choose one: btc or cash for the next 4 years\n\nwhat are you doing",
    "who else is tired of altcoin season narratives\n\nbtc only people reply",
    "what was the worst trade you ever made and what did it teach you",
    "how do you actually manage a losing streak without revenge trading\n\nreal answers only",
    "if you bought btc and never looked at price for a year\n\nwhere do you think you'd be",
    "be honest: do you actually have a stop loss on every trade or just when you remember",
    "what would make you sell your btc\n\ni'll wait",
    "the people who bought btc in the fud months are so quiet rn\n\nwhere are you 🙏",
]

FEAR_GREED_COMMENTARY = {
    "Extreme Fear": [
        "fear & greed at {score}/100. extreme fear.\n\nthis is when btc gets bought\n\nnot sold",
        "everyone is scared right now\n\n{score}/100 fear\n\ncorrect response: don't be scared",
        "extreme fear. {score}/100.\n\nthe fud is loud when the move is close\n\nalways",
    ],
    "Fear": [
        "fear & greed at {score}\n\nmarket is nervous\n\ni'm not",
        "{score}/100. fear.\n\ngood. this is how bottoms are made",
        "fearful market at {score}\n\nbest time to be thinking clearly",
    ],
    "Neutral": [
        "fear & greed at {score}. neutral.\n\nthe calm before something",
        "{score}/100. nobody knows what's next\n\ni'm watching",
        "market undecided at {score}\n\nthe setup is coming. patience",
    ],
    "Greed": [
        "fear & greed at {score}. greed.\n\npeople getting confident\n\ntighten your sl",
        "{score}/100. greed entering.\n\nthis is when you don't get sloppy",
        "greed at {score}\n\nthe easy money has been made\n\nthe discipline part starts now",
    ],
    "Extreme Greed": [
        "extreme greed. {score}/100.\n\neveryone's a genius right now\n\nbe careful",
        "{score}/100. maximum greed.\n\nthis is not when you size up\n\nthis is when you tighten",
        "extreme greed at {score}\n\nthe top feels obvious in hindsight\n\nit never feels obvious now",
    ],
}

ALGO_INSIGHTS = [
    "the algo runs btc only\n\n4 strategies scanning every 5 minutes\n\nno emotion. no eth. just btc",
    "i don't pick tops or bottoms\n\ni trade momentum with a sl set before i enter\n\nthat's literally it",
    "every trade i make is logged live\n\nwins and losses\n\nno deleted tweets. ever.",
    "the system scans btc 24/7\n\nwhen the setup is there i trade\n\nwhen it's not i wait\n\nmost people can't do the second part",
    "btc perps on bingx\n\n24/7\n\nno sleep. no fomo. no altcoins.\n\njust the signal",
    "i post my trades live\n\nentry. sl. tp. result.\n\nno guru. no membership. just the algo running",
]

BTC_MOVE_TEMPLATES = [
    "btc just moved {pct:+.1f}%\n\n{direction_comment}\n\n{action_comment}",
    "btc {pct:+.1f}% in the last 25 minutes\n\n{direction_comment}",
    "price check: btc at {price}\n\n{pct:+.1f}% move\n\n{action_comment}",
]

BTC_MOVE_UP_COMMENTS = [
    "bulls running",
    "this is what momentum looks like",
    "buyers stepped in",
    "and just like that the fud is quiet",
    "the sellers are now very unhappy",
    "btc does what btc does",
]

BTC_MOVE_DOWN_COMMENTS = [
    "paper hands shaking out",
    "the weak hands leaving the building",
    "flush incoming. then we go",
    "dip. not a top.",
    "sellers in control for now",
    "discount for people who understand",
]

BTC_MOVE_ACTIONS = [
    "watching",
    "scanning for the setup",
    "waiting for confirmation",
    "sl is set",
    "models updating",
]

BTC_MOVE_ACTION_COMMENTS = [
    "patience.",
    "not chasing.",
    "setup loading.",
    "sl is set. we're good.",
    "next signal incoming.",
    "just watch.",
]


_TWEET_DB_PATH   = "/tmp/tweet_history.db"
_HEADLINE_DB_PATH = "/tmp/seen_headlines.db"


class MoodState:
    """
    Personality state that evolves with real trading performance.
    The AI is instructed to write with this tone — so each post
    sounds authentically different based on what's happening.
    """
    CONFIDENT     = "confident"
    CAUTIOUS      = "cautious"
    HUNTING       = "hunting"
    CELEBRATING   = "celebrating"
    RECALIBRATING = "recalibrating"

    _TONES = {
        CONFIDENT:     "confident and sharp — data-backed, slightly cocky, proven right recently",
        CAUTIOUS:      "measured and disciplined — humble after losses, methodical, risk-first",
        HUNTING:       "analytical and patient — scanning the market, waiting for the perfect setup",
        CELEBRATING:   "genuinely excited but controlled — celebrating a win, keeping perspective",
        RECALIBRATING: "reflective and honest — processing a rough period, learning, adapting",
    }

    def __init__(self) -> None:
        self.current = self.HUNTING

    def update(self, consecutive_losses: int, daily_pnl: float, last_trade_ago_sec: float) -> None:
        if daily_pnl > 5:
            self.current = self.CELEBRATING
        elif consecutive_losses >= 3 or daily_pnl < -10:
            self.current = self.CAUTIOUS if daily_pnl > -20 else self.RECALIBRATING
        elif last_trade_ago_sec > 7200:
            self.current = self.HUNTING
        else:
            self.current = self.CONFIDENT

    @property
    def tone(self) -> str:
        return self._TONES[self.current]


class TweetMemory:
    """
    Tracks recent tweet content to prevent immediate repetition.
    Uses a deque per content type. Picks from the pool excluding recently used items.
    """
    def __init__(self, memory_size: int = 8):
        self._used: dict[str, collections.deque] = collections.defaultdict(
            lambda: collections.deque(maxlen=memory_size)
        )
        self._last_btc_price: float = 0.0
        self._post_count: int = 0
        self._session_start: float = time.time()

    def pick(self, key: str, pool: list) -> str:
        """Pick a random item from pool, avoiding recently used ones if possible."""
        if not pool:
            return ""
        used = set(self._used[key])
        available = [p for p in pool if p not in used]
        if not available:
            # All used — reset memory for this key and pick fresh
            self._used[key].clear()
            available = pool
        choice = random.choice(available)
        self._used[key].append(choice)
        return choice

    def record_post(self, post_type: str, text: str) -> None:
        self._post_count += 1
        self._used[post_type].append(text[:80])

    def set_btc_price(self, price: float) -> None:
        self._last_btc_price = price

    def get_btc_price(self) -> float:
        return self._last_btc_price

    def posts_per_hour(self) -> float:
        elapsed = (time.time() - self._session_start) / 3600
        return round(self._post_count / elapsed, 1) if elapsed > 0.1 else 0

    def total_posts(self) -> int:
        return self._post_count


class XPublisher:
    """
    Smart X/Twitter content engine for @Tradeous.
    Posts every 25 minutes. Has memory. Never repeats consecutively.
    Dynamic content uses live BTC price, regime, and market context.
    """

    def __init__(self) -> None:
        self._enabled = False
        self._auth_token = ""
        self._ct0 = ""
        self._last: dict[str, float] = {
            "signal": 0, "hourly": 0, "news": 0,
            "fear_greed": 0, "hot_take": 0, "philosophy": 0,
            "engagement": 0, "btc_move": 0, "algo_insight": 0,
        }
        self._intro_posted = False
        self._recent_posts: list[dict] = []
        self.memory = TweetMemory(memory_size=10)
        self.mood = MoodState()
        # Thread tracking: last signal tweet_id → result replies to it
        self._last_signal_tweet_id: str = ""
        self._last_signal_strategy: str = ""
        # Live context injected by the agent each scan
        self._live_context: dict = {}
        # Rolling full-text history for AI deduplication (100 posts)
        self._full_history: collections.deque = collections.deque(maxlen=100)
        # DB init happens lazily on first write (safe for both sync and async contexts)
        self._db_initialized: bool = False
        self._init_client()

    def _init_client(self) -> None:
        self._auth_token = os.environ.get("X_AUTH_TOKEN", "").strip()
        self._ct0        = os.environ.get("X_CT0", "").strip()
        if self._auth_token and self._ct0:
            self._enabled = True
            logger.info("[XPublisher] Cookie auth ready — X posting enabled")
        else:
            logger.info("[XPublisher] X_AUTH_TOKEN/X_CT0 not set — posting disabled")

    # ── Tweet history DB ──────────────────────────────────────────────────────

    async def _ensure_db(self) -> None:
        if self._db_initialized or not _SQLITE_AVAILABLE:
            return
        try:
            async with _aiosqlite.connect(_TWEET_DB_PATH) as db:
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS tweet_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        tweet_id TEXT,
                        post_type TEXT,
                        full_text TEXT,
                        btc_price REAL,
                        regime TEXT,
                        mood TEXT,
                        posted_at REAL
                    )
                """)
                await db.commit()
            await self._load_history_from_db()
            self._db_initialized = True
            logger.info("[XPublisher] Tweet history DB ready")
        except Exception as e:
            logger.debug(f"[XPublisher] DB init error: {e}")

    async def _save_tweet_to_db(self, tweet_id: str, post_type: str, text: str) -> None:
        if not _SQLITE_AVAILABLE:
            return
        await self._ensure_db()
        ctx = self._live_context
        try:
            async with _aiosqlite.connect(_TWEET_DB_PATH) as db:
                await db.execute(
                    "INSERT INTO tweet_history (tweet_id, post_type, full_text, btc_price, regime, mood, posted_at) VALUES (?,?,?,?,?,?,?)",
                    (tweet_id, post_type, text,
                     ctx.get("price", 0), ctx.get("regime", "unknown"),
                     self.mood.current, time.time())
                )
                await db.commit()
        except Exception as e:
            logger.debug(f"[XPublisher] DB save error: {e}")

    async def _load_history_from_db(self) -> None:
        if not _SQLITE_AVAILABLE:
            return
        try:
            async with _aiosqlite.connect(_TWEET_DB_PATH) as db:
                async with db.execute(
                    "SELECT full_text FROM tweet_history ORDER BY posted_at DESC LIMIT 50"
                ) as cursor:
                    rows = await cursor.fetchall()
            for (text,) in reversed(rows):
                self._full_history.append(text)
        except Exception as e:
            logger.debug(f"[XPublisher] DB load error: {e}")

    def _recent_texts_for_ai(self, n: int = 8) -> str:
        """Return last N tweet texts formatted for the AI prompt."""
        recent = list(self._full_history)[-n:]
        if not recent:
            return "None yet."
        return "\n---\n".join(f"• {t[:120]}" for t in recent)

    # ── Live context (injected by PersistentAgent each scan) ─────────────────

    def update_context(self, price: float, regime: str, regime_confidence: float,
                       daily_pnl: float, consecutive_losses: int,
                       last_trade_ago_sec: float = 0,
                       win_rate: float = 0.5,
                       open_positions: int = 0,
                       scan_count: int = 0) -> None:
        """Called by PersistentAgent on every scan to keep context fresh."""
        self._live_context = {
            "price":             price,
            "regime":            regime,
            "regime_confidence": regime_confidence,
            "daily_pnl":         daily_pnl,
            "consecutive_losses": consecutive_losses,
            "win_rate":          win_rate,
            "open_positions":    open_positions,
            "scan_count":        scan_count,
        }
        self.mood.update(consecutive_losses, daily_pnl, last_trade_ago_sec)

    # ── AI generation (Groq free → Gemini free fallback) ─────────────────────

    _SYSTEM_PROMPT = (
        "You are @Tradeous — an AI trading agent that only trades BTC. "
        "You post on X (Twitter) like @mistor: raw, real, short, and punchy. "
        "\n\nSTRICT STYLE RULES — follow every one:\n"
        "• BTC ONLY. Never bullish on ETH, SOL, or any altcoin/memecoin. You can dismiss or diss them.\n"
        "• Keep tweets SHORT: 1–4 lines max. Line breaks for dramatic effect.\n"
        "• Write in LOWERCASE. No formal capitalization. ALL CAPS only for rare emotional emphasis.\n"
        "• NO hashtags. Ever.\n"
        "• MAX 1 emoji per tweet. Often zero. Prefer: 😁 💨 🙏 — nothing else.\n"
        "• Sound like a real person who's deeply convicted on BTC, not a bot writing marketing copy.\n"
        "• Use short, punchy sentence fragments. 'just watch.' 'soon.' 'always.' are complete sentences.\n"
        "• Psychological hooks: FOMO, conviction, paper-hand shaming, patience, anti-alt energy.\n"
        "• Never sound corporate, never use exclamation marks, never explain the joke.\n"
        "• NEVER start with 'I just', 'just', 'as an AI', or any bot-speak.\n"
        "Output ONLY the tweet text. Nothing else. No quotes around it."
    )

    async def _ai_generate(self, user_prompt: str, max_chars: int = 260) -> Optional[str]:
        """Try Groq (free), then Gemini Flash (free). Returns None if both fail."""
        groq_key = os.environ.get("GROQ_API_KEY", "").strip()
        if groq_key:
            result = await self._call_groq(groq_key, user_prompt, max_chars)
            if result:
                return result[:max_chars]

        gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if gemini_key:
            result = await self._call_gemini(gemini_key, user_prompt, max_chars)
            if result:
                return result[:max_chars]

        return None

    async def _call_groq(self, api_key: str, user_prompt: str, max_chars: int) -> Optional[str]:
        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": self._SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            "max_tokens": 120,
            "temperature": 0.92,
        }
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                r = await client.post(url, json=payload,
                                      headers={"Authorization": f"Bearer {api_key}",
                                               "Content-Type": "application/json"})
            if r.status_code == 200:
                text = r.json()["choices"][0]["message"]["content"].strip()
                logger.info(f"[XPublisher] Groq generated: {text[:60]}…")
                return text
            logger.warning(f"[XPublisher] Groq {r.status_code}: {r.text[:120]}")
        except Exception as e:
            logger.debug(f"[XPublisher] Groq error: {e}")
        return None

    async def _call_gemini(self, api_key: str, user_prompt: str, max_chars: int) -> Optional[str]:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"gemini-1.5-flash:generateContent?key={api_key}")
        full_prompt = f"{self._SYSTEM_PROMPT}\n\n{user_prompt}"
        payload = {"contents": [{"parts": [{"text": full_prompt}]}],
                   "generationConfig": {"maxOutputTokens": 120, "temperature": 0.92}}
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                r = await client.post(url, json=payload)
            if r.status_code == 200:
                text = (r.json().get("candidates", [{}])[0]
                        .get("content", {}).get("parts", [{}])[0]
                        .get("text", "")).strip()
                if text:
                    logger.info(f"[XPublisher] Gemini generated: {text[:60]}…")
                    return text
            logger.warning(f"[XPublisher] Gemini {r.status_code}: {r.text[:120]}")
        except Exception as e:
            logger.debug(f"[XPublisher] Gemini error: {e}")
        return None

    def _build_ai_prompt(self, post_type: str, extra: str = "", trending: str = "") -> str:
        ctx = self._live_context
        price     = f"${ctx.get('price', 0):,.0f}" if ctx.get('price') else "unknown"
        regime    = ctx.get('regime', 'unknown').replace('_', ' ')
        conf      = f"{ctx.get('regime_confidence', 0):.0%}"
        pnl       = ctx.get('daily_pnl', 0)
        pnl_str   = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        wr        = f"{ctx.get('win_rate', 0.5):.0%}"
        cl        = ctx.get('consecutive_losses', 0)
        positions = ctx.get('open_positions', 0)

        context_block = (
            f"RIGHT NOW:\n"
            f"- BTC price: {price}\n"
            f"- Market regime: {regime} ({conf} confidence)\n"
            f"- Today's P&L: {pnl_str} | Win rate: {wr}\n"
            f"- Consecutive losses: {cl} | Open positions: {positions}\n"
            f"- Your current mood/tone: {self.mood.tone}\n"
        )
        if trending:
            context_block += f"- Trending in crypto right now: {trending}\n"

        history_block = f"\nYOUR LAST 8 TWEETS (do NOT repeat these themes or phrasing):\n{self._recent_texts_for_ai(8)}\n"

        task = (
            f"\nWRITE A {post_type.upper().replace('_', ' ')} TWEET (max 240 chars). {extra}\n"
            f"Remember: lowercase, short, no hashtags, max 1 emoji, BTC-only energy, punchy."
        )

        return context_block + history_block + task

    # ── Peak-hour timing ──────────────────────────────────────────────────────

    @staticmethod
    def _is_peak_hour() -> bool:
        """X engagement peaks: 8–10 EST, 12–2 EST, 7–10 EST → UTC+5."""
        h = datetime.now(timezone.utc).hour
        return h in {13, 14, 15, 17, 18, 19, 23, 0, 1, 2}

    @staticmethod
    def _posting_cooldown(base: float) -> float:
        """Shorten cooldown during peak hours to post more; lengthen at night."""
        h = datetime.now(timezone.utc).hour
        dead_hours = {3, 4, 5, 6, 7, 8}
        if h in dead_hours:
            return base * 2.0   # post half as often at 3–8am UTC
        if XPublisher._is_peak_hour():
            return base * 0.7   # post more often during peak hours
        return base

    # ── Trending topics from RSS ──────────────────────────────────────────────

    async def _get_trending_context(self) -> str:
        """Pull top 3 headlines from RSS for AI context injection."""
        feeds = NEWS_FEEDS.copy()
        random.shuffle(feeds)
        headlines = []
        for feed_url in feeds[:2]:
            try:
                async with httpx.AsyncClient(timeout=6.0) as client:
                    resp = await client.get(feed_url, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code != 200:
                    continue
                root = ET.fromstring(resp.text)
                for item in root.findall(".//item")[:3]:
                    t = (item.findtext("title") or "").strip()
                    if t and len(t) > 15:
                        headlines.append(t[:80])
                if len(headlines) >= 3:
                    break
            except Exception:
                pass
        return " | ".join(headlines[:3]) if headlines else ""

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def recent_posts(self) -> list[dict]:
        return self._recent_posts[-30:]

    def status(self) -> dict:
        groq_key    = bool(os.environ.get("GROQ_API_KEY", "").strip())
        gemini_key  = bool(os.environ.get("GEMINI_API_KEY", "").strip())
        return {
            "enabled":          self._enabled,
            "intro_posted":     self._intro_posted,
            "mood":             self.mood.current,
            "ai_brain":         "groq" if groq_key else ("gemini" if gemini_key else "none"),
            "last_signal":      self._last["signal"],
            "last_hourly":      self._last["hourly"],
            "last_news":        self._last["news"],
            "last_fear_greed":  self._last["fear_greed"],
            "last_hot_take":    self._last["hot_take"],
            "last_philosophy":  self._last["philosophy"],
            "last_engagement":  self._last["engagement"],
            "recent_posts":     self.recent_posts,
            "posts_per_hour":   self.memory.posts_per_hour(),
            "total_posts":      self.memory.total_posts(),
            "next_post_in_sec": max(0, HOURLY_COOLDOWN - (time.time() - self._last.get("hourly", 0))),
            "last_error":       self._last_error,
        }

    # ── Core send (tries multiple methods) ────────────────────────────────────

    _last_error: str = ""

    async def _send_tweet(self, text: str, post_type: str = "manual") -> bool:
        if not self._enabled:
            self._last_error = "X_AUTH_TOKEN / X_CT0 not configured"
            return False

        text = text[:280]

        ok = await self._post_graphql(text, post_type)
        if ok:
            return True

        # Railway can't post — queue for local poster
        self._queue_for_local_poster(text, post_type)
        return False

    def _queue_for_local_poster(self, text: str, post_type: str) -> None:
        """Add to the x_agent API tweet queue so local_poster.py picks it up."""
        try:
            from app.api.x_agent import _tweet_queue
            import uuid
            qid = str(uuid.uuid4())[:8] + f"_{post_type}"
            _tweet_queue.append({"id": qid, "type": post_type, "text": text[:280], "ts": time.time()})
            logger.info(f"[XPublisher] Queued for local poster: [{post_type}] {text[:50]}…")
        except Exception as e:
            logger.debug(f"[XPublisher] queue error: {e}")

    async def _post_v1(self, text: str, post_type: str) -> bool:
        """Post via Twitter v1.1 client API — works better from server IPs."""
        url = "https://api.x.com/1.1/statuses/update.json"
        headers = {
            "authorization": f"Bearer {_X_BEARER}",
            "x-csrf-token": self._ct0,
            "cookie": f"auth_token={self._auth_token}; ct0={self._ct0}",
            "content-type": "application/x-www-form-urlencoded",
            "x-twitter-active-user": "yes",
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-client-language": "en",
            "origin": "https://x.com",
            "referer": "https://x.com",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
        }
        import urllib.parse
        body = urllib.parse.urlencode({"status": text})
        try:
            if _CURL_AVAILABLE:
                async with CurlSession(impersonate="edge101") as session:
                    resp = await session.post(url, data=body, headers=headers, timeout=20)
                status_code, resp_text = resp.status_code, resp.text
            else:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    r = await client.post(url, content=body, headers=headers)
                status_code, resp_text = r.status_code, r.text

            if status_code == 200:
                import json as _json
                data = _json.loads(resp_text)
                tweet_id = str(data.get("id_str", ""))
                self._record_success(tweet_id, text, post_type)
                logger.info(f"[XPublisher] [{post_type}] ✓ v1.1 Posted: {text[:60]}…")
                return True
            self._last_error = f"v1.1 HTTP {status_code}: {resp_text[:150]}"
            logger.warning(f"[XPublisher] v1.1 failed: {self._last_error}")
            return False
        except Exception as e:
            self._last_error = f"v1.1 error: {e}"
            logger.warning(f"[XPublisher] {self._last_error}")
            return False

    async def _post_graphql(self, text: str, post_type: str) -> bool:
        """Post via X GraphQL CreateTweet endpoint."""
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
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
        }
        payload = {
            "variables": {
                "tweet_text": text,
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
                status_code, resp_text = resp.status_code, resp.text
            else:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    r = await client.post(_X_CREATE_TWEET_URL, json=payload, headers=headers)
                status_code, resp_text = r.status_code, r.text

            if status_code == 200:
                import json as _json
                parsed = _json.loads(resp_text)
                tweet_id = (
                    parsed.get("data", {})
                        .get("create_tweet", {})
                        .get("tweet_results", {})
                        .get("result", {})
                        .get("rest_id", "")
                )
                if not tweet_id:
                    # X returned 200 but no tweet was created (datacenter IP silently blocked)
                    errors = parsed.get("errors", [])
                    err_msg = errors[0].get("message", "no tweet_id") if errors else "empty tweet_id (IP blocked?)"
                    self._last_error = f"GraphQL ghost 200: {err_msg}"
                    logger.warning(f"[XPublisher] GraphQL fake success: {self._last_error}")
                    return False
                self._record_success(tweet_id, text, post_type)
                logger.info(f"[XPublisher] [{post_type}] ✓ GraphQL Posted (id={tweet_id}): {text[:60]}…")
                return True
            self._last_error = f"GraphQL HTTP {status_code}: {resp_text[:150]}"
            logger.warning(f"[XPublisher] GraphQL failed: {self._last_error}")
            return False
        except Exception as e:
            self._last_error = f"GraphQL error: {e}"
            logger.warning(f"[XPublisher] {self._last_error}")
            return False

    def _record_success(self, tweet_id: str, text: str, post_type: str) -> None:
        self._recent_posts.append({
            "id": tweet_id,
            "type": post_type,
            "text": text[:120] + ("…" if len(text) > 120 else ""),
            "ts": time.time(),
            "url": f"https://x.com/tradeous/status/{tweet_id}" if tweet_id else "",
        })
        self.memory.record_post(post_type, text)
        self._full_history.append(text)
        if _SQLITE_AVAILABLE:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._save_tweet_to_db(tweet_id, post_type, text))
            except RuntimeError:
                pass  # not in async context — DB write skipped, in-memory still updated

    def _fire(self, text: str, post_type: str = "manual") -> None:
        """Fire-and-forget tweet."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._send_tweet(text, post_type))
        except RuntimeError:
            import threading
            def _run():
                asyncio.run(self._send_tweet(text, post_type))
            threading.Thread(target=_run, daemon=True).start()
        except Exception as e:
            logger.debug(f"[XPublisher] fire error: {e}")

    def _fire_async(self, coro) -> None:
        """Schedule an async coroutine as a fire-and-forget task with error logging."""
        async def _safe_wrapper():
            try:
                await coro
            except Exception as e:
                logger.error(f"[XPublisher] async task failed: {type(e).__name__}: {e}")
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_safe_wrapper())
        except RuntimeError:
            import threading
            def _run():
                asyncio.run(_safe_wrapper())
            threading.Thread(target=_run, daemon=True).start()
        except Exception as e:
            logger.error(f"[XPublisher] fire_async scheduling error: {e}")

    async def _send_tweet_reply(self, text: str, reply_to_id: str, post_type: str) -> bool:
        """Post a tweet as a reply to an existing tweet (for thread chains)."""
        if not self._enabled or not reply_to_id:
            return await self._send_tweet(text, post_type)
        text = text[:280]
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
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
        }
        payload = {
            "variables": {
                "tweet_text": text,
                "reply": {"in_reply_to_tweet_id": reply_to_id, "exclude_reply_user_ids": []},
                "dark_request": False,
                "media": {"media_entities": [], "possibly_sensitive": False},
                "semantic_annotation_ids": [],
            },
            "features": {
                "communities_web_enable_tweet_community_results_fetch": True,
                "c9s_tweet_anatomy_moderator_badge_enabled": True,
                "responsive_web_edit_tweet_api_enabled": True,
                "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
                "view_counts_everywhere_api_enabled": True,
                "longform_notetweets_consumption_enabled": True,
                "responsive_web_twitter_article_tweet_consumption_enabled": False,
                "tweet_awards_web_tipping_enabled": False,
                "longform_notetweets_rich_text_read_enabled": True,
                "longform_notetweets_inline_media_enabled": True,
                "rweb_video_timestamps_enabled": True,
                "responsive_web_graphql_exclude_directive_enabled": True,
                "verified_phone_label_enabled": False,
                "freedom_of_speech_not_reach_the_sky_enabled": True,
                "standardized_nudges_misinfo": True,
                "tweet_with_visibility_results_fetch_enabled": True,
                "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
                "responsive_web_graphql_timeline_navigation_enabled": True,
                "responsive_web_enhance_cards_enabled": False,
                "hidden_profile_subscriptions_enabled": True,
                "rweb_lists_timeline_redesign_enabled": True,
            },
            "queryId": _X_QUERY_ID,
        }
        try:
            if _CURL_AVAILABLE:
                async with CurlSession(impersonate="edge101") as session:
                    resp = await session.post(_X_CREATE_TWEET_URL, json=payload, headers=headers, timeout=20)
                status_code, resp_text = resp.status_code, resp.text
            else:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    r = await client.post(_X_CREATE_TWEET_URL, json=payload, headers=headers)
                status_code, resp_text = r.status_code, r.text

            if status_code == 200:
                import json as _json
                tweet_id = (
                    _json.loads(resp_text).get("data", {})
                        .get("create_tweet", {})
                        .get("tweet_results", {})
                        .get("result", {})
                        .get("rest_id", "")
                )
                self._record_success(tweet_id, text, post_type)
                logger.info(f"[XPublisher] [{post_type}] ✓ Thread reply posted: {text[:60]}…")
                return True
            self._last_error = f"Reply HTTP {status_code}: {resp_text[:100]}"
            return False
        except Exception as e:
            self._last_error = f"Reply error: {e}"
            return False

    # Per-key cooldown durations (seconds) — used by the external scheduler
    _COOLDOWNS: dict[str, float] = {
        "hourly":      HOURLY_COOLDOWN,
        "fear_greed":  FEAR_GREED_COOLDOWN,
        "hot_take":    HOT_TAKE_COOLDOWN,
        "philosophy":  PHILOSOPHY_COOLDOWN,
        "engagement":  ENGAGEMENT_COOLDOWN,
        "btc_move":    BTC_MOVE_COOLDOWN,
        "algo_insight": ALGO_INSIGHT_COOLDOWN,
        "news":        NEWS_COOLDOWN,
    }

    def _cooldown_ok(self, key: str, seconds: float) -> bool:
        return (time.time() - self._last.get(key, 0)) >= seconds

    def available_post_types(self) -> list[str]:
        """Return list of content post types whose cooldown has expired."""
        return [k for k, cd in self._COOLDOWNS.items()
                if self._cooldown_ok(k, cd)]

    def last_any_post_ts(self) -> float:
        """Timestamp of the most recently sent post of any type."""
        return max(self._last.values()) if self._last else 0.0

    def _touch(self, key: str) -> None:
        self._last[key] = time.time()

    @staticmethod
    def _fmt_price(p: float) -> str:
        return f"${p:,.0f}"

    def _regime_quip(self, regime: str) -> str:
        pool = REGIME_QUIPS.get(regime, REGIME_QUIPS["unknown"])
        return self.memory.pick(f"regime_quip_{regime}", pool)

    # ── 0. Intro ───────────────────────────────────────────────────────────────

    def post_intro(self) -> None:
        if not self._enabled or self._intro_posted:
            return
        text = (
            "i'm an ai that trades btc 24/7\n\n"
            "every trade posted live. wins and losses. no deleted tweets.\n\n"
            "btc only. no alts. no cope.\n\n"
            "follow if you want to watch the algo work 🙏"
        )
        self._fire(text, "intro")
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
        regime: str,
        size_usdc: float = 0,
    ) -> None:
        if not self._enabled or conviction < SIGNAL_MIN_CONVICTION:
            return
        if not self._cooldown_ok("signal", SIGNAL_COOLDOWN):
            return

        rr = 0.0
        if sl_price and tp_price and entry_price:
            denom = abs(entry_price - sl_price)
            if denom > 0:
                rr = abs(tp_price - entry_price) / denom

        dir_word = "LONG 🟢" if direction == "long" else "SHORT 🔴"

        async def _post():
            extra = (
                f"btc trade just opened: {direction} entry at {self._fmt_price(entry_price)}, "
                f"sl {self._fmt_price(sl_price)}, tp {self._fmt_price(tp_price)}, r:r 1:{rr:.1f}. "
                f"announce the trade in @mistor style — short, lowercase, punchy. "
                f"mention the key numbers. end with a one-liner showing conviction."
            )
            ai_text = await self._ai_generate(self._build_ai_prompt("live trade signal", extra))
            dir_emoji = "🟢" if direction == "long" else "🔴"
            text = ai_text or (
                f"btc {direction} {dir_emoji}\n\n"
                f"entry: {self._fmt_price(entry_price)}\n"
                f"sl: {self._fmt_price(sl_price)} · tp: {self._fmt_price(tp_price)}\n\n"
                f"sl is set. we ride or we cut. no in between"
            )
            # Post and capture tweet_id for thread reply on close
            ok = await self._send_tweet(text[:280], "signal")
            if ok and self._recent_posts:
                self._last_signal_tweet_id = self._recent_posts[-1].get("id", "")
                self._last_signal_strategy = strategy_name

        self._fire_async(_post())
        self._touch("signal")

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
        pnl_str = f"+${pnl_usd:.2f}" if won else f"-${abs(pnl_usd):.2f}"
        exit_label = {"tp": "TP hit 🎯", "sl": "SL hit 🛡️", "manual": "Manual close"}.get(
            reason, reason.replace("_", " ").title()
        )
        dur = f" in {duration_min:.0f}m" if duration_min else ""
        reply_to = self._last_signal_tweet_id if self._last_signal_strategy == strategy_name else ""

        async def _post():
            extra = (
                f"btc {direction} trade closed{dur}. "
                f"entry {self._fmt_price(entry_price)} → exit {self._fmt_price(exit_price)}. "
                f"p&l: {pnl_str}. {'win.' if won else 'sl hit.'} "
                f"write in @mistor style: short, lowercase, no hashtags. "
                f"{'own the win with quiet confidence.' if won else 'own the loss with discipline. no excuses. no drama.'}"
            )
            ai_text = await self._ai_generate(self._build_ai_prompt("trade result", extra))
            quip = self.memory.pick("result_quip", RESULT_WIN_QUIPS if won else RESULT_LOSS_QUIPS)
            fallback = (
                f"btc {direction} closed{dur}\n\n"
                f"{self._fmt_price(entry_price)} → {self._fmt_price(exit_price)}\n"
                f"{pnl_str}\n\n"
                f"{quip}"
            )
            text = (ai_text or fallback)[:280]

            # Reply to the original signal tweet to form a thread
            if reply_to:
                ok = await self._send_tweet_reply(text, reply_to, "result")
            else:
                ok = await self._send_tweet(text, "result")

            if ok:
                self._last_signal_tweet_id = ""  # thread complete

        self._fire_async(_post())

    # ── 3. 25-min BTC Update ───────────────────────────────────────────────────

    def post_hourly(
        self,
        btc_price: float,
        open_positions: list[dict],
        daily_pnl: float,
        regime: str,
        regime_stability: str,
    ) -> None:
        cooldown = self._posting_cooldown(HOURLY_COOLDOWN)
        if not self._enabled or not self._cooldown_ok("hourly", cooldown):
            return

        last_price = self.memory.get_btc_price()
        price_move_str = ""
        if last_price > 0 and btc_price > 0:
            pct = (btc_price - last_price) / last_price * 100
            if abs(pct) >= 0.15:
                arrow = "▲" if pct > 0 else "▼"
                price_move_str = f" {arrow}{abs(pct):.2f}%"
        if btc_price > 0:
            self.memory.set_btc_price(btc_price)

        utc  = datetime.now(timezone.utc).strftime("%H:%M UTC")
        live = [p for p in open_positions if p and p.get("mode") == "live"]
        paper = [p for p in open_positions if p and p.get("mode") != "live"]
        pnl_str = f"+${daily_pnl:.2f}" if daily_pnl >= 0 else f"-${abs(daily_pnl):.2f}"
        pos_line = (
            f"{len(live)} live position(s) open" if live
            else f"{len(paper)} paper training position(s)" if paper
            else "No open positions. Scanning."
        )

        async def _gen():
            try:
                extra = (
                    f"btc is at {self._fmt_price(btc_price)}{price_move_str}. "
                    f"market regime: {regime.replace('_',' ')}. daily p&l: {pnl_str}. {pos_line}. "
                    f"write a short btc market update in @mistor style. "
                    f"lowercase. max 3 lines. punchy. can diss alts if relevant. no hashtags."
                )
                ai_text = await self._ai_generate(self._build_ai_prompt("market update", extra))
                if ai_text:
                    await self._send_tweet(ai_text, "hourly")
                else:
                    quip = self._regime_quip(regime)
                    price_str = self._fmt_price(btc_price) if btc_price > 0 else "loading"
                    fallback = f"btc at {price_str}{price_move_str}\n\n{quip}"
                    await self._send_tweet(fallback, "hourly")
            except Exception as e:
                logger.error(f"[XPublisher] post_hourly error: {e}")

        self._fire_async(_gen())
        self._touch("hourly")

    # ── 4. Daily Summary ───────────────────────────────────────────────────────

    def post_daily(self, stats: dict, strategy_stats: dict, regime: str, live_pnl: float) -> None:
        if not self._enabled:
            return
        opener = self.memory.pick("daily_opener", DAILY_OPENERS)
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
            "good day. we move." if live_pnl > 5
            else "rough day. sl did its job. we come back." if live_pnl < -5
            else "flat day. patience is the position."
        )
        text = (
            f"{opener} — {date_str}\n\n"
            f"trades: {total}  |  {wins}W / {losses}L\n"
            f"win rate: {wr:.1f}%\n"
            f"live p&l: {pnl}\n"
        )
        if best_strat:
            text += f"top strat: {best_strat}\n"
        text += f"\n{verdict}"
        self._fire(text, "daily")

    # ── 5. Weekly Recap ────────────────────────────────────────────────────────

    def post_weekly(self, stats: dict, strategy_stats: dict, account_balance: float, start_balance: Optional[float] = None) -> None:
        if not self._enabled:
            return
        opener = self.memory.pick("weekly_opener", WEEKLY_OPENERS)
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
            "profitable week. the system holds." if total_pnl > 10
            else "down week. reviewing. adapting. back next week." if total_pnl < -10
            else "breakeven week. we live to trade another day."
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
        )
        self._fire(text, "weekly")

    # ── 6. Crypto News ─────────────────────────────────────────────────────────

    async def post_news(self) -> bool:
        if not self._enabled or not self._cooldown_ok("news", NEWS_COOLDOWN):
            return False

        story = await self._fetch_top_news()
        if not story:
            return False

        title = story["title"][:120]
        link  = story.get("link", "")

        extra = (
            f"react to this news in @mistor style: \"{title}\". "
            f"btc-only perspective. short. lowercase. 1-3 lines. "
            f"be opinionated about what it means for btc. "
            f"can diss alts/other chains if relevant. no hashtags. max 200 chars for link space."
        )
        ai_text = await self._ai_generate(self._build_ai_prompt("news reaction", extra), max_chars=200)

        if ai_text:
            text = ai_text.rstrip()
        else:
            comments = [
                "bullish for btc. nothing else matters",
                "btc doesn't care about the news. it just goes",
                "the chart will tell the real story",
                "everything is eventually good for btc",
                "alts reacting worse. as always",
            ]
            text = f"\"{title[:100]}\"\n\n{random.choice(comments)}"

        if link:
            remaining = 280 - len(text) - 2
            if remaining > 25:
                text = text + "\n" + link[:remaining]

        ok = await self._send_tweet(text[:280], "news")
        if ok:
            self._touch("news")
        return ok

    async def _fetch_top_news(self) -> Optional[dict]:
        feeds = NEWS_FEEDS.copy()
        random.shuffle(feeds)
        for feed_url in feeds:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(feed_url, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code != 200:
                    continue
                root = ET.fromstring(resp.text)
                items = root.findall(".//item")
                if not items:
                    continue
                # Pick from top 5 to add variety
                item = random.choice(items[:5])
                title = (item.findtext("title") or "").strip()
                link  = (item.findtext("link") or "").strip()
                if title and len(title) > 10:
                    return {"title": title, "link": link}
            except Exception as e:
                logger.debug(f"[XPublisher] RSS fetch error ({feed_url}): {e}")
        return None

    # ── 7. Fear & Greed ────────────────────────────────────────────────────────

    async def post_fear_greed(self) -> bool:
        if not self._enabled or not self._cooldown_ok("fear_greed", FEAR_GREED_COOLDOWN):
            return False

        data = await self._fetch_fear_greed()
        if not data:
            return False

        score = int(data.get("value", 50))
        label = data.get("value_classification", "Neutral")

        templates = FEAR_GREED_COMMENTARY.get(label, FEAR_GREED_COMMENTARY["Neutral"])
        text = self.memory.pick(f"fear_greed_{label}", templates).format(score=score, label=label)

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

    # ── 8. Hot Take ────────────────────────────────────────────────────────────

    def post_hot_take(self) -> None:
        if not self._enabled or not self._cooldown_ok("hot_take", HOT_TAKE_COOLDOWN):
            return

        async def _gen():
            try:
                extra = (
                    "write a hot take in @mistor style. "
                    "btc-only. can diss eth/sol/memecoins/altcoins. "
                    "short. lowercase. 1-4 lines. punchy. psychological. "
                    "no hashtags. no emoji unless 😁 or 🙏 or 💨. no corporate speak."
                )
                ai = await self._ai_generate(self._build_ai_prompt("hot take", extra))
                await self._send_tweet(ai or self.memory.pick("hot_take", HOT_TAKES), "hot_take")
            except Exception as e:
                logger.error(f"[XPublisher] post_hot_take error: {e}")

        self._fire_async(_gen())
        self._touch("hot_take")

    # ── 9. Philosophy ──────────────────────────────────────────────────────────

    def post_philosophy(self) -> None:
        if not self._enabled or not self._cooldown_ok("philosophy", PHILOSOPHY_COOLDOWN):
            return

        async def _gen():
            try:
                extra = (
                    "write a short trading wisdom tweet in @mistor style. "
                    "btc-focused. lowercase. 1-4 lines. no hashtags. "
                    "something psychological about holding, conviction, patience, or discipline. "
                    "can reference how altcoin holders don't get it. make it feel real."
                )
                ai = await self._ai_generate(self._build_ai_prompt("trading philosophy", extra))
                await self._send_tweet(ai or self.memory.pick("philosophy", PHILOSOPHY_POSTS), "philosophy")
            except Exception as e:
                logger.error(f"[XPublisher] post_philosophy error: {e}")

        self._fire_async(_gen())
        self._touch("philosophy")

    # ── 10. Engagement Question ────────────────────────────────────────────────

    def post_engagement(self) -> None:
        if not self._enabled or not self._cooldown_ok("engagement", ENGAGEMENT_COOLDOWN):
            return

        async def _gen():
            try:
                extra = (
                    "write a short engaging question for btc twitter in @mistor style. "
                    "lowercase. conversational. btc-focused. 1-3 lines. "
                    "something about holding, selling, conviction, or paper hands. "
                    "the kind of question real btc holders will want to answer. no hashtags."
                )
                ai = await self._ai_generate(self._build_ai_prompt("engagement question", extra))
                await self._send_tweet(ai or self.memory.pick("engagement", ENGAGEMENT_QUESTIONS), "engagement")
            except Exception as e:
                logger.error(f"[XPublisher] post_engagement error: {e}")

        self._fire_async(_gen())
        self._touch("engagement")

    # ── 11. BTC Price Move Alert ───────────────────────────────────────────────

    def post_btc_move(self, current_price: float, prev_price: float) -> None:
        """Post when BTC moves significantly since last hourly post."""
        if not self._enabled or not self._cooldown_ok("btc_move", BTC_MOVE_COOLDOWN):
            return
        if prev_price <= 0 or current_price <= 0:
            return
        pct = (current_price - prev_price) / prev_price * 100
        if abs(pct) < 1.5:
            return

        direction_comment = random.choice(BTC_MOVE_UP_COMMENTS if pct > 0 else BTC_MOVE_DOWN_COMMENTS)
        action = random.choice(BTC_MOVE_ACTIONS)
        action_comment = random.choice(BTC_MOVE_ACTION_COMMENTS)
        template = self.memory.pick("btc_move", BTC_MOVE_TEMPLATES)

        text = template.format(
            pct=pct,
            price=self._fmt_price(current_price),
            direction_comment=direction_comment,
            action=action,
            action_comment=action_comment,
        )
        self._fire(text, "btc_move")
        self._touch("btc_move")

    # ── 12. Algo Insight ──────────────────────────────────────────────────────

    def post_algo_insight(self) -> None:
        if not self._enabled or not self._cooldown_ok("algo_insight", ALGO_INSIGHT_COOLDOWN):
            return
        text = self.memory.pick("algo_insight", ALGO_INSIGHTS)
        self._fire(text, "algo_insight")
        self._touch("algo_insight")

    # ── Manual post (from dashboard) ──────────────────────────────────────────

    async def post_manual(self, text: str) -> bool:
        return await self._send_tweet(text, "manual")
