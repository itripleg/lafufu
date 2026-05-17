export type Emotion = 'happy' | 'sad' | 'angry' | 'surprised' | 'neutral' | 'agree' | 'disagree';

export const EMOTION_COLORS: Record<Emotion, string> = {
  happy: "#fcd34d",
  sad: "#60a5fa",
  angry: "#f87171",
  surprised: "#a78bfa",
  neutral: "#94a3b8",
  agree: "#34d399",
  disagree: "#f97316",
};

export function emotionToColor(e: Emotion | string | undefined): string {
  if (!e) return EMOTION_COLORS.neutral;
  return EMOTION_COLORS[e as Emotion] ?? EMOTION_COLORS.neutral;
}

/** Map RMS [0,1] → CSS scale factor or height percentage. */
export function rmsToHeightPct(rms: number): number {
  return Math.max(0, Math.min(1, rms)) * 100;
}
