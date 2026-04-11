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

    FIXED_MARGIN_USD    = 5.0    # fixed $5 margin per trade
    MAX_LEVERAGE        = 60     # hard cap on leverage
    RISK_PER_TRADE_PCT  = 0.02   # 2% risk per trade (display / logging only)

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

    MIN_MARGIN_USD = 5.0   # BingX minimum order floor

    def compute_trade_size(self, total_capital: float) -> float:
        """Fixed $5 margin per trade regardless of account size."""
        return self.FIXED_MARGIN_USD

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

        # Fixed $5 margin per trade — capped by available free margin
        risk_size = self.compute_trade_size(total_capital)
        max_available = max(1.0, free_capital * 0.95)
        capped_usdc = min(risk_size, max_available)

        # Ensure we can at least meet BingX minimum (0.0001 BTC)
        if capped_usdc < self.MIN_MARGIN_USD and free_capital < self.MIN_MARGIN_USD:
            self.last_error = f"Insufficient capital (free=${free_capital:.2f}, need=${self.MIN_MARGIN_USD:.0f})"
            logger.warning(f"[LiveExecutor] {self.last_error}")
            return None

        leverage = min(leverage, self.MAX_LEVERAGE)

        logger.info(f"[LiveExecutor] Sizing: capital=${total_capital:.2f} · "
                    f"margin=${capped_usdc:.2f} · leverage={leverage}× · "
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

            # Step 2: Place SL/TP as separate stop orders, track their IDs
            sl_order_id = ""
            tp_order_id = ""
            pos_side = "LONG" if direction == "long" else "SHORT"
            close_side = "sell" if direction == "long" else "buy"

            try:
                if sl_price > 0:
                    sl_order = await self._exchange.create_order(
                        SYMBOL, "market", close_side, btc_qty, None,
                        params={
                            "stopPrice": round(sl_price, 2),
                            "positionSide": pos_side,
                            "reduceOnly": True,
                            "triggerType": "MARK_PRICE",
                        },
                    )
                    sl_order_id = str(sl_order.get("id", ""))
                    logger.info(f"[LiveExecutor] SL placed @ ${sl_price:.2f} (order_id={sl_order_id})")
            except Exception as sl_err:
                logger.warning(f"[LiveExecutor] SL order failed (position still open): {sl_err}")

            try:
                if tp_price > 0:
                    tp_order = await self._exchange.create_order(
                        SYMBOL, "market", close_side, btc_qty, None,
                        params={
                            "stopPrice": round(tp_price, 2),
                            "positionSide": pos_side,
                            "reduceOnly": True,
                            "triggerType": "MARK_PRICE",
                        },
                    )
                    tp_order_id = str(tp_order.get("id", ""))
                    logger.info(f"[LiveExecutor] TP placed @ ${tp_price:.2f} (order_id={tp_order_id})")
            except Exception as tp_err:
                logger.warning(f"[LiveExecutor] TP order failed (position still open): {tp_err}")

            pos = {
                "id":               f"{strategy_key}-live-{int(time.time()*1000)}",
                "exchange_order_id": order_id,
                "sl_order_id":      sl_order_id,
                "tp_order_id":      tp_order_id,
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

    # ── Cancel SL/TP orders for a specific position ─────────────────────────

    async def cancel_position_orders(self, strategy_key: str) -> None:
        """
        Cancel only the SL and TP orders belonging to a specific trade.
        Uses stored order IDs so other trades' SL/TP are not touched.
        Falls back to cancelling all orders only if no other positions are open.
        """
        pos = self.live_positions.get(strategy_key)
        order_ids_to_cancel: list[str] = []

        if pos:
            if pos.get("sl_order_id"):
                order_ids_to_cancel.append(pos["sl_order_id"])
            if pos.get("tp_order_id"):
                order_ids_to_cancel.append(pos["tp_order_id"])

        if order_ids_to_cancel:
            for oid in order_ids_to_cancel:
                try:
                    await self._exchange.cancel_order(oid, SYMBOL)
                    logger.info(f"[LiveExecutor] Cancelled order {oid} for {strategy_key}")
                except Exception as ce:
                    err_str = str(ce).lower()
                    if any(kw in err_str for kw in ["not exist", "not found", "already", "cancelled"]):
                        logger.info(f"[LiveExecutor] Order {oid} already gone (SL/TP fired)")
                    else:
                        logger.warning(f"[LiveExecutor] Failed to cancel order {oid}: {ce}")
        else:
            # No stored IDs (legacy position) — only safe to cancel all if no other live positions
            other_positions = {k: v for k, v in self.live_positions.items() if k != strategy_key}
            if not other_positions:
                await self._cancel_all_open_orders()
            else:
                logger.warning(
                    f"[LiveExecutor] No stored order IDs for {strategy_key} and "
                    f"{len(other_positions)} other position(s) open — skipping blanket cancel "
                    f"to protect other trades' SL/TP"
                )

    async def _cancel_all_open_orders(self) -> None:
        """Cancel every open order on SYMBOL. Only used when no other positions are open."""
        try:
            open_orders = await self._exchange.fetch_open_orders(SYMBOL)
            if not open_orders:
                return
            logger.info(f"[LiveExecutor] Cancelling {len(open_orders)} orphaned order(s) on {SYMBOL}")
            for o in open_orders:
                oid = o.get("id")
                if oid:
                    try:
                        await self._exchange.cancel_order(oid, SYMBOL)
                        logger.info(f"[LiveExecutor] Cancelled order {oid} (type={o.get('type')} "
                                    f"side={o.get('side')} stopPrice={o.get('stopPrice')})")
                    except Exception as ce:
                        logger.warning(f"[LiveExecutor] Failed to cancel order {oid}: {ce}")
        except Exception as e:
            logger.warning(f"[LiveExecutor] _cancel_all_open_orders failed: {e}")

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

        direction = pos["direction"]
        side      = "sell" if direction == "long" else "buy"
        btc_qty   = pos["btc_size"]
        entry     = pos["entry"]

        try:
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
            diff       = (fill_price - entry) if direction == "long" else (entry - fill_price)
            pnl        = round(diff * btc_qty * pos["leverage"], 2)

            self.record_pnl(pnl)

            # Cancel only THIS trade's remaining SL/TP (not other trades')
            await self.cancel_position_orders(strategy_key)
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
            err_str = str(e).lower()
            already_closed = any(kw in err_str for kw in [
                "insufficient", "no position", "position not exist",
                "reduce only", "order would not reduce", "80014",
            ])
            if already_closed:
                logger.info(f"[LiveExecutor] Position {strategy_key} already closed on exchange "
                            f"(SL/TP fired) — cancelling counterpart order")
                diff = (exit_price - entry) if direction == "long" else (entry - exit_price)
                pnl  = round(diff * btc_qty * pos["leverage"], 2)
                self.record_pnl(pnl)
                # Cancel only THIS trade's counterpart order (e.g. TP when SL fired)
                await self.cancel_position_orders(strategy_key)
                del self.live_positions[strategy_key]
                return {
                    **pos,
                    "exit_price":  exit_price,
                    "exit_reason": f"{reason}_exchange_closed",
                    "pnl_usd":     pnl,
                    "pnl_pct":     round(diff / entry * 100, 4) if entry > 0 else 0,
                    "closed_at":   datetime.now(timezone.utc).isoformat(),
                    "status":      "confirmed",
                }
            logger.error(f"[LiveExecutor] close_position FAILED for {strategy_key}: {type(e).__name__}: {e}")
            return None

    # ── Sync open positions with exchange (source of truth) ──────────────────

    async def sync_positions(self, live_price: float) -> list[str]:
        """
        Sync with BingX exchange:
        1. Update unrealized P&L for all local positions
        2. Detect positions closed on exchange by checking their SL/TP order status
        3. Cancel orphaned counterpart orders when a trade's SL or TP already filled
        """
        closed_keys: list[str] = []

        if not self.live_positions:
            return closed_keys

        # Update P&L for all tracked positions
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

        # Fetch open stop orders from BingX to detect which SL/TP have fired
        try:
            open_orders = await self._exchange.fetch_open_orders(SYMBOL)
        except Exception as e:
            logger.debug(f"[LiveExecutor] fetch_open_orders failed: {e}")
            open_orders = []
        open_order_ids = {str(o.get("id", "")) for o in open_orders}

        # Also fetch actual exchange positions for total-gone detection
        exchange_positions = await self.fetch_exchange_positions()
        exchange_qty = sum(abs(p.get("contracts", 0)) for p in exchange_positions)

        for key, pos in list(self.live_positions.items()):
            sl_oid = pos.get("sl_order_id", "")
            tp_oid = pos.get("tp_order_id", "")

            if not sl_oid and not tp_oid:
                continue

            sl_still_open = sl_oid in open_order_ids if sl_oid else False
            tp_still_open = tp_oid in open_order_ids if tp_oid else False

            # If SL fired (gone from open orders) but TP is still there → SL closed this trade
            sl_fired = sl_oid and not sl_still_open
            tp_fired = tp_oid and not tp_still_open

            if sl_fired and tp_still_open:
                logger.info(f"[LiveExecutor] {key}: SL order {sl_oid} fired — "
                            f"cancelling orphaned TP {tp_oid}")
                await self.cancel_position_orders(key)
                closed_keys.append(key)
            elif tp_fired and sl_still_open:
                logger.info(f"[LiveExecutor] {key}: TP order {tp_oid} fired — "
                            f"cancelling orphaned SL {sl_oid}")
                await self.cancel_position_orders(key)
                closed_keys.append(key)
            elif sl_fired and tp_fired:
                logger.info(f"[LiveExecutor] {key}: Both SL and TP gone — "
                            f"position fully closed on exchange")
                closed_keys.append(key)

        # Fallback: if exchange has zero position but we still track some, clean up all
        if self.live_positions and exchange_qty == 0 and not closed_keys:
            for key in list(self.live_positions.keys()):
                await self.cancel_position_orders(key)
                logger.info(f"[LiveExecutor] {key} gone from exchange (zero net position) — cleaning up")
                closed_keys.append(key)

        return closed_keys

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
            "connected":         True,
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
