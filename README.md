# TradeOS — Crypto Trading Operating System

A production-style crypto trading dashboard with paper trading, live trading, strategy management, risk controls, and real-time monitoring.

---

## Architecture

```
TradeOS/
├── backend/          FastAPI + SQLAlchemy + Redis + WebSockets
│   ├── app/
│   │   ├── agents/   market_data · signal · execution · risk · journal
│   │   ├── api/      REST endpoints for every dashboard section
│   │   ├── core/     config · security · database · redis
│   │   ├── exchange/ base adapter · CCXT adapter · paper engine
│   │   ├── models/   SQLAlchemy ORM models
│   │   ├── risk/     risk engine (server-side enforcement)
│   │   ├── strategies/ EMA crossover · breakout · mean reversion
│   │   └── websockets/ connection manager
│   └── tests/
└── frontend/         Next.js 14 (App Router) + Tailwind CSS
    └── src/
        ├── app/      pages (login · dashboard · all sub-pages)
        ├── components/ layout · charts · tables · UI primitives
        ├── context/  auth · trading state
        ├── hooks/    useWebSocket · useAuth
        └── lib/      API client · utils
```

---

## Quick Start (Docker)

### 1. Clone & configure

```bash
git clone <repo>
cd TradingAgent
cp .env.example .env
# Edit .env — set SECRET_KEY and ADMIN_PASSWORD at minimum
```

### 2. Start all services

```bash
docker compose up --build
```

### 3. Seed the database

```bash
docker compose exec backend python -m app.seeds.seed_data
```

### 4. Open the dashboard

- Frontend → http://localhost:3000
- Backend API docs → http://localhost:8000/docs
- Default login: `admin` / `changeme123!`

---

## Local Development (without Docker)

### Backend

```bash
cd backend
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt

# Run migrations
alembic upgrade head

# Seed data
python -m app.seeds.seed_data

# Start dev server
uvicorn main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

---

## Running Tests

```bash
cd backend
pytest tests/ -v
```

---

## Trading Modes

| Mode  | Description                                                |
|-------|------------------------------------------------------------|
| Paper | Simulated orders, no real money, full strategy logic       |
| Live  | Real exchange orders — requires `ALLOW_LIVE_TRADING=1` in `.env` **and** explicit confirmation in the UI |

The system defaults to **paper trading**. Live mode is double-gated: environment variable + UI confirmation modal.

---

## Key Features

- **Risk Engine** — server-side enforcement of daily loss limits, position size caps, max leverage, symbol blacklists, and circuit breakers
- **Strategy Engine** — pluggable strategies (EMA Crossover, Breakout, Mean Reversion); each generates signals → risk check → execution
- **Emergency Stop** — global kill switch halts all execution immediately
- **Real-time** — WebSockets push position/order/balance/log updates to the UI
- **Backtesting** — run strategies against historical OHLCV data with equity curve and trade log
- **Journal** — every signal, trade, error, and risk block is logged and searchable

---

## Adding a New Strategy

1. Create `backend/app/strategies/my_strategy.py` extending `BaseStrategy`
2. Implement `generate_signal(candles)` returning a `Signal` object
3. Register the strategy in `backend/app/strategies/__init__.py`
4. It appears automatically in the Strategies dashboard

---

## Environment Variables

See `.env.example` for all options with descriptions.

Critical ones:

| Variable | Description |
|---|---|
| `SECRET_KEY` | JWT signing key — use `openssl rand -hex 32` |
| `ADMIN_PASSWORD` | Initial admin password (changed after first login) |
| `TRADING_MODE` | `paper` or `live` |
| `ALLOW_LIVE_TRADING` | Must be `1` to enable live mode |
| `USE_MOCK_EXCHANGE` | `1` = no real API calls (safe for dev) |

---

## License

MIT
