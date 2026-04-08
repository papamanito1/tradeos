/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        // Premium dark palette
        surface: {
          950: "#05060c",   // deepest — true dark
          900: "#08090f",   // page background
          800: "#0c0e16",   // card surface
          700: "#131722",   // elevated card / panel
          600: "#1e2236",   // separator / border zone
          500: "#2a2f4a",   // active border / accent border
        },
        accent: {
          DEFAULT: "#0a84ff",   // Apple blue — dark mode
          hover:   "#409cff",
          muted:   "rgba(10,132,255,0.15)",
        },
        profit:  "#30d158",   // Apple green — dark mode
        loss:    "#ff453a",   // Apple red   — dark mode
        warning: "#ffd60a",   // Apple yellow
        neutral: "#6e6e73",   // Apple tertiary label
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "sans-serif"],
        mono: ["'JetBrains Mono'", "ui-monospace", "Consolas", "monospace"],
      },
      borderRadius: {
        apple:    "16px",
        "apple-lg": "20px",
        pill:     "980px",
      },
      boxShadow: {
        apple:    "0 4px 24px rgba(0,0,0,0.7), 0 1px 0 rgba(255,255,255,0.05) inset",
        "apple-sm": "0 2px 8px rgba(0,0,0,0.5), 0 1px 0 rgba(255,255,255,0.04) inset",
        "apple-lg": "0 8px 40px rgba(0,0,0,0.8), 0 1px 0 rgba(255,255,255,0.05) inset",
        glow:     "0 0 24px rgba(10,132,255,0.3)",
        "glow-profit": "0 0 20px rgba(48,209,88,0.2)",
        "glow-loss":   "0 0 20px rgba(255,69,58,0.2)",
      },
      animation: {
        "fade-in":  "fadeIn 0.2s cubic-bezier(0.25,0.46,0.45,0.94)",
        "slide-up": "slideUp 0.25s cubic-bezier(0.25,0.46,0.45,0.94)",
        pulse:      "pulse 2s cubic-bezier(0.4,0,0.6,1) infinite",
      },
      keyframes: {
        fadeIn: {
          "0%":   { opacity: 0, transform: "translateY(-6px)" },
          "100%": { opacity: 1, transform: "translateY(0)" },
        },
        slideUp: {
          "0%":   { opacity: 0, transform: "translateY(8px)" },
          "100%": { opacity: 1, transform: "translateY(0)" },
        },
      },
      backdropBlur: { apple: "40px" },
    },
  },
  plugins: [],
};
