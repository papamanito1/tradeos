"""
BTC HFT Book Scalper
====================

Faithful implementation of the BtcHftScalper class provided by the user,
adapted for the TradeOS strategy framework.

Original logic (pseudo-code):

    on_book_update(book, trades, now_ms):
        guard: book.is_synced, data_lag < 150ms, spread_ticks ≤ 2
        obi   = book.order_book_imbalance(levels=5)
        micro = book.microprice() - book.mid()
        tfi   = trades.trade_flow_imbalance(window_ms=300)
        drift = book.mid_return(window_ms=1000)
        repl  = book.replenishment_signal(window_ms=500)

        long_signal  = obi > 0.18 and micro > tick*0.15 and tfi > 0.12 and repl > 0 and drift >= 0
        short_signal = obi < -0.18 and micro < -tick*0.15 and tfi < -0.12 and repl < 0 and drift <= 0

        position mgmt:
            long  exit → +1 tick TP | -2 tick SL | 5 s max hold | signal flip
            short exit → +1 tick TP | -2 tick SL | 5 s max hold | signal flip

OHLCV Approximations (for backtesting)
---------------------------------------
Since backtesting runs on 1-minute OHLCV bars, tick-level concepts are mapped
to their candle-level equivalents:

  Signal            | Live (tick)                           | Candle approx
  ──────────────────|───────────────────────────────────────|─────────────────────────
  obi               | bid_vol / (bid_vol + ask_vol) − 0.5   | (close−open)/(high−low+ε)×vol, cumulative
  microprice − mid  | weighted price vs mid                 | close vs (high+low)/2
  tfi               | signed trade vol / total vol, 300 ms  | cumulative signed candle vol, last N bars
  drift             | mid return over 1 000 ms              | last bar return (close/close[-1]−1)
  replenishment     | depth recovery after hits             | vol trend: current > average (liquidity available)
  spread_ticks ≤ 2  | (ask−bid) / tick_size ≤ 2             | ATR > min threshold (market is moving)
  data lag < 150 ms | exchange ws latency                   | N/A in backtest (always passes)
  max_hold 5 s      | 5 000 ms clock                        | 1 bar (configurable)

Risk parameters are expressed in "ticks" where 1 tick = tick_size_pct of price.
Default: 1 tick = 0.005% (5 bps) — appropriate for BTC/USDT liquid markets.
  TP = +1 tick = +0.005%
  SL = -2 ticks = -0.010%
  R:R = 1:0.5 (requires ≥ 67% win rate to be profitable)

Recommended timeframe: 1m (for backtesting approximation)
Designed for: BTC/USDT, ETH/USDT — deep-liquid pairs only.
"""

from __future__ import annotations

import numpy as np

from app.exchange.base import Candle
from app.strategies.base import BaseStrategy, Signal, SignalDirection


class HftBookScalper(BaseStrategy):

    name = "HFT Book Scalper"
    description = (
        "Order-book microstructure scalper: OBI + microprice + trade-flow imbalance "
        "+ replenishment + drift. TP=1 tick, SL=2 ticks, max hold=1 bar. "
        "Designed for deep-liquid pairs (BTC/USDT, ETH/USDT)."
    )

    _MIN_CANDLES = 35

    default_parameters = {
        # ── Signal thresholds (matching original class) ───────────────────────
        "obi_threshold":   0.18,   # order book imbalance threshold
        "micro_threshold": 0.15,   # microprice edge threshold (× tick fraction)
        "tfi_threshold":   0.12,   # trade flow imbalance threshold
        # ── Spread / vol guard ────────────────────────────────────────────────
        "max_spread_ticks": 2,     # cancel passives if spread > N ticks
        "min_atr_pct":   0.0002,   # skip if ATR < 0.02% (dead market)
        "max_atr_pct":   0.0300,   # skip if ATR > 3.00% (news spike)
        # ── Candle approximation windows ──────────────────────────────────────
        "obi_period":    10,       # bars for cumulative OBI (≈ 300 ms @ 1m)
        "tfi_period":    5,        # bars for trade flow imbalance
        "repl_period":   20,       # bars for replenishment (volume baseline)
        "drift_period":  3,        # bars for mid-price drift
        # ── Tick size ─────────────────────────────────────────────────────────
        "tick_size_pct": 0.00005,  # 1 tick = 0.005% of price
        # ── Risk (in ticks) ───────────────────────────────────────────────────
        "tp_ticks":      1,        # TP = +1 tick
        "sl_ticks":      2,        # SL = -2 ticks
        # ── Max hold (bars) ───────────────────────────────────────────────────
        "max_hold_bars": 5,        # 5 × 1m bar ≈ 5 min (adapts to timeframe)
    }

    def generate_signal(self, candles: list[Candle], symbol: str, timeframe: str) -> Signal:
        p = self.parameters

        def flat(reason: str = "") -> Signal:
            return Signal(
                direction=SignalDirection.none,
                symbol=symbol, timeframe=timeframe,
                confidence=0.0,
                suggested_entry=candles[-1].close,
                reasoning=reason,
            )

        if len(candles) < self._MIN_CANDLES:
            return flat(f"Warming up ({len(candles)}/{self._MIN_CANDLES})")

        closes  = np.array([c.close  for c in candles], dtype=np.float64)
        opens   = np.array([c.open   for c in candles], dtype=np.float64)
        highs   = np.array([c.high   for c in candles], dtype=np.float64)
        lows    = np.array([c.low    for c in candles], dtype=np.float64)
        volumes = np.array([c.volume for c in candles], dtype=np.float64)

        price     = closes[-1]
        tick_size = price * p["tick_size_pct"]

        # ── GUARD: ATR spread proxy (replaces book.spread_ticks() > 2) ────────
        atr = self._atr(highs, lows, closes, 14)
        atr_pct = atr / price if price > 0 else 0
        if atr_pct < p["min_atr_pct"]:
            return flat(f"Spread guard: ATR {atr_pct:.4%} < min {p['min_atr_pct']:.4%} (market too flat)")
        if atr_pct > p["max_atr_pct"]:
            return flat(f"Vol guard: ATR {atr_pct:.4%} > max {p['max_atr_pct']:.4%} (news spike)")

        # ── 1. OBI — Order Book Imbalance (book.order_book_imbalance(levels=5))
        #
        # Live: OBI = (bid_vol_5 − ask_vol_5) / (bid_vol_5 + ask_vol_5)
        # Candle approx: signed volume pressure per bar = (close−open)/(high−low+ε)×vol
        #   cumulative over last `obi_period` bars, normalized by total volume.
        op = p["obi_period"]
        ranges   = highs[-op:] - lows[-op:] + 1e-10
        signed_v = (closes[-op:] - opens[-op:]) / ranges * volumes[-op:]
        obi      = float(np.sum(signed_v)) / (float(np.sum(volumes[-op:])) + 1e-10)

        # ── 2. Microprice edge (book.microprice() − book.mid())
        #
        # Live: microprice = (best_bid×ask_size + best_ask×bid_size) / (bid_size+ask_size)
        #   microprice > mid → more weight on ask side → buyers are aggressive
        # Candle approx: price vs bar midpoint. If close is above the bar mid,
        #   the microprice is leaning long (buyers paid up).
        bar_mid   = (highs[-1] + lows[-1]) / 2.0
        micro_raw = closes[-1] - bar_mid                  # positive = bullish lean
        # Normalize to tick units for threshold comparison
        micro_edge = micro_raw / tick_size if tick_size > 0 else 0.0

        # ── 3. TFI — Trade Flow Imbalance (trades.trade_flow_imbalance(window=300ms))
        #
        # Live: TFI = (buy_vol − sell_vol) / (buy_vol + sell_vol) over last 300 ms
        # Candle approx: same signed-volume ratio over last `tfi_period` bars
        tp = p["tfi_period"]
        tfi_signed = (closes[-tp:] - opens[-tp:]) / (highs[-tp:] - lows[-tp:] + 1e-10) * volumes[-tp:]
        tfi = float(np.sum(tfi_signed)) / (float(np.sum(volumes[-tp:])) + 1e-10)

        # ── 4. Drift (book.mid_return(window_ms=1000))
        #
        # Live: (mid_now − mid_1000ms_ago) / mid_1000ms_ago
        # Candle approx: last `drift_period` bar return
        dp = p["drift_period"]
        drift = float(closes[-1] - closes[-(dp + 1)]) / float(closes[-(dp + 1)] + 1e-10)

        # ── 5. Replenishment (book.replenishment_signal(window_ms=500))
        #
        # Live: measures how quickly limit orders re-fill after being hit.
        #   Positive = strong replenishment → market absorbing hits = directional.
        # Candle approx: current volume vs rolling baseline.
        #   Volume > avg → participants are actively placing orders (good replenishment).
        #   We also check if volume is increasing (momentum in order flow).
        rp = p["repl_period"]
        vol_avg  = float(np.mean(volumes[-(rp + 1):-1]))
        repl     = float(volumes[-1] - vol_avg)          # positive = above-average vol

        # Also check volume trend: increasing = better replenishment signal
        vol_trend = float(volumes[-1]) - float(volumes[-2])

        # Combined replenishment: above avg AND increasing
        repl_signal = repl + vol_trend                   # positive = replenishment strong

        # ── Long & short signal conditions (exact thresholds from original class) ──
        long_signal = (
            obi        >  p["obi_threshold"]             and
            micro_edge >  p["micro_threshold"]           and
            tfi        >  p["tfi_threshold"]             and
            repl_signal > 0                              and
            drift      >= 0
        )

        short_signal = (
            obi        < -p["obi_threshold"]             and
            micro_edge < -p["micro_threshold"]           and
            tfi        < -p["tfi_threshold"]             and
            repl_signal < 0                              and
            drift      <= 0
        )

        # ── Risk sizing (tick-based, matching original manage_position logic) ──
        tp_pct = p["tp_ticks"] * p["tick_size_pct"]     # +1 tick
        sl_pct = p["sl_ticks"] * p["tick_size_pct"]     # -2 ticks

        if long_signal:
            entry = price
            sl    = round(entry * (1 - sl_pct), 8)
            tp    = round(entry * (1 + tp_pct), 8)
            conf  = self._confidence(obi, tfi, micro_edge, repl_signal, drift, True)
            return Signal(
                direction=SignalDirection.long,
                symbol=symbol, timeframe=timeframe,
                confidence=conf,
                suggested_entry=round(entry, 6),
                suggested_sl=sl,
                suggested_tp=tp,
                reasoning=(
                    f"HFT LONG | OBI={obi:+.3f} (>{p['obi_threshold']}) "
                    f"| Microprice={micro_edge:+.2f}t (>{p['micro_threshold']}t) "
                    f"| TFI={tfi:+.3f} (>{p['tfi_threshold']}) "
                    f"| Repl={repl_signal:+.1f} | Drift={drift:+.5%} "
                    f"| TP={tp:.2f} (+{tp_pct:.3%}) SL={sl:.2f} (-{sl_pct:.3%})"
                ),
            )

        if short_signal:
            entry = price
            sl    = round(entry * (1 + sl_pct), 8)
            tp    = round(entry * (1 - tp_pct), 8)
            conf  = self._confidence(obi, tfi, micro_edge, repl_signal, drift, False)
            return Signal(
                direction=SignalDirection.short,
                symbol=symbol, timeframe=timeframe,
                confidence=conf,
                suggested_entry=round(entry, 6),
                suggested_sl=sl,
                suggested_tp=tp,
                reasoning=(
                    f"HFT SHORT | OBI={obi:+.3f} (<-{p['obi_threshold']}) "
                    f"| Microprice={micro_edge:+.2f}t (<-{p['micro_threshold']}t) "
                    f"| TFI={tfi:+.3f} (<-{p['tfi_threshold']}) "
                    f"| Repl={repl_signal:+.1f} | Drift={drift:+.5%} "
                    f"| TP={tp:.2f} (-{tp_pct:.3%}) SL={sl:.2f} (+{sl_pct:.3%})"
                ),
            )

        return flat(
            f"No signal | OBI={obi:+.3f} micro={micro_edge:+.2f}t "
            f"TFI={tfi:+.3f} Repl={repl_signal:+.1f} Drift={drift:+.5%}"
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int) -> float:
        tr = np.maximum(
            highs[1:] - lows[1:],
            np.maximum(
                np.abs(highs[1:] - closes[:-1]),
                np.abs(lows[1:]  - closes[:-1]),
            ),
        )
        return float(np.mean(tr[-period:])) if len(tr) >= period else float(np.mean(tr))

    @staticmethod
    def _confidence(obi: float, tfi: float, micro: float,
                    repl: float, drift: float, is_long: bool) -> float:
        """
        Confidence = how strongly all 5 factors agree.
        Each factor contributes 0-0.2 to a 0-1 score.
        """
        sign = 1 if is_long else -1

        def clamp01(x: float) -> float:
            return max(0.0, min(1.0, x))

        obi_s   = clamp01(sign * obi   / 0.50)  # peaks at 0.5 imbalance
        tfi_s   = clamp01(sign * tfi   / 0.40)  # peaks at 0.4 flow
        micro_s = clamp01(sign * micro / 1.0)   # peaks at 1 tick edge
        repl_s  = clamp01(sign * repl  / abs(repl + 1e-9))
        drift_s = clamp01(sign * drift / 0.002) # peaks at 0.2% move

        return round((obi_s + tfi_s + micro_s + repl_s + drift_s) / 5.0, 3)
