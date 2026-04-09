"""
Market data API — serves live Binance prices from the in-memory stream cache.
All endpoints return real data (zero delay) when the stream agent is running.
"""
from fastapi import APIRouter, Depends, Query
from app.core.security import get_current_user
from app.exchange.paper_trading import PaperTradingEngine

router = APIRouter(prefix="/api/market", tags=["market"])
_engine = PaperTradingEngine()

WATCHLIST = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "ADA/USDT"]


@router.get("/ticker/{symbol:path}")
async def get_ticker(symbol: str, current_user: dict = Depends(get_current_user)):
    ticker = await _engine.fetch_ticker(symbol)
    return {
        "symbol": ticker.symbol,
        "bid": ticker.bid,
        "ask": ticker.ask,
        "last": ticker.last,
        "volume": ticker.volume,
        "change_pct": ticker.change_pct,
    }


@router.get("/tickers")
async def get_tickers(current_user: dict = Depends(get_current_user)):
    """
    Return live tickers for the full watchlist.
    Data comes from the in-memory LIVE_PRICES cache (populated by WebSocket stream).
    """
    from app.agents.live_market_stream import LIVE_PRICES

    results = []
    for symbol in WATCHLIST:
        live = LIVE_PRICES.get(symbol)
        if live and live.get("last", 0) > 0:
            results.append({
                "symbol": live["symbol"],
                "last": live["last"],
                "bid": live["bid"],
                "ask": live["ask"],
                "change_pct": live["change_pct"],
                "volume": live["volume"],
                "quote_volume": live.get("quote_volume", 0),
                "high_24h": live.get("high_24h", 0),
                "low_24h": live.get("low_24h", 0),
                "updated_at": live.get("updated_at"),
                "source": "live_ws",
            })
        else:
            # Fallback to REST while stream warms up
            try:
                ticker = await _engine.fetch_ticker(symbol)
                results.append({
                    "symbol": ticker.symbol,
                    "last": ticker.last,
                    "bid": ticker.bid,
                    "ask": ticker.ask,
                    "change_pct": ticker.change_pct,
                    "volume": ticker.volume,
                    "quote_volume": 0,
                    "high_24h": 0,
                    "low_24h": 0,
                    "updated_at": None,
                    "source": "rest",
                })
            except Exception:
                pass

    return results


@router.get("/candles/{symbol:path}")
async def get_candles(
    symbol: str,
    timeframe: str = Query("1h"),
    limit: int = Query(200, le=1000),
    current_user: dict = Depends(get_current_user),
):
    candles = await _engine.fetch_candles(symbol, timeframe, limit)
    return [
        {
            "timestamp": c.timestamp.isoformat(),
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
        }
        for c in candles
    ]


@router.get("/orderbook/{symbol:path}")
async def get_orderbook(
    symbol: str,
    depth: int = Query(20, le=100),
    current_user: dict = Depends(get_current_user),
):
    from app.agents.live_market_stream import LIVE_ORDERBOOK
    live = LIVE_ORDERBOOK.get(symbol)
    if live:
        return {
            "symbol": live["symbol"],
            "bids": live["bids"][:depth],
            "asks": live["asks"][:depth],
            "timestamp": live["timestamp"],
            "source": "live_ws_100ms",
        }

    ob = await _engine.fetch_order_book(symbol, depth)
    return {
        "symbol": ob.symbol,
        "bids": [{"price": b.price, "amount": b.amount} for b in ob.bids],
        "asks": [{"price": a.price, "amount": a.amount} for a in ob.asks],
        "timestamp": ob.timestamp.isoformat(),
        "source": "rest",
    }


@router.get("/watchlist")
async def get_watchlist(current_user: dict = Depends(get_current_user)):
    return WATCHLIST


@router.get("/signals")
async def get_signals(
    symbol: str = Query("BTC/USDT"),
    limit: int = Query(50, le=200),
    current_user: dict = Depends(get_current_user),
):
    """Return recent strategy signals for a symbol (used for chart overlays)."""
    from app.core.signal_cache import get_signals
    return get_signals(symbol, limit)


@router.get("/stream-status")
async def get_stream_status(current_user: dict = Depends(get_current_user)):
    """Report whether the live WebSocket stream is connected."""
    from app.agents.live_market_stream import get_live_stream_agent, LIVE_PRICES
    agent = get_live_stream_agent()
    live_count = sum(1 for v in LIVE_PRICES.values() if v.get("last", 0) > 0)
    return {
        "connected": agent.connected if agent else False,
        "symbols_live": live_count,
        "watched_symbols": WATCHLIST,
        "prices": {
            sym: LIVE_PRICES.get(sym, {}).get("last", 0) for sym in WATCHLIST
        },
    }
