import { defineConfig } from "vite";
import solid from "vite-plugin-solid";

// Where the live control service is running. Override with LAFUFU_HOST when
// dev-ing on a machine that isn't the Pi:
//   LAFUFU_HOST=172.20.10.11:8080 npm run dev
// Default 172.20.10.11:8080 matches the current Pi on the hotspot — change
// this once if your Pi lives elsewhere.
const BACKEND = process.env.LAFUFU_HOST ?? "172.20.10.11:8080";

export default defineConfig({
  plugins: [solid()],
  server: {
    port: 5173,
    proxy: {
      "/api": `http://${BACKEND}`,
      "/ws":  { target: `ws://${BACKEND}`, ws: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
  },
});
