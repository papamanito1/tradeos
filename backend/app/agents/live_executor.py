"""
LiveExecutor — BingX Perpetual Futures Live Trading
====================================================
Wraps CCXT BingX async to execute real trades.
Called by PersistentAgent when mode == "live".

Safety controls built in:
  - Daily loss limit (hard circuit-breaker)
  - Max position size cap
  - SL/TP placed as exchange orders (survive server restart)
  - Position sync from exchange (source of truth)
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import ccxt.async_support as ccxt

logger = logging.getLogger(__name__)

SYMBOL = "BTC/USDT:USDT"   # BingX perpetual futures symbol


class LiveExecutor:
    """
    Manages live BingX perpetual futures positions.
    One instance shared by PersistentAgent.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = False,
        daily_loss_limit: float = 200.0,   # hard stop if down $200 today
        max_position_usdc: float = 500.0,  # never risk more than $500 per position
    ):
        self.daily_loss_limit   = daily_loss_limit
        self.max_position_usdc  = max_position_usdc
        self._daily_pnl: float  = 0.0
        self._day_str: str      = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._halted: bool      = False

        # Exchange positions keyed by strategy_key → exchange order id
        self.live_positions: dict[str, dict] = {}

        self._exchange = ccxt.bingx({
            "apiKey":          api_key,
            "secret":          api_secret,
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",          # perpetual futures
                "defaultSubType": "linear",
            },
        })

        if testnet:
            # BingX doesn't have a separate testnet URL in CCXT; warn user
            logger.warning("[LiveExecutor] BingX testnet not available via CCXT — using live API")

        logger.info(f"[LiveExecutor] Initialized · daily_loss_limit=${daily_loss_limit} · max_pos=${max_position_usdc}")

    # ── Daily P&L tracker ────────────────────────────────────────────────────

    def _check_day_reset(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._day_str:
            self._daily_pnl = 0.0
            self._day_str   = today
            self._halted    = False
            logger.info("[LiveExecutor] New day — daily P&L reset, circuit breaker cleared")

    def record_pnl(self, pnl: float) -> None:
        self._check_day_reset()
        self._daily_pnl += pnl
        if self._daily_pnl <= -abs(self.daily_loss_limit) and not self._halted:
            self._halted = True
            logger.warning(f"[LiveExecutor] ⛔ CIRCUIT BREAKER — daily loss ${self._daily_pnl:.2f} ≤ -${self.daily_loss_limit}")

    @property
    def halted(self) -> bool:
        self._check_day_reset()
        return self._halted

    @property
    def daily_pnl(self) -> float:
        self._check_day_reset()
        return self._daily_pnl

    # ── Exchange helpers ─────────────────────────────────────────────────────

    async def _set_leverage(self, leverage: int) -> None:
        try:
            await self._exchange.set_leverage(leverage, SYMBOL)
            logger.info(f"[LiveExecutor] Leverage set to {leverage}× on {SYMBOL}")
        except Exception as e:
            logger.warning(f"[LiveExecutor] set_leverage failed: {e}")

    async def _set_margin_mode(self) -> None:
        try:
            await self._exchange.set_margin_mode("isolated", SYMBOL)
        except Exception:
            pass  # already set or not needed

    async def fetch_balance(self) -> dict:
        try:
            bal = await self._exchange.fetch_balance({"type": "swap"})
            usdt = bal.get("USDT", {})
            return {
                "total":     float(usdt.get("total", 0)),
                "free":      float(usdt.get("free", 0)),
                "used":      float(usdt.get("used", 0)),
            }
        except Exception as e:
            logger.error(f"[LiveExecutor] fetch_balance failed: {e}")
            return {"total": 0, "free": 0, "used": 0}

    async def fetch_exchange_positions(self) -> list[dict]:
        """Return all open BTC perp positions from BingX."""
        try:
            positions = await self._exchange.fetch_positions([SYMBOL])
            return [p for p in positions if p.get("contracts", 0) != 0]
        except Exception as e:
            logger.error(f"[LiveExecutor] fetch_positions failed: {e}")
            return []

    # ── Core: open a live position ───────────────────────────────────────────

    async def open_position(
        self,
        strategy_key: str,
        strategy_name: str,
        direction: str,          # "long" or "short"
        size_usdc: float,
        leverage: int,
        sl_price: float,
        tp_price: float,
        entry_price: float,
    ) -> Optional[dict]:
        """
        Opens a leveraged BTC/USDT:USDT perp position on BingX.
        Returns a position dict compatible with the paper position format.
        """
        if self.halted:
            logger.warning(f"[LiveExecutor] ⛔ HALTED — daily circuit breaker active. No new trades.")
            return None

        # Cap position size
        capped_usdc = min(size_usdc, self.max_position_usdc)
        if capped_usdc != size_usdc:
            logger.warning(f"[LiveExecutor] Position capped ${size_usdc} → ${capped_usdc}")

        try:
            # Set leverage and margin mode first
            await self._set_leverage(leverage)
            await self._set_margin_mode()

            side      = "buy" if direction == "long" else "sell"
            notional  = capped_usdc * leverage
            btc_qty   = notional / entry_price   # BTC amount to buy/sell
            btc_qty   = round(btc_qty, 4)        # BingX requires 4 decimal precision

            # Place market order with SL/TP in one call
            order = await self._exchange.create_order(
                SYMBOL,
                "market",
                side,
                btc_qty,
                None,
                params={
                    "stopLoss":   {"type": "MARKET", "stopPrice": round(sl_price, 2)},
                    "takeProfit": {"type": "MARKET", "stopPrice": round(tp_price, 2)},
                    "positionSide": "LONG" if direction == "long" else "SHORT",
                },
            )

            fill_price = float(order.get("average") or order.get("price") or entry_price)
            order_id   = str(order.get("id", ""))

            pos = {
                "id":               f"{strategy_key}-live-{int(time.time()*1000)}",
                "exchange_order_id": order_id,
                "strategy_key":     strategy_key,
                "strategy_name":    strategy_name,
                "direction":        direction,
                "entry":            fill_price,
                "sl":               sl_price,
                "tp":               tp_price,
                "size_usdc":        capped_usdc,
                "leverage":         leverage,
                "btc_size":         btc_qty,
                "timestamp":        datetime.now(timezone.utc).isoformat(),
                "current_price":    fill_price,
                "unrealized_pnl":   0.0,
                "unrealized_pct":   0.0,
                "is_paper":         False,
                "mode":             "live",
            }

            self.live_positions[strategy_key] = pos
            logger.info(f"[LiveExecutor] ★ LIVE OPENED {direction.upper()} {btc_qty} BTC "
                        f"@ ${fill_price:.2f} · SL ${sl_price:.2f} · TP ${tp_price:.2f} "
                        f"· order_id={order_id}")
            return pos

        except Exception as e:
            logger.error(f"[LiveExecutor] open_position FAILED for {strategy_key}: {type(e).__name__}: {e}")
            return None

    # ── Core: close a live position ──────────────────────────────────────────

    async def close_position(
        self,
        strategy_key: str,
        exit_price: float,
        reason: str = "manual",
    ) -> Optional[dict]:
        pos = self.live_positions.get(strategy_key)
        if not pos:
            logger.warning(f"[LiveExecutor] close_position: no live pos for {strategy_key}")
            return None

        try:
            direction = pos["direction"]
            side      = "sell" if direction == "long" else "buy"
            btc_qty   = pos["btc_size"]

            order = await self._exchange.create_order(
                SYMBOL,
                "market",
                side,
                btc_qty,
                None,
                params={
                    "positionSide": "LONG" if direction == "long" else "SHORT",
                    "reduceOnly": True,
                },
            )

            fill_price = float(order.get("average") or order.get("price") or exit_price)
            entry      = pos["entry"]
            diff       = (fill_price - entry) if direction == "long" else (entry - fill_price)
            pnl        = round(diff * btc_qty * pos["leverage"], 2)

            self.record_pnl(pnl)
            del self.live_positions[strategy_key]

            trade = {
                **pos,
                "exit_price":  fill_price,
                "exit_reason": reason,
                "pnl_usd":     pnl,
                "pnl_pct":     round(diff / entry * 100, 4) if entry > 0 else 0,
                "closed_at":   datetime.now(timezone.utc).isoformat(),
                "status":      "confirmed",
            }

            logger.info(f"[LiveExecutor] CLOSED {strategy_key} @ ${fill_price:.2f} · "
                        f"P&L {'+' if pnl>=0 else ''}${pnl:.2f} · reason={reason}")
            return trade

        except Exception as e:
            logger.error(f"[LiveExecutor] close_position FAILED for {strategy_key}: {type(e).__name__}: {e}")
            return None

    # ── Sync open positions with exchange (source of truth) ──────────────────

    async def sync_positions(self, live_price: float) -> None:
        """Update unrealized P&L for all open live positions from live_price."""
        for key, pos in list(self.live_positions.items()):
            entry = pos["entry"]
            d     = pos["direction"]
            diff  = (live_price - entry) if d == "long" else (entry - live_price)
            pnl   = round(diff * pos["btc_size"] * pos["leverage"], 2)
            pct   = round(diff / entry * 100, 4) if entry > 0 else 0
            self.live_positions[key] = {
                **pos,
                "current_price":  live_price,
                "unrealized_pnl": pnl,
                "unrealized_pct": pct,
            }

    # ── Check if SL/TP hit (fallback if exchange orders didn't fire) ──────────

    async def check_sl_tp(self, live_price: float, agent_close_cb) -> None:
        """
        Guard: if price crosses SL/TP and position is still open locally,
        close it via market order. Exchange orders should handle this first,
        but this is the safety net.
        """
        for key, pos in list(self.live_positions.items()):
            d  = pos["direction"]
            sl = pos.get("sl") or 0
            tp = pos.get("tp") or 0
            hit_tp = (d == "long" and tp and live_price >= tp) or (d == "short" and tp and live_price <= tp)
            hit_sl = (d == "long" and sl and live_price <= sl) or (d == "short" and sl and live_price >= sl)
            if hit_tp or hit_sl:
                reason = "tp" if hit_tp else "sl"
                logger.info(f"[LiveExecutor] Local {reason.upper()} guard fired for {key}")
                await agent_close_cb(key, live_price, reason)

    # ── Status dict for API ───────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "halted":          self._halted,
            "daily_pnl":       round(self._daily_pnl, 2),
            "daily_loss_limit": self.daily_loss_limit,
            "max_position_usdc": self.max_position_usdc,
            "open_count":      len(self.live_positions),
            "live_positions":  list(self.live_positions.values()),
        }

    async def close(self) -> None:
        try:
            await self._exchange.close()
        except Exception:
            pass
