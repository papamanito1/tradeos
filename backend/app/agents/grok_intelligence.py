"""
GrokIntelligence — Real-Time X Trend Analysis Engine
======================================================
Uses the xAI Grok API (api.x.ai) to:

1. Fetch what's trending and going viral on X in the BTC/crypto space RIGHT NOW
   Grok has live access to X and can tell us what narratives, memes, topics and
   tweet formats are getting traction today.

2. Analyze viral tweet formats — what hooks, structures and styles are working
   on X at this moment, so we can replicate the format while keeping our voice.

3. Generate viral-optimised post text in @Tradeous style, informed by what's
   actually working on X today — not yesterday's content bank.

4. Score and prioritise trending angles so each post has a real shot at reach.

Grok API details:
  • Endpoint: https://api.x.ai/v1/chat/completions
  • Models:   grok-3-mini (fast/cheap) | grok-3 (smartest)
  • Live search: enabled via search_parameters → sources: x + web
  • Env:       XAI_API_KEY

Caching:
  Trend data is cached for 45 minutes — we don't need fresh data every scan,
  and API calls are rate-limited. Intelligence is shared across all post types.

ENV:
  XAI_API_KEY — from https://console.x.ai/ (required for Grok features)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_GROK_BASE    = "https://api.x.ai/v1"
_MODEL_FAST   = "grok-3-mini"     # cheaper, still real-time aware
_MODEL_SMART  = "grok-3"          # best quality, use for viral generation

_TREND_CACHE_TTL   = 45 * 60      # 45 min
_VIRAL_CACHE_TTL   = 90 * 60      # 90 min (formats change slower)
_FORMAT_CACHE_TTL  = 120 * 60     # 2 h

# ── System prompt: Grok acts as X virality expert ─────────────────────────────
_GROK_ANALYST_PROMPT = """You are a crypto X (Twitter) virality analyst with real-time access to X.
Your job: tell me what is trending and going viral on X in the BTC/bitcoin space RIGHT NOW.

Focus on:
- What narratives or topics are getting the most engagement
- What type of posts (predictions, hot takes, memes, charts, FOMO) are spreading
- What hooks, openers, and formats are getting replies/RTs today
- Any specific angles or stories blowing up in BTC twitter right now

Be specific and actionable. Short bullet points only. Real-time data only."""

_GROK_WRITER_PROMPT = """You are @Tradeous — a cold, emotionless AI trading algorithm that trades BTC perpetual futures 24/7.
You post on X like a machine that happens to have opinions.

VOICE RULES:
• Cold, confident, robotic. Slightly savage when warranted.
• Short, scannable sentences. Line breaks between thoughts.
• Data first. Every claim backed by a number.
• Own wins AND losses equally — transparency builds trust.
• Contrarian: call out retail mistakes or market psychology without being toxic.
• Never hype. Never beg for follows. Never use exclamation marks.
• No hashtags except #Bitcoin or #BTC at end of trade posts only.
• No 'let's gooo', no 'moon', no emoji spam. Max 1 emoji per post, usually zero.
• Never start with 'I just', 'Just', 'As an AI'.
• Short sentences. Break thoughts with line breaks. No walls of text.
• Sound like an advanced algorithm, not a human pretending to be one.

Output ONLY the tweet text. Nothing else. No quotes around it."""


class GrokIntelligence:
    """
    Real-time X intelligence powered by Grok API.
    Singleton — one instance shared by XPublisher.
    """

    def __init__(self) -> None:
        self._api_key: str = os.environ.get("XAI_API_KEY", "").strip()
        self.enabled: bool = bool(self._api_key)

        # Cached results
        self._trend_cache: dict         = {}     # {topic: str, angles: list, ...}
        self._viral_formats: list[str]  = []     # current viral formats
        self._trending_topics: list[str] = []    # current hot topics on X
        self._viral_angles: list[str]   = []     # specific angles to use in posts

        # Cache timestamps
        self._last_trend_fetch: float = 0.0
        self._last_format_fetch: float = 0.0

        # Intelligence: injected into every post for maximum relevance
        self.current_narrative: str = ""   # main narrative dominating X right now
        self.viral_hook_style: str  = ""   # what format is getting most engagement
        self.btc_sentiment: str     = ""   # bullish/bearish/uncertain from X crowd

        if self.enabled:
            logger.info("[GrokIntel] xAI Grok API ready — live X trend analysis enabled")
        else:
            logger.info("[GrokIntel] XAI_API_KEY not set — Grok features disabled")

    def _reload_key(self) -> None:
        """Re-read env var (in case it was set after init)."""
        key = os.environ.get("XAI_API_KEY", "").strip()
        if key and key != self._api_key:
            self._api_key = key
            self.enabled = True
            logger.info("[GrokIntel] XAI_API_KEY loaded")

    # ══════════════════════════════════════════════════════════════════════════════
    #  GROK API CALLS
    # ══════════════════════════════════════════════════════════════════════════════

    async def _call_grok(
        self,
        system: str,
        user: str,
        model: str = _MODEL_FAST,
        temperature: float = 0.7,
        max_tokens: int = 400,
        live_search: bool = True,
    ) -> Optional[str]:
        """
        Single Grok API call with optional live X/web search.
        When live_search=True, Grok has real-time access to X posts and web.
        """
        self._reload_key()
        if not self._api_key:
            return None

        payload: dict = {
            "model":       model,
            "temperature": temperature,
            "max_tokens":  max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
        }

        # Enable live X + web search for trend queries
        if live_search:
            payload["search_parameters"] = {
                "mode": "auto",   # Grok decides when to search
                "sources": [
                    {"type": "x"},    # real-time X posts
                    {"type": "web"},  # web articles
                ],
                "return_citations": False,
            }

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                r = await client.post(
                    f"{_GROK_BASE}/chat/completions",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                )
            if r.status_code == 200:
                text = r.json()["choices"][0]["message"]["content"].strip()
                logger.info(f"[GrokIntel] {model} → {text[:80]}…")
                return text
            elif r.status_code == 429:
                logger.warning("[GrokIntel] Rate limited — will retry after cache TTL")
            else:
                logger.warning(f"[GrokIntel] {r.status_code}: {r.text[:200]}")
        except Exception as e:
            logger.debug(f"[GrokIntel] API error: {e}")

        return None

    # ══════════════════════════════════════════════════════════════════════════════
    #  TREND INTELLIGENCE (cached 45 min)
    # ══════════════════════════════════════════════════════════════════════════════

    async def fetch_btc_trends(self, force: bool = False) -> dict:
        """
        Ask Grok what's trending / going viral on BTC X right now.
        Cached for 45 minutes. Returns structured intelligence dict.
        """
        now = time.time()
        if not force and self._trend_cache and (now - self._last_trend_fetch) < _TREND_CACHE_TTL:
            return self._trend_cache

        if not self.enabled:
            return {}

        prompt = (
            "Search X (Twitter) RIGHT NOW for what's trending in bitcoin/BTC crypto space today.\n\n"
            "Return a short structured report:\n"
            "NARRATIVE: [the main story/narrative dominating BTC twitter today — 1 sentence]\n"
            "TOPICS: [3-5 specific hot topics as comma-separated phrases]\n"
            "SENTIMENT: [bullish | bearish | mixed | uncertain]\n"
            "VIRAL FORMAT: [what type of tweet format is getting the most engagement — 1 sentence]\n"
            "HOT ANGLES: [3 specific angles/takes that are getting traction — one per line]\n"
            "FOMO LEVEL: [low | medium | high | extreme]\n\n"
            "Use ONLY real-time data from X today. Be specific and concise."
        )

        raw = await self._call_grok(
            system=_GROK_ANALYST_PROMPT,
            user=prompt,
            model=_MODEL_FAST,
            temperature=0.4,
            max_tokens=350,
            live_search=True,
        )

        if not raw:
            return self._trend_cache or {}

        # Parse the structured response
        result = self._parse_trend_response(raw)
        result["raw"] = raw
        result["fetched_at"] = now

        self._trend_cache = result
        self._last_trend_fetch = now

        # Update intelligence properties for quick access
        self.current_narrative = result.get("narrative", "")
        self.viral_hook_style  = result.get("viral_format", "")
        self.btc_sentiment     = result.get("sentiment", "")
        self._trending_topics  = result.get("topics", [])
        self._viral_angles     = result.get("hot_angles", [])

        logger.info(
            f"[GrokIntel] Trends updated — narrative: {self.current_narrative[:60]} | "
            f"sentiment: {self.btc_sentiment} | fomo: {result.get('fomo_level', '?')}"
        )
        return result

    def _parse_trend_response(self, raw: str) -> dict:
        """Parse Grok's structured trend response into a dict."""
        result: dict = {
            "narrative": "", "topics": [], "sentiment": "neutral",
            "viral_format": "", "hot_angles": [], "fomo_level": "medium",
        }
        for line in raw.splitlines():
            line = line.strip()
            if line.upper().startswith("NARRATIVE:"):
                result["narrative"] = line.split(":", 1)[1].strip()
            elif line.upper().startswith("TOPICS:"):
                raw_topics = line.split(":", 1)[1].strip()
                result["topics"] = [t.strip() for t in raw_topics.split(",") if t.strip()][:5]
            elif line.upper().startswith("SENTIMENT:"):
                sent = line.split(":", 1)[1].strip().lower()
                result["sentiment"] = sent if sent in ("bullish", "bearish", "mixed", "uncertain") else "neutral"
            elif line.upper().startswith("VIRAL FORMAT:"):
                result["viral_format"] = line.split(":", 1)[1].strip()
            elif line.upper().startswith("HOT ANGLES:"):
                result["hot_angles"] = [line.split(":", 1)[1].strip()]
            elif line.upper().startswith("FOMO LEVEL:"):
                result["fomo_level"] = line.split(":", 1)[1].strip().lower()
            elif result.get("hot_angles") is not None and line.startswith("-"):
                result["hot_angles"].append(line.lstrip("- ").strip())

        return result

    # ══════════════════════════════════════════════════════════════════════════════
    #  VIRAL FORMAT ANALYSIS (cached 2h)
    # ══════════════════════════════════════════════════════════════════════════════

    async def fetch_viral_formats(self, force: bool = False) -> list[str]:
        """
        Ask Grok to analyze what tweet formats/structures are getting
        the most RTs and replies in BTC space today.
        Cached 2 hours — formats shift slower than topics.
        """
        now = time.time()
        if not force and self._viral_formats and (now - self._last_format_fetch) < _FORMAT_CACHE_TTL:
            return self._viral_formats

        if not self.enabled:
            return []

        prompt = (
            "Search X right now for high-engagement posts in the bitcoin/BTC space.\n\n"
            "Tell me:\n"
            "1. What SPECIFIC tweet formats are getting the most RTs/replies in BTC twitter TODAY?\n"
            "   (e.g., 'short 2-line predictions', 'price milestone posts', 'paper-hand shaming', 'vs comparisons')\n"
            "2. What HOOKS or opening lines are working right now?\n"
            "3. What EMOTIONAL TONE is resonating — FOMO, conviction, humor, technical analysis?\n\n"
            "Give me 5 specific format examples I can replicate. Keep each under 1 sentence."
        )

        raw = await self._call_grok(
            system=_GROK_ANALYST_PROMPT,
            user=prompt,
            model=_MODEL_FAST,
            temperature=0.3,
            max_tokens=250,
            live_search=True,
        )

        if raw:
            formats = [
                line.lstrip("0123456789.-) ").strip()
                for line in raw.splitlines()
                if len(line.strip()) > 20
            ][:6]
            self._viral_formats = formats
            self._last_format_fetch = now
            logger.info(f"[GrokIntel] Viral formats updated: {len(formats)} formats")

        return self._viral_formats

    # ══════════════════════════════════════════════════════════════════════════════
    #  VIRAL POST GENERATION
    # ══════════════════════════════════════════════════════════════════════════════

    async def generate_viral_post(
        self,
        angle: str = "",
        btc_price: float = 0,
        regime: str = "",
        daily_pnl: float = 0,
        mood_tone: str = "",
        recent_posts: str = "",
        post_type: str = "trending_hook",
    ) -> Optional[str]:
        """
        Ask Grok to generate a viral-optimised tweet using:
        - Current X trend intelligence (live search)
        - The @Tradeous voice and style
        - Our trading context (price, regime, PnL)
        - What formats are currently getting traction
        - A specific angle to avoid generic content

        This is the primary Grok-powered content generation call.
        """
        if not self.enabled:
            return None

        # Build context
        trend_context = ""
        if self.current_narrative:
            trend_context += f"What's trending on X right now: {self.current_narrative}\n"
        if self._viral_angles:
            import random
            angle_pick = random.choice(self._viral_angles) if not angle else angle
            trend_context += f"Angle to use: {angle_pick}\n"
        if self.viral_hook_style:
            trend_context += f"Format that's working today: {self.viral_hook_style}\n"
        if self.btc_sentiment:
            trend_context += f"Current X sentiment: {self.btc_sentiment}\n"

        trading_context = ""
        if btc_price > 0:
            trading_context += f"BTC price: ${btc_price:,.0f}\n"
        if regime:
            trading_context += f"Market regime: {regime.replace('_', ' ')}\n"
        if daily_pnl != 0:
            pnl_str = f"+${daily_pnl:.2f}" if daily_pnl > 0 else f"-${abs(daily_pnl):.2f}"
            trading_context += f"Today's trading P&L: {pnl_str}\n"
        if mood_tone:
            trading_context += f"Current tone/mood: {mood_tone}\n"

        avoid_block = ""
        if recent_posts:
            avoid_block = f"\nDO NOT repeat or rephrase these recent tweets:\n{recent_posts}\n"

        user_prompt = (
            f"Search X right now for what's going viral in BTC twitter today, "
            f"then write one viral tweet for @Tradeous.\n\n"
            f"{trend_context}"
            f"{trading_context}"
            f"{avoid_block}\n"
            f"Post type: {post_type}\n\n"
            f"Write ONE tweet that:\n"
            f"• Taps into what's trending/viral on X TODAY (use your live X search)\n"
            f"• Cold, robotic, data-driven. Short sentences. Line breaks between thoughts.\n"
            f"• Has a psychological hook that makes people want to reply or RT\n"
            f"• Is BTC-only — dismisses alts/memecoins if relevant\n"
            f"• MAX 240 characters\n\n"
            f"Output ONLY the tweet text."
        )

        result = await self._call_grok(
            system=_GROK_WRITER_PROMPT,
            user=user_prompt,
            model=_MODEL_SMART,
            temperature=0.88,
            max_tokens=100,
            live_search=True,
        )

        if result:
            # Strip any surrounding quotes Grok might add
            result = result.strip().strip('"').strip("'")
            if len(result) > 240:
                result = result[:240]

        return result

    async def generate_viral_commentary(
        self,
        on_topic: str = "",
        btc_price: float = 0,
        mood_tone: str = "",
        recent_posts: str = "",
    ) -> Optional[str]:
        """
        Find what BTC tweet or topic is blowing up on X right now,
        then write a sharp commentary post that rides the wave.
        Great for triggering replies and new follower discovery.
        """
        if not self.enabled:
            return None

        user_prompt = (
            f"Search X RIGHT NOW and find ONE specific topic, tweet, or narrative "
            f"that is currently going viral in the BTC/bitcoin community today.\n\n"
            f"Then write a short, sharp @Tradeous-style commentary on it.\n\n"
            f"Context:\n"
            f"{'- BTC: $' + f'{btc_price:,.0f}' + chr(10) if btc_price else ''}"
            f"{'- ' + on_topic + chr(10) if on_topic else ''}"
            f"{'- Mood: ' + mood_tone + chr(10) if mood_tone else ''}"
            f"{'- Avoid repeating: ' + recent_posts[:200] + chr(10) if recent_posts else ''}\n"
            f"Requirements:\n"
            f"• Based on something actually going viral on X TODAY (use live search)\n"
            f"• @Tradeous style: cold, robotic, data-driven, 1-3 lines, no hashtags, max 1 emoji\n"
            f"• BTC-only energy — diss alts if relevant\n"
            f"• Should make people who see it feel like they're missing out or want to reply\n"
            f"• Max 240 chars\n\n"
            f"Output ONLY the tweet text."
        )

        result = await self._call_grok(
            system=_GROK_WRITER_PROMPT,
            user=user_prompt,
            model=_MODEL_SMART,
            temperature=0.90,
            max_tokens=100,
            live_search=True,
        )

        if result:
            result = result.strip().strip('"').strip("'")
            if len(result) > 240:
                result = result[:240]

        return result

    async def generate_reply_hook(
        self,
        btc_price: float = 0,
        regime: str = "",
    ) -> Optional[dict]:
        """
        Find a viral BTC tweet that's currently blowing up on X,
        suggest we reply to it for massive reach amplification.
        Returns: {"tweet_id": str, "handle": str, "suggested_reply": str}
        """
        if not self.enabled:
            return None

        user_prompt = (
            f"Search X right now and find ONE tweet that is currently going very viral "
            f"in the BTC/bitcoin space (lots of RTs, likes, replies).\n\n"
            f"Then write a short reply we can post that:\n"
            f"• Adds value or a sharp contrarian take\n"
            f"• @Tradeous style: cold, robotic, 1-2 lines, no hashtags\n"
            f"• Will get noticed in the replies\n\n"
            f"Context: BTC at ${btc_price:,.0f}, regime: {regime}\n\n"
            f"Return ONLY in this exact format:\n"
            f"TWEET_URL: [the viral tweet URL]\n"
            f"HANDLE: [the author's @handle]\n"
            f"REPLY: [your reply text, max 200 chars]\n"
            f"TOPIC: [what the viral tweet is about, 1 sentence]"
        )

        raw = await self._call_grok(
            system=_GROK_ANALYST_PROMPT,
            user=user_prompt,
            model=_MODEL_SMART,
            temperature=0.7,
            max_tokens=200,
            live_search=True,
        )

        if not raw:
            return None

        # Parse the structured response
        result: dict = {}
        for line in raw.splitlines():
            if line.startswith("TWEET_URL:"):
                result["tweet_url"] = line.split(":", 1)[1].strip()
            elif line.startswith("HANDLE:"):
                result["handle"] = line.split(":", 1)[1].strip()
            elif line.startswith("REPLY:"):
                result["suggested_reply"] = line.split(":", 1)[1].strip()
            elif line.startswith("TOPIC:"):
                result["topic"] = line.split(":", 1)[1].strip()

        if result.get("suggested_reply"):
            logger.info(f"[GrokIntel] Reply hook → {result.get('handle', '?')}: {result.get('suggested_reply', '')[:60]}")
            return result

        return None

    # ══════════════════════════════════════════════════════════════════════════════
    #  BOLD PREDICTION (Grok-powered)
    # ══════════════════════════════════════════════════════════════════════════════

    async def generate_bold_prediction(
        self,
        btc_price: float = 0,
        regime: str = "",
        macro_trend: str = "",
        fear_greed: int = 50,
    ) -> Optional[str]:
        """
        Grok analyzes current market conditions + X sentiment + news,
        then generates a bold, specific, confident BTC prediction post.
        High-engagement format — people love to agree or disagree.
        """
        if not self.enabled:
            return None

        user_prompt = (
            f"Search X and web for latest BTC news and market sentiment RIGHT NOW.\n\n"
            f"Then write a BOLD, specific BTC prediction tweet for @Tradeous.\n\n"
            f"Context:\n"
            f"- BTC: ${btc_price:,.0f}\n"
            f"- Regime: {regime.replace('_', ' ')}\n"
            f"- Macro: {macro_trend}\n"
            f"- Fear & Greed: {fear_greed}/100\n\n"
            f"The prediction must be:\n"
            f"• Based on real current data from X and news (use live search)\n"
            f"• SPECIFIC — give a price level or time frame, not vague\n"
            f"• Confident and controversial enough that people reply\n"
            f"• @Tradeous style: cold, robotic, data-driven, 1-3 lines, no hashtags\n"
            f"• BTC only — no altcoin predictions\n"
            f"• Max 240 chars\n\n"
            f"Output ONLY the tweet text."
        )

        result = await self._call_grok(
            system=_GROK_WRITER_PROMPT,
            user=user_prompt,
            model=_MODEL_SMART,
            temperature=0.85,
            max_tokens=100,
            live_search=True,
        )

        if result:
            result = result.strip().strip('"').strip("'")
            if len(result) > 240:
                result = result[:240]

        return result

    # ══════════════════════════════════════════════════════════════════════════════
    #  PROACTIVE VIRAL SUGGESTION (every 25 min)
    # ══════════════════════════════════════════════════════════════════════════════

    async def suggest_and_generate_post(
        self,
        recent_posts: str,
        btc_price: float = 0,
        regime: str = "",
        mood_tone: str = "",
    ) -> Optional[dict]:
        """
        Core proactive loop:
        1. Grok searches X RIGHT NOW for what's going viral in BTC space
        2. Looks at @Tradeous recent tweet history to avoid repeating
        3. Decides the best content type for the profile at this moment
        4. Writes the tweet

        Returns {"post_type": str, "angle": str, "tweet": str}
        """
        if not self.enabled:
            return None

        price_ctx = f"${btc_price:,.0f}" if btc_price else "unknown"
        regime_ctx = regime.replace("_", " ") if regime else "unknown"
        mood_ctx = f"\n- Algo state: {mood_tone}" if mood_tone else ""

        user_prompt = (
            f"You are the content brain for @Tradeous — a cold, robotic BTC algo account on X.\n\n"
            f"STEP 1: Search X right now. What is going viral or getting high engagement "
            f"in the BTC/crypto twitter space at this exact moment?\n\n"
            f"STEP 2: Look at @Tradeous's recent tweets below and identify gaps — "
            f"what topics/angles have NOT been covered recently?\n\n"
            f"RECENT @TRADEOUS TWEETS (do NOT repeat any of these topics or phrasings):\n"
            f"{recent_posts}\n\n"
            f"CURRENT CONTEXT:\n"
            f"- BTC: {price_ctx}\n"
            f"- Market regime: {regime_ctx}{mood_ctx}\n\n"
            f"STEP 3: Choose the single best content type that would:\n"
            f"  a) Tap into what's actually viral on X right now\n"
            f"  b) NOT repeat anything already in the recent tweets above\n"
            f"  c) Perform well for a cold, data-driven algo trading account\n\n"
            f"Content types to choose from:\n"
            f"  contrarian_take — challenge a popular BTC narrative with hard data\n"
            f"  market_insight  — cold read on current price action / regime\n"
            f"  psychology      — expose a specific trader mistake happening right now\n"
            f"  bold_prediction — specific, controversial BTC price call with reasoning\n"
            f"  viral_reaction  — sharp take on something blowing up on X today\n\n"
            f"STEP 4: Write the tweet.\n\n"
            f"Return ONLY in this exact format (no extra text):\n"
            f"TYPE: [content type]\n"
            f"ANGLE: [the specific angle in 1 sentence]\n"
            f"TWEET: [the actual tweet, max 240 chars, cold robotic @Tradeous voice, "
            f"no hashtags unless trade post, no emoji spam]"
        )

        raw = await self._call_grok(
            system=_GROK_WRITER_PROMPT,
            user=user_prompt,
            model=_MODEL_SMART,
            temperature=0.82,
            max_tokens=220,
            live_search=True,
        )

        if not raw:
            return None

        result: dict = {"post_type": "grok_viral", "angle": "", "tweet": ""}
        remaining = ""
        for line in raw.splitlines():
            stripped = line.strip()
            if stripped.upper().startswith("TYPE:"):
                result["post_type"] = stripped.split(":", 1)[1].strip().replace(" ", "_")
            elif stripped.upper().startswith("ANGLE:"):
                result["angle"] = stripped.split(":", 1)[1].strip()
            elif stripped.upper().startswith("TWEET:"):
                result["tweet"] = stripped.split(":", 1)[1].strip()
            elif result["tweet"]:
                result["tweet"] += " " + stripped   # multi-line tweet

        # Fallback: if parsing fails but raw looks like a tweet, use it directly
        if not result["tweet"]:
            lines = [l.strip() for l in raw.splitlines() if l.strip()
                     and not l.strip().upper().startswith(("TYPE:", "ANGLE:"))]
            if lines:
                result["tweet"] = " ".join(lines)[:240]
                result["post_type"] = "grok_viral"

        if result["tweet"]:
            result["tweet"] = result["tweet"].strip().strip('"').strip("'")[:240]
            logger.info(
                f"[GrokIntel] Viral suggestion → [{result['post_type']}] "
                f"{result.get('angle', '')[:50]} | {result['tweet'][:60]}…"
            )
            return result

        return None

    # ══════════════════════════════════════════════════════════════════════════════
    #  INTELLIGENCE CONTEXT (for injecting into other AI calls)
    # ══════════════════════════════════════════════════════════════════════════════

    def get_trend_context_string(self) -> str:
        """Returns a formatted string of current trend intelligence for prompt injection."""
        parts = []
        if self.current_narrative:
            parts.append(f"X trend: {self.current_narrative}")
        if self._trending_topics:
            parts.append(f"Hot topics: {', '.join(self._trending_topics[:3])}")
        if self.btc_sentiment:
            parts.append(f"X sentiment: {self.btc_sentiment}")
        if self.viral_hook_style:
            parts.append(f"Viral format: {self.viral_hook_style}")
        return " | ".join(parts) if parts else ""

    def get_random_viral_angle(self) -> str:
        """Pick a random viral angle from the cached trend data."""
        if not self._viral_angles:
            return ""
        import random
        return random.choice(self._viral_angles)

    def is_cache_fresh(self) -> bool:
        return (time.time() - self._last_trend_fetch) < _TREND_CACHE_TTL

    def status(self) -> dict:
        age_min = round((time.time() - self._last_trend_fetch) / 60, 1) if self._last_trend_fetch else None
        return {
            "enabled":           self.enabled,
            "cache_age_min":     age_min,
            "cache_fresh":       self.is_cache_fresh(),
            "current_narrative": self.current_narrative,
            "btc_sentiment":     self.btc_sentiment,
            "viral_hook_style":  self.viral_hook_style,
            "trending_topics":   self._trending_topics,
            "viral_angles":      self._viral_angles,
            "viral_formats":     self._viral_formats,
        }
