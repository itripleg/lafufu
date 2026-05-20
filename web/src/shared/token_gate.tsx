import { Component, Show, createSignal, onMount } from "solid-js";
import { Portal } from "solid-js/web";
import { Blob } from "./blob";
import { api } from "./api";
import { locked } from "./auth";

/**
 * Lock screen for the optional shared-token auth. It is mounted app-wide but
 * stays invisible until a request comes back 401 — so the on-device kiosk
 * (loopback, trusted) and token-less deployments never see it.
 *
 * Flow: enter the token once → POST /api/auth/login sets an HttpOnly,
 * SameSite=Strict cookie → reload into the fully working app. The cookie then
 * rides every fetch, image and the WebSocket handshake automatically.
 */
export const TokenGate: Component = () => {
  const [token, setToken] = createSignal("");
  const [reveal, setReveal] = createSignal(false);
  const [busy, setBusy] = createSignal(false);
  const [done, setDone] = createSignal(false);
  const [error, setError] = createSignal("");
  const [shaking, setShaking] = createSignal(false);

  // Proactively probe on load so the gate also appears on WS-only routes
  // (/face) where no HTTP request would otherwise surface the 401.
  onMount(() => void api.authCheck().catch(() => {}));

  const submit = async (e: Event) => {
    e.preventDefault();
    const value = token().trim();
    if (!value || busy() || done()) return;
    setBusy(true);
    setError("");
    try {
      await api.authLogin(value);
      setDone(true);
      // Cookie is set — reload so every panel refetches cleanly with it.
      setTimeout(() => window.location.reload(), 600);
    } catch (err) {
      setBusy(false);
      setError(err instanceof Error ? err.message : "could not verify that token");
      setShaking(true);
      setTimeout(() => setShaking(false), 520);
    }
  };

  return (
    <Show when={locked()}>
      <Portal>
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="token-gate-title"
          style={{
            position: "fixed",
            inset: 0,
            "z-index": 9000,
            display: "flex",
            "align-items": "center",
            "justify-content": "center",
            padding: "24px",
            background:
              "radial-gradient(ellipse at 32% 18%, #2a2018 0%, #1a1410 58%, #0c0907 100%)",
            overflow: "clip",
          }}
        >
          <Blob
            size="58vmin"
            color="var(--c-amber)"
            opacity={0.16}
            blur={84}
            drift
            style={{ top: "-18vmin", left: "-16vmin" }}
          />
          <Blob
            size="46vmin"
            color="var(--c-moss)"
            opacity={done() ? 0.22 : 0.1}
            blur={74}
            variant={2}
            drift
            delay={4}
            style={{
              bottom: "-14vmin",
              right: "-12vmin",
              transition: "opacity var(--t-slow)",
            }}
          />

          {/* outer wrapper does the entrance; inner card does the error shake */}
          <div class="anim-fade-up" style={{ position: "relative", width: "100%", "max-width": "412px" }}>
            <form
              onSubmit={submit}
              class={shaking() ? "anim-shake" : ""}
              style={{
                display: "flex",
                "flex-direction": "column",
                "align-items": "center",
                gap: "0",
                padding: "40px 34px 30px",
                "border-radius": "var(--r-stone)",
                background:
                  "linear-gradient(168deg, rgba(57,46,37,.94) 0%, rgba(34,28,23,.96) 100%)",
                "backdrop-filter": "blur(18px)",
                "-webkit-backdrop-filter": "blur(18px)",
                border: "1px solid var(--c-edge)",
                "box-shadow":
                  "inset 0 1px 0 rgba(255,240,210,.05), 0 32px 70px -28px rgba(0,0,0,.8)",
              }}
            >
              {/* sleeping-eye emblem — pupil breathes, opens green on success */}
              <div
                aria-hidden="true"
                class="blob-shape"
                style={{
                  width: "62px",
                  height: "62px",
                  display: "flex",
                  "align-items": "center",
                  "justify-content": "center",
                  background:
                    "radial-gradient(circle at 34% 30%, rgba(212,162,89,.22), rgba(22,18,16,.6) 72%)",
                  border: "1px solid var(--c-edge)",
                  "box-shadow": "inset 0 2px 10px rgba(0,0,0,.5)",
                  "margin-bottom": "22px",
                }}
              >
                <div
                  style={{
                    width: "16px",
                    height: "16px",
                    "border-radius": "50%",
                    background: done()
                      ? "radial-gradient(circle at 32% 32%, #cbe0ab, var(--c-moss) 70%)"
                      : "radial-gradient(circle at 32% 32%, #f3ecdc, var(--c-amber) 70%, #a37e44)",
                    "box-shadow": done()
                      ? "0 0 14px rgba(149,176,122,.6)"
                      : "0 0 10px rgba(212,162,89,.35)",
                    animation: "breathe 4s ease-in-out infinite",
                    transition: "background var(--t-base)",
                  }}
                />
              </div>

              <div class="eyebrow" style={{ color: "var(--c-stone)", "margin-bottom": "12px" }}>
                locked · operator access
              </div>
              <h1
                id="token-gate-title"
                class="f-display"
                style={{
                  margin: 0,
                  "font-size": "46px",
                  color: "var(--c-bone)",
                  "text-align": "center",
                }}
              >
                say the word
              </h1>
              <p
                style={{
                  margin: "12px 0 26px",
                  "max-width": "300px",
                  "text-align": "center",
                  "font-size": "13px",
                  "line-height": 1.55,
                  color: "var(--c-mist)",
                }}
              >
                This Lafufu only opens for a known token. Enter it once — this
                browser stays remembered.
              </p>

              <div style={{ position: "relative", width: "100%" }}>
                <input
                  ref={(el) => requestAnimationFrame(() => el.focus())}
                  class="field"
                  type={reveal() ? "text" : "password"}
                  value={token()}
                  onInput={(e) => setToken(e.currentTarget.value)}
                  disabled={busy() || done()}
                  placeholder="pass-token"
                  autocomplete="current-password"
                  aria-label="Access token"
                  spellcheck={false}
                  style={{
                    width: "100%",
                    "font-size": "15px",
                    padding: "13px 60px 13px 16px",
                    "letter-spacing": "0.06em",
                    "border-color": error() ? "var(--c-coral)" : "var(--c-edge)",
                  }}
                />
                <button
                  type="button"
                  class="btn btn--ghost btn--micro"
                  onClick={() => setReveal((r) => !r)}
                  tabindex={-1}
                  style={{
                    position: "absolute",
                    right: "6px",
                    top: "50%",
                    transform: "translateY(-50%)",
                  }}
                >
                  {reveal() ? "hide" : "show"}
                </button>
              </div>

              <Show when={error()}>
                <div
                  class="f-mono"
                  role="alert"
                  style={{
                    "align-self": "flex-start",
                    "margin-top": "10px",
                    "font-size": "11px",
                    color: "var(--c-coral)",
                  }}
                >
                  × {error()}
                </div>
              </Show>

              <button
                type="submit"
                class="btn btn--primary"
                disabled={busy() || done() || !token().trim()}
                style={{ width: "100%", "margin-top": "20px", padding: "12px" }}
              >
                {done() ? "✓ unlocked" : busy() ? "checking…" : "unlock"}
              </button>

              <div
                class="f-mono"
                style={{
                  "margin-top": "20px",
                  "font-size": "10px",
                  "letter-spacing": "0.04em",
                  color: "var(--c-stone)",
                  "text-align": "center",
                }}
              >
                token lives in the control service config on the pi
              </div>
            </form>
          </div>
        </div>
      </Portal>
    </Show>
  );
};
