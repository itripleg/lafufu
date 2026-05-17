type Frame = { topic: string; payload: any };
type Handler = (frame: Frame) => void;

export class NatsWs {
  private ws: WebSocket | null = null;
  private listeners = new Map<string, Set<Handler>>();
  private reconnectDelay = 1000;
  private maxDelay = 30000;
  private url: string;
  private active = false;

  constructor(url: string = "/ws") {
    this.url = url;
  }

  start(): void {
    this.active = true;
    this.connect();
  }

  stop(): void {
    this.active = false;
    this.ws?.close();
    this.ws = null;
  }

  private connect(): void {
    if (!this.active) return;
    const wsUrl = this.url.startsWith("ws") ? this.url
      : `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}${this.url}`;
    this.ws = new WebSocket(wsUrl);
    this.ws.onopen = () => {
      this.reconnectDelay = 1000;
      const topics = Array.from(this.listeners.keys());
      if (topics.length > 0) {
        this.ws!.send(JSON.stringify({ op: "sub", topics }));
      }
    };
    this.ws.onmessage = (ev) => {
      try {
        const frame: Frame = JSON.parse(ev.data);
        for (const [pattern, handlers] of this.listeners) {
          if (matchesPattern(pattern, frame.topic)) {
            handlers.forEach((h) => h(frame));
          }
        }
      } catch {
        // drop
      }
    };
    this.ws.onclose = () => {
      this.ws = null;
      if (this.active) {
        setTimeout(() => this.connect(), this.reconnectDelay);
        this.reconnectDelay = Math.min(this.maxDelay, this.reconnectDelay * 2);
      }
    };
    this.ws.onerror = () => {/* onclose handles reconnect */};
  }

  subscribe(pattern: string, handler: Handler): () => void {
    let handlers = this.listeners.get(pattern);
    if (!handlers) {
      handlers = new Set();
      this.listeners.set(pattern, handlers);
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ op: "sub", topics: [pattern] }));
      }
    }
    handlers.add(handler);
    return () => {
      handlers!.delete(handler);
      if (handlers!.size === 0) {
        this.listeners.delete(pattern);
        if (this.ws?.readyState === WebSocket.OPEN) {
          this.ws.send(JSON.stringify({ op: "unsub", topics: [pattern] }));
        }
      }
    };
  }
}

/** Match NATS-style wildcards: '*' for one token, '>' for tail. */
export function matchesPattern(pattern: string, topic: string): boolean {
  const p = pattern.split(".");
  const t = topic.split(".");
  for (let i = 0; i < p.length; i++) {
    if (p[i] === ">") return true;
    if (i >= t.length) return false;
    if (p[i] === "*") continue;
    if (p[i] !== t[i]) return false;
  }
  return p.length === t.length;
}
