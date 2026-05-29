/**
 * Lafufu-head emotion icon — ported from the design system's
 * preview/lafufu-icons.js. A 40×44 SVG silhouette (Labubu-style bunny
 * ears + face plate) tinted to the emotion's accent colour, with
 * per-emotion eyes/mouth/brows in ink.
 *
 * The spec reserves these heads for EMOTIONS (happy/sad/angry/surprised/
 * neutral/agree/disagree). Everything else — controls, play/pause,
 * status — stays unicode.
 */
import { Component, mergeProps } from "solid-js";

export const LAFUFU_EMOTION_COLORS: Record<string, string> = {
  happy:     "#e4b15a",
  sad:       "#8a9bc4",
  angry:     "#e07061",
  surprised: "#b990a6",
  neutral:   "#b9ad94",
  agree:     "#95b07a",
  disagree:  "#cf8459",
};

export const LAFUFU_EMOTIONS = Object.keys(LAFUFU_EMOTION_COLORS);

type Features = { earTilt?: number; brows?: string; eyes: string; mouth: string };

const FEATURES: Record<string, Features> = {
  happy: {
    eyes:  '<path d="M13.5 24 Q 16 27 18.5 24" stroke="#1a1410" stroke-width="1.5" fill="none" stroke-linecap="round"/><path d="M21.5 24 Q 24 27 26.5 24" stroke="#1a1410" stroke-width="1.5" fill="none" stroke-linecap="round"/>',
    mouth: '<path d="M13.5 30 Q 20 35 26.5 30" stroke="#1a1410" stroke-width="1.5" fill="#1a1410" stroke-linejoin="round"/><path d="M15 31 L 16 33 L 17 31 L 18 33 L 19 31 L 20 33 L 21 31 L 22 33 L 23 31 L 24 33 L 25 31" stroke="#f3ecdc" stroke-width=".7" fill="none"/>',
  },
  sad: {
    earTilt: 12,
    eyes:  '<circle cx="16" cy="25" r="1.6" fill="#1a1410"/><circle cx="24" cy="25" r="1.6" fill="#1a1410"/><path d="M16 27 Q 15.2 29 16 30 Q 16.8 29 16 27" fill="#8a9bc4"/>',
    mouth: '<path d="M14.5 33 Q 20 28 25.5 33" stroke="#1a1410" stroke-width="1.5" fill="none" stroke-linecap="round"/>',
  },
  angry: {
    brows: '<line x1="12" y1="20" x2="17" y2="22" stroke="#1a1410" stroke-width="1.6" stroke-linecap="round"/><line x1="23" y1="22" x2="28" y2="20" stroke="#1a1410" stroke-width="1.6" stroke-linecap="round"/>',
    eyes:  '<path d="M14 25 L 18 25.5" stroke="#1a1410" stroke-width="2" stroke-linecap="round"/><path d="M22 25.5 L 26 25" stroke="#1a1410" stroke-width="2" stroke-linecap="round"/>',
    mouth: '<path d="M13.5 32 L 26.5 32 L 25 34 L 23 32.5 L 21 34 L 19 32.5 L 17 34 L 15 32.5 Z" fill="#1a1410"/>',
  },
  surprised: {
    eyes:  '<circle cx="16" cy="25.5" r="2.6" fill="#f3ecdc" stroke="#1a1410" stroke-width="1.2"/><circle cx="16" cy="25.5" r="1.2" fill="#1a1410"/><circle cx="24" cy="25.5" r="2.6" fill="#f3ecdc" stroke="#1a1410" stroke-width="1.2"/><circle cx="24" cy="25.5" r="1.2" fill="#1a1410"/>',
    mouth: '<ellipse cx="20" cy="32" rx="2.2" ry="2.6" fill="#1a1410"/>',
  },
  neutral: {
    eyes:  '<circle cx="16" cy="25" r="1.6" fill="#1a1410"/><circle cx="24" cy="25" r="1.6" fill="#1a1410"/>',
    mouth: '<line x1="16" y1="32" x2="24" y2="32" stroke="#1a1410" stroke-width="1.5" stroke-linecap="round"/>',
  },
  agree: {
    eyes:  '<path d="M13.5 25.5 Q 16 22.5 18.5 25.5" stroke="#1a1410" stroke-width="1.5" fill="none" stroke-linecap="round"/><path d="M21.5 25.5 Q 24 22.5 26.5 25.5" stroke="#1a1410" stroke-width="1.5" fill="none" stroke-linecap="round"/>',
    mouth: '<path d="M15.5 31 Q 20 34 24.5 31" stroke="#1a1410" stroke-width="1.5" fill="none" stroke-linecap="round"/>',
  },
  disagree: {
    eyes:  '<circle cx="16" cy="25" r="2.2" fill="#f3ecdc" stroke="#1a1410" stroke-width="1"/><circle cx="14.8" cy="25.2" r="1.1" fill="#1a1410"/><circle cx="24" cy="25" r="2.2" fill="#f3ecdc" stroke="#1a1410" stroke-width="1"/><circle cx="22.8" cy="25.2" r="1.1" fill="#1a1410"/>',
    mouth: '<path d="M14 32.5 Q 18 30, 22 32 Q 24.5 33, 26 31.5" stroke="#1a1410" stroke-width="1.5" fill="none" stroke-linecap="round"/>',
  },
};

/** Build the inline SVG markup string for one emotion. */
export function lafufuHeadSvg(emotion: string, size: number, colorOverride?: string): string {
  const f = FEATURES[emotion] ?? FEATURES.neutral;
  const color =
    colorOverride ??
    LAFUFU_EMOTION_COLORS[emotion] ??
    LAFUFU_EMOTION_COLORS.neutral;
  const tilt = f.earTilt ?? 0;
  const earL = `<g transform="rotate(${-tilt} 11 14)"><ellipse cx="11" cy="8.5" rx="3.4" ry="8" fill="${color}"/></g>`;
  const earR = `<g transform="rotate(${tilt} 29 14)"><ellipse cx="29" cy="8.5" rx="3.4" ry="8" fill="${color}"/></g>`;
  const w = size;
  const h = Math.round(size * 1.1);
  return (
    `<svg viewBox="0 0 40 44" width="${w}" height="${h}" xmlns="http://www.w3.org/2000/svg" style="display:inline-block;vertical-align:middle">` +
    earL +
    earR +
    '<path d="M6.5 21 C 6.5 13.5, 12 12.5, 20 12.5 C 28 12.5, 33.5 13.5, 33.5 21 C 33.5 33, 28 38.5, 20 38.5 C 12 38.5, 6.5 33, 6.5 21 Z" fill="' +
    color +
    '"/>' +
    '<ellipse cx="20" cy="25" rx="9.5" ry="9" fill="#f3ecdc"/>' +
    (f.brows ?? "") +
    f.eyes +
    f.mouth +
    "</svg>"
  );
}

/** Solid component rendering the head as inline SVG. */
export const LafufuHead: Component<{
  emotion: string;
  size?: number;
  color?: string;
  title?: string;
}> = (rawProps) => {
  const props = mergeProps({ size: 32 }, rawProps);
  return (
    <span
      role="img"
      aria-label={props.emotion}
      title={props.title ?? props.emotion}
      style={{
        display: "inline-flex",
        "vertical-align": "middle",
        "line-height": "0",
      }}
      innerHTML={lafufuHeadSvg(props.emotion, props.size, props.color)}
    />
  );
};
