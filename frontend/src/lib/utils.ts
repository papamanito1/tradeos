import { clsx, type ClassValue } from "clsx";

export function cn(...inputs: ClassValue[]) {
  return clsx(inputs);
}

export function formatUSD(value: number, decimals = 2): string {
  const abs = Math.abs(value);
  const formatted = abs.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  return `${value < 0 ? "-" : ""}$${formatted}`;
}

export function formatPct(value: number, decimals = 2): string {
  const sign = value >= 0 ? "+" : "";
  return `${sign}${value.toFixed(decimals)}%`;
}

export function formatNumber(value: number, decimals = 4): string {
  return value.toLocaleString("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: decimals,
  });
}

export function pnlColor(value: number): string {
  if (value > 0) return "text-profit";
  if (value < 0) return "text-loss";
  return "text-neutral";
}

export function pnlBg(value: number): string {
  if (value > 0) return "bg-profit/10 text-profit";
  if (value < 0) return "bg-loss/10 text-loss";
  return "bg-surface-700 text-neutral";
}

export function timeAgo(isoString: string): string {
  const date = new Date(isoString);
  const diff = Date.now() - date.getTime();
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export function formatDate(isoString: string): string {
  return new Date(isoString).toLocaleString("en-US", {
    month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

export function sideColor(side: string): string {
  return side === "long" || side === "buy" ? "text-profit" : "text-loss";
}

export function statusColor(status: string): string {
  switch (status) {
    case "filled": return "text-profit";
    case "open": return "text-accent";
    case "cancelled": return "text-neutral";
    case "error": return "text-loss";
    default: return "text-neutral";
  }
}
