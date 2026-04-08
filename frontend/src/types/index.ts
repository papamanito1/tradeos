export interface User {
  username: string;
}

export interface Overview {
  equity: number;
  available_balance: number;
  unrealized_pnl: number;
  daily_pnl: number;
  daily_pnl_pct: number;
  win_rate: number;
  total_trades: number;
  active_strategies: number;
  open_positions: number;
  kill_switch_active: boolean;
  trading_mode: "paper" | "live";
  exchange_connected: boolean;
  positions: Position[];
  recent_orders: Order[];
}

export interface Position {
  id: number;
  strategy_id: number | null;
  symbol: string;
  side: "long" | "short";
  size: number;
  entry_price: number;
  current_price: number;
  unrealized_pnl: number;
  realized_pnl: number;
  stop_loss: number | null;
  take_profit: number | null;
  leverage: number;
  mode: "paper" | "live";
  is_open: boolean;
  opened_at: string;
  closed_at: string | null;
}

export interface Order {
  id: number;
  exchange_order_id: string | null;
  strategy_id: number | null;
  symbol: string;
  order_type: string;
  side: "buy" | "sell";
  amount: number;
  price: number | null;
  filled: number;
  remaining: number;
  average_fill_price: number | null;
  status: "open" | "filled" | "cancelled" | "error";
  mode: "paper" | "live";
  fee: number;
  slippage: number;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface Strategy {
  id: number;
  name: string;
  strategy_type: string;
  mode: "off" | "paper" | "live";
  status: string;
  is_enabled: boolean;
  symbols: string[];
  timeframe: string;
  parameters: Record<string, unknown>;
  capital_allocation: number;
  last_signal: string | null;
  last_action: string | null;
  consecutive_failures: number;
  created_at: string;
  updated_at: string;
}

export interface StrategyType {
  type: string;
  name: string;
  description: string;
  default_parameters: Record<string, unknown>;
}

export interface RiskSettings {
  id: number;
  max_daily_loss_usd: number;
  max_daily_loss_pct: number;
  max_position_size_usd: number;
  max_position_size_pct: number;
  max_leverage: number;
  max_open_trades: number;
  max_symbol_exposure_pct: number;
  cooldown_after_losses: number;
  cooldown_minutes: number;
  circuit_breaker_enabled: boolean;
  circuit_breaker_threshold_pct: number;
  symbol_blacklist: string[];
  kill_switch_active: boolean;
  updated_at: string;
}

export interface JournalEntry {
  id: number;
  entry_type: string;
  level: string;
  strategy_id: number | null;
  symbol: string | null;
  message: string;
  details: string | null;
  created_at: string;
}

export interface Candle {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Ticker {
  symbol: string;
  bid: number;
  ask: number;
  last: number;
  volume: number;
  change_pct: number;
}

export interface BacktestMetrics {
  total_return_pct: number;
  annualized_return_pct: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  calmar_ratio: number;
  max_drawdown_pct: number;
  max_drawdown_usd: number;
  max_drawdown_duration_bars: number;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  avg_win: number;
  avg_loss: number;
  largest_win: number;
  largest_loss: number;
  profit_factor: number;
  expectancy: number;
  avg_duration_bars: number;
  max_consecutive_wins: number;
  max_consecutive_losses: number;
  total_fees_paid: number;
  exposure_pct: number;
  gross_profit: number;
  gross_loss: number;
  drawdown_series: number[];
}

export interface BacktestEquityPoint {
  timestamp: string;
  equity: number;
}

export interface BacktestResult {
  strategy_type: string;
  strategy_name: string;
  symbol: string;
  timeframe: string;
  parameters: Record<string, number | string | boolean>;
  date_range: { start: string; end: string };
  candle_count: number;
  initial_capital: number;
  final_capital: number;
  metrics: BacktestMetrics;
  equity_curve: BacktestEquityPoint[];
  monthly_returns: Record<string, number>;
  trades: BacktestTrade[];
}

export interface BacktestTrade {
  id: number;
  side: string;
  entry_bar: number;
  exit_bar: number;
  entry_time: string;
  exit_time: string;
  entry_price: number;
  exit_price: number;
  size: number;
  pnl: number;
  pnl_pct: number;
  fees: number;
  reason: string;
  duration_bars: number;
  duration_human: string;
}

export interface WSMessage {
  event?: string;
  type?: string;
  data?: unknown;
  [key: string]: unknown;
}
