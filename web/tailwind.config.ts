import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Emotion palette (Phase 0 — adjustable in DB later)
        happy: "#fcd34d",
        sad: "#60a5fa",
        angry: "#f87171",
        surprised: "#a78bfa",
        neutral: "#94a3b8",
        agree: "#34d399",
        disagree: "#f97316",
      },
    },
  },
} satisfies Config;
