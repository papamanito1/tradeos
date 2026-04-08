import { cn } from "@/lib/utils";

interface BadgeProps {
  children: React.ReactNode;
  variant?: "default" | "profit" | "loss" | "warning" | "accent" | "neutral";
  className?: string;
}

const variants = {
  default: "bg-surface-700 text-gray-300 border-surface-600",
  profit:  "bg-profit/10 text-profit border-profit/20",
  loss:    "bg-loss/10 text-loss border-loss/20",
  warning: "bg-warning/10 text-warning border-warning/20",
  accent:  "bg-accent/10 text-accent border-accent/20",
  neutral: "bg-surface-700 text-neutral border-surface-600",
};

export function Badge({ children, variant = "default", className }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold border uppercase tracking-wide",
        variants[variant],
        className
      )}
    >
      {children}
    </span>
  );
}
