"use client";

/**
 * useBingXStream
 * ──────────────
 * Polls BingX's public REST API for perpetual swap market data.
 * No API key required — all endpoints are publicly accessible.
 *
 * BingX swap REST endpoints used:
 *   /openApi/swap/v2/quote/ticker   → 24h ticker stats
 *   /openApi/swap/v3/quote/klines   → OHLCV candles
 *   /openApi/swap/v2/quote/depth    → Order book levels
 *
 * Polls every ~2 seconds for near-real-time updates.
 */

import { useEffect, useRef, useState } from "react";

const BINGX_BASE = "https://open-api.bingx.com";

export interface BingXTicker {
  symbol: string;
  last: number;
  bid: number;
  ask: number;
  open_24h: number;
  high_24h: number;
  low_24h: number;
  volume: number;
  quote_volume: number;
  change_pct: number;
  updated_ms: number;
}

export interface BingXCandle {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  is_closed: boolean;
}

export interface BingXOrderBookLevel {
  price: number;
  amount: number;
}

export interface BingXOrderBook {
  symbol: string;
  bids: BingXOrderBookLevel[];
  asks: BingXOrderBookLevel[];
  timestamp: string;
}

export interface BingXAggTrade {
  symbol: string;
  price: number;
  quantity: number;
  is_buyer_maker: boolean;
  timestamp: number;
}

// Re-export under Binance names so existing strategy hooks work without changes
export type BinanceTicker        = BingXTicker;
export type BinanceCandle        = BingXCandle;
export type BinanceOrderBookLevel = BingXOrderBookLevel;
export type BinanceOrderBook     = BingXOrderBook;
export type BinanceAggTrade      = BingXAggTrade;

interface UseBingXStreamOptions {
  symbols: string[];
  timeframe?: string;
  onTicker?: (ticker: BingXTicker) => void;
  onCandle?: (symbol: string, candle: BingXCandle) => void;
  onOrderBook?: (ob: BingXOrderBook) => void;
  onAggTrade?: (trade: BingXAggTrade) => void;
}

/** "BTC/USDT" → "BTC-USDT" */
function toBingX(symbol: string): string {
  return symbol.replace("/", "-");
}

const TF_MAP: Record<string, string> = {
  "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
  "1h": "1h", "4h": "4h", "1d": "1d",
};

function sleep(ms: number): Promise<void> {
  return new Promise(r => setTimeout(r, ms));
}

export function useBingXStream({
  symbols,
  timeframe = "1m",
  onTicker,
  onCandle,
  onOrderBook,
  onAggTrade,
}: UseBingXStreamOptions) {
  const [connected, setConnected] = useState(false);
  const activeRef = useRef(true);

  const onTickerRef    = useRef(onTicker);
  const onCandleRef    = useRef(onCandle);
  const onOrderBookRef = useRef(onOrderBook);
  const onAggTradeRef  = useRef(onAggTrade);
  useEffect(() => { onTickerRef.current    = onTicker;    }, [onTicker]);
  useEffect(() => { onCandleRef.current    = onCandle;    }, [onCandle]);
  useEffect(() => { onOrderBookRef.current = onOrderBook; }, [onOrderBook]);
  useEffect(() => { onAggTradeRef.current  = onAggTrade;  }, [onAggTrade]);

  useEffect(() => {
    if (typeof window === "undefined" || !symbols.length) return;

    activeRef.current = true;
    const tf = TF_MAP[timeframe] ?? "1m";
    let prevCandleTs: Record<string, string> = {};

    async function pollTicker() {
      while (activeRef.current) {
        for (const sym of symbols) {
          if (!activeRef.current) break;
          try {
            const r = await fetch(
              `${BINGX_BASE}/openApi/swap/v2/quote/ticker?symbol=${toBingX(sym)}`
            );
            if (r.ok) {
              const json = await r.json();
              if (json.code === 0 && json.data) {
                const d = json.data;
                setConnected(true);
                onTickerRef.current?.({
                  symbol: sym,
                  last:         parseFloat(d.lastPrice         || "0"),
                  bid:          parseFloat(d.bidPrice          || "0"),
                  ask:          parseFloat(d.askPrice          || "0"),
                  open_24h:     parseFloat(d.openPrice         || "0"),
                  high_24h:     parseFloat(d.highPrice         || "0"),
                  low_24h:      parseFloat(d.lowPrice          || "0"),
                  volume:       parseFloat(d.volume            || "0"),
                  quote_volume: parseFloat(d.quoteVolume       || "0"),
                  change_pct:   parseFloat(d.priceChangePercent || "0"),
                  updated_ms:   Date.now(),
                });
              }
            }
          } catch { /* network error — retry next cycle */ }
        }
        await sleep(2000);
      }
    }

    async function pollKlines() {
      while (activeRef.current) {
        for (const sym of symbols) {
          if (!activeRef.current) break;
          try {
            const r = await fetch(
              `${BINGX_BASE}/openApi/swap/v3/quote/klines?symbol=${toBingX(sym)}&interval=${tf}&limit=2`
            );
            if (r.ok) {
              const json = await r.json();
              if (json.code === 0 && json.data) {
                const sorted = [...json.data].sort(
                  (a: Record<string, unknown>, b: Record<string, unknown>) =>
                    (a.time as number) - (b.time as number)
                );
                for (const k of sorted) {
                  const ts = new Date(k.time as number).toISOString();
                  const prev = prevCandleTs[sym];
                  const isClosed = prev !== undefined && prev !== ts && prev < ts;
                  prevCandleTs[sym] = ts;

                  const candle: BingXCandle = {
                    timestamp: ts,
                    open:   parseFloat((k.open   as string) || "0"),
                    high:   parseFloat((k.high   as string) || "0"),
                    low:    parseFloat((k.low    as string) || "0"),
                    close:  parseFloat((k.close  as string) || "0"),
                    volume: parseFloat((k.volume as string) || "0"),
                    is_closed: isClosed,
                  };
                  onCandleRef.current?.(sym, candle);
                }
              }
            }
          } catch { /* retry next cycle */ }
        }
        await sleep(2000);
      }
    }

    async function pollDepth() {
      while (activeRef.current) {
        for (const sym of symbols) {
          if (!activeRef.current) break;
          try {
            const r = await fetch(
              `${BINGX_BASE}/openApi/swap/v2/quote/depth?symbol=${toBingX(sym)}&limit=20`
            );
            if (r.ok) {
              const json = await r.json();
              if (json.code === 0 && json.data) {
                const d = json.data;
                onOrderBookRef.current?.({
                  symbol: sym,
                  bids: (d.bids || []).map((b: string[]) => ({
                    price: parseFloat(b[0]),
                    amount: parseFloat(b[1]),
                  })),
                  asks: (d.asks || []).map((a: string[]) => ({
                    price: parseFloat(a[0]),
                    amount: parseFloat(a[1]),
                  })),
                  timestamp: new Date().toISOString(),
                });
              }
            }
          } catch { /* retry next cycle */ }
        }
        await sleep(2000);
      }
    }

    pollTicker();
    pollKlines();
    if (onOrderBookRef.current) pollDepth();

    return () => {
      activeRef.current = false;
      setConnected(false);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbols.join(","), timeframe]);

  return { connected };
}

// ─── BingX candle seeder (replaces Binance/Bybit seedCandles) ─────────────────

export async function seedBingXCandles(
  tf: "1m" | "15m",
  limit: number = 300,
): Promise<BingXCandle[]> {
  const interval = TF_MAP[tf] ?? tf;
  const bingxSym = "BTC-USDT";

  // Primary: BingX perpetual swap klines
  try {
    const r = await fetch(
      `${BINGX_BASE}/openApi/swap/v3/quote/klines?symbol=${bingxSym}&interval=${interval}&limit=${limit}`
    );
    if (r.ok) {
      const json = await r.json();
      if (json.code === 0 && json.data && json.data.length > 0) {
        return json.data
          .sort((a: Record<string, unknown>, b: Record<string, unknown>) =>
            (a.time as number) - (b.time as number)
          )
          .map((k: Record<string, string | number>) => ({
            timestamp: new Date(k.time as number).toISOString(),
            open:      parseFloat((k.open   as string) || "0"),
            high:      parseFloat((k.high   as string) || "0"),
            low:       parseFloat((k.low    as string) || "0"),
            close:     parseFloat((k.close  as string) || "0"),
            volume:    parseFloat((k.volume as string) || "0"),
            is_closed: true,
          }));
      }
    }
  } catch { /* fall through to fallback */ }

  // Fallback: Bybit linear
  try {
    const bybitInterval = tf === "1m" ? "1" : "15";
    const r = await fetch(
      `https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=${bybitInterval}&limit=${limit}`
    );
    if (r.ok) {
      const json = await r.json();
      const list: string[][] = json?.result?.list ?? [];
      if (list.length > 0) {
        return [...list].reverse().map(k => ({
          timestamp: new Date(parseInt(k[0])).toISOString(),
          open: parseFloat(k[1]), high: parseFloat(k[2]),
          low:  parseFloat(k[3]), close: parseFloat(k[4]),
          volume: parseFloat(k[5]), is_closed: true,
        }));
      }
    }
  } catch { /* ignore */ }

  return [];
}
