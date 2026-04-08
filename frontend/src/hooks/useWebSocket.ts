"use client";

import { useEffect, useRef, useCallback, useState } from "react";

const WS_URL = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";

// Flexible message type — backend sends {type, data} or {event, data}
export interface WSMessage {
  type?: string;
  event?: string;
  data?: any;
  [key: string]: any;
}

interface UseWebSocketOptions {
  onMessage?: (msg: WSMessage) => void;
  reconnectInterval?: number;
}

export function useWebSocket({ onMessage, reconnectInterval = 3000 }: UseWebSocketOptions = {}) {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const [lastMessage, setLastMessage] = useState<WSMessage | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const intentionalClose = useRef(false);
  const onMessageRef = useRef(onMessage);

  // Keep ref up to date without re-connecting on callback changes
  useEffect(() => {
    onMessageRef.current = onMessage;
  }, [onMessage]);

  const connect = useCallback(() => {
    if (typeof window === "undefined") return;
    const token = localStorage.getItem("tradeos_token");
    if (!token) return;

    const url = `${WS_URL}/ws?token=${token}`;

    try {
      const ws = new WebSocket(url);
      wsRef.current = ws;

      let pingInterval: ReturnType<typeof setInterval>;

      ws.onopen = () => {
        setConnected(true);
        pingInterval = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) ws.send("ping");
        }, 25000);
      };

      ws.onclose = () => {
        clearInterval(pingInterval);
        setConnected(false);
        if (!intentionalClose.current) {
          reconnectTimer.current = setTimeout(connect, reconnectInterval);
        }
      };

      ws.onmessage = (event) => {
        try {
          const raw = JSON.parse(event.data);
          // Normalise: backend may send {event, data} or {type, data}
          const msg: WSMessage = {
            ...raw,
            type: raw.type ?? raw.event,
            event: raw.event ?? raw.type,
          };
          setLastMessage(msg);
          onMessageRef.current?.(msg);
        } catch {
          // ignore malformed frames
        }
      };

      ws.onerror = () => {
        ws.close();
      };
    } catch {
      // ignore connection errors — will retry
    }
  }, [reconnectInterval]);

  useEffect(() => {
    intentionalClose.current = false;
    connect();
    return () => {
      intentionalClose.current = true;
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  const send = useCallback((data: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(data);
    }
  }, []);

  return { connected, send, lastMessage };
}
