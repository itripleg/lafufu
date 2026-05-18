import { Component, For, createSignal } from "solid-js";
import { Portal } from "solid-js/web";

export type ToastKind = "ok" | "warn" | "err" | "info";

interface Toast {
  id: number;
  kind: ToastKind;
  title: string;
  detail?: string;
  ts: number;
}

const [toasts, setToasts] = createSignal<Toast[]>([]);
let nextId = 1;

/** Push a toast. Auto-dismisses after `ttl` ms (default 3500). Returns the id. */
export function pushToast(kind: ToastKind, title: string, detail?: string, ttl = 3500): number {
  const id = nextId++;
  const t: Toast = { id, kind, title, detail, ts: Date.now() };
  setToasts((all) => [...all, t]);
  if (ttl > 0) window.setTimeout(() => dismissToast(id), ttl);
  return id;
}

export const toast = {
  ok:   (title: string, detail?: string) => pushToast("ok",   title, detail),
  warn: (title: string, detail?: string) => pushToast("warn", title, detail, 5000),
  err:  (title: string, detail?: string) => pushToast("err",  title, detail, 6000),
  info: (title: string, detail?: string) => pushToast("info", title, detail),
};

export function dismissToast(id: number) {
  setToasts((all) => all.filter((t) => t.id !== id));
}

const kindStyle = (k: ToastKind) => {
  switch (k) {
    case "ok":   return { glyph: "✓", c: "var(--c-moss)"  };
    case "warn": return { glyph: "!", c: "var(--c-amber)" };
    case "err":  return { glyph: "×", c: "var(--c-coral)" };
    case "info": return { glyph: "·", c: "var(--c-iris)"  };
  }
};

export const ToastLayer: Component = () => {
  return (
    <Portal>
      <div
        style={{
          position: "fixed",
          top: "max(20px, env(safe-area-inset-top))",
          right: "max(20px, env(safe-area-inset-right))",
          display: "flex",
          "flex-direction": "column",
          gap: "10px",
          "z-index": 10000,
          "max-width": "min(380px, calc(100vw - 40px))",
          "pointer-events": "none",
        }}
      >
        <For each={toasts()}>
          {(t) => {
            const s = kindStyle(t.kind);
            return (
              <div
                onClick={() => dismissToast(t.id)}
                style={{
                  "pointer-events": "auto",
                  display: "flex",
                  "align-items": "flex-start",
                  gap: "12px",
                  padding: "12px 14px 12px 12px",
                  background:
                    "linear-gradient(180deg, rgba(57,46,37,.96) 0%, rgba(45,37,30,.96) 100%)",
                  "backdrop-filter": "blur(14px)",
                  "-webkit-backdrop-filter": "blur(14px)",
                  border: `1px solid var(--c-edge)`,
                  "border-left": `3px solid ${s.c}`,
                  "border-radius": "14px",
                  "box-shadow":
                    "0 1px 0 rgba(255,240,210,.05) inset, 0 18px 40px -16px rgba(0,0,0,.55)",
                  animation: "slide-in-r .35s cubic-bezier(.2,.7,.3,1.1) both",
                  cursor: "pointer",
                }}
                role="status"
              >
                <span
                  class="f-mono"
                  style={{
                    color: s.c,
                    "font-size": "18px",
                    "line-height": 1,
                    "padding-top": "1px",
                    "min-width": "16px",
                    "text-align": "center",
                  }}
                >
                  {s.glyph}
                </span>
                <div style={{ flex: 1, "min-width": 0 }}>
                  <div
                    style={{
                      "font-family": "var(--f-sans)",
                      "font-weight": 600,
                      "font-size": "13px",
                      color: "var(--c-bone)",
                      "letter-spacing": ".005em",
                    }}
                  >
                    {t.title}
                  </div>
                  {t.detail && (
                    <div
                      style={{
                        "font-family": "var(--f-mono)",
                        "font-size": "11px",
                        color: "var(--c-mist)",
                        "margin-top": "3px",
                        "word-break": "break-word",
                      }}
                    >
                      {t.detail}
                    </div>
                  )}
                </div>
              </div>
            );
          }}
        </For>
      </div>
    </Portal>
  );
};
