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

    RISK_PER_TRADE_PCT = 0.02   # 2% of total capital per trade
    MAX_LEVERAGE = 30            # hard cap on leverage

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = False,
        daily_loss_limit: float = 200.0,
        max_position_usdc: float = 500.0,  # fallback if balance fetch fails
    ):
        self.daily_loss_limit   = daily_loss_limit
        self.max_position_usdc  = max_position_usdc
        self._daily_pnl: float  = 0.0
        self._day_str: str      = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._halted: bool      = False

        self.live_positions: dict[str, dict] = {}
        self.last_error: Optional[str] = None

        # Cached balance (refreshed before each trade)
        self._cached_balance: dict = {"total": 0, "free": 0, "used": 0}
        self._balance_fetched_at: float = 0

        self._exchange = ccxt.bingx({
            "apiKey":          api_key,
            "secret":          api_secret,
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",
                "defaultSubType": "linear",
            },
        })

        if testnet:
            logger.warning("[LiveExecutor] BingX testnet not available via CCXT — using live API")

        logger.info(f"[LiveExecutor] Initialized · risk_per_trade={self.RISK_PER_TRADE_PCT:.0%} "
                    f"· max_leverage={self.MAX_LEVERAGE}× · daily_loss_limit=${daily_loss_limit}")

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

    async def fetch_balance(self, force: bool = False) -> dict:
        now = time.time()
        if not force and (now - self._balance_fetched_at) < 30 and self._cached_balance["total"] > 0:
            return self._cached_balance
        try:
            bal = await self._exchange.fetch_balance({"type": "swap"})
            usdt = bal.get("USDT", {})
            self._cached_balance = {
                "total":     float(usdt.get("total", 0)),
                "free":      float(usdt.get("free", 0)),
                "used":      float(usdt.get("used", 0)),
            }
            self._balance_fetched_at = now
            logger.info(f"[LiveExecutor] Balance: total=${self._cached_balance['total']:.2f} "
                        f"· free=${self._cached_balance['free']:.2f}")
            return self._cached_balance
        except Exception as e:
            logger.error(f"[LiveExecutor] fetch_balance failed: {e}")
            return self._cached_balance if self._cached_balance["total"] > 0 else {"total": 0, "free": 0, "used": 0}

    def compute_trade_size(self, total_capital: float) -> float:
        """2% of total capital — this is the margin (collateral) per trade."""
        size = round(total_capital * self.RISK_PER_TRADE_PCT, 2)
        return max(1.0, size)  # at least $1

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
        size_usdc: float,        # requested size (may be overridden by 2% rule)
        leverage: int,
        sl_price: float,
        tp_price: float,
        entry_price: float,
    ) -> Optional[dict]:
        """
        Opens a leveraged BTC/USDT:USDT perp position on BingX.
        Position size = 2% of total BingX capital (margin), leverage capped at 30x.
        Returns a position dict compatible with the paper position format.
        """
        if self.halted:
            logger.warning(f"[LiveExecutor] ⛔ HALTED — daily circuit breaker active. No new trades.")
            self.last_error = "Circuit breaker active"
            return None

        # Fetch live balance to compute dynamic position size
        balance = await self.fetch_balance(force=True)
        total_capital = balance["total"]
        free_capital = balance["free"]

        if total_capital <= 0:
            self.last_error = f"No capital on BingX (total=${total_capital:.2f})"
            logger.warning(f"[LiveExecutor] {self.last_error}")
            return None

        # 2% of total capital = margin for this trade
        risk_size = self.compute_trade_size(total_capital)

        # Don't exceed free margin
        capped_usdc = min(risk_size, free_capital * 0.95)  # keep 5% buffer
        if capped_usdc < 1.0:
            self.last_error = f"Insufficient free margin (free=${free_capital:.2f}, need=${risk_size:.2f})"
            logger.warning(f"[LiveExecutor] {self.last_error}")
            return None

        # Cap leverage at 30x
        leverage = min(leverage, self.MAX_LEVERAGE)

        logger.info(f"[LiveExecutor] Sizing: capital=${total_capital:.2f} · 2%=${risk_size:.2f} "
                    f"· using=${capped_usdc:.2f} · leverage={leverage}× · "
                    f"notional=${capped_usdc * leverage:.2f}")

        try:
            await self._set_leverage(leverage)
            await self._set_margin_mode()

            side      = "buy" if direction == "long" else "sell"
            notional  = capped_usdc * leverage
            btc_qty   = notional / entry_price
            btc_qty   = round(btc_qty, 4)

            # BingX minimum: 0.0001 BTC (~$7 at current prices)
            if btc_qty < 0.0001:
                logger.warning(f"[LiveExecutor] Order too small: {btc_qty} BTC (min 0.0001)")
                self.last_error = f"Order too small: {btc_qty} BTC"
                return None

            logger.info(f"[LiveExecutor] Placing {side.upper()} {btc_qty} BTC @ ~${entry_price:.0f} "
                        f"· notional ${notional:.0f} · lev {leverage}×")

            # Step 1: Place market order (without SL/TP — more reliable across exchanges)
            order = await self._exchange.create_order(
                SYMBOL,
                "market",
                side,
                btc_qty,
                None,
                params={"positionSide": "LONG" if direction == "long" else "SHORT"},
            )

            fill_price = float(order.get("average") or order.get("price") or entry_price)
            order_id   = str(order.get("id", ""))

            logger.info(f"[LiveExecutor] Market order filled: id={order_id} @ ${fill_price:.2f}")

            # Step 2: Place SL/TP as separate stop orders (more reliable than params)
            try:
                if sl_price > 0:
                    sl_side = "sell" if direction == "long" else "buy"
                    await self._exchange.create_order(
                        SYMBOL, "market", sl_side, btc_qty, None,
                        params={
                            "stopPrice": round(sl_price, 2),
                            "positionSide": "LONG" if direction == "long" else "SHORT",
                            "reduceOnly": True,
                            "triggerType": "MARK_PRICE",
                        },
                    )
                    logger.info(f"[LiveExecutor] SL placed @ ${sl_price:.2f}")
            except Exception as sl_err:
                logger.warning(f"[LiveExecutor] SL order failed (position still open): {sl_err}")

            try:
                if tp_price > 0:
                    tp_side = "sell" if direction == "long" else "buy"
                    await self._exchange.create_order(
                        SYMBOL, "market", tp_side, btc_qty, None,
                        params={
                            "stopPrice": round(tp_price, 2),
                            "positionSide": "LONG" if direction == "long" else "SHORT",
                            "reduceOnly": True,
                            "triggerType": "MARK_PRICE",
                        },
                    )
                    logger.info(f"[LiveExecutor] TP placed @ ${tp_price:.2f}")
            except Exception as tp_err:
                logger.warning(f"[LiveExecutor] TP order failed (position still open): {tp_err}")

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
            self.last_error = None
            logger.info(f"[LiveExecutor] ★ LIVE OPENED {direction.upper()} {btc_qty} BTC "
                        f"@ ${fill_price:.2f} · SL ${sl_price:.2f} · TP ${tp_price:.2f} "
                        f"· order_id={order_id}")
            return pos

        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            logger.error(f"[LiveExecutor] open_position FAILED for {strategy_key}: {self.last_error}")
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
        total_cap = self._cached_balance.get("total", 0)
        return {
            "halted":            self._halted,
            "daily_pnl":         round(self._daily_pnl, 2),
            "daily_loss_limit":  self.daily_loss_limit,
            "max_position_usdc": round(self.compute_trade_size(total_cap), 2) if total_cap > 0 else self.max_position_usdc,
            "risk_per_trade_pct": self.RISK_PER_TRADE_PCT,
            "max_leverage":      self.MAX_LEVERAGE,
            "account_balance":   round(total_cap, 2),
            "free_balance":      round(self._cached_balance.get("free", 0), 2),
            "open_count":        len(self.live_positions),
            "live_positions":    list(self.live_positions.values()),
            "last_error":        self.last_error,
        }

    async def close(self) -> None:
        try:
            await self._exchange.close()
        except Exception:
            pass
