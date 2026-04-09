"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import {
  Activity, TrendingUp, TrendingDown, BookOpen,
  Wifi, WifiOff, RefreshCw, ChevronUp, ChevronDown, Zap,
} from "lucide-react";
import { marketAPI } from "@/lib/api";
import { formatUSD, formatNumber } from "@/lib/utils";
import {
  useBinanceStream,
  BinanceTicker,
  BinanceCandle,
  BinanceOrderBook,
} from "@/hooks/useBinanceStream";

// ─── Constants ───────────────────────────────────────────────────────────────
const WATCHED_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "ADA/USDT"];
const TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"];
const BAR_W = 6;
const CHART_W = 800;
const CHART_H = 200;
const PADDING = 12;

// ─── Types ────────────────────────────────────────────────────────────────────
interface SignalMarker {
  timestamp: string;
  direction: "long" | "short";
  entry: number;
  sl?: number | null;
  tp?: number | null;
  strategy_name: string;
  confidence: number;
  reasoning: string;
  symbol: string;
  timeframe: string;
}

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
  ticker, selected, onClick, history, hasSignal,
}: {
  ticker: BinanceTicker; selected: boolean; onClick: () => void;
  history: number[]; hasSignal?: "long" | "short" | null;
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
        <div className="text-sm font-semibold text-white truncate flex items-center gap-1.5">
          {ticker.symbol.replace("/USDT", "")}
          <span className="text-neutral-500 font-normal">/USDT</span>
          {hasSignal === "long" && (
            <span className="text-[9px] bg-green-500/20 text-green-400 border border-green-500/30 rounded px-1 py-0.5 font-bold">▲ LONG</span>
          )}
          {hasSignal === "short" && (
            <span className="text-[9px] bg-red-500/20 text-red-400 border border-red-500/30 rounded px-1 py-0.5 font-bold">▼ SHORT</span>
          )}
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

// ─── Candle Chart with Signal Overlays ────────────────────────────────────────
function CandleChart({
  candles, signals,
}: {
  candles: BinanceCandle[];
  signals: SignalMarker[];
}) {
  if (candles.length < 2) return (
    <div className="flex items-center justify-center h-48 text-neutral-500 text-sm">
      Loading chart data...
    </div>
  );

  const visible = candles.slice(-Math.floor(CHART_W / (BAR_W + 2)));
  const prices = visible.flatMap(c => [c.high, c.low]);
  const minP = Math.min(...prices);
  const maxP = Math.max(...prices);
  const range = maxP - minP || 1;
  const toY = (p: number) => PADDING + ((maxP - p) / range) * (CHART_H - PADDING * 2);
  const last = visible[visible.length - 1];
  const chartUp = (last?.close ?? 0) >= (visible[0]?.close ?? 0);

  // Map signal timestamps → candle index
  const signalMap: { idx: number; sig: SignalMarker }[] = [];
  for (const sig of signals) {
    const sigTs = new Date(sig.timestamp).getTime();
    // Find closest candle
    let bestIdx = -1;
    let bestDiff = Infinity;
    visible.forEach((c, i) => {
      const diff = Math.abs(new Date(c.timestamp).getTime() - sigTs);
      if (diff < bestDiff) { bestDiff = diff; bestIdx = i; }
    });
    // Only show if within 2 hours
    if (bestIdx >= 0 && bestDiff < 2 * 60 * 60 * 1000) {
      signalMap.push({ idx: bestIdx, sig });
    }
  }

  // Most recent signal's SL/TP lines
  const latestSig = signalMap[signalMap.length - 1]?.sig ?? null;

  // Up-triangle path pointing up (long): ▲
  const upTriangle = (cx: number, cy: number, size = 6) =>
    `M ${cx} ${cy - size} L ${cx + size} ${cy + size / 2} L ${cx - size} ${cy + size / 2} Z`;
  // Down-triangle path (short): ▼
  const downTriangle = (cx: number, cy: number, size = 6) =>
    `M ${cx} ${cy + size} L ${cx + size} ${cy - size / 2} L ${cx - size} ${cy - size / 2} Z`;

  // Price levels to label (calculated from visible candles)
  const priceLevels = [
    { price: maxP, label: formatUSD(maxP), pct: PADDING / CHART_H },
    { price: (maxP + minP) / 2, label: formatUSD((maxP + minP) / 2), pct: 0.5 },
    { price: minP, label: formatUSD(minP), pct: (CHART_H - PADDING) / CHART_H },
  ];

  return (
    <div className="relative overflow-hidden">
      {/* Y-axis price labels — positioned to match SVG coordinate space */}
      {priceLevels.map(({ label, pct }) => (
        <div
          key={label}
          className="absolute right-1 text-[10px] text-neutral-600 font-mono -translate-y-1/2"
          style={{ top: `${pct * 100}%` }}
        >
          {label}
        </div>
      ))}
      <svg
        viewBox={`0 0 ${CHART_W} ${CHART_H}`}
        className="w-full"
        style={{ height: CHART_H }}
        preserveAspectRatio="none"
      >
        {/* Grid lines */}
        {[0.25, 0.5, 0.75].map(pct => (
          <line key={pct} x1="0" x2={CHART_W}
            y1={PADDING + pct * (CHART_H - PADDING * 2)}
            y2={PADDING + pct * (CHART_H - PADDING * 2)}
            stroke="#262626" strokeWidth="1" />
        ))}

        {/* SL / TP lines for most recent signal */}
        {latestSig?.sl && latestSig.sl > 0 && (
          <>
            <line x1="0" x2={CHART_W} y1={toY(latestSig.sl)} y2={toY(latestSig.sl)}
              stroke="#ef4444" strokeWidth="1" strokeDasharray="6 3" opacity="0.7" />
            <text x={CHART_W - 4} y={toY(latestSig.sl) - 3} textAnchor="end"
              fontSize="9" fill="#ef4444" opacity="0.9">SL</text>
          </>
        )}
        {latestSig?.tp && latestSig.tp > 0 && (
          <>
            <line x1="0" x2={CHART_W} y1={toY(latestSig.tp)} y2={toY(latestSig.tp)}
              stroke="#22c55e" strokeWidth="1" strokeDasharray="6 3" opacity="0.7" />
            <text x={CHART_W - 4} y={toY(latestSig.tp) - 3} textAnchor="end"
              fontSize="9" fill="#22c55e" opacity="0.9">TP</text>
          </>
        )}
        {latestSig?.entry && latestSig.entry > 0 && (
          <>
            <line x1="0" x2={CHART_W} y1={toY(latestSig.entry)} y2={toY(latestSig.entry)}
              stroke="#60a5fa" strokeWidth="1" strokeDasharray="3 3" opacity="0.5" />
            <text x={CHART_W - 4} y={toY(latestSig.entry) - 3} textAnchor="end"
              fontSize="9" fill="#60a5fa" opacity="0.9">ENTRY</text>
          </>
        )}

        {/* Candle bodies */}
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

        {/* Live price line */}
        {last && (
          <line x1="0" x2={CHART_W} y1={toY(last.close)} y2={toY(last.close)}
            stroke={chartUp ? "#22c55e" : "#ef4444"} strokeWidth="1"
            strokeDasharray="4 4" opacity="0.6" />
        )}

        {/* Signal markers (triangles) */}
        {signalMap.map(({ idx, sig }, k) => {
          const cx = idx * (BAR_W + 2) + BAR_W / 2;
          const candle = visible[idx];
          const isLong = sig.direction === "long";
          // Long: arrow below the low, Short: arrow above the high
          const cy = isLong
            ? toY(candle.low) + 10
            : toY(candle.high) - 10;
          const color = isLong ? "#22c55e" : "#ef4444";
          const pathFn = isLong ? upTriangle : downTriangle;

          return (
            <g key={k}>
              {/* Glow circle */}
              <circle cx={cx} cy={cy} r={8} fill={color} opacity={0.15} />
              {/* Triangle */}
              <path d={pathFn(cx, cy, 5)} fill={color} opacity={0.95} />
              {/* Confidence dot */}
              <circle
                cx={cx + 7}
                cy={isLong ? cy - 6 : cy + 6}
                r={2.5}
                fill={sig.confidence > 0.7 ? "#facc15" : color}
                opacity={0.9}
              />
            </g>
          );
        })}
      </svg>

      {/* Signal legend */}
      {signalMap.length > 0 && (
        <div className="absolute bottom-1 left-2 flex items-center gap-3 text-[10px] text-neutral-500">
          <span className="flex items-center gap-1">
            <span className="text-green-400">▲</span> Long signal
          </span>
          <span className="flex items-center gap-1">
            <span className="text-red-400">▼</span> Short signal
          </span>
          <span className="flex items-center gap-1">
            <span className="w-3 h-px bg-yellow-400 inline-block" /> High confidence
          </span>
        </div>
      )}
    </div>
  );
}

// ─── Signal Feed Panel ────────────────────────────────────────────────────────
function SignalFeed({ signals }: { signals: SignalMarker[] }) {
  const sorted = [...signals].reverse().slice(0, 12);
  return (
    <div className="space-y-1.5 max-h-80 overflow-y-auto pr-1">
      {sorted.length === 0 ? (
        <div className="text-xs text-neutral-600 text-center py-6">
          Waiting for strategy signals...
          <div className="text-[10px] mt-1 text-neutral-700">Strategies run every 60s</div>
        </div>
      ) : (
        sorted.map((sig, i) => {
          const isLong = sig.direction === "long";
          const ts = new Date(sig.timestamp);
          const timeStr = ts.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
          return (
            <div
              key={i}
              className={`rounded border p-2 text-xs ${
                isLong
                  ? "border-green-500/20 bg-green-500/5"
                  : "border-red-500/20 bg-red-500/5"
              }`}
            >
              <div className="flex items-center justify-between mb-1">
                <div className="flex items-center gap-1.5">
                  <span className={`font-bold text-[11px] ${isLong ? "text-green-400" : "text-red-400"}`}>
                    {isLong ? "▲ LONG" : "▼ SHORT"}
                  </span>
                  <span className="text-neutral-400 font-mono">
                    {sig.symbol.replace("/USDT", "")}
                  </span>
                  <span className="text-neutral-600">{sig.timeframe}</span>
                </div>
                <span className="text-neutral-600 text-[10px] font-mono">{timeStr}</span>
              </div>
              <div className="text-neutral-500 text-[10px] truncate mb-1">
                {sig.strategy_name}
              </div>
              {sig.entry > 0 && (
                <div className="flex gap-3 font-mono text-[10px]">
                  <span className="text-blue-400">E {formatUSD(sig.entry)}</span>
                  {sig.sl && sig.sl > 0 && <span className="text-red-400">SL {formatUSD(sig.sl)}</span>}
                  {sig.tp && sig.tp > 0 && <span className="text-green-400">TP {formatUSD(sig.tp)}</span>}
                </div>
              )}
              <div className="text-neutral-600 text-[9px] mt-1 leading-tight line-clamp-2">
                {sig.reasoning}
              </div>
              <div className="mt-1 flex items-center gap-1">
                <div className="flex-1 h-0.5 bg-neutral-800 rounded">
                  <div
                    className={`h-full rounded ${sig.confidence > 0.7 ? "bg-yellow-400" : isLong ? "bg-green-500" : "bg-red-500"}`}
                    style={{ width: `${Math.min(sig.confidence * 100, 100)}%` }}
                  />
                </div>
                <span className="text-neutral-600 text-[9px]">{(sig.confidence * 100).toFixed(0)}%</span>
              </div>
            </div>
          );
        })
      )}
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
  const [signals, setSignals] = useState<SignalMarker[]>([]);
  const [latestSignalBySymbol, setLatestSignalBySymbol] = useState<Record<string, "long" | "short" | null>>({});

  // ── Signal polling ─────────────────────────────────────────────────────────
  const fetchSignals = useCallback(async (sym: string) => {
    try {
      const token = localStorage.getItem("token");
      const resp = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL || ""}/api/market/signals?symbol=${encodeURIComponent(sym)}&limit=50`,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      if (resp.ok) {
        const data: SignalMarker[] = await resp.json();
        setSignals(data);
      }
    } catch { /* silent */ }
  }, []);

  // Poll signals for all symbols (to show badges on watchlist)
  const fetchAllSignals = useCallback(async () => {
    const latest: Record<string, "long" | "short" | null> = {};
    for (const sym of WATCHED_SYMBOLS) {
      try {
        const token = localStorage.getItem("token");
        const resp = await fetch(
          `${process.env.NEXT_PUBLIC_API_URL || ""}/api/market/signals?symbol=${encodeURIComponent(sym)}&limit=5`,
          { headers: { Authorization: `Bearer ${token}` } }
        );
        if (resp.ok) {
          const data: SignalMarker[] = await resp.json();
          if (data.length > 0) {
            const last = data[data.length - 1];
            // Only show badge if signal is < 5 minutes old
            const age = Date.now() - new Date(last.timestamp).getTime();
            latest[sym] = age < 5 * 60 * 1000 ? (last.direction as "long" | "short") : null;
          }
        }
      } catch { /* silent */ }
    }
    setLatestSignalBySymbol(latest);
  }, []);

  useEffect(() => {
    fetchSignals(selectedSymbol);
    const t = setInterval(() => fetchSignals(selectedSymbol), 30_000);
    return () => clearInterval(t);
  }, [selectedSymbol, fetchSignals]);

  useEffect(() => {
    fetchAllSignals();
    const t = setInterval(fetchAllSignals, 60_000);
    return () => clearInterval(t);
  }, [fetchAllSignals]);

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
      // Compare by epoch ms — timestamp strings can differ in format
      // (Bybit: "2024-01-15T12:00:00+00:00" vs Binance WS: "2024-01-15T12:00:00.000Z")
      const lastMs = new Date(last.timestamp).getTime();
      const curMs  = new Date(candle.timestamp).getTime();
      if (lastMs === curMs) return [...prev.slice(0, -1), candle];
      if (curMs < lastMs) return prev; // ignore out-of-order older candles
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
      setCandles(data.map((c: Record<string, unknown>) => ({
        timestamp: c.timestamp,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
        volume: c.volume,
        is_closed: (c.is_closed as boolean) ?? true,
      })));
    } catch {
      setCandles([]);
    } finally {
      setLoadingCandles(false);
    }
  }, []);

  useEffect(() => {
    // Clear stale candles immediately so the chart doesn't briefly show
    // the previous symbol's data while new ones are loading
    setCandles([]);
    setOrderBook(null);
    loadCandles(selectedSymbol, timeframe);
  }, [selectedSymbol, timeframe]);

  const selectedTicker = tickers[selectedSymbol];

  // Filter signals for selected symbol + timeframe
  const chartSignals = signals.filter(
    s => s.symbol === selectedSymbol
  );

  return (
    <div className="p-4 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-white">Market Monitor</h1>
          <p className="text-xs text-neutral-500 mt-0.5">
            Direct Binance public feed · zero-latency streaming · strategy signals every 60s
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
        <div className="col-span-2 bg-neutral-900 border border-neutral-800 rounded-lg p-3 space-y-1">
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
                hasSignal={latestSignalBySymbol[sym] ?? null}
              />
            );
          })}
        </div>

        {/* ── Chart + stats ──────────────────────────────────────────────── */}
        <div className="col-span-7 space-y-4">
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
                    {chartSignals.length > 0 && (() => {
                      const last = chartSignals[chartSignals.length - 1];
                      const isLong = last.direction === "long";
                      const age = Date.now() - new Date(last.timestamp).getTime();
                      if (age > 10 * 60 * 1000) return null;
                      return (
                        <span className={`flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded border ${
                          isLong
                            ? "border-green-500/30 bg-green-500/10 text-green-400"
                            : "border-red-500/30 bg-red-500/10 text-red-400"
                        }`}>
                          <Zap size={10} />
                          {isLong ? "▲ LONG" : "▼ SHORT"} · {last.strategy_name}
                        </span>
                      );
                    })()}
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

          {/* Candle chart with signal overlays */}
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
                {chartSignals.length > 0 && (
                  <span className="text-xs text-blue-400 flex items-center gap-1">
                    <Zap size={10} />
                    {chartSignals.length} signal{chartSignals.length !== 1 ? "s" : ""}
                  </span>
                )}
              </div>
              {loadingCandles && <RefreshCw size={12} className="text-neutral-500 animate-spin" />}
            </div>

            <CandleChart candles={candles} signals={chartSignals} />

            {/* Volume bars */}
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

        {/* ── Right panel: Order Book + Signal Feed ────────────────────── */}
        <div className="col-span-3 space-y-4">
          {/* Order Book */}
          <div className="bg-neutral-900 border border-neutral-800 rounded-lg p-3">
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

          {/* Signal Feed */}
          <div className="bg-neutral-900 border border-neutral-800 rounded-lg p-3">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <Zap size={13} className="text-neutral-500" />
                <span className="text-xs font-medium text-neutral-400 uppercase tracking-wider">
                  Strategy Signals
                </span>
              </div>
              {signals.length > 0 && (
                <span className="text-xs bg-blue-500/10 border border-blue-500/20 text-blue-400 px-1.5 py-0.5 rounded font-mono">
                  {signals.length}
                </span>
              )}
            </div>
            <SignalFeed signals={chartSignals} />
          </div>
        </div>
      </div>
    </div>
  );
}
