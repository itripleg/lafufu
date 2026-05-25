import {
  Component,
  createEffect,
  createMemo,
  createResource,
  createSignal,
  For,
  Show,
} from "solid-js";
import { api, type FrameDTO } from "../shared/api";
import { NatsWs } from "../shared/nats_ws";
import { toast } from "../shared/toast";
import { ImagePicker } from "./image_picker";

type Pose = { head_lr: number; head_ud: number; eye: number; jaw: number; brow: number };

const IDLE: Pose = { head_lr: 2063, head_ud: 3082, eye: 2045, jaw: 1811, brow: 2075 };

const RANGES: Array<{ key: keyof Pose; lo: number; hi: number; glyph: string }> = [
  { key: "head_lr", lo: 1828, hi: 2298, glyph: "↔" },
  { key: "head_ud", lo: 2885, hi: 3278, glyph: "↕" },
  { key: "eye",     lo: 1995, hi: 2085, glyph: "◉" },
  { key: "jaw",     lo: 1594, hi: 1811, glyph: "▽" },
  { key: "brow",    lo: 2056, hi: 2087, glyph: "︿" },
];

const PREVIEW_DEBOUNCE_MS = 40;

export const FramesSection: Component<{ nats: NatsWs }> = (_props) => {
  const [frames, { refetch }] = createResource(async () =>
    (await api.listFrames()).items,
  );
  const [selectedName, setSelectedName] = createSignal<string | null>(null);
  const [pose, setPose] = createSignal<Pose>({ ...IDLE });
  const [pickerOpen, setPickerOpen] = createSignal(false);
  // Tracks a not-yet-saved image choice made via the picker.
  const [pendingImage, setPendingImage] = createSignal<string | null | undefined>(undefined);

  // Idle-animation toggle. Reads/writes the animator.idle_animation.enabled
  // setting so the operator can pin Lafufu in a pose without the idle loop
  // taking over after the 1.5s intent-quiet window.
  const [idleEnabled, setIdleEnabled] = createSignal(true);
  (async () => {
    try {
      const items = (await api.listSettings()) as Array<{ key: string; value: string }>;
      const row = items.find((r) => r.key === "animator.idle_animation.enabled");
      if (row) setIdleEnabled(row.value === "true");
    } catch {
      /* settings load failure — leave default */
    }
  })();
  const toggleIdle = async () => {
    const next = !idleEnabled();
    setIdleEnabled(next);
    try {
      await api.patchSetting("animator.idle_animation.enabled", {
        value: next,
        value_type: "bool",
      });
    } catch (e: unknown) {
      setIdleEnabled(!next);  // revert on failure
      toast.err("toggle failed", (e as Error)?.message ?? String(e));
    }
  };

  const selected = createMemo<FrameDTO | null>(
    () => frames()?.find((f) => f.name === selectedName()) ?? null,
  );

  // When selection changes, copy that frame's pose into the local sliders,
  // clear any pending image from the previous selection, AND drive Lafufu
  // physically to the selected pose so the operator can see the frame.
  createEffect(() => {
    const f = selected();
    if (f) {
      const p: Pose = {
        head_lr: f.head_lr, head_ud: f.head_ud,
        eye: f.eye, jaw: f.jaw, brow: f.brow,
      };
      setPose(p);
      setPickerOpen(false);
      setPendingImage(undefined);
      // Atomic full-pose set — uses /animator/set_pose so all 5 servos move
      // in one shot. (5 parallel /preview calls race; only the last servo
      // would stick because each /preview only updates one channel from the
      // bus's current pose at the moment it's processed.)
      api.animatorSetPose(p).catch(() => undefined);
    }
  });

  // Single-shared-timer debouncer for live preview. Slider drags spam
  // change events; we coalesce per-servo to one POST every 40 ms.
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
        try {
          await api.animatorPreview(p.servo, p.value);
        } catch {
          /* swallow — sliders aren't worth a toast per twitch */
        }
      }
    }, PREVIEW_DEBOUNCE_MS);
  };

  const onSliderInput = (key: keyof Pose, raw: string) => {
    const value = Number(raw);
    setPose((p) => ({ ...p, [key]: value }));
    schedulePreview(key, value);
  };

  const onSnapshot = async () => {
    const name = window.prompt("frame name (lowercase, _underscores):");
    if (!name) return;
    try {
      await api.snapshotFrame(name.trim());
      await refetch();
      setSelectedName(name.trim());
      toast.ok(`snapshot saved: ${name.trim()}`);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      toast.err("snapshot failed", msg);
    }
  };

  const onSave = async () => {
    const f = selected();
    if (!f) return;
    // pendingImage === undefined means no change from picker; use f.image.
    const image = pendingImage() !== undefined ? (pendingImage() ?? null) : f.image;
    try {
      const updated = await api.updateFrame(f.name, {
        ...pose(),
        image,
        description: f.description,
      });
      await refetch();
      setSelectedName(updated.name);
      setPendingImage(undefined);
      toast.ok(`saved ${f.name}`);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      toast.err("save failed", msg);
    }
  };

  const onDelete = async () => {
    const f = selected();
    if (!f) return;
    if (!window.confirm(`Delete frame "${f.name}"?`)) return;
    try {
      await api.deleteFrame(f.name);
      await refetch();
      setSelectedName(null);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      toast.err("delete failed", msg);
    }
  };

  const onPickImage = (ref: string | null) => {
    setPendingImage(ref);
    setPickerOpen(false);
  };

  // Effective image: prefer pendingImage (once set by picker) over stored value.
  const effectiveImage = createMemo<string | null>(() => {
    const pending = pendingImage();
    if (pending !== undefined) return pending;
    return selected()?.image ?? null;
  });

  // Parse "{bucket}/{kind}/{name}" → preview URL via api.imageFileUrl.
  const imageUrl = createMemo<string | null>(() => {
    const ref = effectiveImage();
    if (!ref) return null;
    const parts = ref.split("/");
    if (parts.length !== 3) return null;
    const [bucket, kind, name] = parts;
    if (bucket !== "letterheads" && bucket !== "sprites") return null;
    return api.imageFileUrl(bucket as "letterheads" | "sprites", kind, name);
  });

  return (
    <div class="border border-stone-700 rounded-lg p-4 bg-stone-900/40">
      <div class="flex items-baseline justify-between mb-3 gap-3">
        <h2 class="text-lg font-semibold">Frames</h2>
        <label class="flex items-center gap-2 text-sm cursor-pointer select-none">
          <input
            type="checkbox"
            checked={idleEnabled()}
            onChange={toggleIdle}
            class="accent-amber-500"
          />
          <span class={idleEnabled() ? "text-stone-300" : "text-stone-500"}>
            idle animation
          </span>
        </label>
        <button
          type="button"
          onClick={onSnapshot}
          class="px-3 py-1 border border-amber-600 text-amber-300 rounded hover:bg-amber-900/30"
        >
          + Snapshot current pose
        </button>
      </div>

      <div class="grid gap-4 grid-cols-[1fr_1.4fr_1fr]">
        {/* ── left: gallery ── */}
        <div class="border border-stone-700 rounded p-2 max-h-80 overflow-y-auto">
          <For each={frames() ?? []}>
            {(f) => (
              <button
                type="button"
                onClick={() => setSelectedName(f.name)}
                class={`block w-full text-left px-2 py-1 rounded text-sm hover:bg-stone-800 ${
                  selectedName() === f.name ? "bg-amber-500/20 text-amber-200" : ""
                }`}
              >
                {f.name}
              </button>
            )}
          </For>
        </div>

        {/* ── middle: selected frame card ── */}
        <Show
          when={selected()}
          fallback={
            <div class="text-stone-500 text-sm p-4 italic">
              Select a frame to view + edit.
            </div>
          }
        >
          {(_f) => (
            <div class="flex flex-col gap-2">
              <div class="font-mono text-amber-300">{selected()!.name}</div>
              <div class="h-32 border border-stone-700 rounded bg-stone-950 flex items-center justify-center">
                <Show
                  when={imageUrl()}
                  fallback={<span class="text-xs text-stone-600">no image</span>}
                >
                  <img
                    src={imageUrl()!}
                    alt="frame"
                    class="max-h-full max-w-full object-contain"
                  />
                </Show>
              </div>
              <Show when={pendingImage() !== undefined}>
                <p class="text-xs text-amber-400 italic">
                  image changed — save to persist
                </p>
              </Show>
              <div class="flex gap-2 flex-wrap">
                <button
                  type="button"
                  onClick={() => setPickerOpen((o) => !o)}
                  class="px-3 py-1 border border-stone-600 rounded hover:bg-stone-800 text-sm"
                >
                  {pickerOpen() ? "Close picker" : "Pick image"}
                </button>
                <button
                  type="button"
                  onClick={onSave}
                  class="px-3 py-1 border border-green-700 text-green-300 rounded hover:bg-green-900/30 text-sm"
                >
                  Save
                </button>
                <button
                  type="button"
                  onClick={onDelete}
                  class="px-3 py-1 border border-red-800 text-red-300 rounded hover:bg-red-900/30 text-sm ml-auto"
                >
                  Delete
                </button>
              </div>
              <Show when={pickerOpen()}>
                <ImagePicker
                  bucket="sprites"
                  current={effectiveImage() ?? undefined}
                  onPick={onPickImage}
                />
              </Show>
            </div>
          )}
        </Show>

        {/* ── right: sliders ── */}
        <div class="flex flex-col gap-2">
          <For each={RANGES}>
            {(r) => (
              <div class="flex items-center gap-2">
                <span class="font-mono text-stone-400 w-8 text-center">{r.glyph}</span>
                <input
                  type="range"
                  min={r.lo}
                  max={r.hi}
                  value={pose()[r.key]}
                  onInput={(e) => onSliderInput(r.key, e.currentTarget.value)}
                  class="flex-1"
                />
                <span class="font-mono text-stone-300 w-12 text-right text-xs">
                  {pose()[r.key]}
                </span>
              </div>
            )}
          </For>
        </div>
      </div>
    </div>
  );
};
