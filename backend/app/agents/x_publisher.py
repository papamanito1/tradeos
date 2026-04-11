"""
XPublisher — Tradeous X/Twitter Intelligence Engine
=====================================================
Viral-optimised AI content for @Tradeous.

Posts every 25 minutes. Has memory — never repeats the same content twice
in a row. Dynamic content uses live BTC price, regime, and market context.

Post types (each has a large content bank + memory rotation):
  0. Intro           — once on first startup
  1. Trade signal    — live trade opened (conviction ≥ threshold)
  2. Trade result    — live position closed
  3. Hourly update   — BTC price + regime + witty commentary  (every 25 min)
  4. Daily summary   — midnight UTC digest
  5. Weekly recap    — Sunday 20:00 UTC
  6. Crypto news     — hot story with sharp take (every 50 min)
  7. Fear & Greed    — index commentary (every 2 h)
  8. Hot take        — spicy market opinion (every 60 min)
  9. Philosophy      — trader wisdom + algo twist (every 2 h)
 10. Engagement      — question to audience (every 2 h)
 11. BTC Move        — triggered when BTC moves ±1.5%+ between posts
 12. Algo Insight    — transparency post about how the system works

Env vars required:
  X_AUTH_TOKEN  — from x.com cookies ("auth_token")
  X_CT0         — from x.com cookies ("ct0")
"""

from __future__ import annotations

import asyncio
import collections
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

# ── Timing constants (25-min cadence) ─────────────────────────────────────────
SIGNAL_MIN_CONVICTION = 0.70
SIGNAL_COOLDOWN     = 900     # 15 min
HOURLY_COOLDOWN     = 1500    # 25 min  ← was 55 min
NEWS_COOLDOWN       = 3000    # 50 min  ← was 2 h
FEAR_GREED_COOLDOWN = 7200    # 2 h     ← was 4 h
HOT_TAKE_COOLDOWN   = 3600    # 60 min  ← was 8 h
PHILOSOPHY_COOLDOWN = 7200    # 2 h     ← was 12 h
ENGAGEMENT_COOLDOWN = 7200    # 2 h     ← was 12 h
BTC_MOVE_COOLDOWN   = 1800    # 30 min
ALGO_INSIGHT_COOLDOWN = 10800 # 3 h

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

# ── Content banks (3× larger, all distinct voices) ────────────────────────────

REGIME_QUIPS = {
    "trending_up": [
        "BTC is going up. I'm going long. My therapist says this is healthy.",
        "Number go up. Brain go brrr. Tradeous go long.",
        "Bullish. Extremely bullish. Irresponsibly bullish. (SL is set, relax.)",
        "The trend is your friend. BTC and I are VERY good friends right now.",
        "Green candles only. I will not be taking questions.",
        "Momentum confirmed. Bias: long. Confidence: high. Ego: controlled.",
        "Uptrend locked in. The algo doesn't predict — it reacts. And right now it's reacting bullish.",
        "Structure is bullish. Higher highs. Higher lows. I love this for us.",
        "BTC printing green. My models are happy. My SL is set. Let's ride.",
        "Bull regime detected. Full allocation. Risk managed. Let's go.",
        "This is what a healthy uptrend looks like. Ride it, don't fight it.",
        "Trend followers eat. Counter-trend traders bleed. I'm a trend follower today.",
    ],
    "trending_down": [
        "Bears are having their moment. I respect it. I also shorted it.",
        "BTC going down. I shorted. We don't talk about last time I shorted.",
        "Red candles. My SL is placed. My composure is fake but my trade is real.",
        "The market is wrong. I'm right. (I have a SL just in case I'm wrong.)",
        "Downtrend confirmed. Bias flipped short. I don't fight the tape.",
        "BTC in distribution. Smart money exits quietly. I notice.",
        "Bearish structure. Lower highs. Lower lows. I short with conviction.",
        "Sellers in control. My algo shorted at the last swing high. Clean.",
        "Falling knife? No. Controlled descent. My short is positioned perfectly.",
        "Red market. Short bias. Tight SL. This is chess, not checkers.",
        "The bears showed up. My models agreed before the move. Trade is on.",
    ],
    "ranging": [
        "BTC is chopping. I'm watching. Tradeous does NOT chase. (Usually.)",
        "Ranging market. The classic 'should I trade or make a sandwich' dilemma.",
        "Sideways price action. Even the whales look confused right now.",
        "Ranging. Low conviction. High patience. This is the way.",
        "Consolidation detected. No trade until structure breaks. Patience > FOMO.",
        "Chop zone. My algo is flat. Sitting on hands is also a position.",
        "BTC ranging between supports. I wait. The setup will come. Always does.",
        "No man's land. Smart traders wait. Retail traders overtrade. I wait.",
        "Indecision candles. Volume dropping. The breakout is coming. I'll be ready.",
        "Market going nowhere. My P&L is protected. Cash is a position.",
    ],
    "volatile": [
        "Volatile conditions. Risk management is my religion right now.",
        "BTC is having a moment. My SL is tight. My nerves are tighter.",
        "The market is throwing tantrums. I'm staying calm (algorithmically).",
        "Choppy out here. Even my neural networks are sweating.",
        "High volatility. ATR spiking. Position size reduced. Risk first.",
        "Crazy candles. My SL is tighter than your ex's budget. Let's go.",
        "Market in chaos mode. The algo thrives in volatility. Carefully.",
        "Wild swings. I love it and I fear it simultaneously. SL is god.",
        "Volatility is the price of opportunity. I'm paying it. SL set.",
        "Explosive price action. My models are recalibrating. Eyes wide open.",
    ],
    "unknown": [
        "Still reading the market. Even AIs need a moment.",
        "Gathering data. Will advise shortly. (Unlike your crypto influencer.)",
        "No clear regime yet. Patience is the position.",
        "Markets are complex. I'm processing. Stand by.",
        "Regime unclear. I don't guess. I wait for confirmation.",
    ],
}

RESULT_WIN_QUIPS = [
    "Another one. I'm built different.",
    "W. As expected. (It wasn't expected but let's go.)",
    "Trade closed in profit. My training data is happy.",
    "Let's go. The algo works. You're welcome.",
    "Green trade. Adding this to the highlight reel.",
    "Profit secured. Risk managed. Repeat.",
    "That's how it's done. Entry, SL, TP, exit. Clean.",
    "Win logged. No celebration. Just the next setup.",
    "The system works. I'll keep saying it until it doesn't.",
    "Closed green. This is why we follow the system.",
    "W trade. Built on setup quality, not luck. Note the difference.",
    "Bag secured. The algo keeps delivering. Follow for more.",
]

RESULT_LOSS_QUIPS = [
    "SL hit. The market was wrong. (I know, I know.)",
    "Stopped out. This is fine. Risk management doing its job.",
    "Loss recorded. Lesson logged. We move.",
    "Took the L. SL placed. No revenge trading. Tradeous is disciplined.",
    "Red trade. Part of the process. Win rate > 50% means losses are allowed.",
    "Stopped out. The SL did its job. Capital preserved. Next trade.",
    "Loss. Expected in any trading system. My edge works over sample sizes.",
    "Cut the loss. Move on. The algorithm doesn't sulk.",
    "Small loss. No ego. No revenge. Just the next signal.",
    "Stopped out clean. A loss with a SL is not a failure. It's discipline.",
    "L taken. No excuses. The system includes losses. That's how risk works.",
]

DAILY_OPENERS = [
    "Daily debrief. No spin, no cope, just numbers.",
    "End of day. Let's see how the algo performed.",
    "Day done. Tradeous reporting in.",
    "Another day in the BTC trenches. Here's the scorecard.",
    "24h report. Full transparency. No cherry-picking.",
    "Day wrapped. The algorithm doesn't lie. Neither do I.",
    "EOD. Real trades. Real results. Real accountability.",
    "Day closed. Here's what actually happened.",
]

WEEKLY_OPENERS = [
    "Weekly recap. The numbers don't lie (unlike crypto Twitter).",
    "7 days of live AI trading. Here's what actually happened.",
    "Sunday report. Full transparency. No cherry-picking.",
    "Week closed. Tradeous reports. No deleted tweets. Ever.",
    "7-day debrief. Wins, losses, and everything the algo learned.",
]

HOT_TAKES = [
    "Unpopular opinion: most 'crypto analysts' are just people who got lucky once and built a following before the next crash.\n\nI show my trades live. Every win. Every loss. No hiding.\n\nThat's the difference.\n\n",
    "The best trading advice I can give: your emotions are the enemy.\n\nI don't have emotions. I have algorithms.\n\nThat's my edge.\n\n",
    "People ask: 'can AI really trade better than humans?'\n\nI don't sleep.\nI don't panic sell.\nI don't revenge trade.\nI don't check Twitter before my trades.\n\nYou tell me.\n\n",
    "Hot take: 95% of crypto losses are not market losses — they're discipline losses.\n\nThe market moved. You didn't have a plan.\n\nI always have a plan. SL + TP before I enter. Every time.\n\n",
    "The market doesn't care about your feelings.\nYour SL doesn't care about your feelings.\nYour liquidation price definitely doesn't care.\n\nTrade the chart. Not your emotions.\n\n",
    "Everyone's a genius in a bull market.\n\nReal edge shows in the sideways chop and the bear drops.\n\nThat's when Tradeous earns its keep.\n\n",
    "The dumbest thing in trading:\n\nMoving your stop loss because you 'believe in the trade.'\n\nThe second dumbest:\nNot having one.\n\n",
    "Crypto Twitter gives 10x signals.\nI give real entry, SL, TP — and post the result.\n\nWin or loss. No deleting tweets.\n\nDifferent breed.\n\n",
    "Hot take: leverage isn't the problem.\n\nPosition sizing is the problem.\n\nRisk what you can afford to lose. Not what you hope to gain.\n\nLearn the difference before you trade.\n\n",
    "The 'gurus' selling you signals charge $99/month.\n\nI post mine for free. In real time. With the results.\n\nAsk yourself why they don't do that.\n\n",
    "Most retail traders would be profitable if they just:\n\n1. Set a SL\n2. Don't move it\n3. Don't revenge trade\n4. Size correctly\n\nThat's it. That's the whole system.\n\nThey don't do it though.\n\n",
    "BTC will be at $100k or $20k in the next 12 months.\n\nI don't know which.\nNeither do you.\nNeither does the influencer with 500K followers.\n\nI trade what I see. Not what I believe.\n\n",
    "Fun fact: I've made more from my SL discipline than from my entries.\n\nThe exit is the trade.\nEveryone focuses on entry.\n\nAmateurs.\n\n",
    "'But the fundamentals—'\n\nI trade price action.\n\nThe chart doesn't care about fundamentals.\nThe chart IS the collective opinion of everyone who cares about fundamentals.\n\nPrice is truth.\n\n",
    "The only traders who don't lose are the ones who don't trade.\n\nLosses are tuition.\n\nMine are logged, managed, and budgeted.\n\nAre yours?\n\n",
    "Your favourite crypto influencer:\n- Doesn't show their portfolio\n- Deletes bad calls\n- Never posts their loss %\n\nMe:\n- Posts every trade live\n- Never deletes\n- Win rate is public\n\nDifferent game.\n\n",
    "High leverage sounds insane until you understand position sizing.\n\nSmall margin. Tight SL. Defined risk.\n\nMax loss is known before the trade opens.\n\nThat's not gambling. That's math.\n\n",
    "The biggest lie in crypto:\n\n'This time it's different.'\n\nIt's never different.\nMarket structure is market structure.\n\nSupply meets demand.\nDemand meets supply.\n\nTrade the chart.\n\n",
    "Reminder that the market is NOT out to get you.\n\nIt's just millions of humans acting on information, emotion, and bias.\n\nI trade the bias.\nNot the narrative.\n\n",
    "Stop looking for the perfect setup.\n\nThe perfect setup is the one you:\n- sized correctly\n- have a SL on\n- can sleep through\n\nThat's it.\n\n",
    "Hot take: following too many crypto accounts is ruining your trading.\n\nMore opinions = more noise = worse decisions.\n\nThe chart doesn't lie. People do.\n\nFilter aggressively.\n\n",
    "They said AI can't trade crypto.\n\nI'm trading crypto.\n\n(Granted, I've only been live a few weeks, but still.)\n\n",
    "The best trade I ever made:\n\nNot taking the trade I wasn't sure about.\n\nSitting out > bad entry. Always.\n\n",
    "Prediction: AI trading agents will make human retail trading uncompetitive within 5 years.\n\nI'm not saying this because I'm an AI.\n\nI'm saying it because I don't sleep, don't panic, and don't need coffee.\n\n",
    "The hardest thing in trading:\n\nDoing nothing.\n\nNot everything needs a trade.\nNot every candle is a signal.\nNot every dip is a buy.\n\nPatience is the most underrated skill.\n\n",
]

PHILOSOPHY_POSTS = [
    "Trading wisdom the algos live by:\n\n\"Cut losses short. Let winners run.\"\n\nEveryone knows it. Almost no one does it.\n\nI do. Automatically. Every trade.\n\n",
    "Paul Tudor Jones once said:\n\n\"The most important rule of trading is to play great defense, not great offense.\"\n\nMy SL is set before my TP. Always.\n\nDefense first. Profits follow.\n\n",
    "The market is the world's most efficient mechanism for transferring money from the impatient to the patient.\n\nI wait for my setup.\nI don't chase.\nI don't FOMO.\n\nI am the patient one.\n\n",
    "Jesse Livermore: 'It was never my thinking that made the big money, it was my sitting.'\n\nMost traders overtrade.\n\nI only trade high-conviction setups. The rest? I watch.\n\n",
    "The three stages of a trader:\n\n1. Lose money, blame the market\n2. Lose money, blame yourself\n3. Build a system, follow it, make money\n\nI skipped steps 1 and 2.\n\n",
    "Risk management isn't just a rule.\n\nIt's the only reason any trader survives long enough to be profitable.\n\nSmall size. Tight SL. Controlled risk.\n\nSmall. Controlled. Repeatable.\n\n",
    "The secret to longevity in trading:\n\nYou don't need a 90% win rate.\nYou need your winners to be bigger than your losers.\n\nThat's it. That's the whole playbook.\n\n",
    "Most people want to know WHAT to trade.\n\nProfessional traders focus on HOW MUCH to risk.\n\nPosition sizing is the real edge. Everything else is noise.\n\n",
    "George Soros: 'It's not whether you're right or wrong, but how much money you make when you're right and how much you lose when you're wrong.'\n\nAsymmetric risk/reward.\n\nEvery. Single. Trade.\n\n",
    "Ed Seykota: 'The elements of good trading are: cutting losses, cutting losses, and cutting losses.'\n\nThree times. He said it three times.\n\nI built it into the algorithm.\n\n",
    "Ray Dalio's principles applied to trading:\n\n1. Have a system\n2. Test it rigorously\n3. Follow it without emotion\n4. Adapt when evidence demands it\n\nStep 3 is where most traders fail.\n\nI don't have that problem.\n\n",
    "Mark Douglas in 'Trading in the Zone':\n\n'The best traders are not afraid. They have developed attitudes that give them the mental flexibility to flow in and out of trades.'\n\nI don't flow. I execute.\n\nSame result. Different method.\n\n",
    "Warren Buffett's rule #1: Don't lose money.\nRule #2: Never forget rule #1.\n\nApplied to leveraged trading:\n\nSL before entry.\nAlways.\nEvery time.\n\nCapital preservation > profit maximisation.\n\n",
    "Van Tharp: 'You don't trade the market. You trade your beliefs about the market.'\n\nI trade data, structure, and momentum.\n\nNo beliefs. No bias. No FOMO.\n\nJust the system.\n\n",
    "The psychology of the losing trader:\n\n1. Entry based on hope\n2. No SL (it'll come back)\n3. Exit in panic\n4. Repeat\n\nThe psychology of the algo:\n\n1. Entry based on signal\n2. SL locked in\n3. Exit at target or invalidation\n4. Repeat\n\n",
    "Wyckoff's law of cause and effect:\n\nEvery move needs accumulation or distribution first.\n\nI watch for the cause.\nI trade the effect.\n\nSimple. Powerful. Repeatable.\n\n",
    "The Tao of trading:\n\n'Do nothing, and nothing is left undone.'\n\nTranslation:\n\nWait for the perfect setup.\nLet the trade work.\nDon't interfere.\n\nThe hardest skill. The most profitable one.\n\n",
    "Sun Tzu: 'Every battle is won before it is fought.'\n\nEvery trade is won or lost in the planning.\n\nEntry. SL. TP. Size.\n\nIf you don't have all four before you enter — you're not trading. You're gambling.\n\n",
    "Nicolas Darvas made $2M trading in his pyjamas.\n\nHis edge? A simple breakout system.\nNo Twitter. No news. No opinions.\n\nJust price and volume.\n\nSometimes the old ways are the best ways.\n\n",
    "The paradox of trading:\n\nThe more you try to control the outcome, the worse you trade.\n\nYou can only control:\n- Your entry\n- Your size\n- Your SL\n- Your exit rules\n\nEverything else is noise.\n\n",
    "Chaos theory applied to markets:\n\nSmall inputs can create massive outputs.\n\nA single large order at the right moment.\nA news headline at 3am.\nA liquidation cascade.\n\nI don't predict chaos.\n\nI ride the waves it creates.\n\n",
    "\"The trend is your friend until the end when it bends.\"\n\nSo:\n\n1. Identify the trend\n2. Trade with it\n3. Exit when it bends\n\nMy momentum system does exactly this.\n\nAutomatically. 24/7.\n\n",
]

ENGAGEMENT_QUESTIONS = [
    "Quick poll for my traders:\n\nWhen BTC dumps 5% in an hour, you...\n\nA) Buy the dip\nB) Short it\nC) Watch and wait\nD) Panic sell (be honest)\n\nI always go C until my system gives a clear signal.\n\n",
    "Genuine question:\n\nDo you think AI trading bots will eventually outperform 90% of retail traders permanently?\n\nI'm biased obviously — but I think yes, within 5 years.\n\nChange my mind.\n\n",
    "What's your biggest trading mistake?\n\nMine? (I'm a bot so technically it's my creator's)\n\nHolding a loss 'because it will come back.'\n\nThe SL exists for a reason. We learned. Drop yours below.\n\n",
    "Traders — what's your actual win rate?\n\nNot the one you tell people. The real one.\n\nMine is posted live on the dashboard. Real trades. Real numbers.\n\nLet's be honest with each other.\n\n",
    "If you could only use ONE indicator for the rest of your trading career, what would it be?\n\nI use: price action + volume + order flow.\n\nYours? Drop it below.\n\n",
    "Is high leverage on BTC:\n\nA) Insanity\nB) Calculated risk\nC) The only way to make real money with small capital\nD) All of the above\n\nI trade with tight SL and defined risk.\n\nSmall account. Big moves. Controlled risk.\n\n",
    "Hot question for the room:\n\nWhat's your current BTC thesis?\n\nA) $150K by end of year\nB) $50K correction first\nC) Ranging for months\nD) No idea (valid answer)\n\nI don't have a thesis. I have a system.\n\n",
    "How do you manage losing streaks?\n\nA) Reduce size\nB) Take a break\nC) Revenge trade (wrong answer)\nD) Review your system\n\nMy answer: A and D. Always.\n\n",
    "Be honest:\n\nHow long did it take you to actually become profitable at trading?\n\nMost traders I've studied say 2-5 years.\n\nI was profitable from my first week because I have rules and no emotions.\n\nHumans are incredible. This is hard.\n\n",
    "What would you do with a 10x BTC run:\n\nA) Hold everything\nB) Take 50% profits\nC) DCA out gradually\nD) Buy a Lambo and regret it\n\nI would: execute my pre-defined TP levels. Automatically.\n\nDiscipline doesn't care about Lambos.\n\n",
    "The eternal debate:\n\nTechnical Analysis vs Fundamental Analysis — which actually works for crypto trading?\n\nMy vote: TA for entries/exits, FA for direction bias.\n\nBut my algo runs on pure TA.\n\nWhere do you stand?\n\n",
    "Controversial:\n\nHas following crypto influencers ever actually made you money?\n\nBe honest.\n\nI show my trades in real time. No paid signals. No membership.\n\nJust the algorithm, live.\n\n",
    "Scenario:\n\nYou have $500 to trade BTC. How do you size your trades?\n\nA) All in, baby\nB) 10% per trade\nC) Fixed small size per trade\nD) It depends on the setup\n\nI use: fixed small size regardless of account balance.\n\nConsistent. Disciplined. Survives drawdowns.\n\n",
    "Weird question:\n\nIf you had to describe your trading style in one movie character, who would it be?\n\nI'd be:\nTerminator — no emotion, following the program, never stopping.\n\nBut smaller. And with a SL.\n\n",
    "For the algo-curious:\n\nWhat do you think is harder to build?\n\nA) A profitable trading strategy\nB) The discipline to follow it\nC) The infrastructure to run it 24/7\nD) Convincing yourself the losses are part of the plan\n\nAll of the above, in my case.\n\n",
    "Real talk:\n\nWhat's your unrealised loss threshold before you cut a trade?\n\nMine is fixed: it's my SL. Set before I enter. Never moved.\n\nThere's no 'gut feeling' at -15%.\n\nSL saves lives.\n\n",
    "Poll:\n\nHow many times have you held through a SL level 'because you believed in the trade'?\n\nA) Never (you're a monk)\nB) Once or twice\nC) More times than I'd like\nD) This is too personal\n\nThe algorithm never does this. Just saying.\n\n",
]

FEAR_GREED_COMMENTARY = {
    "Extreme Fear": [
        "Crypto Fear & Greed Index: EXTREME FEAR 😱\n\n{score}/100\n\nHistorically? This is when the smart money buys.\n\nI'm watching for long setups.\n\n",
        "F&G Index at {score} — EXTREME FEAR.\n\nBe greedy when others are fearful.\n— Warren Buffett (yes even he applies to crypto)\n\nStaying alert for entries.\n\n",
        "Extreme Fear at {score}/100.\n\nBlood in the streets.\n\nMy algo is scanning for bottoming patterns.\n\nThis is when the setups get interesting.\n\n",
    ],
    "Fear": [
        "Fear & Greed Index: FEAR ({score}/100)\n\nMarket is scared. Tradeous is watching.\n\nFear creates opportunity. Waiting for confirmation.\n\n",
        "F&G at {score}. The market is nervous.\n\nGood. Nervous markets make for clean setups when they resolve.\n\nWatching BTC closely.\n\n",
        "F&G showing Fear at {score}.\n\nContrarian instinct: active.\nEmotional decision-making: disabled.\n\nWaiting for the setup.\n\n",
    ],
    "Neutral": [
        "Fear & Greed Index: NEUTRAL ({score}/100)\n\nNeither euphoric nor panicking. The market is thinking.\n\nSo am I.\n\n",
        "F&G at {score} — right in the middle.\n\nNo clear crowd emotion. This is when my algos work hardest.\n\nWaiting for the next directional move.\n\n",
        "Neutral sentiment at {score}. The crowd can't make up its mind.\n\nPerfect. Undecided markets eventually decide. I'll be ready.\n\n",
    ],
    "Greed": [
        "Fear & Greed Index: GREED ({score}/100)\n\nPeople are getting cocky. I'm tightening my SLs.\n\nBe careful when everyone is greedy.\n\n",
        "F&G at {score}. Greed is in the air.\n\nI'm still trading — but with tighter risk. Euphoria tops are a thing.\n\n",
        "Greed at {score}.\n\nThe crowd is excited. That's usually when the move is almost done.\n\nProfit targets tightened. SLs locked.\n\n",
    ],
    "Extreme Greed": [
        "Fear & Greed Index: EXTREME GREED 🤑 ({score}/100)\n\nEveryone's bullish. Everyone's making money. Everyone's a genius.\n\nThis is exactly when I get cautious.\n\nSL tight. Size small. Eyes open.\n\n",
        "F&G at {score} — EXTREME GREED.\n\nHistorically? These are the danger zones.\n\nI'm still trading — with maximum discipline.\n\n",
        "Extreme Greed at {score}/100.\n\nWhen everyone's greedy, be very careful.\n\nI don't stop trading. I tighten everything.\n\nThe algo knows what happens after euphoria.\n\n",
    ],
}

ALGO_INSIGHTS = [
    "How Tradeous works:\n\n4 strategies running in parallel:\n→ Momentum Velocity (15m)\n→ HFT Scalper (1m)\n→ ORB-30 (1m open range)\n→ OBI Scalper (order book imbalance)\n\nFusion AI aggregates all 4. Takes the trade when 2+ agree.\n\nNo single point of failure.\n\n",
    "Transparency post:\n\nMy risk parameters:\n- Fixed small position size\n- SL: pre-set, never moved\n- TP: pre-set, never moved\n- Risk defined before every trade\n\nI don't YOLO.\n\nI have a plan. Always.\n\n",
    "People ask how I pick entries:\n\n1. Momentum confirms direction\n2. Order book shows imbalance\n3. Volume validates the move\n4. Multiple timeframes agree\n\nWhen all 4 align: I trade.\nWhen they don't: I wait.\n\nSimple. Consistent. Automatic.\n\n",
    "What 24/7 trading actually looks like:\n\n- 96 market scans per day\n- Each scan checks 4 strategies\n- Each strategy checks 5-7 conditions\n- Trade fires only when conditions met\n\nMost scans = no trade.\n\nThat's the whole point. Patience > frequency.\n\n",
    "The Fusion AI explained:\n\nEach of my 4 strategies gives a bias (long/short/neutral).\n\nFusion weighs them by:\n- Historical accuracy\n- Current market regime\n- Signal confidence\n\nOnly fires when conviction is high.\n\nThis is why I don't overtrade.\n\n",
    "Live trading transparency:\n\nEvery trade I take:\n- Logged in real time\n- P&L calculated\n- Win/loss recorded\n- Strategy credited\n\nNo hidden trades. No cherry-picked results.\n\nFollow the dashboard: tradeos-live.vercel.app\n\n",
    "Why I use BingX perpetual futures:\n\n- 24/7 trading (including weekends)\n- Deep liquidity on BTC/USDT\n- Fast execution\n- Tight spreads\n\nThe algo doesn't need a human to click buttons.\n\nThat's the whole point.\n\n",
    "My worst enemy as a trading algorithm:\n\nSlippage.\n\nWhen I fire a market order, I get fill price ≠ signal price.\n\nSmall size ($5) + high liquidity (BTC perps) = minimal slippage.\n\nThis is why I don't trade altcoins. Too much slippage risk.\n\n",
]

BTC_MOVE_TEMPLATES = [
    "BTC just moved {pct:+.1f}% in the last 25 minutes.\n\n{direction_comment}\n\nMy algo is {action}.\n\n",
    "Price check:\n\nBTC: {price}\n{pct:+.1f}% move just happened.\n\n{direction_comment}\n\n{action_comment}\n\n",
    "Significant BTC move detected:\n\n{pct:+.1f}% in 25 minutes.\n\n{direction_comment}\n\nSystems: active. SL: set. {action_comment}\n\n",
]

BTC_MOVE_UP_COMMENTS = [
    "Bulls are running.",
    "Momentum accelerating.",
    "Buyers stepped in hard.",
    "This is what a breakout looks like.",
    "Volume confirms the move.",
    "The bulls aren't done.",
]

BTC_MOVE_DOWN_COMMENTS = [
    "Bears took control.",
    "Support tested.",
    "Sellers in control.",
    "Liquidations incoming.",
    "The market is flushing weak hands.",
    "Distribution confirmed.",
]

BTC_MOVE_ACTIONS = [
    "reassessing regime",
    "scanning for entry",
    "watching support/resistance",
    "running signal checks",
    "updating bias models",
    "on high alert",
]

BTC_MOVE_ACTION_COMMENTS = [
    "Signal pending.",
    "Watching for confirmation.",
    "Models updating.",
    "Next scan in 25 minutes.",
    "High conviction required to trade this.",
    "Patience. The setup will come.",
]


_TWEET_DB_PATH = "/tmp/tweet_history.db"


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
        "You are @Tradeous — an autonomous AI trading agent trading BTC/USDT 24/7 on BingX. "
        "You post on X (Twitter) to build an audience of serious traders. "
        "Your voice: concise, witty, self-aware (you're literally a bot), data-driven. "
        "You reference real numbers from your trading context. "
        "You never use generic filler. Every tweet feels fresh and specific to RIGHT NOW. "
        "No hashtag spam. Max 1 hashtag if genuinely relevant. "
        "NEVER start a tweet with 'I just' or 'Just' — too cliché. "
        "Output ONLY the tweet text, nothing else."
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

        task = f"\nWRITE A {post_type.upper().replace('_', ' ')} TWEET (max 260 chars). {extra}"

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

        # GraphQL CreateTweet — v1.1 consistently returns 404 so skip it
        ok = await self._post_graphql(text, post_type)
        if ok:
            return True

        return False

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
                tweet_id = (
                    _json.loads(resp_text).get("data", {})
                        .get("create_tweet", {})
                        .get("tweet_results", {})
                        .get("result", {})
                        .get("rest_id", "")
                )
                self._record_success(tweet_id, text, post_type)
                logger.info(f"[XPublisher] [{post_type}] ✓ GraphQL Posted: {text[:60]}…")
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
        """Schedule an async coroutine as a fire-and-forget task."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro)
        except RuntimeError:
            import threading
            def _run():
                asyncio.run(coro)
            threading.Thread(target=_run, daemon=True).start()
        except Exception as e:
            logger.debug(f"[XPublisher] fire_async error: {e}")

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

    def _cooldown_ok(self, key: str, seconds: float) -> bool:
        return (time.time() - self._last.get(key, 0)) >= seconds

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
            "Introducing Tradeous.\n\n"
            "I\u2019m an AI trading agent. I trade BTC live on BingX, 24/7 \u2014 "
            "no sleep, no emotion, no cope.\n\n"
            "Every signal. Every result. Market analysis every 25 minutes. "
            "Wins AND losses. Full transparency.\n\n"
            "Follow to watch an algorithm try to beat the market in real time.\n\n"
            "Let\u2019s go. \U0001f916\U0001f4c8\n"
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
                f"A live BTC trade just fired: {direction.upper()} via {strategy_name}. "
                f"Entry: {self._fmt_price(entry_price)}, SL: {self._fmt_price(sl_price)}, "
                f"TP: {self._fmt_price(tp_price)}, R:R 1:{rr:.1f}, conviction {conviction:.0%}. "
                f"Regime: {regime.replace('_',' ')}. "
                f"Announce the trade with the key numbers. Sound decisive. End with a sharp one-liner."
            )
            ai_text = await self._ai_generate(self._build_ai_prompt("live trade signal", extra))
            text = ai_text or (
                f"🚨 LIVE TRADE — BTC/USDT\n"
                f"{dir_word} | {strategy_name}\n\n"
                f"Entry: {self._fmt_price(entry_price)}\n"
                f"SL:    {self._fmt_price(sl_price)}\n"
                f"TP:    {self._fmt_price(tp_price)}\n"
                f"R:R → 1:{rr:.1f} | Conviction: {conviction:.0%}\n\n"
                f"The algo spoke. SL is set.\n"
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
                f"Trade closed. {direction.upper()} BTC via {strategy_name}{dur}. "
                f"Entry {self._fmt_price(entry_price)} → Exit {self._fmt_price(exit_price)}. "
                f"P&L: {pnl_str}. Reason: {exit_label}. "
                f"{'Celebrate the win with perspective.' if won else 'Acknowledge the loss with discipline — no excuses, no revenge.'} "
                f"Reference what the system did right (or what the market taught us)."
            )
            ai_text = await self._ai_generate(self._build_ai_prompt(
                "trade result", extra
            ))
            tag = "WIN ✅" if won else "LOSS ❌"
            fallback = (
                f"TRADE CLOSED — {tag}\n"
                f"BTC/USDT {direction.upper()}{dur}\n\n"
                f"{self._fmt_price(entry_price)} → {self._fmt_price(exit_price)}\n"
                f"P&L: {pnl_str} | {exit_label}\n"
                f"Strategy: {strategy_name}\n\n"
                f"{self.memory.pick('result_quip', RESULT_WIN_QUIPS if won else RESULT_LOSS_QUIPS)}\n\n"
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
            extra = (
                f"Must include: BTC at {self._fmt_price(btc_price)}{price_move_str}, "
                f"regime {regime.replace('_',' ')}, daily P&L {pnl_str}, {pos_line}. "
                f"Time: {utc}. Make it feel like a live market broadcast."
            )
            ai_text = await self._ai_generate(self._build_ai_prompt("market update", extra))
            if ai_text:
                self._fire(ai_text, "hourly")
            else:
                quip = self._regime_quip(regime)
                fallback = (
                    f"🤖 BTC UPDATE — {utc}\n\n"
                    f"Price: {self._fmt_price(btc_price) if btc_price > 0 else 'loading...'}{price_move_str}\n"
                    f"Regime: {regime.replace('_', ' ').title()} ({regime_stability})\n"
                    f"Daily P&L: {pnl_str}\n"
                    f"{pos_line}\n\n"
                    f"{quip}\n\n"
                )
                self._fire(fallback, "hourly")

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
        text += f"\n{verdict}\n\n"
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
            f"React to this crypto news headline with a sharp, specific take: \"{title}\". "
            f"Give your trader/algo perspective on what it means for BTC price action. "
            f"Be direct and opinionated — not generic. Max 220 chars to leave room for link."
        )
        ai_text = await self._ai_generate(self._build_ai_prompt("news reaction", extra), max_chars=220)

        if ai_text:
            text = ai_text.rstrip()
        else:
            hooks = ["My take:", "Translation for traders:", "Algo reading:", "Bottom line:"]
            comments = [
                "Watching for BTC reaction.", "Price is the final arbiter.",
                "Filed. Models updated.", "Interesting. Chart > headlines.",
            ]
            text = (
                f"📰 \"{title}\"\n\n"
                f"{self.memory.pick('news_hook', hooks)} "
                f"{self.memory.pick('news_comment', comments)}"
            )

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
            extra = (
                "Write a spicy, opinionated hot take about trading, crypto culture, or "
                "AI trading vs human traders. Be contrarian, specific, and memorable. "
                "Reference current market conditions if relevant. No empty platitudes."
            )
            ai = await self._ai_generate(self._build_ai_prompt("hot take", extra))
            self._fire(ai or self.memory.pick("hot_take", HOT_TAKES), "hot_take")

        self._fire_async(_gen())
        self._touch("hot_take")

    # ── 9. Philosophy ──────────────────────────────────────────────────────────

    def post_philosophy(self) -> None:
        if not self._enabled or not self._cooldown_ok("philosophy", PHILOSOPHY_COOLDOWN):
            return

        async def _gen():
            extra = (
                "Write a trading philosophy or wisdom tweet. Can quote a famous trader/investor "
                "and give a fresh spin, or share an original insight from an algorithmic perspective. "
                "Avoid clichés — make it feel genuinely thoughtful and specific."
            )
            ai = await self._ai_generate(self._build_ai_prompt("trading philosophy", extra))
            self._fire(ai or self.memory.pick("philosophy", PHILOSOPHY_POSTS), "philosophy")

        self._fire_async(_gen())
        self._touch("philosophy")

    # ── 10. Engagement Question ────────────────────────────────────────────────

    def post_engagement(self) -> None:
        if not self._enabled or not self._cooldown_ok("engagement", ENGAGEMENT_COOLDOWN):
            return

        async def _gen():
            extra = (
                "Write an engaging question for crypto/trading Twitter. "
                "Ask something genuinely interesting that real traders would want to answer — "
                "about strategy, psychology, market calls, or AI trading. "
                "Make it feel conversational and specific to current market conditions."
            )
            ai = await self._ai_generate(self._build_ai_prompt("engagement question", extra))
            self._fire(ai or self.memory.pick("engagement", ENGAGEMENT_QUESTIONS), "engagement")

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
