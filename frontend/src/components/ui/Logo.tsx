interface LogoProps {
  size?: number;
  className?: string;
}

/**
 * TradeOS — "The Signal" logomark.
 *
 * A premium squircle app-icon containing a stylised market-pulse line:
 *   • Rich electric-blue-to-deep-navy gradient background
 *   • Subtle top-edge shine (3-D glass effect)
 *   • Fine inner border for depth
 *   • White EKG-style signal path with a glowing peak dot
 *   • Secondary axis lines for the "trading terminal" feel
 */
export function Logo({ size = 32, className }: LogoProps) {
  // Gradient IDs are file-scoped strings — fine for same-definition reuse.
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      className={className}
      aria-label="TradeOS"
    >
      <defs>
        {/* Background gradient — electric blue → deep navy */}
        <linearGradient id="to-bg" x1="0" y1="0" x2="32" y2="32" gradientUnits="userSpaceOnUse">
          <stop offset="0%"   stopColor="#1d86ff" />
          <stop offset="55%"  stopColor="#0a5ae8" />
          <stop offset="100%" stopColor="#0030b0" />
        </linearGradient>

        {/* Top-edge glass shine */}
        <linearGradient id="to-shine" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"  stopColor="white" stopOpacity="0.22" />
          <stop offset="80%" stopColor="white" stopOpacity="0" />
        </linearGradient>

        {/* Signal line gradient — white with a cyan lead */}
        <linearGradient id="to-line" x1="6" y1="17" x2="26" y2="10" gradientUnits="userSpaceOnUse">
          <stop offset="0%"   stopColor="white" stopOpacity="0.45" />
          <stop offset="60%"  stopColor="white" stopOpacity="0.9" />
          <stop offset="100%" stopColor="#a8d8ff" />
        </linearGradient>

        {/* Peak glow */}
        <radialGradient id="to-glow" cx="50%" cy="50%" r="50%">
          <stop offset="0%"   stopColor="white" stopOpacity="0.9" />
          <stop offset="100%" stopColor="#60b8ff" stopOpacity="0" />
        </radialGradient>

        {/* Clip the shine to the squircle */}
        <clipPath id="to-clip">
          <rect width="32" height="32" rx="8.5" />
        </clipPath>
      </defs>

      {/* ── Background ── */}
      <rect width="32" height="32" rx="8.5" fill="url(#to-bg)" />

      {/* ── Glass shine (top half only) ── */}
      <rect width="32" height="18" rx="8.5" fill="url(#to-shine)" clipPath="url(#to-clip)" />

      {/* ── Inner border ── */}
      <rect
        x="0.6" y="0.6" width="30.8" height="30.8" rx="7.9"
        stroke="white" strokeWidth="0.7" strokeOpacity="0.16" fill="none"
      />

      {/* ── Faint axis grid ── */}
      {/* Baseline */}
      <line x1="6" y1="22.5" x2="26" y2="22.5" stroke="white" strokeWidth="0.6" strokeOpacity="0.12" strokeLinecap="round" />
      {/* Y-axis */}
      <line x1="6" y1="9" x2="6" y2="22.5" stroke="white" strokeWidth="0.6" strokeOpacity="0.12" strokeLinecap="round" />

      {/* ── Signal pulse line ── */}
      {/*   flat → dip → sharp spike → descent → flat  */}
      <polyline
        points="6,18.5  10,18.5  12,21.5  14,11  16,18.5  26,18.5"
        stroke="url(#to-line)"
        strokeWidth="1.9"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />

      {/* ── Spike peak glow ── */}
      <circle cx="14" cy="11" r="4" fill="url(#to-glow)" opacity="0.35" />

      {/* ── Spike peak dot ── */}
      <circle cx="14" cy="11" r="1.7" fill="white" />

      {/* ── Right-end dot (live indicator) ── */}
      <circle cx="26" cy="18.5" r="1.2" fill="white" fillOpacity="0.6" />
    </svg>
  );
}
