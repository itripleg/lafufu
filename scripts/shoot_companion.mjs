// Screenshot the admin Chat tab in each companion layout. Node 22 built-ins.
import { writeFileSync } from "node:fs";

const CDP = "http://localhost:9222";
const ORIGIN = process.env.ORIGIN || "http://localhost:8080";
const LAYOUTS = ["even", "stacked", "pet", "chat"];

const targets = await (await fetch(`${CDP}/json`)).json();
const ws = new WebSocket(targets.find((t) => t.type === "page").webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
let id = 0;
const pending = new Map();
ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
const send = (method, params = {}) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

await send("Page.enable");
// land on origin once so localStorage is writable + set the chat tab
await send("Page.navigate", { url: `${ORIGIN}/admin` });
await sleep(2000);
await send("Runtime.evaluate", { expression: `localStorage.setItem('lafufu/admin/tab', JSON.stringify('chat'));` });

for (const layout of LAYOUTS) {
  await send("Runtime.evaluate", { expression: `localStorage.setItem('lafufu/companion/layout', JSON.stringify('${layout}'));` });
  await send("Page.navigate", { url: `${ORIGIN}/admin?emotion=happy` });
  await sleep(3000);
  const { result } = await send("Page.captureScreenshot", { format: "png" });
  const out = `companion_${layout}.png`;
  writeFileSync(out, Buffer.from(result.data, "base64"));
  console.log("wrote", out);
}
ws.close();
process.exit(0);
