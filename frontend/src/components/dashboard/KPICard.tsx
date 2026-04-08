import { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

interface KPICardProps {
  label: string;
  value: string;
  subValue?: string;
  subColor?: string;
  icon?: LucideIcon;
  trend?: "up" | "down" | "neutral";
  className?: string;
}

export function KPICard({ label, value, subValue, subColor, icon: Icon, trend, className }: KPICardProps) {
  const accent =
    trend === "up"
      ? { orb: "rgba(48,209,88,0.07)", iconBg: "rgba(48,209,88,0.1)", iconColor: "#30d158" }
      : trend === "down"
      ? { orb: "rgba(255,69,58,0.07)", iconBg: "rgba(255,69,58,0.1)", iconColor: "#ff453a" }
      : { orb: "rgba(10,132,255,0.06)", iconBg: "rgba(10,132,255,0.1)", iconColor: "#0a84ff" };

  return (
    <div
      className={cn("relative overflow-hidden rounded-apple p-5 transition-all duration-200", className)}
      style={{
        background: "linear-gradient(145deg, #0f1119 0%, #0c0e16 100%)",
        border: "1px solid rgba(255,255,255,0.06)",
        boxShadow: "0 1px 0 rgba(255,255,255,0.04) inset, 0 4px 20px rgba(0,0,0,0.4)",
      }}
      onMouseEnter={(e) => {
        const el = e.currentTarget as HTMLElement;
        el.style.borderColor = "rgba(255,255,255,0.1)";
        el.style.boxShadow = "0 1px 0 rgba(255,255,255,0.05) inset, 0 8px 32px rgba(0,0,0,0.5)";
      }}
      onMouseLeave={(e) => {
        const el = e.currentTarget as HTMLElement;
        el.style.borderColor = "rgba(255,255,255,0.06)";
        el.style.boxShadow = "0 1px 0 rgba(255,255,255,0.04) inset, 0 4px 20px rgba(0,0,0,0.4)";
      }}
    >
      {/* Corner orb */}
      <div
        className="absolute top-0 right-0 w-28 h-28 rounded-full pointer-events-none"
        style={{
          background: `radial-gradient(circle, ${accent.orb} 0%, transparent 70%)`,
          transform: "translate(35%, -35%)",
        }}
      />

      <div className="label mb-3">{label}</div>

      <div
        className="font-numeric leading-none mb-2"
        style={{ fontSize: "26px", fontWeight: 700, letterSpacing: "-0.03em", color: "#f0f0f8" }}
      >
        {value}
      </div>

      {subValue && (
        <div className={cn("text-[13px] font-medium font-numeric", subColor || "text-neutral")}>
          {subValue}
        </div>
      )}

      {Icon && (
        <div
          className="absolute bottom-4 right-4 w-8 h-8 rounded-lg flex items-center justify-center"
          style={{ background: accent.iconBg }}
        >
          <Icon size={15} style={{ color: accent.iconColor }} />
        </div>
      )}
    </div>
  );
}
