import { Component, JSX } from "solid-js";

interface BlobProps {
  size?: number | string;
  color?: string;
  opacity?: number;
  blur?: number;
  variant?: 1 | 2;
  style?: JSX.CSSProperties;
  class?: string;
  drift?: boolean;
  delay?: number;
}

/** Decorative biomorphic blob — used as background atmosphere. Purely visual. */
export const Blob: Component<BlobProps> = (props) => {
  const size = () => (typeof props.size === "number" ? `${props.size}px` : props.size ?? "320px");
  const radius = () =>
    props.variant === 2
      ? "38% 62% 46% 54% / 56% 44% 56% 44%"
      : "54% 46% 62% 38% / 48% 56% 44% 52%";
  return (
    <div
      class={props.class}
      style={{
        position: "absolute",
        width: size(),
        height: size(),
        "border-radius": radius(),
        background: `radial-gradient(circle at 30% 30%, ${props.color ?? "var(--c-amber)"} 0%, transparent 70%)`,
        opacity: props.opacity ?? 0.35,
        filter: `blur(${props.blur ?? 30}px)`,
        "pointer-events": "none",
        animation: props.drift ? "drift 18s ease-in-out infinite" : undefined,
        "animation-delay": props.delay !== undefined ? `${props.delay}s` : undefined,
        ...(props.style ?? {}),
      }}
    />
  );
};
