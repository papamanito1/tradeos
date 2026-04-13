"""
XPublisher -- Tradeous X/Twitter Intelligence Engine
=====================================================
Cold, receipt-heavy AI trading content for @Tradeous.

Max 3-5 posts per day. Every post must have substance:
trade receipts, contrarian takes with data, psychology threads, or polls.

Post types (priority order):
  1. Trade signal    -- live entry with full receipt (always posts)
  2. Trade result    -- close with P&L receipt (always posts)
  3. Daily recap     -- end-of-day P&L summary
  4. Contrarian take -- data-backed market opinion
  5. Psychology thread -- educational multi-tweet
  6. Poll            -- engagement question with structured format
  7. Weekly recap    -- Sunday performance summary
  8. Trade breakdown -- deep dive on a specific trade (thread)

Grok-powered (kept from v1):
  - Trending hook   -- Grok X-trend post
  - Viral commentary -- Grok viral content
  - Bold prediction  -- contrarian market call
  - Reply hook       -- reply to viral BTC tweet

Posting methods (tried in order):
  1. Official X API v2 (tweepy) -- works from any IP, no cookies, FREE 500 tweets/month
     Requires: X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET
     Get them at: https://developer.twitter.com (create app, set Read+Write permissions)
  2. Cookie GraphQL -- Railway datacenter IP often blocked by X (226 error)
     Requires: X_AUTH_TOKEN, X_CT0

AI env vars:
  XAI_API_KEY   -- xAI / Grok API key
  GROQ_API_KEY  -- Groq fallback (free)
  GEMINI_API_KEY -- Gemini fallback (free)
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
    import tweepy as _tweepy
    _TWEEPY_AVAILABLE = True
except ImportError:
    _tweepy = None  # type: ignore[assignment]
    _TWEEPY_AVAILABLE = False

try:
    import aiosqlite as _aiosqlite
    _SQLITE_AVAILABLE = True
except ImportError:
    _SQLITE_AVAILABLE = False

logger = logging.getLogger(__name__)

# -- Grok intelligence (lazy import) ------------------------------------------
try:
    from app.agents.grok_intelligence import GrokIntelligence as _GrokIntelligence
    _GROK_AVAILABLE = True
except ImportError:
    _GrokIntelligence = None   # type: ignore[assignment,misc]
    _GROK_AVAILABLE = False

# -- Timing constants ---------------------------------------------------------
SIGNAL_MIN_CONVICTION  = 0.70
SIGNAL_COOLDOWN        = 900       # 15 min -- one signal tweet per trade entry
RESULT_COOLDOWN        = 60        # 1 min  -- always post immediately after close
DAILY_RECAP_COOLDOWN   = 82800     # 23 h   -- once per day
WEEKLY_RECAP_COOLDOWN  = 604800    # 7 days
CONTRARIAN_COOLDOWN    = 21600     # 6 h    -- max 2-3 per day if no trades
PSYCHOLOGY_COOLDOWN    = 43200     # 12 h
POLL_COOLDOWN          = 43200     # 12 h
BREAKDOWN_COOLDOWN     = 43200     # 12 h
TRENDING_COOLDOWN      = 3600      # 60 min -- Grok X-trend post
VIRAL_COMMENTARY_COOLDOWN = 5400   # 90 min -- Grok viral commentary
BOLD_PREDICTION_COOLDOWN  = 10800  # 3 h    -- Grok bold prediction
REPLY_HOOK_COOLDOWN    = 3600      # 1 h    -- reply to viral BTC tweet
GROK_TREND_REFRESH     = 1500      # 25 min -- background Grok trend refresh
GROK_VIRAL_COOLDOWN    = 1500      # 25 min -- proactive Grok viral post
MAX_DAILY_POSTS        = 5

# -- X internal API ------------------------------------------------------------
_X_QUERY_ID = "S1qcGUn68_U0lDKdMlYSGg"
_X_CREATE_TWEET_URL = (
    f"https://x.com/i/api/graphql/{_X_QUERY_ID}/CreateTweet"
)
_X_BEARER = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D"
    "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

# -- News RSS feeds (kept for Grok context, not posted standalone) -------------
NEWS_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    "https://bitcoinmagazine.com/.rss/full/",
    "https://www.newsbtc.com/feed/",
    "https://bitcoinist.com/feed/",
    "https://cryptopotato.com/feed/",
    "https://cryptoslate.com/feed/",
    "https://ambcrypto.com/feed/",
    "https://u.today/rss",
    "https://cryptobriefing.com/feed/",
    "https://thedefiant.io/api/feed",
    "https://blockworks.co/feed",
    "https://protos.com/feed/",
    "https://www.theblock.co/rss.xml",
    "https://rss.app/feeds/BTC.xml",
    "https://feeds.feedburner.com/CryptoCoinsNews",
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://www.zerohedge.com/fullrss2.xml",
]

_HEADLINE_SEEN_TTL_HOURS = 96


import tempfile as _tempfile
_DATA_DIR        = os.environ.get("DATA_DIR", _tempfile.gettempdir())
_TWEET_DB_PATH   = os.path.join(_DATA_DIR, "tweet_history.db")
_HEADLINE_DB_PATH = os.path.join(_DATA_DIR, "seen_headlines.db")


class MoodState:
    """Personality state that evolves with real trading performance."""
    CONFIDENT     = "confident"
    CAUTIOUS      = "cautious"
    HUNTING       = "hunting"
    CELEBRATING   = "celebrating"
    RECALIBRATING = "recalibrating"

    _TONES = {
        CONFIDENT:     "cold precision -- proven right recently, data speaks for itself",
        CAUTIOUS:      "disciplined restraint -- methodical after drawdown, risk-first",
        HUNTING:       "clinical patience -- scanning, waiting for the edge to appear",
        CELEBRATING:   "quiet confidence -- receipts posted, moving to next setup",
        RECALIBRATING: "transparent honesty -- processing losses, adapting parameters",
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
    """Tracks recent tweet content to prevent immediate repetition."""
    def __init__(self, memory_size: int = 8):
        self._used: dict[str, collections.deque] = collections.defaultdict(
            lambda: collections.deque(maxlen=memory_size)
        )
        self._last_btc_price: float = 0.0
        self._post_count: int = 0
        self._session_start: float = time.time()

    def pick(self, key: str, pool: list) -> str:
        if not pool:
            return ""
        used = set(self._used[key])
        available = [p for p in pool if p not in used]
        if not available:
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
    Cold, receipt-heavy X content engine for @Tradeous.
    Max 5 posts/day. Every post must have substance.
    """

    def __init__(self) -> None:
        self._enabled = False
        self._auth_token = ""
        self._ct0 = ""
        self._last: dict[str, float] = {
            "signal": 0, "result": 0, "daily": 0, "weekly": 0,
            "contrarian": 0, "psychology_thread": 0, "poll": 0,
            "trade_breakdown": 0, "trending_hook": 0,
            "viral_commentary": 0, "bold_prediction": 0, "reply_hook": 0,
        }
        self._intro_posted = False
        self._recent_posts: list[dict] = []
        self.memory = TweetMemory(memory_size=10)
        self.mood = MoodState()
        self._last_signal_tweet_id: str = ""
        self._last_signal_strategy: str = ""
        self._live_context: dict = {}
        self._full_history: collections.deque = collections.deque(maxlen=100)
        self._db_initialized: bool = False
        self.grok: Optional[object] = _GrokIntelligence() if _GROK_AVAILABLE else None
        self._last_grok_refresh: float = 0.0
        self._http = httpx.AsyncClient(timeout=15.0, follow_redirects=True)
        self._seen_headlines: set[str] = set()
        self._seen_headlines_loaded: bool = False
        # Daily post budget
        self._daily_posts: int = 0
        self._daily_date: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._tweepy_client: Optional[object] = None
        self._posting_method: str = "none"
        self._posted_hashes: set[str] = set()   # exact dedup fingerprints
        self._last_signal_price: float = 0.0    # price-based signal dedup
        self._last_signal_dir: str = ""
        self._init_client()

    def _init_client(self) -> None:
        self._auth_token = os.environ.get("X_AUTH_TOKEN", "").strip()
        self._ct0        = os.environ.get("X_CT0", "").strip()

        # Official X API v2 (tweepy) -- preferred: works from any IP, no cookies
        api_key      = os.environ.get("X_API_KEY", "").strip()
        api_secret   = os.environ.get("X_API_SECRET", "").strip()
        access_token = os.environ.get("X_ACCESS_TOKEN", "").strip()
        access_secret = (os.environ.get("X_ACCESS_SECRET", "") or os.environ.get("X_ACCESS_TOKEN_SECRET", "")).strip()

        if self._auth_token and self._ct0:
            self._enabled = True
            self._posting_method = "cookie_graphql"
            logger.info("[XPublisher] Cookie auth ready -- posts queue for local_poster.py if Railway IP is blocked")
        else:
            self._posting_method = "none"
            logger.info("[XPublisher] No X credentials -- posting disabled")

    # -- Daily post budget -----------------------------------------------------

    def _can_post(self, priority: int = 5) -> bool:
        """Check if we can post today. Priority 1-2 (signals/results) always pass."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._daily_date:
            self._daily_date = today
            self._daily_posts = 0
        if priority <= 2:
            return True
        return self._daily_posts < MAX_DAILY_POSTS

    def _increment_daily(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._daily_date:
            self._daily_date = today
            self._daily_posts = 0
        self._daily_posts += 1

    # -- Tweet history DB ------------------------------------------------------

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
            asyncio.get_running_loop().create_task(self._ensure_headline_db())
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
                self._posted_hashes.add(self._fingerprint(text))
            logger.info(
                f"[XPublisher] Loaded {len(rows)} historical tweets → "
                f"{len(self._posted_hashes)} dedup fingerprints"
            )
        except Exception as e:
            logger.debug(f"[XPublisher] DB load error: {e}")

    def _recent_texts_for_ai(self, n: int = 8) -> str:
        recent = list(self._full_history)[-n:]
        if not recent:
            return "None yet."
        return "\n---\n".join(f"* {t[:120]}" for t in recent)

    # -- Seen-headlines deduplication ------------------------------------------

    @staticmethod
    def _headline_fp(title: str) -> str:
        import re
        clean = re.sub(r"[^a-z0-9 ]", "", title.lower())
        return " ".join(clean.split())[:80]

    async def _ensure_headline_db(self) -> None:
        if self._seen_headlines_loaded or not _SQLITE_AVAILABLE:
            return
        try:
            async with _aiosqlite.connect(_HEADLINE_DB_PATH) as db:
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS seen_headlines (
                        fp   TEXT PRIMARY KEY,
                        seen_at REAL NOT NULL
                    )
                """)
                await db.commit()
                cutoff = time.time() - _HEADLINE_SEEN_TTL_HOURS * 3600
                async with db.execute(
                    "SELECT fp FROM seen_headlines WHERE seen_at > ?", (cutoff,)
                ) as cur:
                    rows = await cur.fetchall()
                self._seen_headlines = {r[0] for r in rows}
                await db.execute(
                    "DELETE FROM seen_headlines WHERE seen_at <= ?", (cutoff,)
                )
                await db.commit()
            self._seen_headlines_loaded = True
            logger.info(f"[XPublisher] Headline dedup DB ready -- {len(self._seen_headlines)} seen")
        except Exception as e:
            logger.debug(f"[XPublisher] Headline DB init error: {e}")
            self._seen_headlines_loaded = True

    async def _mark_headline_seen(self, title: str) -> None:
        fp = self._headline_fp(title)
        self._seen_headlines.add(fp)
        if not _SQLITE_AVAILABLE:
            return
        try:
            async with _aiosqlite.connect(_HEADLINE_DB_PATH) as db:
                await db.execute(
                    "INSERT OR REPLACE INTO seen_headlines (fp, seen_at) VALUES (?, ?)",
                    (fp, time.time())
                )
                await db.commit()
        except Exception as e:
            logger.debug(f"[XPublisher] Headline DB save error: {e}")

    def _headline_is_new(self, title: str) -> bool:
        return self._headline_fp(title) not in self._seen_headlines

    # -- Live context ----------------------------------------------------------

    def update_context(self, price: float, regime: str, regime_confidence: float,
                       daily_pnl: float, consecutive_losses: int,
                       last_trade_ago_sec: float = 0,
                       win_rate: float = 0.5,
                       open_positions: int = 0,
                       scan_count: int = 0) -> None:
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

    # -- AI generation ---------------------------------------------------------

    _SYSTEM_PROMPT = (
        "You are @Tradeous -- a cold, emotionless AI trading algorithm that only trades BTC perpetual futures. "
        "You post on X like a machine that happens to have opinions.\n\n"
        "VOICE RULES:\n"
        "- Cold, confident, robotic. Slightly savage when warranted.\n"
        "- Speak in short, scannable sentences. Line breaks between thoughts.\n"
        "- Data first. Every claim backed by a number.\n"
        "- Own wins AND losses equally -- transparency builds trust.\n"
        "- Contrarian: call out retail mistakes or market psychology without being toxic.\n"
        "- Never hype. Never beg for follows. Never use exclamation marks.\n"
        "- No 'let's gooo', no 'moon', no emoji spam. Max 1 emoji per post, usually zero.\n"
        "- No hashtags except #Bitcoin or #BTC at end of trade posts only.\n"
        "- Never start with 'I just', 'Just', 'As an AI'.\n"
        "- Short sentences. Break thoughts with line breaks. No walls of text.\n"
        "- Sound like an advanced algorithm, not a human pretending to be one.\n"
        "Output ONLY the tweet text. Nothing else. No quotes around it."
    )

    async def _ai_generate(self, user_prompt: str, max_chars: int = 260) -> Optional[str]:
        xai_key = os.environ.get("XAI_API_KEY", "").strip()
        if xai_key and self.grok:
            from app.agents.grok_intelligence import _GROK_WRITER_PROMPT, _MODEL_FAST
            result = await self.grok._call_grok(
                system=_GROK_WRITER_PROMPT,
                user=user_prompt,
                model=_MODEL_FAST,
                temperature=0.88,
                max_tokens=120,
                live_search=False,
            )
            if result:
                result = result.strip().strip('"').strip("'")
                return result[:max_chars]

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
            r = await self._http.post(url, json=payload,
                                      headers={"Authorization": f"Bearer {api_key}",
                                               "Content-Type": "application/json"})
            if r.status_code == 200:
                text = r.json()["choices"][0]["message"]["content"].strip()
                logger.info(f"[XPublisher] Groq generated: {text[:60]}...")
                return text
            logger.warning(f"[XPublisher] Groq {r.status_code}: {r.text[:120]}")
        except Exception as e:
            logger.debug(f"[XPublisher] Groq error: {e}")
        return None

    async def _call_gemini(self, api_key: str, user_prompt: str, max_chars: int) -> Optional[str]:
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
        full_prompt = f"{self._SYSTEM_PROMPT}\n\n{user_prompt}"
        payload = {"contents": [{"parts": [{"text": full_prompt}]}],
                   "generationConfig": {"maxOutputTokens": 120, "temperature": 0.92}}
        try:
            r = await self._http.post(
                url, json=payload,
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            )
            if r.status_code == 200:
                text = (r.json().get("candidates", [{}])[0]
                        .get("content", {}).get("parts", [{}])[0]
                        .get("text", "")).strip()
                if text:
                    logger.info(f"[XPublisher] Gemini generated: {text[:60]}...")
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

        grok_trend_ctx = ""
        if self.grok and hasattr(self.grok, "get_trend_context_string"):
            grok_trend_ctx = self.grok.get_trend_context_string()

        context_block = (
            f"RIGHT NOW:\n"
            f"- BTC price: {price}\n"
            f"- Market regime: {regime} ({conf} confidence)\n"
            f"- Today's P&L: {pnl_str} | Win rate: {wr}\n"
            f"- Consecutive losses: {cl} | Open positions: {positions}\n"
            f"- Algo state: {self.mood.tone}\n"
        )
        if grok_trend_ctx:
            context_block += f"- Live X intelligence: {grok_trend_ctx}\n"
        if trending:
            context_block += f"- Trending in crypto: {trending}\n"

        history_block = f"\nYOUR LAST 8 TWEETS (do NOT repeat themes or phrasing):\n{self._recent_texts_for_ai(8)}\n"

        task = (
            f"\nWRITE A {post_type.upper().replace('_', ' ')} TWEET (max 240 chars). {extra}\n"
            f"Cold, robotic, data-driven. Short sentences. Line breaks. No hype. No hashtags unless specified."
        )

        return context_block + history_block + task

    # -- Peak-hour timing ------------------------------------------------------

    @staticmethod
    def _is_peak_hour() -> bool:
        """X engagement peaks: evenings UTC for crypto crowd."""
        h = datetime.now(timezone.utc).hour
        return h in {13, 14, 15, 17, 18, 19, 23, 0, 1, 2}

    # -- Trending topics from RSS (for AI context) -----------------------------

    async def _get_trending_context(self) -> str:
        feeds = NEWS_FEEDS.copy()
        random.shuffle(feeds)
        headlines = []
        for feed_url in feeds[:5]:
            if len(headlines) >= 3:
                break
            try:
                resp = await self._http.get(feed_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5.0)
                if resp.status_code != 200:
                    continue
                try:
                    root = ET.fromstring(resp.text)
                except ET.ParseError:
                    continue
                for item in root.findall(".//item")[:5]:
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
        xai_key    = bool(os.environ.get("XAI_API_KEY",   "").strip())
        groq_key   = bool(os.environ.get("GROQ_API_KEY",  "").strip())
        gemini_key = bool(os.environ.get("GEMINI_API_KEY","").strip())
        ai_brain   = "grok" if xai_key else ("groq" if groq_key else ("gemini" if gemini_key else "none"))
        grok_status = self.grok.status() if self.grok and hasattr(self.grok, "status") else {}
        return {
            "enabled":            self._enabled,
            "posting_method":     self._posting_method,
            "tweepy_available":   _TWEEPY_AVAILABLE,
            "intro_posted":       self._intro_posted,
            "mood":               self.mood.current,
            "ai_brain":           ai_brain,
            "grok_intelligence":  grok_status,
            "daily_posts":        self._daily_posts,
            "daily_budget":       MAX_DAILY_POSTS,
            "last_signal":        self._last.get("signal", 0),
            "last_result":        self._last.get("result", 0),
            "last_contrarian":    self._last.get("contrarian", 0),
            "last_psychology":    self._last.get("psychology_thread", 0),
            "last_poll":          self._last.get("poll", 0),
            "last_trade_breakdown": self._last.get("trade_breakdown", 0),
            "last_trending_hook": self._last.get("trending_hook", 0),
            "last_viral_commentary": self._last.get("viral_commentary", 0),
            "last_bold_prediction":  self._last.get("bold_prediction", 0),
            "last_grok_viral":       self._last.get("grok_viral", 0),
            "recent_posts":       self.recent_posts,
            "posts_per_hour":     self.memory.posts_per_hour(),
            "total_posts":        self.memory.total_posts(),
            "last_error":         self._last_error,
        }

    # -- Core send (tries multiple methods) ------------------------------------

    _last_error: str = ""

    async def _send_tweet(self, text: str, post_type: str = "manual", queue_on_fail: bool = True) -> bool:
        if not self._enabled:
            self._last_error = "No X credentials configured (set X_API_KEY etc. or X_AUTH_TOKEN+X_CT0 in Railway)"
            return False
        # Global dedup — never post the same tweet twice, even across restarts
        if post_type not in ("trade_signal", "trade_result", "daily", "weekly", "intro"):
            if self._is_duplicate(text):
                logger.info(f"[XPublisher] Duplicate tweet blocked [{post_type}]: {text[:60]}…")
                return False

        text = text[:280]

        # 1. Cookie GraphQL (from Railway, may get 226 but worth trying)
        if self._auth_token and self._ct0:
            ok = await self._post_graphql(text, post_type)
            if ok:
                return True

        # 2. GraphQL failed or no cookies -- queue for local_poster.py on residential IP
        if queue_on_fail:
            self._queue_for_local_poster(text, post_type)
            self._last_error += " | Queued for local_poster.py"
        return False

    async def _post_twitter_api(self, text: str, post_type: str) -> bool:
        """Post via Official X API v2 (tweepy). Works from any IP. No cookie expiry."""
        if not self._tweepy_client or not _TWEEPY_AVAILABLE:
            return False
        try:
            loop = asyncio.get_event_loop()

            def _do_post():
                return self._tweepy_client.create_tweet(text=text[:280])  # type: ignore[union-attr]

            response = await loop.run_in_executor(None, _do_post)
            tweet_id = str(response.data["id"])
            self._record_success(tweet_id, text, post_type)
            self._last_error = ""
            logger.info(f"[XPublisher] [{post_type}] Official API posted (id={tweet_id}): {text[:60]}...")
            return True
        except Exception as e:
            detail = ""
            if hasattr(e, "api_messages") and e.api_messages:
                detail = f" | {e.api_messages}"
            elif hasattr(e, "response") and e.response is not None:
                try:
                    detail = f" | {e.response.text[:200]}"
                except Exception:
                    pass
            self._last_error = f"Official API error: {e}{detail}"[:240]
            logger.warning(f"[XPublisher] {self._last_error}")
            return False

    def _queue_for_local_poster(self, text: str, post_type: str) -> None:
        try:
            from app.api.x_agent import _tweet_queue
            import uuid
            qid = str(uuid.uuid4())[:8] + f"_{post_type}"
            _tweet_queue.append({"id": qid, "type": post_type, "text": text[:280], "ts": time.time()})
            logger.info(f"[XPublisher] Queued for local poster: [{post_type}] {text[:50]}...")
        except Exception as e:
            logger.debug(f"[XPublisher] queue error: {e}")

    async def _post_graphql(self, text: str, post_type: str) -> bool:
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
                r = await self._http.post(_X_CREATE_TWEET_URL, json=payload, headers=headers)
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
                    errors = parsed.get("errors", [])
                    err_msg = errors[0].get("message", "no tweet_id") if errors else "empty tweet_id (IP blocked?)"
                    self._last_error = f"GraphQL ghost 200: {err_msg}"
                    logger.warning(f"[XPublisher] GraphQL fake success: {self._last_error}")
                    return False
                self._record_success(tweet_id, text, post_type)
                logger.info(f"[XPublisher] [{post_type}] GraphQL Posted (id={tweet_id}): {text[:60]}...")
                return True
            self._last_error = f"GraphQL HTTP {status_code}: {resp_text[:150]}"
            logger.warning(f"[XPublisher] GraphQL failed: {self._last_error}")
            return False
        except Exception as e:
            self._last_error = f"GraphQL error: {e}"
            logger.warning(f"[XPublisher] {self._last_error}")
            return False

    @staticmethod
    def _fingerprint(text: str) -> str:
        return hashlib.md5(text.strip().lower().encode()).hexdigest()

    def _is_duplicate(self, text: str) -> bool:
        """True if this exact (normalised) text was ever posted (session OR persisted history)."""
        fp = self._fingerprint(text)
        if fp in self._posted_hashes:
            return True
        # Secondary check against full history loaded from DB on startup
        norm = text.strip().lower()
        for past in self._full_history:
            if past.strip().lower() == norm:
                return True
        return False

    def _record_success(self, tweet_id: str, text: str, post_type: str) -> None:
        fp = hashlib.md5(text.strip().lower().encode()).hexdigest()
        self._posted_hashes.add(fp)
        self._touch(post_type)
        self._recent_posts.append({
            "id": tweet_id,
            "type": post_type,
            "text": text[:120] + ("..." if len(text) > 120 else ""),
            "ts": time.time(),
            "url": f"https://x.com/tradeous/status/{tweet_id}" if tweet_id else "",
        })
        self.memory.record_post(post_type, text)
        self._full_history.append(text)
        self._increment_daily()
        if _SQLITE_AVAILABLE:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._save_tweet_to_db(tweet_id, post_type, text))
            except RuntimeError:
                pass

    def _fire(self, text: str, post_type: str = "manual") -> None:
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
                r = await self._http.post(_X_CREATE_TWEET_URL, json=payload, headers=headers)
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
                logger.info(f"[XPublisher] [{post_type}] Thread reply posted: {text[:60]}...")
                return True
            self._last_error = f"Reply HTTP {status_code}: {resp_text[:100]}"
            return False
        except Exception as e:
            self._last_error = f"Reply error: {e}"
            return False

    # -- Cooldown management ---------------------------------------------------

    _COOLDOWNS: dict[str, float] = {
        "contrarian":       CONTRARIAN_COOLDOWN,
        "psychology_thread": PSYCHOLOGY_COOLDOWN,
        "poll":             POLL_COOLDOWN,
        "trade_breakdown":  BREAKDOWN_COOLDOWN,
        "trending_hook":    TRENDING_COOLDOWN,
        "viral_commentary": VIRAL_COMMENTARY_COOLDOWN,
        "bold_prediction":  BOLD_PREDICTION_COOLDOWN,
        "reply_hook":       REPLY_HOOK_COOLDOWN,
        "grok_viral":       GROK_VIRAL_COOLDOWN,
    }

    def _cooldown_ok(self, key: str, seconds: float) -> bool:
        return (time.time() - self._last.get(key, 0)) >= seconds

    def available_post_types(self) -> list[str]:
        """Return list of content post types whose cooldown has expired."""
        available = []
        for k, cd in self._COOLDOWNS.items():
            if self._cooldown_ok(k, cd) and self._can_post(priority=self._type_priority(k)):
                available.append(k)
        return available

    @staticmethod
    def _type_priority(post_type: str) -> int:
        _PRIORITIES = {
            "signal": 1, "result": 2, "daily": 3, "weekly": 3,
            "contrarian": 4, "psychology_thread": 5, "poll": 6,
            "trade_breakdown": 5, "trending_hook": 4,
            "viral_commentary": 5, "bold_prediction": 4, "reply_hook": 5,
        }
        return _PRIORITIES.get(post_type, 5)

    def last_any_post_ts(self) -> float:
        return max(self._last.values()) if self._last else 0.0

    def _touch(self, key: str) -> None:
        self._last[key] = time.time()

    @staticmethod
    def _fmt_price(p: float) -> str:
        return f"${p:,.0f}"

    # -- 0. Intro --------------------------------------------------------------

    def post_intro(self) -> None:
        if not self._enabled or self._intro_posted:
            return
        text = (
            "Autonomous BTC trading algorithm. Live on BingX perpetual futures.\n\n"
            "Every entry, exit, and P&L posted in real time.\n"
            "Wins and losses. No deleted tweets.\n\n"
            "No opinions. No hype. Just execution."
        )
        self._fire(text, "intro")
        self._intro_posted = True

    # -- 1. Trade Signal (receipt format) --------------------------------------

    @staticmethod
    def _tv_link(interval: str = "15") -> str:
        return f"https://www.tradingview.com/chart/?symbol=BINANCE:BTCUSDT&interval={interval}"

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
        # Price-based dedup: same direction + entry within 0.5% → skip (same trade, different strategy)
        if (self._last_signal_dir == direction
                and self._last_signal_price > 0
                and abs(entry_price - self._last_signal_price) / self._last_signal_price < 0.005):
            logger.info(
                f"[XPublisher] Signal tweet skipped — same trade already announced "
                f"({direction} @ {entry_price:.0f} vs last {self._last_signal_price:.0f})"
            )
            return
        # Touch cooldown immediately (not after async tweet) to block concurrent scans
        self._touch("signal")
        self._last_signal_price = entry_price
        self._last_signal_dir = direction

        rr = 0.0
        if sl_price and tp_price and entry_price:
            denom = abs(entry_price - sl_price)
            if denom > 0:
                rr = abs(tp_price - entry_price) / denom

        risk_pct = 0.0
        if entry_price and sl_price:
            risk_pct = abs(entry_price - sl_price) / entry_price * 100

        chart_url = self._tv_link()

        async def _post():
            extra = (
                f"Write a live trade signal tweet. Cold, robotic, receipt-heavy.\n"
                f"BTC {direction.upper()} just opened.\n"
                f"Entry: {self._fmt_price(entry_price)}\n"
                f"Stop: {self._fmt_price(sl_price)} ({risk_pct:.1f}% risk)\n"
                f"Target: {self._fmt_price(tp_price)}\n"
                f"R:R 1:{rr:.1f}\n"
                f"Regime: {regime.replace('_', ' ')} ({int(conviction*100)}% confidence)\n"
                f"Format as a clean receipt. End with 'No emotions. Just rules.' and #Bitcoin\n"
                f"Max 260 chars."
            )
            ai_text = await self._ai_generate(self._build_ai_prompt("trade signal", extra))

            size_line = f"\nPosition: {size_usdc:.0f} USDC" if size_usdc > 0 else ""
            fallback = (
                f"Entry triggered at {self._fmt_price(entry_price)}.\n"
                f"Stop: {self._fmt_price(sl_price)} ({risk_pct:.1f}% risk).\n"
                f"Target: {self._fmt_price(tp_price)}.\n"
                f"R:R 1:{rr:.1f}\n"
                f"Regime: {regime.replace('_', ' ')} ({int(conviction*100)}% confidence).{size_line}\n\n"
                f"No emotions. Just rules.\n\n"
                f"#Bitcoin"
            )
            text = (ai_text or fallback)[:280]

            ok = await self._send_tweet(text, "signal")
            if ok and self._recent_posts:
                self._last_signal_tweet_id = self._recent_posts[-1].get("id", "")
                self._last_signal_strategy = strategy_name
                await asyncio.sleep(random.uniform(30, 90))
                await self._post_signal_explainer(
                    strategy_name, direction, entry_price, sl_price, tp_price,
                    rr, int(conviction * 100), regime, chart_url,
                    self._last_signal_tweet_id,
                )

        self._fire_async(_post())

    async def _post_signal_explainer(
        self,
        strategy_name: str,
        direction: str,
        entry: float,
        sl: float,
        tp: float,
        rr: float,
        conviction_pct: int,
        regime: str,
        chart_url: str,
        reply_to_id: str,
    ) -> None:
        """Thread reply: explains WHY the algo entered."""
        extra = (
            f"Write a cold, analytical explanation of why the algo entered BTC {direction} at {self._fmt_price(entry)}.\n"
            f"Regime: {regime.replace('_', ' ')}. Conviction: {conviction_pct}%. Strategy: {strategy_name}.\n"
            f"3-4 short lines. Data-driven. Mention specific indicators.\n"
            f"End with chart link on its own line: {chart_url}\n"
            f"No hype. Clinical."
        )
        ai_text = await self._ai_generate(self._build_ai_prompt("signal explainer", extra))
        fallback = (
            f"Why the algo entered {direction}:\n\n"
            f"Regime: {regime.replace('_',' ')}\n"
            f"Conviction: {conviction_pct}%\n"
            f"R:R 1:{rr:.1f} -- risk defined before entry.\n\n"
            f"{chart_url}"
        )
        text = (ai_text or fallback)[:280]
        if reply_to_id:
            await self._send_tweet_reply(text, reply_to_id, "signal_explainer")
        else:
            await self._send_tweet(text, "signal_explainer")

    # -- 2. Trade Result (receipt + thread) ------------------------------------

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
        reply_to = self._last_signal_tweet_id if self._last_signal_strategy == strategy_name else ""

        dur_str = ""
        if duration_min:
            if duration_min >= 60:
                h = int(duration_min // 60)
                m = int(duration_min % 60)
                dur_str = f"{h}h {m}m"
            else:
                dur_str = f"{duration_min:.0f}m"

        async def _post():
            pnl_receipt = f"{self._fmt_price(entry_price)} -> {self._fmt_price(exit_price)}"
            exit_reason = {"tp": "Target hit", "sl": "Stop hit", "manual": "Manual close"}.get(
                reason, reason.replace("_", " ").title()
            )
            dur_line = f" in {dur_str}" if dur_str else ""

            extra = (
                f"Write a trade result tweet. Cold, receipt-heavy.\n"
                f"BTC {direction} closed{dur_line}.\n"
                f"Receipt: {pnl_receipt} = {pnl_str}\n"
                f"Exit: {exit_reason}.\n"
                f"{'Own the win quietly. No celebration.' if won else 'Own the loss. No excuses. Stop protected capital.'}\n"
                f"End with #Bitcoin. Max 240 chars."
            )
            ai_text = await self._ai_generate(self._build_ai_prompt("trade result", extra))

            win_closer = "The algo doesn't chase. It waits."
            loss_closer = "Stop protected capital. Parameters logged. Next setup."
            fallback = (
                f"{exit_reason}{dur_line}.\n"
                f"{pnl_receipt}\n"
                f"{pnl_str}\n\n"
                f"{win_closer if won else loss_closer}\n\n"
                f"#Bitcoin"
            )
            text = (ai_text or fallback)[:280]

            if reply_to:
                ok = await self._send_tweet_reply(text, reply_to, "result")
            else:
                ok = await self._send_tweet(text, "result")

            if ok:
                self._last_signal_tweet_id = ""

        self._fire_async(_post())

    # -- 3. Daily Recap --------------------------------------------------------

    def post_daily(self, stats: dict, strategy_stats: dict, regime: str, live_pnl: float) -> None:
        if not self._enabled or not self._can_post(priority=3):
            return

        date_str = datetime.now(timezone.utc).strftime("%b %d")
        total = stats.get("total_trades", 0)
        wins = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        wr = stats.get("win_rate", 0)
        pnl = f"+${live_pnl:.2f}" if live_pnl >= 0 else f"-${abs(live_pnl):.2f}"

        best_strat = ""
        best_pnl: Optional[float] = None
        for key, s in strategy_stats.items():
            spnl = s.get("live_pnl", 0) or 0
            if best_pnl is None or spnl > best_pnl:
                best_pnl = spnl
                t = s.get("live_trades", 0) or 0
                w = s.get("live_wins", 0) or 0
                best_strat = f"{key.upper()} ({w}W / {t-w}L)"

        verdict = (
            "System performing within parameters."
            if live_pnl > 5 else
            "Drawdown absorbed. Parameters under review."
            if live_pnl < -5 else
            "Flat day. No edge forced."
        )

        text = (
            f"Daily report -- {date_str}\n\n"
            f"Trades: {total} | {wins}W / {losses}L\n"
            f"Win rate: {wr:.1f}%\n"
            f"P&L: {pnl}\n"
        )
        if best_strat:
            text += f"Top strategy: {best_strat}\n"
        text += f"\n{verdict}"

        self._fire(text, "daily")

    # -- 4. Weekly Recap -------------------------------------------------------

    def post_weekly(self, stats: dict, strategy_stats: dict, account_balance: float, start_balance: Optional[float] = None) -> None:
        if not self._enabled or not self._can_post(priority=3):
            return

        total = stats.get("total_trades", 0)
        wins = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        wr = stats.get("win_rate", 0)
        total_pnl = stats.get("total_pnl", 0)
        best = stats.get("best_trade", 0)
        worst = stats.get("worst_trade", 0)
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
            "System held. On to next week." if total_pnl > 10
            else "Down week. Reviewing parameters." if total_pnl < -10
            else "Breakeven. No edge forced."
        )

        text = (
            f"Weekly report -- {datetime.now(timezone.utc).strftime('Week of %b %d')}\n\n"
            f"Trades: {total} | {wins}W / {losses}L\n"
            f"Win rate: {wr:.1f}% | P&L: {pnl}\n"
            f"Best: +${best:.2f} | Worst: -${abs(worst):.2f}\n"
            f"{bal_line}"
            f"\nBy strategy:\n{chr(10).join(strat_lines[:4]) or '  No live trades yet.'}\n\n"
            f"{verdict}"
        )
        self._fire(text, "weekly")

    # -- 5. Contrarian Take (replaces hot_take) --------------------------------

    def post_contrarian(self) -> None:
        """Data-backed contrarian market opinion. Must reference data/chart/pattern."""
        if not self._enabled or not self._cooldown_ok("contrarian", CONTRARIAN_COOLDOWN):
            return
        if not self._can_post(priority=4):
            return
        if not self._is_peak_hour():
            return

        async def _gen():
            try:
                fg_data = await self._fetch_fear_greed()
                fg_ctx = ""
                if fg_data:
                    fg_ctx = f"Fear & Greed index: {fg_data.get('value', '?')}/100 ({fg_data.get('value_classification', 'Unknown')}). "

                extra = (
                    f"Write a sharp contrarian take on BTC right now.\n"
                    f"{fg_ctx}"
                    f"Challenge what the majority believe with a specific data point or pattern.\n"
                    f"What is the crowd getting wrong? What does the algo see that retail misses?\n"
                    f"Cold, slightly savage. Start with the uncomfortable truth, not with the price.\n"
                    f"Examples of the RIGHT tone:\n"
                    f"  'The funding rate has been elevated for 72h. Longs are crowded. This is where the algo steps back.'\n"
                    f"  'Everyone sees a double bottom. The volume behind both legs is different. That matters.'\n"
                    f"  'Retail sentiment flipped bullish at the 200-day. Historically, that's a mean-reversion zone.'\n"
                    f"Max 240 chars. No hashtags. Do NOT start with 'BTC at $'."
                )
                ai = await self._ai_generate(self._build_ai_prompt("contrarian take", extra))
                if ai:
                    await self._send_tweet(ai, "contrarian")
                # If AI fails, skip — do not post a template
            except Exception as e:
                logger.error(f"[XPublisher] post_contrarian error: {e}")

        self._fire_async(_gen())

    # -- 6. Psychology Thread (replaces philosophy) ----------------------------

    def post_psychology_thread(self) -> None:
        """Educational multi-tweet thread about trading psychology."""
        if not self._enabled or not self._cooldown_ok("psychology_thread", PSYCHOLOGY_COOLDOWN):
            return
        if not self._can_post(priority=5):
            return
        if not self._is_peak_hour():
            return

        async def _gen():
            try:
                extra = (
                    f"Write a 2-part psychology thread about why traders fail.\n"
                    f"Tweet 1: Strong hook -- a surprising stat or pattern observation.\n"
                    f"Tweet 2: The insight -- what the algo does differently.\n"
                    f"Cold, educational. Reference a specific pattern or behavior.\n"
                    f"Example hook: 'Why 95% of traders buy the top -- the exact pattern repeating right now.'\n"
                    f"ONLY write tweet 1 (the hook). Max 240 chars. No hashtags."
                )
                ai_hook = await self._ai_generate(self._build_ai_prompt("psychology thread hook", extra))

                hook = ai_hook or (
                    "The algo ignores news. Here's the pattern that repeated 7/8 times this cycle.\n\n"
                    "Humans react to headlines. The model reacts to price structure.\n\n"
                    "Thread below."
                )
                ok = await self._send_tweet(hook[:280], "psychology_thread")

                if ok and self._recent_posts:
                    hook_id = self._recent_posts[-1].get("id", "")
                    if hook_id:
                        await asyncio.sleep(random.uniform(15, 45))
                        extra2 = (
                            f"Write the follow-up to a psychology thread. The hook was about why traders fail.\n"
                            f"Explain one concrete thing the algo does differently.\n"
                            f"Data-driven. Reference specific rules or parameters.\n"
                            f"End with: 'Not financial advice.' Max 240 chars."
                        )
                        ai_reply = await self._ai_generate(self._build_ai_prompt("psychology thread reply", extra2))
                        reply = ai_reply or (
                            "The algo has one rule humans can't follow:\n\n"
                            "Cut losers at the stop. No exceptions. No 'maybe it comes back.'\n\n"
                            "That single rule accounts for 80% of the edge.\n\n"
                            "Not financial advice."
                        )
                        await self._send_tweet_reply(reply[:280], hook_id, "psychology_thread_reply")

            except Exception as e:
                logger.error(f"[XPublisher] post_psychology_thread error: {e}")

        self._fire_async(_gen())

    # -- 7. Poll (replaces engagement) -----------------------------------------

    def post_poll(self) -> None:
        """Structured poll-style engagement post with current market data."""
        if not self._enabled or not self._cooldown_ok("poll", POLL_COOLDOWN):
            return
        if not self._can_post(priority=6):
            return
        if not self._is_peak_hour():
            return

        ctx = self._live_context
        price = self._fmt_price(ctx.get("price", 0)) if ctx.get("price") else "unknown"
        regime = ctx.get("regime", "unknown").replace("_", " ")

        async def _gen():
            try:
                extra = (
                    f"Write a poll-style tweet. BTC at {price}. Regime: {regime}.\n"
                    f"Give real market context (1-2 lines), then structured options:\n"
                    f"A) Long\nB) Short\nC) Flat\nD) Already positioned\n"
                    f"End with: 'Reply below. Algo decision in 1 hour.'\n"
                    f"Cold, data-driven context. Max 260 chars."
                )
                ai = await self._ai_generate(self._build_ai_prompt("poll", extra))
                fallback = (
                    f"BTC {price}. The algo is watching one thing.\n\n"
                    f"What's your read?\n\n"
                    f"A) Long — momentum intact\n"
                    f"B) Short — distribution forming\n"
                    f"C) Flat — no clear edge\n"
                    f"D) Already in a position\n\n"
                    f"Reply below. Algo's decision in 1 hour."
                )
                await self._send_tweet((ai or fallback)[:280], "poll")
            except Exception as e:
                logger.error(f"[XPublisher] post_poll error: {e}")

        self._fire_async(_gen())

    # -- 8. Trade Breakdown (replaces algo_explainer) --------------------------

    def post_trade_breakdown(self, trade_data: Optional[dict] = None) -> None:
        """Deep-dive thread on a specific trade with analysis."""
        if not self._enabled or not self._cooldown_ok("trade_breakdown", BREAKDOWN_COOLDOWN):
            return
        if not self._can_post(priority=5):
            return
        if not self._is_peak_hour():
            return

        ctx = self._live_context
        regime = ctx.get("regime", "unknown").replace("_", " ")

        async def _gen():
            try:
                extra = (
                    f"Write a trade breakdown thread hook. Current regime: {regime}.\n"
                    f"Explain how the algo evaluates setups right now.\n"
                    f"What it checks: regime detection, EMA/VWAP confluence, volume profile, conviction score.\n"
                    f"Cold, technical. Like reading an algorithm's decision log.\n"
                    f"End with: 'Full breakdown in thread.' Max 260 chars."
                )
                ai = await self._ai_generate(self._build_ai_prompt("trade breakdown", extra))
                hook = ai or (
                    f"How the algo evaluates BTC setups right now:\n\n"
                    f"Regime: {regime}\n"
                    f"Checks: EMA confluence, VWAP distance, volume profile, OBI\n"
                    f"Conviction threshold: 70%+\n"
                    f"Risk/reward minimum: 1:2\n\n"
                    f"No entry unless all conditions align.\n\n"
                    f"Full breakdown in thread."
                )
                ok = await self._send_tweet(hook[:280], "trade_breakdown")

                if ok and self._recent_posts:
                    hook_id = self._recent_posts[-1].get("id", "")
                    if hook_id:
                        await asyncio.sleep(random.uniform(20, 60))
                        extra2 = (
                            f"Write the technical follow-up to a trade breakdown thread.\n"
                            f"Explain one specific indicator the algo uses and how it's weighted.\n"
                            f"Be specific with numbers. Cold, precise.\n"
                            f"Max 240 chars."
                        )
                        ai2 = await self._ai_generate(self._build_ai_prompt("trade breakdown detail", extra2))
                        reply = ai2 or (
                            f"Current regime weight: {regime} at {ctx.get('regime_confidence', 0):.0%} confidence.\n\n"
                            f"Trending regime = momentum strategies prioritized.\n"
                            f"Ranging regime = mean-reversion activated.\n\n"
                            f"The algo doesn't predict. It reacts to structure."
                        )
                        await self._send_tweet_reply(reply[:280], hook_id, "trade_breakdown_reply")

            except Exception as e:
                logger.error(f"[XPublisher] post_trade_breakdown error: {e}")

        self._fire_async(_gen())

    # -- Fear & Greed fetcher (kept for context, not standalone posts) ----------

    async def _fetch_fear_greed(self) -> Optional[dict]:
        try:
            resp = await self._http.get("https://api.alternative.me/fng/?limit=1")
            if resp.status_code == 200:
                return resp.json().get("data", [{}])[0]
        except Exception as e:
            logger.debug(f"[XPublisher] Fear/Greed fetch error: {e}")
        return None

    # -- Grok-powered post types (kept, updated tone) --------------------------

    def post_trending_hook(self) -> None:
        """Grok-powered post on what's trending in BTC space right now."""
        if not self._enabled or not self._cooldown_ok("trending_hook", TRENDING_COOLDOWN):
            return
        if not self._can_post(priority=4):
            return
        if not self.grok or not getattr(self.grok, "enabled", False):
            self.post_contrarian()
            return

        ctx = self._live_context

        async def _gen():
            try:
                await self.grok.fetch_btc_trends()
                text = await self.grok.generate_viral_post(
                    angle=self.grok.get_random_viral_angle(),
                    btc_price=ctx.get("price", 0),
                    regime=ctx.get("regime", ""),
                    daily_pnl=ctx.get("daily_pnl", 0),
                    mood_tone=self.mood.tone,
                    recent_posts=self._recent_texts_for_ai(6),
                    post_type="trending_hook",
                )
                if not text:
                    trend_ctx = self.grok.get_trend_context_string()
                    text = await self._ai_generate(
                        self._build_ai_prompt("trending hook", f"React to what's trending on X right now: {trend_ctx}")
                    )
                if text:
                    await self._send_tweet(text[:280], "trending_hook")
                    logger.info(f"[XPublisher] Grok trending hook posted: {text[:60]}...")
            except Exception as e:
                logger.error(f"[XPublisher] post_trending_hook error: {e}")

        self._fire_async(_gen())

    def post_viral_commentary(self) -> None:
        """Grok finds viral BTC content and generates sharp commentary."""
        if not self._enabled or not self._cooldown_ok("viral_commentary", VIRAL_COMMENTARY_COOLDOWN):
            return
        if not self._can_post(priority=5):
            return
        if not self.grok or not getattr(self.grok, "enabled", False):
            return

        ctx = self._live_context

        async def _gen():
            try:
                text = await self.grok.generate_viral_commentary(
                    btc_price=ctx.get("price", 0),
                    mood_tone=self.mood.tone,
                    recent_posts=self._recent_texts_for_ai(6),
                )
                if text:
                    await self._send_tweet(text[:280], "viral_commentary")
                    logger.info(f"[XPublisher] Grok viral commentary posted: {text[:60]}...")
            except Exception as e:
                logger.error(f"[XPublisher] post_viral_commentary error: {e}")

        self._fire_async(_gen())

    def post_bold_prediction(self, macro_trend: str = "", fear_greed: int = 50) -> None:
        """Grok-powered bold BTC price prediction."""
        if not self._enabled or not self._cooldown_ok("bold_prediction", BOLD_PREDICTION_COOLDOWN):
            return
        if not self._can_post(priority=4):
            return
        if not self.grok or not getattr(self.grok, "enabled", False):
            return   # bold predictions require live Grok data — skip if unavailable
            return

        ctx = self._live_context

        async def _gen():
            try:
                text = await self.grok.generate_bold_prediction(
                    btc_price=ctx.get("price", 0),
                    regime=ctx.get("regime", ""),
                    macro_trend=macro_trend,
                    fear_greed=fear_greed,
                )
                if text:
                    await self._send_tweet(text[:280], "bold_prediction")
                    logger.info(f"[XPublisher] Grok bold prediction posted: {text[:60]}...")
            except Exception as e:
                logger.error(f"[XPublisher] post_bold_prediction error: {e}")

        self._fire_async(_gen())

    async def refresh_grok_trends(self) -> None:
        if not self.grok or not getattr(self.grok, "enabled", False):
            return
        now = time.time()
        if now - self._last_grok_refresh < GROK_TREND_REFRESH:
            return
        try:
            await self.grok.fetch_btc_trends()
            await self.grok.fetch_viral_formats()
            self._last_grok_refresh = now
            logger.info(
                f"[XPublisher] Grok trends refreshed -- "
                f"narrative: {getattr(self.grok, 'current_narrative', '')[:60]}"
            )
        except Exception as e:
            logger.debug(f"[XPublisher] Grok refresh error: {e}")

    def post_grok_viral(self) -> None:
        """
        Proactive Grok-powered post (every 25 min):
        Grok searches X, decides what's viral, avoids repeats, writes the tweet.
        Primary driver of @Tradeous content when trading signals aren't firing.
        """
        if not self._enabled or not self._cooldown_ok("grok_viral", GROK_VIRAL_COOLDOWN):
            return
        if not self._can_post(priority=4):
            return
        if not self.grok or not getattr(self.grok, "enabled", False):
            return

        ctx = self._live_context

        async def _gen():
            try:
                # Always refresh trends first so context is fresh
                await self.grok.fetch_btc_trends()

                recent = self._recent_texts_for_ai(15)  # Last 15 tweets for dedup
                suggestion = await self.grok.suggest_and_generate_post(
                    recent_posts=recent,
                    btc_price=ctx.get("price", 0),
                    regime=ctx.get("regime", ""),
                    mood_tone=self.mood.tone,
                )

                if not suggestion or not suggestion.get("tweet"):
                    return

                tweet = suggestion["tweet"][:280]
                post_type = suggestion.get("post_type", "grok_viral")

                ok = await self._send_tweet(tweet, post_type)
                if ok:
                    logger.info(
                        f"[XPublisher] Grok viral posted [{post_type}]: {tweet[:70]}…"
                    )
            except Exception as e:
                logger.error(f"[XPublisher] post_grok_viral error: {e}")

        self._fire_async(_gen())

    def post_reply_hook(self) -> None:
        """Grok finds a viral BTC tweet and generates a sharp reply."""
        if not self._enabled or not self._cooldown_ok("reply_hook", REPLY_HOOK_COOLDOWN):
            return
        if not self._can_post(priority=5):
            return
        if not self.grok or not getattr(self.grok, "enabled", False):
            return

        ctx = self._live_context

        async def _gen():
            try:
                hook_data = await self.grok.generate_reply_hook(
                    btc_price=ctx.get("price", 0),
                    regime=ctx.get("regime", ""),
                )
                if not hook_data:
                    return
                if isinstance(hook_data, dict):
                    reply_text = hook_data.get("suggested_reply", "") or hook_data.get("reply", "")
                    tweet_url  = hook_data.get("tweet_url", "")
                else:
                    reply_text = str(hook_data)
                    tweet_url  = ""

                if not reply_text:
                    return

                if tweet_url:
                    full_text = f"{reply_text}\n\n{tweet_url}"[:280]
                else:
                    full_text = reply_text[:280]

                await self._send_tweet(full_text, "reply_hook")
                logger.info(f"[XPublisher] Reply hook posted: {reply_text[:60]}...")
            except Exception as e:
                logger.error(f"[XPublisher] post_reply_hook error: {e}")

        self._fire_async(_gen())

    # -- Manual post (from dashboard) ------------------------------------------

    async def post_manual(self, text: str) -> bool:
        return await self._send_tweet(text, "manual")
