import { reportUnauthorized } from "./auth";

const BASE = "/api";

/** Build a human-readable error from a failed response. FastAPI HTTPExceptions
 *  carry a `detail` that is either a string or a `{error_code, message}` object —
 *  surface that message so toasts say something useful (e.g. "activate a
 *  letterhead first") instead of a bare "POST /printer/compose: 404". */
async function errorMessage(r: Response, method: string, path: string): Promise<string> {
  try {
    const data = await r.json();
    const d = data?.detail;
    if (typeof d === "string" && d) return d;
    if (d && typeof d.message === "string" && d.message) return d.message;
  } catch {
    /* response body was not JSON — fall through to the generic message */
  }
  return `${method} ${path}: ${r.status}`;
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    // A 401 means the optional shared-token auth is on and this browser hasn't
    // presented it — raise the lock screen. (The login call itself also 401s on
    // a bad token; TokenGate handles that thrown error locally.)
    if (r.status === 401) reportUnauthorized();
    throw new Error(await errorMessage(r, method, path));
  }
  return r.status === 204 ? (undefined as T) : (await r.json() as T);
}

/** Multipart file upload — the browser sets the multipart Content-Type itself. */
async function upload<T>(path: string, file: File): Promise<T> {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch(`${BASE}${path}`, { method: "POST", body: fd });
  if (!r.ok) {
    if (r.status === 401) reportUnauthorized();
    throw new Error(await errorMessage(r, "POST", path));
  }
  return (await r.json()) as T;
}

/** A letterhead or font asset — bundled with the repo or operator-uploaded. */
export type PrinterAsset = {
  kind: "default" | "upload";
  name: string;
  active: boolean;
  size_bytes: number;
};

export const api = {
  /** 200 when this browser is authorized (or auth is disabled / loopback);
   *  401 otherwise — `req` then raises the lock screen. */
  authCheck: () => req<{ ok: boolean }>("GET", "/auth/check"),
  /** Exchange the shared token for a session cookie. Throws on a bad token. */
  authLogin: (token: string) => req<{ ok: boolean }>("POST", "/auth/login", { token }),
  snapshot: () => req<{
    settings: Array<{ key: string; value: string; value_type: string }>;
    services: Record<string, any>;
    last_pose: any;
    server_now: number;
  }>("GET", "/state/snapshot"),
  listSettings: () => req("GET", "/settings"),
  listSettingDefaults: () =>
    req<Array<{ key: string; value: string; value_type: string; description?: string | null }>>(
      "GET",
      "/settings/_defaults",
    ),
  patchSetting: (key: string, body: { value: unknown; value_type?: string }) => req("PATCH", `/settings/${key}`, body),
  putSetting: (key: string, body: { value: unknown; value_type: string }) => req("PUT", `/settings/${key}`, body),
  restartService: (name: string) => req("POST", `/system/services/${name}/restart`),
  animatorPreview: (name: string, position: number) => req("POST", "/animator/preview", { name, position }),
  animatorExpression: (name: string, intensity = 1.0) => req("POST", "/animator/expression", { name, intensity }),
  animatorGesture: (name: "nod_yes" | "nod_no" | "look_around") =>
    req("POST", "/animator/gesture", { name }),
  agentTextMessage: (text: string) => req("POST", "/agent/text_message", { text }),
  agentSpeakText: (text: string, emotion: string = "neutral") =>
    req("POST", "/agent/speak_text", { text, emotion }),
  listLlmModels: () =>
    req<{ models: Array<{ name: string; size?: number; modified_at?: string }> }>(
      "GET",
      "/agent/models",
    ),
  listSttBackends: () =>
    req<{ backends: Array<{ id: string; label: string; available: boolean }> }>(
      "GET",
      "/agent/stt_backends",
    ),

  // Printer letterhead + font galleries.
  listLetterheads: () => req<{ items: PrinterAsset[] }>("GET", "/printer/letterheads"),
  letterheadFileUrl: (kind: string, name: string) =>
    `${BASE}/printer/letterheads/${kind}/${encodeURIComponent(name)}`,
  letterheadUrl: () => `${BASE}/printer/letterhead`,
  uploadLetterhead: async (file: File): Promise<{ ok: boolean; kind: string; name: string }> =>
    upload("/printer/letterhead", file),
  activateLetterhead: (kind: string, name: string) =>
    req("POST", `/printer/letterheads/${kind}/${encodeURIComponent(name)}/activate`),
  deleteLetterheadFile: (kind: string, name: string) =>
    req("DELETE", `/printer/letterheads/${kind}/${encodeURIComponent(name)}`),

  listFonts: () => req<{ items: PrinterAsset[] }>("GET", "/printer/fonts"),
  fontFileUrl: (kind: string, name: string) =>
    `${BASE}/printer/fonts/${kind}/${encodeURIComponent(name)}`,
  uploadFont: async (file: File): Promise<{ ok: boolean; kind: string; name: string }> =>
    upload("/printer/font", file),
  activateFont: (kind: string, name: string) =>
    req("POST", `/printer/fonts/${kind}/${encodeURIComponent(name)}/activate`),
  deleteFont: (kind: string, name: string) =>
    req("DELETE", `/printer/fonts/${kind}/${encodeURIComponent(name)}`),

  printLetterhead: () => req("POST", "/printer/print_letterhead"),
  testPrint:       () => req("POST", "/printer/test_print"),
  composePrint:    (body: { text: string; lucky_subway_stop?: string; lucky_numbers?: number[] }) =>
    req("POST", "/printer/compose", body),
};
