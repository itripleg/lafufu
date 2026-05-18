const BASE = "/api";

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`${method} ${path}: ${r.status}`);
  return r.status === 204 ? (undefined as T) : (await r.json() as T);
}

export const api = {
  snapshot: () => req<{ settings: Array<{ key: string; value: string; value_type: string }>; services: Record<string, any>; last_pose: any }>("GET", "/state/snapshot"),
  listSettings: () => req("GET", "/settings"),
  patchSetting: (key: string, body: { value: unknown; value_type?: string }) => req("PATCH", `/settings/${key}`, body),
  putSetting: (key: string, body: { value: unknown; value_type: string }) => req("PUT", `/settings/${key}`, body),
  restartService: (name: string) => req("POST", `/system/services/${name}/restart`),
  animatorPreview: (name: string, position: number) => req("POST", "/animator/preview", { name, position }),
  animatorExpression: (name: string, intensity = 1.0) => req("POST", "/animator/expression", { name, intensity }),
  agentTextMessage: (text: string) => req("POST", "/agent/text_message", { text }),
  agentSpeakText: (text: string, emotion: string = "neutral") =>
    req("POST", "/agent/speak_text", { text, emotion }),
};
