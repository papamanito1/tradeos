"use client";

/**
 * useBinanceStream
 * ─────────────────
 * Connects the browser DIRECTLY to Binance's free public WebSocket streams.
 * No backend relay, no Redis, zero latency.
 *
 * Binance public streams need NO API key and are accessible from browsers.
 * Docs: https://binance-docs.github.io/apidocs/spot/en/#websocket-market-streams
 */

import { useEffect, useRef, useCallback, useState } from "react";

const BINANCE_WS = "wss://stream.binance.com:9443/stream";

export interface BinanceTicker {
  symbol: string;          // "BTC/USDT"
  last: number;
  bid: number;
  ask: number;
  open_24h: number;
  high_24h: number;
  low_24h: number;
  volume: number;          // base volume
  quote_volume: number;    // USDT volume
  change_pct: number;      // 24h % change
  updated_ms: number;
}

export interface BinanceCandle {
  timestamp: string;       // ISO
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  is_closed: boolean;
}

export interface BinanceOrderBookLevel {
  price: number;
  amount: number;
}

export interface BinanceOrderBook {
  symbol: string;
  bids: BinanceOrderBookLevel[];
  asks: BinanceOrderBookLevel[];
  timestamp: string;
}

export interface BinanceAggTrade {
  symbol: string;
  price: number;
  quantity: number;
  is_buyer_maker: boolean; // true = sell (seller is aggressor), false = buy
  timestamp: number;
}

interface UseBinanceStreamOptions {
  symbols: string[];
  timeframe?: string;
  onTicker?: (ticker: BinanceTicker) => void;
  onCandle?: (symbol: string, candle: BinanceCandle) => void;
  onOrderBook?: (ob: BinanceOrderBook) => void;
  onAggTrade?: (trade: BinanceAggTrade) => void;
}

/** "BTC/USDT" → "btcusdt" */
function toBinance(symbol: string): string {
  return symbol.replace("/", "").toLowerCase();
}

/** "BTCUSDT" → "BTC/USDT" */
function fromBinance(raw: string): string {
  const s = raw.toUpperCase();
  for (const q of ["USDT", "BTC", "ETH", "BNB", "BUSD"]) {
    if (s.endsWith(q)) return `${s.slice(0, -q.length)}/${q}`;
  }
  return s;
}

const TF_MAP: Record<string, string> = {
  "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
  "1h": "1h", "4h": "4h", "1d": "1d",
};

export function useBinanceStream({
  symbols,
  timeframe = "1m",
  onTicker,
  onCandle,
  onOrderBook,
  onAggTrade,
}: UseBinanceStreamOptions) {
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const retryDelay = useRef(1000);
  const intentionalClose = useRef(false);

  const onTickerRef    = useRef(onTicker);
  const onCandleRef    = useRef(onCandle);
  const onOrderBookRef = useRef(onOrderBook);
  const onAggTradeRef  = useRef(onAggTrade);
  useEffect(() => { onTickerRef.current    = onTicker;    }, [onTicker]);
  useEffect(() => { onCandleRef.current    = onCandle;    }, [onCandle]);
  useEffect(() => { onOrderBookRef.current = onOrderBook; }, [onOrderBook]);
  useEffect(() => { onAggTradeRef.current  = onAggTrade;  }, [onAggTrade]);

  const connect = useCallback(() => {
    if (typeof window === "undefined" || !symbols.length) return;

    // Build stream list
    const tf = TF_MAP[timeframe] ?? "1m";
    const streams: string[] = [];
    for (const sym of symbols) {
      const b = toBinance(sym);
      streams.push(`${b}@ticker`);
      streams.push(`${b}@kline_${tf}`);
      streams.push(`${b}@depth20@100ms`);
      if (onAggTradeRef.current) streams.push(`${b}@aggTrade`);
    }

    const url = `${BINANCE_WS}?streams=${streams.join("/")}`;

    try {
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        retryDelay.current = 1000;
      };

      ws.onclose = () => {
        setConnected(false);
        wsRef.current = null;
        if (!intentionalClose.current) {
          retryTimer.current = setTimeout(() => {
            retryDelay.current = Math.min(retryDelay.current * 1.5, 30000);
            connect();
          }, retryDelay.current);
        }
      };

      ws.onerror = () => ws.close();

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data as string);
          const stream: string = msg.stream ?? "";
          const data = msg.data ?? msg;
          const eventType: string = data.e ?? "";

          // ── Ticker ───────────────────────────────────────────────────────────
          if (eventType === "24hrTicker") {
            const symbol = fromBinance(data.s ?? "");
            onTickerRef.current?.({
              symbol,
              last: parseFloat(data.c ?? "0"),
              bid: parseFloat(data.b ?? "0"),
              ask: parseFloat(data.a ?? "0"),
              open_24h: parseFloat(data.o ?? "0"),
              high_24h: parseFloat(data.h ?? "0"),
              low_24h: parseFloat(data.l ?? "0"),
              volume: parseFloat(data.v ?? "0"),
              quote_volume: parseFloat(data.q ?? "0"),
              change_pct: parseFloat(data.P ?? "0"),
              updated_ms: Date.now(),
            });
          }

          // ── Kline / candle ───────────────────────────────────────────────────
          else if (eventType === "kline") {
            const k = data.k ?? {};
            const symbol = fromBinance(k.s ?? data.s ?? "");
            onCandleRef.current?.(symbol, {
              timestamp: new Date(k.t ?? 0).toISOString(),
              open: parseFloat(k.o ?? "0"),
              high: parseFloat(k.h ?? "0"),
              low: parseFloat(k.l ?? "0"),
              close: parseFloat(k.c ?? "0"),
              volume: parseFloat(k.v ?? "0"),
              is_closed: k.x ?? false,
            });
          }

          // ── Agg trades ───────────────────────────────────────────────────────
          else if (eventType === "aggTrade") {
            const symbol = fromBinance(data.s ?? "");
            onAggTradeRef.current?.({
              symbol,
              price:           parseFloat(data.p ?? "0"),
              quantity:        parseFloat(data.q ?? "0"),
              is_buyer_maker:  data.m ?? false,
              timestamp:       data.T ?? Date.now(),
            });
          }

          // ── Depth / order book ───────────────────────────────────────────────
          else if (stream.includes("@depth")) {
            const rawSym = stream.split("@")[0];   // "btcusdt"
            const symbol = fromBinance(rawSym.replace("usdt", "USDT").toUpperCase());
            const bids = (data.bids ?? []).slice(0, 20).map((b: string[]) => ({
              price: parseFloat(b[0]),
              amount: parseFloat(b[1]),
            }));
            const asks = (data.asks ?? []).slice(0, 20).map((a: string[]) => ({
              price: parseFloat(a[0]),
              amount: parseFloat(a[1]),
            }));
            onOrderBookRef.current?.({
              symbol,
              bids,
              asks,
              timestamp: new Date().toISOString(),
            });
          }
        } catch {
          // ignore parse errors
        }
      };
    } catch {
      // ignore connection errors — will retry
    }
  }, [symbols.join(","), timeframe]); // reconnect when symbols or timeframe change

  useEffect(() => {
    intentionalClose.current = false;
    connect();
    return () => {
      intentionalClose.current = true;
      if (retryTimer.current) clearTimeout(retryTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return { connected };
}
