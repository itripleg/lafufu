/**
 * Typed localStorage helpers — used for draft settings + per-user UI prefs.
 *
 * All keys live under a single prefix so we can wipe everything cleanly.
 */
const PREFIX = "lafufu/";

export function lsGet<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(PREFIX + key);
    if (raw === null) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

export function lsSet<T>(key: string, value: T): void {
  try {
    window.localStorage.setItem(PREFIX + key, JSON.stringify(value));
  } catch {
    /* quota or disabled — silently ignore */
  }
}

export function lsRemove(key: string): void {
  try { window.localStorage.removeItem(PREFIX + key); } catch { /* */ }
}

/** Returns every lafufu/* key (un-prefixed). */
export function lsKeys(): string[] {
  const out: string[] = [];
  try {
    for (let i = 0; i < window.localStorage.length; i++) {
      const k = window.localStorage.key(i);
      if (k && k.startsWith(PREFIX)) out.push(k.slice(PREFIX.length));
    }
  } catch { /* */ }
  return out;
}

export function lsClearAll(): void {
  for (const k of lsKeys()) lsRemove(k);
}
