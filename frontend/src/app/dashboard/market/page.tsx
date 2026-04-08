"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import {
  Activity, TrendingUp, TrendingDown, BookOpen,
  Wifi, WifiOff, RefreshCw, ChevronUp, ChevronDown,
} from "lucide-react";
import { marketAPI } from "@/lib/api";
import { formatUSD, formatNumber } from "@/lib/utils";
import {
  useBinanceStream,
  BinanceTicker,
  BinanceCandle,
  BinanceOrderBook,
} from "@/hooks/useBinanceStream";

// ─── Types ────────────────────────────────────────────────────────────────────
const WATCHED_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "ADA/USDT"];
const TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"];

// ─── Mini sparkline ───────────────────────────────────────────────────────────
function Sparkline({ prices, up }: { prices: number[]; up: boolean }) {
  if (prices.length < 2) return null;
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const range = max - min || 1;
  const W = 80, H = 24;
  const pts = prices.map((p, i) => {
    const x = (i / (prices.length - 1)) * W;
    const y = H - ((p - min) / range) * H;
    return `${x},${y}`;
  }).join(" ");
  return (
    <svg width={W} height={H} className="opacity-80">
      <polyline points={pts} fill="none" stroke={up ? "#22c55e" : "#ef4444"} strokeWidth="1.5" />
    </svg>
  );
}

// ─── Price flash ──────────────────────────────────────────────────────────────
function useFlash(value: number) {
  const [flash, setFlash] = useState<"up" | "down" | null>(null);
  const prev = useRef(value);
  useEffect(() => {
    if (value === prev.current) return;
    setFlash(value > prev.current ? "up" : "down");
    prev.current = value;
    const t = setTimeout(() => setFlash(null), 400);
    return () => clearTimeout(t);
  }, [value]);
  return flash;
}

// ─── Watchlist Row ────────────────────────────────────────────────────────────
function WatchlistRow({
  ticker, selected, onClick, history,
}: {
  ticker: BinanceTicker; selected: boolean; onClick: () => void; history: number[];
}) {
  const flash = useFlash(ticker.last);
  const up = ticker.change_pct >= 0;
  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-3 py-2.5 rounded flex items-center gap-3 transition-all border ${
        selected ? "border-blue-500/40 bg-blue-500/10" : "border-transparent hover:border-neutral-700 hover:bg-neutral-800"
      }`}
    >
      <div className="flex-1 min-w-0">
        <div className="text-sm font-semibold text-white truncate">
          {ticker.symbol.replace("/USDT", "")}
          <span className="text-neutral-500 font-normal">/USDT</span>
        </div>
        <div className="text-xs text-neutral-500 mt-0.5">
          Vol {formatNumber(ticker.quote_volume / 1_000_000)}M
        </div>
      </div>
      <Sparkline prices={history} up={up} />
      <div className="text-right min-w-[90px]">
        <div className={`text-sm font-mono font-semibold transition-colors duration-300 ${
          flash === "up" ? "text-green-400" : flash === "down" ? "text-red-400" : "text-white"
        }`}>
          {formatUSD(ticker.last)}
        </div>
        <div className={`text-xs font-mono mt-0.5 flex items-center justify-end gap-0.5 ${
          up ? "text-green-400" : "text-red-400"
        }`}>
          {up ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
          {Math.abs(ticker.change_pct).toFixed(2)}%
        </div>
      </div>
    </button>
  );
}

// ─── Order Book ───────────────────────────────────────────────────────────────
function OrderBookPanel({ ob }: { ob: BinanceOrderBook | null }) {
  if (!ob) return (
    <div className="flex items-center justify-center h-40 text-neutral-500 text-sm">
      Connecting to order book...
    </div>
  );
  const maxSize = Math.max(...ob.bids.map(b => b.amount), ...ob.asks.map(a => a.amount)) || 1;
  const spread = ob.asks.length > 0 && ob.bids.length > 0 ? ob.asks[0].price - ob.bids[0].price : 0;

  const Row = ({ level, side }: { level: { price: number; amount: number }; side: "bid" | "ask" }) => {
    const pct = Math.min((level.amount / maxSize) * 100, 100);
    const isAsk = side === "ask";
    return (
      <div className="relative flex items-center text-xs font-mono h-5 overflow-hidden">
        <div
          className={`absolute inset-y-0 ${isAsk ? "right-0 bg-red-500/10" : "left-0 bg-green-500/10"}`}
          style={{ width: `${pct}%` }}
        />
        <span className={`relative z-10 flex-1 ${isAsk ? "text-red-400" : "text-green-400"}`}>
          {formatUSD(level.price)}
        </span>
        <span className="relative z-10 text-neutral-400 tabular-nums">{level.amount.toFixed(4)}</span>
      </div>
    );
  };

  return (
    <div className="space-y-0.5">
      <div className="space-y-0.5">
        {[...ob.asks].reverse().map((a, i) => <Row key={i} level={a} side="ask" />)}
      </div>
      <div className="py-1 text-center text-xs text-neutral-500 border-y border-neutral-800">
        Spread {formatUSD(spread)} ({ob.bids.length > 0 ? ((spread / ob.bids[0].price) * 100).toFixed(3) : "0.000"}%)
      </div>
      <div className="space-y-0.5">
        {ob.bids.map((b, i) => <Row key={i} level={b} side="bid" />)}
      </div>
    </div>
  );
}

// ─── Candle Chart ─────────────────────────────────────────────────────────────
function CandleChart({ candles }: { candles: BinanceCandle[] }) {
  if (candles.length < 2) return (
    <div className="flex items-center justify-center h-48 text-neutral-500 text-sm">
      Loading chart data...
    </div>
  );
  const W = 800, H = 200, BAR_W = 6, PADDING = 12;
  const visible = candles.slice(-Math.floor(W / (BAR_W + 2)));
  const prices = visible.flatMap(c => [c.high, c.low]);
  const minP = Math.min(...prices);
  const maxP = Math.max(...prices);
  const range = maxP - minP || 1;
  const toY = (p: number) => PADDING + ((maxP - p) / range) * (H - PADDING * 2);
  const last = visible[visible.length - 1];
  const chartUp = (last?.close ?? 0) >= (visible[0]?.close ?? 0);

  return (
    <div className="relative overflow-hidden">
      <div className="absolute right-0 top-0 bottom-0 flex flex-col justify-between text-xs text-neutral-600 font-mono pr-1 py-3">
        <span>{formatUSD(maxP)}</span>
        <span>{formatUSD((maxP + minP) / 2)}</span>
        <span>{formatUSD(minP)}</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: H }} preserveAspectRatio="none">
        {[0.25, 0.5, 0.75].map(pct => (
          <line key={pct} x1="0" x2={W}
            y1={PADDING + pct * (H - PADDING * 2)} y2={PADDING + pct * (H - PADDING * 2)}
            stroke="#262626" strokeWidth="1" />
        ))}
        {visible.map((c, i) => {
          const x = i * (BAR_W + 2);
          const up = c.close >= c.open;
          const color = up ? "#22c55e" : "#ef4444";
          const bodyTop = toY(Math.max(c.open, c.close));
          const bodyBot = toY(Math.min(c.open, c.close));
          const bodyH = Math.max(bodyBot - bodyTop, 1);
          return (
            <g key={i}>
              <line x1={x + BAR_W / 2} x2={x + BAR_W / 2} y1={toY(c.high)} y2={toY(c.low)}
                stroke={color} strokeWidth="1" opacity="0.6" />
              <rect x={x} y={bodyTop} width={BAR_W} height={bodyH} fill={color}
                fillOpacity={c.is_closed === false ? 0.5 : 0.85} />
            </g>
          );
        })}
        {last && (
          <line x1="0" x2={W} y1={toY(last.close)} y2={toY(last.close)}
            stroke={chartUp ? "#22c55e" : "#ef4444"} strokeWidth="1"
            strokeDasharray="4 4" opacity="0.6" />
        )}
      </svg>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────
export default function MarketPage() {
  const [tickers, setTickers] = useState<Record<string, BinanceTicker>>({});
  const [priceHistory, setPriceHistory] = useState<Record<string, number[]>>({});
  const [selectedSymbol, setSelectedSymbol] = useState("BTC/USDT");
  const [candles, setCandles] = useState<BinanceCandle[]>([]);
  const [orderBook, setOrderBook] = useState<BinanceOrderBook | null>(null);
  const [timeframe, setTimeframe] = useState("1m");
  const [loadingCandles, setLoadingCandles] = useState(false);

  // ── Handlers ───────────────────────────────────────────────────────────────
  const handleTicker = useCallback((ticker: BinanceTicker) => {
    setTickers(prev => ({ ...prev, [ticker.symbol]: ticker }));
    setPriceHistory(prev => {
      const hist = [...(prev[ticker.symbol] ?? []), ticker.last];
      return { ...prev, [ticker.symbol]: hist.slice(-60) };
    });
  }, []);

  const handleCandle = useCallback((symbol: string, candle: BinanceCandle) => {
    if (symbol !== selectedSymbol) return;
    setCandles(prev => {
      if (!prev.length) return [candle];
      const last = prev[prev.length - 1];
      if (last.timestamp === candle.timestamp) return [...prev.slice(0, -1), candle];
      return [...prev.slice(-499), candle];
    });
  }, [selectedSymbol]);

  const handleOrderBook = useCallback((ob: BinanceOrderBook) => {
    if (ob.symbol === selectedSymbol) setOrderBook(ob);
  }, [selectedSymbol]);

  // ── Direct Binance stream (zero-latency) ───────────────────────────────────
  const { connected } = useBinanceStream({
    symbols: WATCHED_SYMBOLS,
    timeframe,
    onTicker: handleTicker,
    onCandle: handleCandle,
    onOrderBook: handleOrderBook,
  });

  // ── Load historical candles via REST when symbol/timeframe changes ──────────
  const loadCandles = useCallback(async (symbol: string, tf: string) => {
    setLoadingCandles(true);
    try {
      const data = await marketAPI.getCandles(symbol, tf, 200);
      // Convert from backend format if needed
      setCandles(data.map((c: any) => ({
        timestamp: c.timestamp,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
        volume: c.volume,
        is_closed: c.is_closed ?? true,
      })));
    } catch {
      setCandles([]);
    } finally {
      setLoadingCandles(false);
    }
  }, []);

  useEffect(() => {
    loadCandles(selectedSymbol, timeframe);
    setOrderBook(null); // clear stale book on symbol change
  }, [selectedSymbol, timeframe]);

  const selectedTicker = tickers[selectedSymbol];

  return (
    <div className="p-4 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-white">Market Monitor</h1>
          <p className="text-xs text-neutral-500 mt-0.5">
            Direct Binance public feed · zero-latency streaming
          </p>
        </div>

        <div className={`flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-medium border ${
          connected
            ? "border-green-500/30 bg-green-500/10 text-green-400"
            : "border-yellow-500/30 bg-yellow-500/10 text-yellow-400"
        }`}>
          {connected ? (
            <>
              <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
              <Wifi size={12} />
              Live · Binance Direct
            </>
          ) : (
            <>
              <WifiOff size={12} />
              Connecting to Binance...
            </>
          )}
        </div>
      </div>

      <div className="grid grid-cols-12 gap-4">
        {/* ── Watchlist ─────────────────────────────────────────────────── */}
        <div className="col-span-3 bg-neutral-900 border border-neutral-800 rounded-lg p-3 space-y-1">
          <div className="flex items-center gap-2 mb-3">
            <Activity size={13} className="text-neutral-500" />
            <span className="text-xs font-medium text-neutral-400 uppercase tracking-wider">Watchlist</span>
          </div>
          {WATCHED_SYMBOLS.map(sym => {
            const t = tickers[sym];
            if (!t) return (
              <div key={sym} className="px-3 py-2.5 text-sm text-neutral-600 animate-pulse">{sym}</div>
            );
            return (
              <WatchlistRow
                key={sym}
                ticker={t}
                selected={selectedSymbol === sym}
                onClick={() => setSelectedSymbol(sym)}
                history={priceHistory[sym] ?? []}
              />
            );
          })}
        </div>

        {/* ── Chart + stats ──────────────────────────────────────────────── */}
        <div className="col-span-6 space-y-4">
          <div className="bg-neutral-900 border border-neutral-800 rounded-lg p-4">
            <div className="flex items-start justify-between mb-4">
              <div>
                <h2 className="text-2xl font-bold text-white">
                  {selectedSymbol.replace("/USDT", "")}
                  <span className="text-neutral-500 font-normal">/USDT</span>
                </h2>
                {selectedTicker && (
                  <div className="flex items-center gap-4 mt-2">
                    <span className="text-3xl font-mono font-semibold text-white">
                      {formatUSD(selectedTicker.last)}
                    </span>
                    <span className={`flex items-center gap-1 text-sm font-medium px-2 py-0.5 rounded ${
                      selectedTicker.change_pct >= 0 ? "bg-green-500/10 text-green-400" : "bg-red-500/10 text-red-400"
                    }`}>
                      {selectedTicker.change_pct >= 0 ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
                      {selectedTicker.change_pct >= 0 ? "+" : ""}{selectedTicker.change_pct.toFixed(2)}%
                    </span>
                  </div>
                )}
              </div>

              {/* Timeframe selector */}
              <div className="flex gap-1 bg-neutral-800 rounded p-1">
                {TIMEFRAMES.map(tf => (
                  <button
                    key={tf}
                    onClick={() => setTimeframe(tf)}
                    className={`px-2 py-0.5 text-xs rounded transition-all ${
                      timeframe === tf ? "bg-blue-600 text-white" : "text-neutral-400 hover:text-white"
                    }`}
                  >
                    {tf}
                  </button>
                ))}
              </div>
            </div>

            {selectedTicker && (
              <div className="grid grid-cols-4 gap-3 text-xs border-t border-neutral-800 pt-3">
                <div>
                  <div className="text-neutral-500">24h High</div>
                  <div className="text-green-400 font-mono font-medium mt-0.5">{formatUSD(selectedTicker.high_24h)}</div>
                </div>
                <div>
                  <div className="text-neutral-500">24h Low</div>
                  <div className="text-red-400 font-mono font-medium mt-0.5">{formatUSD(selectedTicker.low_24h)}</div>
                </div>
                <div>
                  <div className="text-neutral-500">Bid</div>
                  <div className="text-white font-mono mt-0.5">{formatUSD(selectedTicker.bid)}</div>
                </div>
                <div>
                  <div className="text-neutral-500">Ask</div>
                  <div className="text-white font-mono mt-0.5">{formatUSD(selectedTicker.ask)}</div>
                </div>
              </div>
            )}
          </div>

          {/* Candle chart */}
          <div className="bg-neutral-900 border border-neutral-800 rounded-lg p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium text-neutral-400 uppercase tracking-wider">
                  {selectedSymbol} · {timeframe}
                </span>
                {connected && (
                  <span className="text-xs text-green-400 flex items-center gap-1">
                    <span className="w-1 h-1 rounded-full bg-green-400 animate-pulse" />
                    live
                  </span>
                )}
              </div>
              {loadingCandles && <RefreshCw size={12} className="text-neutral-500 animate-spin" />}
            </div>

            <CandleChart candles={candles} />

            {candles.length > 0 && (
              <div className="mt-2 flex items-end gap-0.5 h-8">
                {candles.slice(-80).map((c, i) => {
                  const maxVol = Math.max(...candles.slice(-80).map(x => x.volume));
                  const pct = (c.volume / maxVol) * 100;
                  return (
                    <div
                      key={i}
                      className={`flex-1 min-w-0 rounded-t ${c.close >= c.open ? "bg-green-500/30" : "bg-red-500/30"}`}
                      style={{ height: `${Math.max(pct, 2)}%` }}
                    />
                  );
                })}
              </div>
            )}
          </div>
        </div>

        {/* ── Order Book ────────────────────────────────────────────────── */}
        <div className="col-span-3 bg-neutral-900 border border-neutral-800 rounded-lg p-3">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <BookOpen size={13} className="text-neutral-500" />
              <span className="text-xs font-medium text-neutral-400 uppercase tracking-wider">Order Book</span>
            </div>
            {orderBook && (
              <span className="text-xs text-green-400 flex items-center gap-1">
                <span className="w-1 h-1 rounded-full bg-green-400 animate-pulse" />
                100ms
              </span>
            )}
          </div>
          <div className="flex justify-between text-xs text-neutral-600 mb-1 font-mono">
            <span>Price (USDT)</span>
            <span>Amount</span>
          </div>
          <OrderBookPanel ob={orderBook} />
        </div>
      </div>
    </div>
  );
}
