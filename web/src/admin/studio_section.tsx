/**
 * Studio — secondary animator UI modeled on motherhaven/maven-demo.
 *
 * Two-panel layout: expression list on the left, editor on the right.
 * The editor stacks an expression header, frame steps + live animation
 * preview, and an at-bottom frame gallery the operator drags from.
 *
 * Each "frame" is a Lafufu pose. Clicking a frame opens a modal whose
 * servo sliders drive the head live (the missing piece in the original
 * Body tab). Drag-and-drop is provided by @thisbeyond/solid-dnd.
 *
 * All colours, typography, and motion come from the canonical design
 * tokens in src/index.css — no hardcoded greys/golds.
 */
import {
  Component,
  createEffect,
  createMemo,
  createSignal,
  For,
  onCleanup,
  Show,
} from "solid-js";
import {
  DragDropProvider,
  DragDropSensors,
  DragOverlay,
  SortableProvider,
  createDraggable,
  createDroppable,
  createSortable,
  type Id,
} from "@thisbeyond/solid-dnd";
import {
  api,
  type ExpressionDTO,
  type ExpressionStepDTO,
  type FrameDTO,
} from "../shared/api";
import { toast } from "../shared/toast";
import type { NatsWs } from "../shared/nats_ws";
import { createReactiveResource } from "../shared/reactive_resource";
import { lsGet, lsSet } from "../shared/local_storage";
import { LafufuHead } from "../shared/lafufu_head";
import { ImagePicker } from "./image_picker";

type Pose = { head_lr: number; head_ud: number; eye: number; jaw: number; brow: number };

const SLIDER_DEFS: Array<{ key: keyof Pose; glyph: string; label: string }> = [
  { key: "head_lr", glyph: "↔", label: "head lr" },
  { key: "head_ud", glyph: "↕", label: "head ud" },
  { key: "eye",     glyph: "◉", label: "eye" },
  { key: "jaw",     glyph: "▽", label: "jaw" },
  { key: "brow",    glyph: "︿", label: "brow" },
];

const DEFAULT_RANGES: Record<keyof Pose, readonly [number, number]> = {
  head_lr: [1828, 2298],
  head_ud: [2885, 3278],
  eye:     [1995, 2085],
  jaw:     [1594, 1811],
  brow:    [2056, 2087],
};

const poseOfFrame = (f: FrameDTO): Pose => ({
  head_lr: f.head_lr,
  head_ud: f.head_ud,
  eye: f.eye,
  jaw: f.jaw,
  brow: f.brow,
});

// updateFrame / updateExpression are full-replace PUTs: the backend overwrites
// every field from the body, and omitted fields fall back to schema defaults
// (steps=[], emotion=null, image=null, …). So a partial body silently wipes the
// fields it leaves out. These builders send the complete current object with a
// patch merged in, matching what the Head tab already does.
const frameUpdateBody = (f: FrameDTO, patch: Partial<FrameDTO>) => ({
  head_lr: f.head_lr,
  head_ud: f.head_ud,
  eye: f.eye,
  jaw: f.jaw,
  brow: f.brow,
  image: f.image,
  description: f.description,
  ...patch,
});

const exprUpdateBody = (e: ExpressionDTO, patch: Partial<ExpressionDTO>) => ({
  playback: e.playback,
  default_duration_ms: e.default_duration_ms,
  default_delay_ms: e.default_delay_ms,
  default_easing: e.default_easing,
  steps: e.steps,
  random_walk_config: e.random_walk_config,
  emotion: e.emotion,
  description: e.description,
  ...patch,
});

const imageUrlOf = (ref: string | null): string | null => {
  if (!ref) return null;
  const parts = ref.split("/");
  if (parts.length !== 3) return null;
  const [bucket, kind, name] = parts;
  if (bucket !== "letterheads" && bucket !== "sprites") return null;
  return api.imageFileUrl(bucket as "letterheads" | "sprites", kind, name);
};

/** Gallery render mode: image thumbnails (with base-image fallback) or the
 *  raw pose-data block per frame. */
type GalleryView = "thumb" | "data";

/** Bundled fallback sprite — used as a frame's thumbnail when it has no image
 *  of its own and the operator hasn't set animator.base_image. Resolves to
 *  assets/images/sprites/base.png via the "default" kind. */
const BASE_IMAGE_DEFAULT = "sprites/default/base.png";

// ─────────────────────────────────────────────────────────────
// Draggable: gallery frame (drag source — not in sortable list)
// ─────────────────────────────────────────────────────────────
const GalleryFrame: Component<{
  frame: FrameDTO;
  usageCount: number;
  view: GalleryView;
  /** Effective base-image ref shown when the frame has no image of its own. */
  baseImage: string;
  onClick: () => void;
}> = (props) => {
  const draggable = createDraggable(`gallery-${props.frame.name}`);
  const [hovered, setHovered] = createSignal(false);
  // The frame's own image, falling back to the base image.
  const url = createMemo(() => imageUrlOf(props.frame.image ?? props.baseImage));
  // Show the thumbnail only in "thumb" view; "data" always shows the pose block.
  const showThumb = createMemo(() => props.view === "thumb" && !!url());
  return (
    <div
      ref={draggable.ref}
      {...draggable.dragActivators}
      onClick={(e) => { e.stopPropagation(); props.onClick(); }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        position: "relative",
        background: "var(--c-shell)",
        border: hovered() ? "1px solid var(--c-mist)" : "1px solid var(--c-edge)",
        "border-radius": "var(--r-pebble)",
        padding: "6px",
        cursor: "grab",
        display: "flex",
        "align-items": "center",
        "justify-content": "center",
        "aspect-ratio": "1",
        transition: "border-color var(--t-fast), background var(--t-fast)",
        "user-select": "none",
        "touch-action": "none",
        "box-shadow": "inset 0 1px 0 rgba(255,240,210,.03)",
      }}
    >
      <Show
        when={showThumb()}
        fallback={
          <div
            class="f-mono"
            style={{
              color: "var(--c-stone)",
              "font-size": "10px",
              "text-align": "center",
              "line-height": "1.4",
            }}
          >
            <div style={{ color: "var(--c-cream)", "font-size": "11px", "margin-bottom": "4px" }}>
              {props.frame.name}
            </div>
            <div>lr {props.frame.head_lr}</div>
            <div>ud {props.frame.head_ud}</div>
            <div>e {props.frame.eye} j {props.frame.jaw} b {props.frame.brow}</div>
          </div>
        }
      >
        <img
          src={url()!}
          alt={props.frame.name}
          style={{
            width: "100%",
            height: "100%",
            "object-fit": "contain",
            "image-rendering": "pixelated",
            display: "block",
          }}
        />
      </Show>
      <Show when={props.usageCount > 0}>
        <div
          class="f-mono"
          style={{
            position: "absolute",
            top: "4px",
            right: "4px",
            background: "var(--c-moss)",
            color: "#1a2210",
            "font-size": "9px",
            "font-weight": "600",
            padding: "2px 6px",
            "border-radius": "var(--r-pill, 999px)",
          }}
        >
          ●{props.usageCount}
        </div>
      </Show>
      <Show when={props.frame.is_builtin}>
        <div
          class="f-mono"
          style={{
            position: "absolute",
            bottom: "4px",
            right: "4px",
            background: "rgba(15,11,8,.7)",
            color: "var(--c-stone)",
            "font-size": "8px",
            padding: "1px 5px",
            "border-radius": "var(--r-pill, 999px)",
            "letter-spacing": ".05em",
          }}
        >
          builtin
        </div>
      </Show>
      <Show when={showThumb()}>
        <div
          class="f-mono"
          style={{
            position: "absolute",
            bottom: "4px",
            left: "4px",
            background: "rgba(15,11,8,.75)",
            color: "var(--c-cream)",
            "font-size": "9px",
            padding: "1px 5px",
            "border-radius": "var(--r-pill, 999px)",
          }}
        >
          {props.frame.name}
        </div>
      </Show>
    </div>
  );
};

// ─────────────────────────────────────────────────────────────
// Sortable: step in expression frame strip
// ─────────────────────────────────────────────────────────────
const SortableStep: Component<{
  id: Id;
  index: number;
  step: ExpressionStepDTO;
  frame: FrameDTO | undefined;
  defaultDuration: number;
  defaultDelay: number;
  isPlaying: boolean;
  onClick: () => void;
  onRemove: () => void;
}> = (props) => {
  const sortable = createSortable(props.id);
  const [hovered, setHovered] = createSignal(false);
  const url = createMemo(() => (props.frame ? imageUrlOf(props.frame.image) : null));
  const border = () => {
    if (props.isPlaying) return "2px solid var(--c-amber)";
    if (hovered()) return "2px solid var(--c-mist)";
    return "2px solid var(--c-edge)";
  };
  const dur = () => props.step.duration_ms ?? props.defaultDuration;
  const delay = () => props.step.delay_ms ?? props.defaultDelay;
  return (
    <div
      ref={sortable.ref}
      {...sortable.dragActivators}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onClick={(e) => { e.stopPropagation(); props.onClick(); }}
      style={{
        position: "relative",
        width: "120px",
        height: "120px",
        "border-radius": "var(--r-pebble)",
        border: border(),
        cursor: "grab",
        background: props.isPlaying
          ? "linear-gradient(180deg, var(--c-raised), var(--c-membrane))"
          : "var(--c-shell)",
        "background-image": url() ? `url(${url()})` : undefined,
        "background-size": "contain",
        "background-position": "center",
        "background-repeat": "no-repeat",
        "image-rendering": "pixelated",
        transition: "border-color var(--t-fast), transform var(--t-fast)",
        "user-select": "none",
        "touch-action": "none",
        opacity: sortable.isActiveDraggable ? 0.4 : 1,
        "box-shadow": props.isPlaying
          ? "0 0 0 3px rgba(212,162,89,.18), inset 0 1px 0 rgba(255,240,210,.04)"
          : "inset 0 1px 0 rgba(255,240,210,.03)",
      }}
    >
      <div
        class="f-mono"
        style={{
          position: "absolute",
          top: "4px",
          left: "4px",
          background: "rgba(15,11,8,.7)",
          color: "var(--c-cream)",
          "font-size": "9px",
          padding: "1px 5px",
          "border-radius": "var(--r-pill, 999px)",
          "letter-spacing": ".04em",
        }}
      >
        {props.index + 1}
      </div>
      <div
        class="f-mono"
        style={{
          position: "absolute",
          bottom: "4px",
          left: "4px",
          right: "4px",
          background: "rgba(15,11,8,.78)",
          color: "var(--c-cream)",
          "font-size": "9px",
          padding: "2px 6px",
          "border-radius": "var(--r-pill, 999px)",
          display: "flex",
          "justify-content": "space-between",
          gap: "4px",
          overflow: "hidden",
        }}
      >
        <span style={{
          "white-space": "nowrap",
          overflow: "hidden",
          "text-overflow": "ellipsis",
        }}>
          {props.step.frame}
        </span>
        <span style={{ color: "var(--c-stone)", "flex-shrink": "0" }}>
          {delay() > 0 ? `+${delay()} / ` : ""}{dur()}ms
        </span>
      </div>
      <Show when={hovered()}>
        <button
          type="button"
          class="btn btn--micro"
          onClick={(e) => { e.stopPropagation(); props.onRemove(); }}
          style={{
            position: "absolute",
            top: "4px",
            right: "4px",
            background: "rgba(233,136,117,.16)",
            color: "var(--c-coral)",
            "border-color": "rgba(233,136,117,.4)",
            padding: "2px 7px",
          }}
        >
          ✕
        </button>
      </Show>
    </div>
  );
};

// ─────────────────────────────────────────────────────────────
// Droppable: expression frame strip (accepts gallery drops)
// ─────────────────────────────────────────────────────────────
const ExpressionDropZone: Component<{ children: any }> = (props) => {
  const droppable = createDroppable("expression-dropzone");
  return (
    <div
      ref={droppable.ref}
      class="pebble--inset"
      style={{
        flex: "1 1 0",
        "min-height": "0",
        "overflow-y": "auto",
        background: droppable.isActiveDroppable
          ? "rgba(149,176,122,.08)"
          : "var(--c-shell)",
        border: droppable.isActiveDroppable
          ? "1px dashed var(--c-moss)"
          : "1px dashed var(--c-edge)",
        "border-radius": "var(--r-pebble)",
        padding: "10px",
        display: "flex",
        gap: "8px",
        "flex-wrap": "wrap",
        transition: "border-color var(--t-fast), background var(--t-fast)",
        "align-content": "flex-start",
      }}
    >
      {props.children}
    </div>
  );
};

// ─────────────────────────────────────────────────────────────
// Pose slider card: 5 unlabeled servo sliders for the previewed frame.
// Sits in the editor row beside the preview; live-drives the head as you drag
// and saves the frame's pose on release. Disabled while playback cycles frames.
// ─────────────────────────────────────────────────────────────
const PoseSliderCard: Component<{
  frame: FrameDTO | undefined;
  ranges: Record<keyof Pose, readonly [number, number]>;
  disabled: boolean;
  onCommit: (frameName: string, pose: Pose, image: string | null) => void;
}> = (props) => {
  const [pose, setPose] = createSignal<Pose>(
    props.frame ? poseOfFrame(props.frame) : { head_lr: 0, head_ud: 0, eye: 0, jaw: 0, brow: 0 },
  );
  // Re-seed when the previewed frame changes (incl. as playback cycles).
  createEffect(() => {
    const f = props.frame;
    if (f) setPose(poseOfFrame(f));
  });

  let previewTimer: number | undefined;
  let pending: { servo: keyof Pose; value: number } | null = null;
  const schedulePreview = (servo: keyof Pose, value: number) => {
    pending = { servo, value };
    if (previewTimer != null) return;
    previewTimer = window.setTimeout(async () => {
      previewTimer = undefined;
      const p = pending;
      pending = null;
      if (p) { try { await api.animatorPreview(p.servo, p.value); } catch { /* no toast per twitch */ } }
    }, 40);
  };
  onCleanup(() => { if (previewTimer != null) window.clearTimeout(previewTimer); });

  const onInput = (key: keyof Pose, raw: string) => {
    const v = Number(raw);
    setPose((p) => ({ ...p, [key]: v }));
    schedulePreview(key, v);
  };
  const commit = () => {
    const f = props.frame;
    // Capture the image alongside the frame read here so the parent can't save
    // this pose against a different frame's image if the preview advances.
    if (f) props.onCommit(f.name, pose(), f.image);
  };

  return (
    <div
      class="pebble--inset"
      style={{
        width: "150px",
        "flex-shrink": "0",
        "border-radius": "var(--r-pebble)",
        padding: "14px 12px",
        display: "flex",
        "flex-direction": "column",
        "justify-content": "space-evenly",
        gap: "12px",
        "min-height": "0",
        opacity: props.frame && !props.disabled ? 1 : 0.45,
      }}
    >
      <For each={SLIDER_DEFS}>
        {(def) => (
          <input
            type="range"
            class="slider"
            min={props.ranges[def.key][0]}
            max={props.ranges[def.key][1]}
            value={pose()[def.key]}
            disabled={!props.frame || props.disabled}
            onInput={(ev) => onInput(def.key, ev.currentTarget.value)}
            onChange={commit}
            title={def.label}
            style={{ width: "100%" }}
          />
        )}
      </For>
    </div>
  );
};

// ─────────────────────────────────────────────────────────────
// Frame editor modal: pose sliders + image picker + timing
// ─────────────────────────────────────────────────────────────
type ModalState =
  | { kind: "gallery"; frameName: string }
  | { kind: "step"; frameName: string; exprName: string; stepIndex: number };

const FrameEditorModal: Component<{
  state: ModalState;
  frame: FrameDTO;
  expression: ExpressionDTO | null;
  ranges: Record<keyof Pose, readonly [number, number]>;
  defaultDuration: number;
  defaultDelay: number;
  onSaveFrame: (pose: Pose, image: string | null) => Promise<void>;
  onSaveStepTiming: (duration: number, delay: number) => Promise<void>;
  onRemoveStep: () => Promise<void>;
  onDeleteFrame: () => Promise<void>;
  onResetFrame: () => Promise<void>;
  onClose: () => void;
}> = (props) => {
  const [pose, setPose] = createSignal<Pose>(poseOfFrame(props.frame));
  const stepRef = () =>
    props.state.kind === "step" && props.expression
      ? props.expression.steps[props.state.stepIndex] ?? null
      : null;
  const [duration, setDuration] = createSignal(stepRef()?.duration_ms ?? props.defaultDuration);
  const [delay, setDelay] = createSignal(stepRef()?.delay_ms ?? props.defaultDelay);
  const [pickerOpen, setPickerOpen] = createSignal(false);
  const [pendingImage, setPendingImage] = createSignal<string | null | undefined>(undefined);

  // Sync local state when frame switches (modal stays mounted)
  createEffect(() => {
    setPose(poseOfFrame(props.frame));
    setPendingImage(undefined);
    setPickerOpen(false);
    const s = stepRef();
    setDuration(s?.duration_ms ?? props.defaultDuration);
    setDelay(s?.delay_ms ?? props.defaultDelay);
  });

  // Live drive lafufu while user drags sliders (debounced per servo)
  let previewTimer: number | undefined;
  let pendingPreview: { servo: keyof Pose; value: number } | null = null;
  const schedulePreview = (servo: keyof Pose, value: number) => {
    pendingPreview = { servo, value };
    if (previewTimer != null) return;
    previewTimer = window.setTimeout(async () => {
      previewTimer = undefined;
      const p = pendingPreview;
      pendingPreview = null;
      if (p) {
        try { await api.animatorPreview(p.servo, p.value); }
        catch { /* don't toast per twitch */ }
      }
    }, 40);
  };
  onCleanup(() => { if (previewTimer != null) window.clearTimeout(previewTimer); });

  const handleKey = (e: KeyboardEvent) => {
    if (e.key === "Escape") { e.preventDefault(); props.onClose(); }
  };

  const url = createMemo<string | null>(() => {
    const pending = pendingImage();
    const ref = pending !== undefined ? pending : props.frame.image;
    return imageUrlOf(ref);
  });

  const saveAll = async () => {
    // Save pose + image in a single full-body PUT so neither clobbers the other.
    const pending = pendingImage();
    const image = pending !== undefined ? pending : props.frame.image;
    await props.onSaveFrame(pose(), image);
    if (props.state.kind === "step") await props.onSaveStepTiming(duration(), delay());
    props.onClose();
  };

  const apply = async () => {
    try { await api.animatorSetPose(pose()); }
    catch (e) { toast.err("apply failed", (e as Error)?.message ?? String(e)); }
  };

  return (
    <div
      onClick={props.onClose}
      onKeyDown={handleKey}
      tabIndex={-1}
      ref={(el) => setTimeout(() => el?.focus(), 0)}
      style={{
        position: "fixed",
        top: "0",
        left: "0",
        right: "0",
        bottom: "0",
        background: "rgba(15,11,8,.78)",
        "backdrop-filter": "blur(8px) saturate(120%)",
        "-webkit-backdrop-filter": "blur(8px) saturate(120%)",
        display: "flex",
        "align-items": "center",
        "justify-content": "center",
        "z-index": "1001",
        animation: "fade-up .2s cubic-bezier(.2,.7,.3,1.1)",
      }}
    >
      <div
        class="pebble"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(940px, 92vw)",
          "max-height": "90vh",
          "overflow-y": "auto",
          padding: "22px 24px",
          color: "var(--c-bone)",
          border: "1px solid var(--c-edge)",
        }}
      >
        <header
          style={{
            display: "flex",
            "align-items": "baseline",
            "margin-bottom": "16px",
            gap: "12px",
            "flex-wrap": "wrap",
          }}
        >
          <h3
            class="f-display-roman"
            style={{
              margin: "0",
              "font-size": "22px",
              color: "var(--c-bone)",
            }}
          >
            {props.frame.name}
          </h3>
          <Show when={props.frame.is_builtin}>
            <span class="eyebrow" style={{ color: "var(--c-stone)" }}>
              builtin
            </span>
          </Show>
          <Show when={props.state.kind === "step"}>
            <span class="f-mono" style={{ color: "var(--c-mist)", "font-size": "11px" }}>
              step {(props.state as { stepIndex: number }).stepIndex + 1}
              {props.expression ? ` of ${props.expression.steps.length}` : ""}
            </span>
          </Show>
          <button
            type="button"
            class="btn btn--ghost btn--tiny"
            style={{ "margin-left": "auto" }}
            onClick={props.onClose}
          >
            close ✕
          </button>
        </header>

        <div style={{ display: "grid", "grid-template-columns": "260px 1fr", gap: "20px" }}>
          {/* Left: image / preview card */}
          <div style={{ display: "flex", "flex-direction": "column", gap: "10px" }}>
            <div
              class="pebble--inset"
              style={{
                height: "220px",
                "border-radius": "var(--r-pebble)",
                display: "flex",
                "align-items": "center",
                "justify-content": "center",
                overflow: "hidden",
                padding: "8px",
              }}
            >
              <Show
                when={url()}
                fallback={
                  <div
                    class="f-mono"
                    style={{ "text-align": "center", color: "var(--c-stone)", "font-size": "11px" }}
                  >
                    no image
                  </div>
                }
              >
                <img
                  src={url()!}
                  alt={props.frame.name}
                  style={{
                    "max-width": "100%",
                    "max-height": "100%",
                    "object-fit": "contain",
                    "image-rendering": "pixelated",
                  }}
                />
              </Show>
            </div>
            <div style={{ display: "flex", gap: "6px" }}>
              <button
                type="button"
                class="btn btn--tiny"
                style={{ flex: "1" }}
                onClick={() => setPickerOpen((o) => !o)}
              >
                {pickerOpen() ? "close picker" : "pick image"}
              </button>
              <button
                type="button"
                class="btn btn--primary btn--tiny"
                style={{ flex: "1" }}
                onClick={apply}
              >
                ▶ apply pose
              </button>
            </div>
            <Show when={pendingImage() !== undefined}>
              <div
                class="f-mono"
                style={{
                  color: "var(--c-amber)",
                  "font-size": "10px",
                  "text-align": "center",
                  "font-style": "italic",
                  "letter-spacing": ".04em",
                }}
              >
                image changed · save to persist
              </div>
            </Show>
          </div>

          {/* Right: sliders + timing */}
          <div style={{ display: "flex", "flex-direction": "column", gap: "12px" }}>
            <span class="eyebrow">servo pose</span>
            <For each={SLIDER_DEFS}>
              {(def) => {
                const range = () => props.ranges[def.key] ?? DEFAULT_RANGES[def.key];
                return (
                  <div style={{ display: "flex", "align-items": "center", gap: "10px" }}>
                    <span
                      style={{
                        "font-family": "var(--f-mono)",
                        color: "var(--c-mist)",
                        width: "26px",
                        "text-align": "center",
                        "font-size": "14px",
                      }}
                    >
                      {def.glyph}
                    </span>
                    <span
                      class="f-mono"
                      style={{
                        color: "var(--c-stone)",
                        "font-size": "10px",
                        width: "60px",
                      }}
                    >
                      {def.label}
                    </span>
                    <input
                      type="range"
                      class="slider"
                      min={range()[0]}
                      max={range()[1]}
                      value={pose()[def.key]}
                      onInput={(ev) => {
                        const v = Number(ev.currentTarget.value);
                        setPose((p) => ({ ...p, [def.key]: v }));
                        schedulePreview(def.key, v);
                      }}
                      style={{ flex: "1" }}
                    />
                    <span
                      class="f-mono f-num"
                      style={{
                        color: "var(--c-cream)",
                        width: "48px",
                        "text-align": "right",
                        "font-size": "11px",
                      }}
                    >
                      {pose()[def.key]}
                    </span>
                  </div>
                );
              }}
            </For>

            <Show when={props.state.kind === "step"}>
              <div
                style={{
                  "margin-top": "8px",
                  "padding-top": "12px",
                  "border-top": "1px solid var(--c-edge)",
                  display: "flex",
                  "flex-direction": "column",
                  gap: "8px",
                }}
              >
                <span class="eyebrow">step timing</span>
                <div style={{ display: "flex", gap: "16px", "align-items": "center" }}>
                  <label
                    class="f-mono"
                    style={{
                      display: "flex",
                      "align-items": "center",
                      gap: "6px",
                      color: "var(--c-mist)",
                      "font-size": "11px",
                    }}
                  >
                    duration
                    <input
                      type="number"
                      class="field"
                      min={0}
                      step={50}
                      value={duration()}
                      onInput={(e) => setDuration(Number(e.currentTarget.value))}
                      style={{ width: "84px", padding: "5px 10px", "font-size": "11px" }}
                    />
                    <span style={{ color: "var(--c-stone)" }}>ms</span>
                  </label>
                  <label
                    class="f-mono"
                    style={{
                      display: "flex",
                      "align-items": "center",
                      gap: "6px",
                      color: "var(--c-mist)",
                      "font-size": "11px",
                    }}
                  >
                    delay
                    <input
                      type="number"
                      class="field"
                      min={0}
                      step={50}
                      value={delay()}
                      onInput={(e) => setDelay(Number(e.currentTarget.value))}
                      style={{ width: "84px", padding: "5px 10px", "font-size": "11px" }}
                    />
                    <span style={{ color: "var(--c-stone)" }}>ms</span>
                  </label>
                </div>
              </div>
            </Show>
          </div>
        </div>

        <Show when={pickerOpen()}>
          <div
            style={{
              "margin-top": "20px",
              "padding-top": "16px",
              "border-top": "1px solid var(--c-edge)",
            }}
          >
            <ImagePicker
              bucket="sprites"
              current={pendingImage() !== undefined ? pendingImage() : props.frame.image}
              onPick={(ref) => { setPendingImage(ref); setPickerOpen(false); }}
            />
          </div>
        </Show>

        {/* Footer actions */}
        <div
          style={{
            display: "flex",
            gap: "8px",
            "margin-top": "22px",
            "padding-top": "16px",
            "border-top": "1px solid var(--c-edge)",
            "flex-wrap": "wrap",
          }}
        >
          <button type="button" class="btn btn--primary" onClick={saveAll}>
            save
          </button>
          <Show when={props.state.kind === "step"}>
            <button
              type="button"
              class="btn btn--coral btn--tiny"
              onClick={async () => { await props.onRemoveStep(); props.onClose(); }}
            >
              remove from expression
            </button>
          </Show>
          <Show when={props.state.kind === "gallery"}>
            <Show
              when={!props.frame.is_builtin}
              fallback={
                <button
                  type="button"
                  class="btn btn--tiny"
                  onClick={async () => { await props.onResetFrame(); }}
                  style={{ color: "var(--c-iris)", "border-color": "rgba(138,155,196,.5)" }}
                >
                  reset to defaults
                </button>
              }
            >
              <button
                type="button"
                class="btn btn--coral btn--tiny"
                onClick={async () => {
                  if (!window.confirm(`delete frame "${props.frame.name}"?`)) return;
                  await props.onDeleteFrame();
                  props.onClose();
                }}
              >
                delete frame
              </button>
            </Show>
          </Show>
          <button
            type="button"
            class="btn btn--ghost"
            style={{ "margin-left": "auto" }}
            onClick={props.onClose}
          >
            cancel
          </button>
        </div>
        <div
          class="f-mono"
          style={{
            color: "var(--c-stone)",
            "font-size": "10px",
            "margin-top": "10px",
            "text-align": "center",
            "letter-spacing": ".06em",
          }}
        >
          sliders drive lafufu live · save to persist · esc to close
        </div>
      </div>
    </div>
  );
};

// ─────────────────────────────────────────────────────────────
// Main Studio panel
// ─────────────────────────────────────────────────────────────
export const StudioSection: Component<{ nats: NatsWs }> = (props) => {
  const [expressions, refetchExprs] = createReactiveResource(
    async () => (await api.listExpressions()).items,
    ["expressions.changed"],
    props.nats,
  );
  const [frames, refetchFrames] = createReactiveResource(
    async () => (await api.listFrames()).items,
    ["frames.changed"],
    props.nats,
  );
  const [servoConfig] = createReactiveResource(
    async () => await api.getAnimatorConfig(),
    ["config.changed.animator.idle_pose"],
    props.nats,
  );

  // Track animator hardware health so the UI can explain "no U2D2 attached"
  // rather than silently doing nothing when the operator drags a slider.
  // undefined = no signal yet (assume hardware until proven otherwise);
  // true = u2d2 found and bus open; false = animator started without hardware.
  const [hasU2d2, setHasU2d2] = createSignal<boolean | undefined>(undefined);
  const unsubAnimatorState = props.nats.subscribe("animator.state.*", (f) => {
    const v = (f.payload as { has_u2d2?: boolean })?.has_u2d2;
    if (typeof v === "boolean") setHasU2d2(v);
  });
  onCleanup(unsubAnimatorState);

  const ranges = createMemo<Record<keyof Pose, readonly [number, number]>>(() => {
    const cfg = servoConfig();
    if (!cfg?.ranges) return DEFAULT_RANGES;
    return {
      head_lr: cfg.ranges.head_lr ?? DEFAULT_RANGES.head_lr,
      head_ud: cfg.ranges.head_ud ?? DEFAULT_RANGES.head_ud,
      eye:     cfg.ranges.eye ?? DEFAULT_RANGES.eye,
      jaw:     cfg.ranges.jaw ?? DEFAULT_RANGES.jaw,
      brow:    cfg.ranges.brow ?? DEFAULT_RANGES.brow,
    };
  });

  const [selectedExpr, setSelectedExpr] = createSignal<string | null>(
    lsGet<string | null>("studio/selectedExpr", null),
  );
  createEffect(() => { lsSet("studio/selectedExpr", selectedExpr()); });

  createEffect(() => {
    const list = expressions();
    if (!list || selectedExpr()) return;
    if (list.length > 0) setSelectedExpr(list[0].name);
  });

  const [newExprName, setNewExprName] = createSignal("");
  const [playing, setPlaying] = createSignal(false);
  const [frameIndex, setFrameIndex] = createSignal(0);
  const [activeId, setActiveId] = createSignal<Id | null>(null);
  const [modal, setModal] = createSignal<ModalState | null>(null);

  const [gallerySize, setGallerySize] = createSignal<0 | 1 | 2>(
    lsGet<0 | 1 | 2>("studio/gallerySize", 1),
  );
  createEffect(() => { lsSet("studio/gallerySize", gallerySize()); });

  // Gallery render mode — thumbnails (with base-image fallback) vs pose data.
  const [galleryView, setGalleryView] = createSignal<GalleryView>(
    lsGet<GalleryView>("studio/galleryView", "thumb"),
  );
  createEffect(() => { lsSet("studio/galleryView", galleryView()); });

  // animator.base_image — the sprite shown as a thumbnail for any frame with no
  // image of its own. Empty/unset falls back to the bundled default. Studio-only
  // (no compositing, no runtime effect).
  const [baseImage, setBaseImage] = createSignal<string | null>(null);
  const [basePickerOpen, setBasePickerOpen] = createSignal(false);
  const effectiveBase = createMemo(() => baseImage() ?? BASE_IMAGE_DEFAULT);
  (async () => {
    try {
      const items = (await api.listSettings()) as Array<{ key: string; value: string }>;
      const row = items.find((r) => r.key === "animator.base_image");
      if (row) setBaseImage(row.value || null);
    } catch {
      /* settings load failure — keep the bundled default */
    }
  })();
  const onPickBase = async (ref: string | null) => {
    setBasePickerOpen(false);
    const prev = baseImage();
    setBaseImage(ref);
    try {
      await api.patchSetting("animator.base_image", { value: ref ?? "", value_type: "str" });
      toast.ok(ref ? "base image updated" : "base image cleared");
    } catch (e: unknown) {
      setBaseImage(prev);  // revert on failure
      toast.err("base image failed", (e as Error)?.message ?? String(e));
    }
  };

  const framesByName = createMemo<Map<string, FrameDTO>>(() => {
    const m = new Map<string, FrameDTO>();
    for (const f of frames() ?? []) m.set(f.name, f);
    return m;
  });
  const frameUsage = createMemo<Map<string, number>>(() => {
    const m = new Map<string, number>();
    for (const e of expressions() ?? []) {
      for (const s of e.steps) m.set(s.frame, (m.get(s.frame) ?? 0) + 1);
    }
    return m;
  });

  const currentExpr = createMemo<ExpressionDTO | null>(() => {
    const name = selectedExpr();
    if (!name) return null;
    return expressions()?.find((e) => e.name === name) ?? null;
  });

  // The frame currently shown in the editor preview (the step at the play
  // index). Shared by the preview pane and the pose-slider card beside it.
  const previewIdx = createMemo(() => {
    const e = currentExpr();
    if (!e || e.steps.length === 0) return 0;
    return Math.min(frameIndex(), e.steps.length - 1);
  });
  const previewFrame = createMemo<FrameDTO | undefined>(() => {
    const e = currentExpr();
    if (!e || e.steps.length === 0) return undefined;
    return framesByName().get(e.steps[previewIdx()]?.frame ?? "");
  });

  let playTimer: number | undefined;
  const stopPlayback = () => {
    if (playTimer != null) { window.clearTimeout(playTimer); playTimer = undefined; }
  };
  onCleanup(stopPlayback);

  const startPlayback = () => {
    stopPlayback();
    setPlaying(true);
    setFrameIndex(0);
    const run = (idx: number) => {
      const expr = currentExpr();
      if (!expr || expr.steps.length === 0) { setPlaying(false); return; }
      const step = expr.steps[idx];
      const fr = framesByName().get(step.frame);
      const delay = step.delay_ms ?? expr.default_delay_ms ?? 0;
      const duration = step.duration_ms ?? expr.default_duration_ms ?? 200;
      playTimer = window.setTimeout(() => {
        setFrameIndex(idx);
        if (fr) {
          api.animatorSetPose(poseOfFrame(fr)).catch(() => undefined);
        }
        playTimer = window.setTimeout(() => {
          const next = idx + 1;
          if (next >= expr.steps.length) {
            if (expr.playback === "loop") run(0);
            else { setPlaying(false); }
          } else {
            run(next);
          }
        }, duration);
      }, delay);
    };
    run(0);
  };

  createEffect(() => {
    selectedExpr();
    stopPlayback();
    setPlaying(false);
    setFrameIndex(0);
  });

  // ─── actions ───
  const apiCall = async (fn: () => Promise<unknown>, label: string) => {
    try { await fn(); }
    catch (e) { toast.err(`${label} failed`, (e as Error)?.message ?? String(e)); }
  };

  const createExpression = async () => {
    const raw = newExprName().trim();
    if (!raw) return;
    const name = raw.toLowerCase().replace(/\s+/g, "_");
    try {
      await api.createExpression({ name, steps: [], playback: "loop" });
      await refetchExprs();
      setNewExprName("");
      setSelectedExpr(name);
      toast.ok(`created ${name}`);
    } catch (e) {
      toast.err("create failed", (e as Error)?.message ?? String(e));
    }
  };

  const deleteCurrentExpression = async () => {
    const e = currentExpr();
    if (!e) return;
    if (e.is_builtin) { toast.err("cannot delete builtin"); return; }
    if (!window.confirm(`delete expression "${e.name}"?`)) return;
    await apiCall(() => api.deleteExpression(e.name), "delete");
    await refetchExprs();
    setSelectedExpr(null);
  };

  const resetCurrentExpression = async () => {
    const e = currentExpr();
    if (!e) return;
    if (!window.confirm(`reset "${e.name}" to factory defaults?`)) return;
    await apiCall(() => api.resetExpression(e.name), "reset");
    await refetchExprs();
  };

  const toggleLoop = async () => {
    const e = currentExpr();
    if (!e) return;
    const next: ExpressionDTO["playback"] = e.playback === "loop" ? "once" : "loop";
    await apiCall(
      () => api.updateExpression(e.name, exprUpdateBody(e, { playback: next })),
      "loop toggle",
    );
    await refetchExprs();
  };

  const persistSteps = async (steps: ExpressionStepDTO[]) => {
    const e = currentExpr();
    if (!e) return;
    await apiCall(
      () => api.updateExpression(e.name, exprUpdateBody(e, { steps })),
      "save steps",
    );
    await refetchExprs();
  };

  const addStepFromFrame = async (frameName: string) => {
    const e = currentExpr();
    if (!e) return;
    await persistSteps([...e.steps, { frame: frameName }]);
  };

  const removeStep = async (idx: number) => {
    const e = currentExpr();
    if (!e) return;
    const next = e.steps.slice();
    next.splice(idx, 1);
    await persistSteps(next);
  };

  const reorderSteps = async (from: number, to: number) => {
    const e = currentExpr();
    if (!e) return;
    if (from === to || Number.isNaN(from) || Number.isNaN(to)) return;
    const next = e.steps.slice();
    const [m] = next.splice(from, 1);
    next.splice(to, 0, m);
    await persistSteps(next);
  };

  const updateStepTiming = async (idx: number, duration_ms: number, delay_ms: number) => {
    const e = currentExpr();
    if (!e) return;
    const next = e.steps.slice();
    next[idx] = { ...next[idx], duration_ms, delay_ms };
    await persistSteps(next);
  };

  const saveFrame = async (frameName: string, pose: Pose, image: string | null) => {
    const f = framesByName().get(frameName);
    if (!f) {
      // Cache miss (e.g. the frame was deleted while its editor was open) —
      // surface it instead of silently dropping the operator's edit.
      toast.err("save failed", `frame "${frameName}" not found — try refreshing`);
      return;
    }
    await apiCall(
      () => api.updateFrame(frameName, frameUpdateBody(f, { ...pose, image })),
      "save frame",
    );
    await refetchFrames();
  };

  const deleteFrame = async (frameName: string) => {
    await apiCall(() => api.deleteFrame(frameName), "delete frame");
    await refetchFrames();
  };

  const resetFrame = async (frameName: string) => {
    await apiCall(() => api.resetFrame(frameName), "reset frame");
    await refetchFrames();
  };

  const snapshotFrame = async () => {
    const name = window.prompt("new frame name (lowercase, _underscores):");
    if (!name) return;
    const trimmed = name.trim();
    try {
      await api.snapshotFrame(trimmed);
      await refetchFrames();
      toast.ok(`snapshot saved · ${trimmed}`);
    } catch (e) {
      toast.err("snapshot failed", (e as Error)?.message ?? String(e));
    }
  };

  // ─── drag handlers ───
  const onDragStart = (ev: { draggable: { id: Id } | null }) => {
    if (ev.draggable) setActiveId(ev.draggable.id);
  };
  const onDragEnd = async (ev: { draggable: { id: Id } | null; droppable?: { id: Id } | null }) => {
    const did = ev.draggable?.id;
    const ddid = ev.droppable?.id;
    setActiveId(null);
    if (!did || !ddid) return;
    const ds = String(did);
    const dds = String(ddid);

    if (ds.startsWith("gallery-") && (dds === "expression-dropzone" || dds.startsWith("step-"))) {
      const frameName = ds.slice("gallery-".length);
      await addStepFromFrame(frameName);
      return;
    }
    if (ds.startsWith("step-") && dds.startsWith("step-")) {
      const from = parseInt(ds.slice("step-".length), 10);
      const to = parseInt(dds.slice("step-".length), 10);
      await reorderSteps(from, to);
      return;
    }
  };

  // ─── modal helpers ───
  const openGalleryFrame = (frameName: string) => {
    setModal({ kind: "gallery", frameName });
    const fr = framesByName().get(frameName);
    if (fr) api.animatorSetPose(poseOfFrame(fr)).catch(() => undefined);
  };
  const openStepFrame = (stepIndex: number, frameName: string) => {
    const e = currentExpr();
    if (!e) return;
    setModal({ kind: "step", frameName, exprName: e.name, stepIndex });
    const fr = framesByName().get(frameName);
    if (fr) api.animatorSetPose(poseOfFrame(fr)).catch(() => undefined);
  };
  const modalFrame = createMemo<FrameDTO | null>(() => {
    const m = modal();
    if (!m) return null;
    return framesByName().get(m.frameName) ?? null;
  });

  const onKey = (e: KeyboardEvent) => {
    if (modal()) return;
    const expr = currentExpr();
    if (!expr || expr.steps.length === 0) return;
    if (e.key === " ") {
      e.preventDefault();
      if (playing()) { stopPlayback(); setPlaying(false); }
      else { startPlayback(); }
    }
    if (e.key === "ArrowLeft") {
      e.preventDefault();
      const idx = frameIndex();
      const next = idx <= 0 ? expr.steps.length - 1 : idx - 1;
      setFrameIndex(next);
      const fr = framesByName().get(expr.steps[next].frame);
      if (fr) api.animatorSetPose(poseOfFrame(fr)).catch(() => undefined);
    }
    if (e.key === "ArrowRight") {
      e.preventDefault();
      const idx = frameIndex();
      const next = idx >= expr.steps.length - 1 ? 0 : idx + 1;
      setFrameIndex(next);
      const fr = framesByName().get(expr.steps[next].frame);
      if (fr) api.animatorSetPose(poseOfFrame(fr)).catch(() => undefined);
    }
  };

  return (
    <div
      tabIndex={0}
      onKeyDown={onKey}
      class="pebble"
      style={{
        "font-family": "var(--f-sans)",
        color: "var(--c-bone)",
        display: "flex",
        "flex-direction": "column",
        height: "calc(75vh + 50px)",
        "min-height": "610px",
        overflow: "hidden",
        outline: "none",
        padding: "0",
      }}
    >
      {/* No-hardware banner — appears only when the animator service has come
          up but couldn't open a U2D2 bus (developing without a Pi, USB
          unplugged, etc). Sliders + play still work as recorded intents; the
          servos just don't physically respond. */}
      <Show when={hasU2d2() === false}>
        <div
          style={{
            display: "flex",
            "align-items": "center",
            gap: "10px",
            padding: "10px 18px",
            background: "rgba(212,162,89,.10)",
            "border-bottom": "1px solid rgba(212,162,89,.4)",
            color: "var(--c-amber)",
            "font-family": "var(--f-mono)",
            "font-size": "11px",
            "letter-spacing": ".04em",
            "flex-shrink": "0",
          }}
          title="animator service is running, but no U2D2 / Dynamixel servos were found on the USB bus"
        >
          <span style={{ "font-size": "14px" }}>⚠</span>
          <span>
            <span style={{ color: "var(--c-cream)" }}>animator running without hardware.</span>
            {" "}
            edits save fine · sliders won't move the servos
          </span>
        </div>
      </Show>

      <div style={{ display: "flex", flex: "1 1 0", "min-height": "0" }}>
      <DragDropProvider onDragStart={onDragStart} onDragEnd={onDragEnd}>
        <DragDropSensors />

        {/* Left: expression list */}
        <div
          style={{
            width: "248px",
            "min-width": "248px",
            background: "var(--c-shell)",
            "border-right": "1px solid var(--c-edge)",
            padding: "16px",
            display: "flex",
            "flex-direction": "column",
            "border-radius": "var(--r-pebble) 0 0 var(--r-pebble)",
          }}
        >
          <div class="eyebrow" style={{ "margin-bottom": "14px" }}>expressions</div>

          <input
            type="text"
            class="field"
            value={newExprName()}
            onInput={(e) => setNewExprName(e.currentTarget.value)}
            onKeyDown={(e) => { if (e.key === "Enter") createExpression(); }}
            placeholder="new expression…"
            style={{
              width: "100%",
              padding: "7px 10px",
              "font-size": "12px",
              "margin-bottom": "12px",
            }}
          />

          <div style={{ flex: "1", "overflow-y": "auto", display: "flex", "flex-direction": "column", gap: "5px" }}>
            <For each={expressions() ?? []}>
              {(e) => {
                const active = () => selectedExpr() === e.name;
                return (
                  <div
                    onClick={() => { setSelectedExpr(e.name); setFrameIndex(0); }}
                    style={{
                      padding: "9px 11px",
                      background: active()
                        ? "linear-gradient(180deg, var(--c-raised), var(--c-membrane))"
                        : "var(--c-deep)",
                      border: active()
                        ? "1px solid var(--c-moss)"
                        : "1px solid var(--c-edge)",
                      "border-radius": "12px",
                      cursor: "pointer",
                      transition: "background var(--t-fast), border-color var(--t-fast)",
                      "box-shadow": active()
                        ? "0 0 0 3px rgba(149,176,122,.12), inset 0 1px 0 rgba(255,240,210,.04)"
                        : "inset 0 1px 0 rgba(255,240,210,.02)",
                      display: "flex",
                      "align-items": "center",
                      gap: "10px",
                    }}
                  >
                    <Show when={e.emotion}>
                      <LafufuHead emotion={e.emotion!} size={22} />
                    </Show>
                    <div style={{ "min-width": "0", flex: "1" }}>
                      <div
                        class="f-mono"
                        style={{
                          color: active() ? "var(--c-bone)" : "var(--c-cream)",
                          "font-weight": active() ? "600" : "400",
                          "font-size": "12px",
                          "letter-spacing": ".02em",
                          display: "flex",
                          "align-items": "center",
                          gap: "6px",
                        }}
                      >
                        {e.name}
                        <Show when={e.is_builtin}>
                          <span style={{ color: "var(--c-stone)", "font-size": "9px" }}>builtin</span>
                        </Show>
                      </div>
                      <div
                        class="f-mono"
                        style={{
                          "font-size": "10px",
                          color: "var(--c-stone)",
                          "margin-top": "2px",
                          "letter-spacing": ".04em",
                        }}
                      >
                        {e.steps.length} frame{e.steps.length === 1 ? "" : "s"} · {e.playback}
                      </div>
                    </div>
                  </div>
                );
              }}
            </For>
          </div>
        </div>

        {/* Right: editor */}
        <div
          style={{
            flex: "1",
            "min-width": "0",
            display: "flex",
            "flex-direction": "column",
            background: "var(--c-deep)",
            overflow: "hidden",
          }}
        >
          <Show
            when={currentExpr()}
            fallback={
              <div
                style={{
                  flex: "1",
                  padding: "40px",
                  "text-align": "center",
                  color: "var(--c-stone)",
                  display: "flex",
                  "align-items": "center",
                  "justify-content": "center",
                  "font-size": "13px",
                  "font-style": "italic",
                  "font-family": "var(--f-display)",
                }}
              >
                select or create an expression
              </div>
            }
          >
            {(e) => (
              <div
                style={{
                  flex: "1",
                  padding: "18px",
                  "min-height": "0",
                  display: "flex",
                  "flex-direction": "column",
                  overflow: "hidden",
                }}
              >
                {/* Header */}
                <div
                  style={{
                    display: "flex",
                    "align-items": "center",
                    gap: "12px",
                    "margin-bottom": "14px",
                    "flex-wrap": "wrap",
                  }}
                >
                  <Show when={e().emotion}>
                    <LafufuHead emotion={e().emotion!} size={36} />
                  </Show>
                  <h3
                    class="f-display-roman"
                    style={{ margin: "0", "font-size": "24px", color: "var(--c-bone)" }}
                  >
                    {e().name}
                  </h3>
                  <Show when={e().emotion}>
                    <span
                      class="f-mono"
                      style={{
                        color: "var(--c-stone)",
                        "font-size": "10px",
                        "letter-spacing": ".18em",
                        "text-transform": "uppercase",
                      }}
                    >
                      {e().emotion}
                    </span>
                  </Show>
                  <label
                    class="f-mono"
                    style={{
                      color: "var(--c-mist)",
                      "font-size": "11px",
                      display: "flex",
                      "align-items": "center",
                      gap: "6px",
                      cursor: "pointer",
                      "user-select": "none",
                      "letter-spacing": ".04em",
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={e().playback === "loop"}
                      onChange={toggleLoop}
                      style={{ "accent-color": "var(--c-moss)" }}
                    />
                    loop
                  </label>
                  <button
                    type="button"
                    class={playing() ? "btn btn--coral" : "btn btn--primary"}
                    onClick={() => {
                      if (playing()) { stopPlayback(); setPlaying(false); }
                      else { startPlayback(); }
                    }}
                    disabled={e().steps.length === 0}
                    style={{
                      "min-width": "82px",
                    }}
                  >
                    {playing() ? "❚❚ stop" : "▶ play"}
                  </button>
                  <span
                    class="f-mono"
                    style={{
                      color: "var(--c-stone)",
                      "font-size": "10px",
                      "letter-spacing": ".04em",
                    }}
                  >
                    space=play · ←→=nav · drag frames to add
                  </span>
                  <Show when={e().is_builtin}>
                    <button
                      type="button"
                      class="btn btn--tiny"
                      onClick={resetCurrentExpression}
                      style={{
                        "margin-left": "auto",
                        color: "var(--c-iris)",
                        "border-color": "rgba(138,155,196,.5)",
                      }}
                    >
                      reset
                    </button>
                  </Show>
                  <Show when={!e().is_builtin}>
                    <button
                      type="button"
                      class="btn btn--coral btn--tiny"
                      onClick={deleteCurrentExpression}
                      style={{ "margin-left": "auto" }}
                    >
                      delete
                    </button>
                  </Show>
                </div>

                {/* Frames + preview side-by-side */}
                <div
                  style={{
                    display: "flex",
                    gap: "16px",
                    "align-items": "stretch",
                    flex: "1",
                    "min-height": "0",
                  }}
                >
                  <div
                    style={{
                      width: "544px",
                      "max-width": "544px",
                      "flex-shrink": "0",
                      display: "flex",
                      "flex-direction": "column",
                    }}
                  >
                    <ExpressionDropZone>
                      <SortableProvider ids={e().steps.map((_, i) => `step-${i}`) as Id[]}>
                        <For each={e().steps}>
                          {(step, i) => (
                            <SortableStep
                              id={`step-${i()}`}
                              index={i()}
                              step={step}
                              frame={framesByName().get(step.frame)}
                              defaultDuration={e().default_duration_ms}
                              defaultDelay={e().default_delay_ms}
                              isPlaying={playing() && frameIndex() === i()}
                              onClick={() => openStepFrame(i(), step.frame)}
                              onRemove={() => removeStep(i())}
                            />
                          )}
                        </For>
                      </SortableProvider>
                      <Show when={e().steps.length === 0}>
                        <div
                          style={{
                            color: "var(--c-stone)",
                            padding: "20px",
                            "text-align": "center",
                            width: "100%",
                            "font-size": "12px",
                            "font-style": "italic",
                            "font-family": "var(--f-display)",
                          }}
                        >
                          drag frames here from the gallery below
                        </div>
                      </Show>
                    </ExpressionDropZone>
                  </div>

                  <div
                    class="pebble--inset"
                    style={{
                      flex: "1",
                      display: "flex",
                      "align-items": "center",
                      "justify-content": "center",
                      padding: "14px",
                      "border-radius": "var(--r-pebble)",
                      position: "relative",
                      overflow: "hidden",
                      "min-height": "0",
                    }}
                  >
                    <Show
                      when={e().steps.length > 0}
                      fallback={
                        <div
                          style={{
                            color: "var(--c-stone)",
                            "font-family": "var(--f-display)",
                            "font-size": "13px",
                            display: "flex",
                            "flex-direction": "column",
                            "align-items": "center",
                            gap: "10px",
                            "font-style": "italic",
                          }}
                        >
                          <Show when={e().emotion}>
                            <LafufuHead emotion={e().emotion!} size={96} />
                          </Show>
                          no frames
                        </div>
                      }
                    >
                      {(() => {
                        const safeIdx = createMemo(() => Math.min(frameIndex(), e().steps.length - 1));
                        const currentFrame = createMemo(() => framesByName().get(e().steps[safeIdx()]?.frame ?? ""));
                        const previewUrl = createMemo(() => {
                          const fr = currentFrame();
                          return fr ? imageUrlOf(fr.image) : null;
                        });
                        return (
                          <>
                            <Show
                              when={previewUrl()}
                              fallback={
                                <div
                                  class="f-mono"
                                  style={{
                                    color: "var(--c-mist)",
                                    "text-align": "center",
                                    "font-size": "11px",
                                    "line-height": "1.7",
                                  }}
                                >
                                  <Show when={currentFrame()}>
                                    {(fr) => (
                                      <>
                                        <div
                                          class="f-display-roman"
                                          style={{
                                            "font-size": "18px",
                                            color: "var(--c-bone)",
                                            "margin-bottom": "10px",
                                          }}
                                        >
                                          {fr().name}
                                        </div>
                                        <div>head_lr {fr().head_lr}</div>
                                        <div>head_ud {fr().head_ud}</div>
                                        <div>eye {fr().eye}</div>
                                        <div>jaw {fr().jaw}</div>
                                        <div>brow {fr().brow}</div>
                                      </>
                                    )}
                                  </Show>
                                </div>
                              }
                            >
                              <img
                                src={previewUrl()!}
                                alt="preview"
                                style={{
                                  "max-width": "100%",
                                  "max-height": "100%",
                                  "object-fit": "contain",
                                  "image-rendering": "pixelated",
                                }}
                              />
                            </Show>
                            <div
                              class="f-mono f-num"
                              style={{
                                position: "absolute",
                                top: "10px",
                                right: "12px",
                                background: playing()
                                  ? "rgba(212,162,89,.18)"
                                  : "rgba(15,11,8,.6)",
                                color: playing() ? "var(--c-amber)" : "var(--c-mist)",
                                "font-size": "11px",
                                padding: "4px 10px",
                                "border-radius": "var(--r-pill, 999px)",
                                border: playing()
                                  ? "1px solid rgba(212,162,89,.5)"
                                  : "1px solid var(--c-edge)",
                                "letter-spacing": ".04em",
                              }}
                            >
                              {playing() ? "▶" : "■"} {safeIdx() + 1}/{e().steps.length}
                            </div>
                          </>
                        );
                      })()}
                    </Show>
                  </div>

                  <PoseSliderCard
                    frame={previewFrame()}
                    ranges={ranges()}
                    disabled={playing()}
                    onCommit={(name, pose, image) => saveFrame(name, pose, image)}
                  />
                </div>
              </div>
            )}
          </Show>

          {/* Gallery toggle bar */}
          <div
            style={{
              height: "36px",
              background: "var(--c-shell)",
              "border-top": "1px solid var(--c-edge)",
              display: "flex",
              "align-items": "center",
              padding: "0 18px",
              gap: "10px",
              "flex-shrink": "0",
            }}
          >
            <span class="eyebrow">all frames ({frames()?.length ?? 0})</span>
            <button
              type="button"
              class="btn btn--micro"
              onClick={snapshotFrame}
              style={{
                color: "var(--c-iris)",
                "border-color": "rgba(138,155,196,.4)",
              }}
            >
              + snapshot current pose
            </button>
            <div style={{ flex: "1" }} />
            {/* View mode: image thumbnails (with base fallback) vs pose data */}
            <For each={["thumb", "data"] as const}>
              {(v) => (
                <button
                  type="button"
                  class={galleryView() === v ? "btn btn--primary btn--micro" : "btn btn--micro"}
                  onClick={() => setGalleryView(v)}
                >
                  {v === "thumb" ? "thumbnails" : "data"}
                </button>
              )}
            </For>
            {/* Base image — fallback thumbnail for frames without their own */}
            <button
              type="button"
              class="btn btn--micro"
              onClick={() => setBasePickerOpen((o) => !o)}
              title="Set the fallback image shown for frames without their own image"
              style={{ display: "flex", "align-items": "center", gap: "6px" }}
            >
              <Show
                when={imageUrlOf(effectiveBase())}
                fallback={<span class="f-mono" style={{ "font-size": "9px", color: "var(--c-stone)" }}>none</span>}
              >
                <img
                  src={imageUrlOf(effectiveBase())!}
                  alt="base"
                  style={{
                    width: "18px",
                    height: "18px",
                    "object-fit": "contain",
                    "image-rendering": "pixelated",
                    "border-radius": "3px",
                  }}
                />
              </Show>
              base image
            </button>
            <div style={{ width: "1px", height: "18px", background: "var(--c-edge)" }} />
            <For each={["hide", "normal", "large"] as const}>
              {(label, i) => (
                <button
                  type="button"
                  class={gallerySize() === i() ? "btn btn--primary btn--micro" : "btn btn--micro"}
                  onClick={() => setGallerySize(i() as 0 | 1 | 2)}
                >
                  {label}
                </button>
              )}
            </For>
          </div>

          {/* Base image picker — opens inline below the toggle bar */}
          <Show when={basePickerOpen()}>
            <div
              style={{
                padding: "12px 18px",
                background: "var(--c-deep)",
                "border-top": "1px solid var(--c-edge)",
              }}
            >
              <div class="eyebrow" style={{ "margin-bottom": "8px" }}>
                base image — shown for frames without their own image
              </div>
              <ImagePicker
                bucket="sprites"
                current={baseImage() ?? undefined}
                onPick={onPickBase}
              />
            </div>
          </Show>

          <Show when={gallerySize() > 0}>
            <div
              style={{
                "max-height": gallerySize() === 2 ? "340px" : "208px",
                padding: "14px 18px",
                "overflow-y": "auto",
                "flex-shrink": "0",
                background: "var(--c-deep)",
              }}
            >
              <div
                style={{
                  display: "grid",
                  "grid-template-columns": `repeat(auto-fill, minmax(${gallerySize() === 2 ? 160 : 124}px, 1fr))`,
                  gap: "12px",
                }}
              >
                <For each={frames() ?? []}>
                  {(f) => (
                    <GalleryFrame
                      frame={f}
                      usageCount={frameUsage().get(f.name) ?? 0}
                      view={galleryView()}
                      baseImage={effectiveBase()}
                      onClick={() => openGalleryFrame(f.name)}
                    />
                  )}
                </For>
              </div>
            </div>
          </Show>
        </div>

        {/* Drag overlay */}
        <DragOverlay>
          {(() => {
            const id = activeId();
            if (!id) return null;
            const s = String(id);
            if (s.startsWith("gallery-")) {
              const fr = framesByName().get(s.slice("gallery-".length));
              if (!fr) return null;
              const url = imageUrlOf(fr.image);
              return (
                <div
                  class="pebble"
                  style={{
                    border: "1px solid var(--c-amber)",
                    "border-radius": "var(--r-pebble)",
                    padding: "4px",
                    opacity: 0.94,
                    width: "120px",
                    height: "120px",
                    "background-image": url ? `url(${url})` : undefined,
                    "background-size": "contain",
                    "background-position": "center",
                    "background-repeat": "no-repeat",
                    display: "flex",
                    "align-items": "flex-end",
                    "justify-content": "center",
                    color: "var(--c-mist)",
                    "font-size": "10px",
                    "font-family": "var(--f-mono)",
                  }}
                >
                  {url ? "" : fr.name}
                </div>
              );
            }
            if (s.startsWith("step-")) {
              return (
                <div
                  class="pebble"
                  style={{
                    border: "1px solid var(--c-iris)",
                    "border-radius": "var(--r-pebble)",
                    padding: "8px",
                    opacity: 0.88,
                    width: "120px",
                    height: "120px",
                    color: "var(--c-iris)",
                    "font-family": "var(--f-mono)",
                    "font-size": "11px",
                    display: "flex",
                    "align-items": "center",
                    "justify-content": "center",
                    "letter-spacing": ".04em",
                  }}
                >
                  reorder
                </div>
              );
            }
            return null;
          })()}
        </DragOverlay>
      </DragDropProvider>
      </div>

      <Show when={modal() && modalFrame()}>
        {(() => {
          const m = modal()!;
          const fr = modalFrame()!;
          return (
            <FrameEditorModal
              state={m}
              frame={fr}
              expression={m.kind === "step" ? currentExpr() : null}
              ranges={ranges()}
              defaultDuration={currentExpr()?.default_duration_ms ?? 200}
              defaultDelay={currentExpr()?.default_delay_ms ?? 0}
              onSaveFrame={(pose, image) => saveFrame(fr.name, pose, image)}
              onSaveStepTiming={(d, dl) =>
                m.kind === "step"
                  ? updateStepTiming(m.stepIndex, d, dl)
                  : Promise.resolve()
              }
              onRemoveStep={() =>
                m.kind === "step" ? removeStep(m.stepIndex) : Promise.resolve()
              }
              onDeleteFrame={() => deleteFrame(fr.name)}
              onResetFrame={() => resetFrame(fr.name)}
              onClose={() => setModal(null)}
            />
          );
        })()}
      </Show>
    </div>
  );
};
