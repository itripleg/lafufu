// Drive headless Chrome via CDP to screenshot the Studio tab with an
// expression selected, proving the lafufu sprite mapping renders in-app.
// Node 22 built-ins only (global fetch + WebSocket).
import { writeFileSync } from "node:fs";

const CDP = "http://localhost:9222";
const ORIGIN = process.env.ORIGIN || "http://localhost:8080";
const OUT = process.argv[2] || "studio_shot.png";
const EXPR = process.argv[3] || "happy";

const targets = await (await fetch(`${CDP}/json`)).json();
let page = targets.find((t) => t.type === "page");
const wsUrl = page.webSocketDebuggerUrl;
const ws = new WebSocket(wsUrl);
await new Promise((r) => (ws.onopen = r));

let id = 0;
const pending = new Map();
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
};
const send = (method, params = {}) =>
  new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

await send("Page.enable");
await send("Runtime.enable");

// 1) land on origin so localStorage is writable
await send("Page.navigate", { url: `${ORIGIN}/admin` });
await sleep(2500);
// 2) seed the studio tab + selected expression (lsSet stores JSON strings)
await send("Runtime.evaluate", {
  expression: `localStorage.setItem('lafufu/admin/tab', JSON.stringify('studio'));
               localStorage.setItem('lafufu/studio/selectedExpr', JSON.stringify('${EXPR}'));
               localStorage.setItem('lafufu/studio/gallerySize', JSON.stringify(1));
               localStorage.setItem('lafufu/studio/galleryView', JSON.stringify('thumb'));`,
});
// 3) reload into the studio tab and let images load
await send("Page.navigate", { url: `${ORIGIN}/admin` });
await sleep(4000);

const { result } = await send("Page.captureScreenshot", { format: "png" });
writeFileSync(OUT, Buffer.from(result.data, "base64"));
console.log("wrote", OUT, "for expression", EXPR);
ws.close();
process.exit(0);
