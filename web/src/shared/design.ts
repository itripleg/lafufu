export type Emotion = 'happy' | 'sad' | 'angry' | 'surprised' | 'neutral' | 'agree' | 'disagree';

// Warm-organic palette matching the CSS tokens in index.css.
// Tints used for emotion-driven gradients across the kiosk + pet routes.
export const EMOTION_COLORS: Record<Emotion, string> = {
  happy:     "#e4b15a",
  sad:       "#8a9bc4",
  angry:     "#e07061",
  surprised: "#b990a6",
  neutral:   "#b9ad94",
  agree:     "#95b07a",
  disagree:  "#cf8459",
};

/** Short tactile glyph for each emotion — used in chat bubbles + buttons. */
export const EMOTION_GLYPH: Record<Emotion, string> = {
  happy:     "◐",
  sad:       "◓",
  angry:     "◢",
  surprised: "◉",
  neutral:   "○",
  agree:     "✓",
  disagree:  "✗",
};

export function emotionToColor(e: Emotion | string | undefined): string {
  if (!e) return EMOTION_COLORS.neutral;
  return EMOTION_COLORS[e as Emotion] ?? EMOTION_COLORS.neutral;
}

/** Map RMS [0,1] → CSS scale factor or height percentage. */
export function rmsToHeightPct(rms: number): number {
  return Math.max(0, Math.min(1, rms)) * 100;
}

/** Detect mobile/touch from UA — used for landing-page routing hint. */
export function isMobileLikeUA(): boolean {
  if (typeof navigator === "undefined") return false;
  return /android|iphone|ipad|ipod|mobile/i.test(navigator.userAgent);
}
