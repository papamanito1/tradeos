import axios, { AxiosInstance } from "axios";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "https://tradeos-production-8f21.up.railway.app";

function createApiClient(): AxiosInstance {
  const client = axios.create({ baseURL: API_URL });

  client.interceptors.request.use((config) => {
    const token =
      typeof window !== "undefined" ? localStorage.getItem("tradeos_token") : null;
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  });

  client.interceptors.response.use(
    (res) => res,
    (err) => {
      if (err.response?.status === 401) {
        if (typeof window !== "undefined") {
          localStorage.removeItem("tradeos_token");
          window.location.href = "/login";
        }
      }
      return Promise.reject(err);
    }
  );

  return client;
}

export const api = createApiClient();

// ── Auth ──────────────────────────────────────────────────────────────────────
export const authApi = {
  login: async (username: string, password: string) => {
    const form = new FormData();
    form.append("username", username);
    form.append("password", password);
    const res = await api.post("/api/auth/token", form);
    return res.data;
  },
  me: async () => {
    const res = await api.get("/api/auth/me");
    return res.data;
  },
};

// ── Overview ──────────────────────────────────────────────────────────────────
export const overviewApi = {
  get: async () => {
    const res = await api.get("/api/overview/");
    return res.data;
  },
};

// ── Strategies ────────────────────────────────────────────────────────────────
export const strategiesApi = {
  list: async () => (await api.get("/api/strategies/")).data,
  types: async () => (await api.get("/api/strategies/types")).data,
  create: async (data: unknown) => (await api.post("/api/strategies/", data)).data,
  update: async (id: number, data: unknown) =>
    (await api.patch(`/api/strategies/${id}`, data)).data,
  delete: async (id: number) => (await api.delete(`/api/strategies/${id}`)).data,
};

// ── Positions ─────────────────────────────────────────────────────────────────
export const positionsApi = {
  list: async (openOnly = true) =>
    (await api.get(`/api/positions/?open_only=${openOnly}`)).data,
  close: async (id: number, reducePct = 100) =>
    (await api.post(`/api/positions/${id}/close`, { reduce_pct: reducePct })).data,
};

// ── Orders ────────────────────────────────────────────────────────────────────
export const ordersApi = {
  list: async (params?: { status?: string; symbol?: string; limit?: number }) =>
    (await api.get("/api/orders/", { params })).data,
  placeManual: async (data: unknown) =>
    (await api.post("/api/orders/manual", data)).data,
  cancel: async (id: number) => (await api.post(`/api/orders/${id}/cancel`)).data,
};

// ── Risk ──────────────────────────────────────────────────────────────────────
export const riskApi = {
  getSettings: async () => (await api.get("/api/risk/settings")).data,
  updateSettings: async (data: unknown) =>
    (await api.patch("/api/risk/settings", data)).data,
  toggleKillSwitch: async (active: boolean) =>
    (await api.post(`/api/risk/kill-switch?active=${active}`)).data,
  getStatus: async () => (await api.get("/api/risk/status")).data,
};

// ── Market ────────────────────────────────────────────────────────────────────
export const marketApi = {
  tickers: async () => (await api.get("/api/market/tickers")).data,
  ticker: async (symbol: string) =>
    (await api.get(`/api/market/ticker/${encodeURIComponent(symbol)}`)).data,
  candles: async (symbol: string, timeframe = "1h", limit = 200) =>
    (await api.get(`/api/market/candles/${encodeURIComponent(symbol)}`, {
      params: { timeframe, limit },
    })).data,
  orderbook: async (symbol: string, depth = 20) =>
    (await api.get(`/api/market/orderbook/${encodeURIComponent(symbol)}`, {
      params: { depth },
    })).data,
  watchlist: async () => (await api.get("/api/market/watchlist")).data,
  getStreamStatus: async () => (await api.get("/api/market/stream-status")).data,
};

// Alias with consistent naming used in market page
export const marketAPI = {
  getTickers: () => marketApi.tickers(),
  getTicker: (symbol: string) => marketApi.ticker(symbol),
  getCandles: (symbol: string, timeframe = "1h", limit = 200) =>
    marketApi.candles(symbol, timeframe, limit),
  getOrderBook: (symbol: string, depth = 20) =>
    marketApi.orderbook(symbol, depth),
  getWatchlist: () => marketApi.watchlist(),
  getStreamStatus: () => marketApi.getStreamStatus(),
};

// ── Backtest ──────────────────────────────────────────────────────────────────
export const backtestApi = {
  run: async (data: unknown) => (await api.post("/api/backtest/run", data)).data,
  getStrategyParams: async (strategyType: string) =>
    (await api.get(`/api/backtest/strategy-params/${strategyType}`)).data,
};

// ── Journal ───────────────────────────────────────────────────────────────────
export const journalApi = {
  list: async (params?: {
    entry_type?: string;
    level?: string;
    symbol?: string;
    search?: string;
    limit?: number;
    offset?: number;
  }) => (await api.get("/api/journal/", { params })).data,
  addManual: async (data: unknown) =>
    (await api.post("/api/journal/manual", data)).data,
};

// ── Settings ──────────────────────────────────────────────────────────────────
export const settingsApi = {
  get: async () => (await api.get("/api/settings/")).data,
  setTradingMode: async (mode: string, confirmed = false) =>
    (await api.post(`/api/settings/trading-mode?mode=${mode}&confirmed=${confirmed}`)).data,
};
