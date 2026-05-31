import { Component, createResource, For, Show } from "solid-js";
import { api, type ImageAsset, type ImageBucket } from "../shared/api";
import { toast } from "../shared/toast";

type Props = {
  bucket: ImageBucket;
  /** Current ref string in the canonical "bucket/kind/name" format, or null. */
  current?: string | null;
  onPick: (ref: string | null) => void;
};

const refOf = (bucket: ImageBucket, item: ImageAsset) =>
  `${bucket}/${item.kind}/${item.name}`;

const isVideoName = (name: string) => /\.mp4$/i.test(name);

export const ImagePicker: Component<Props> = (props) => {
  const [items, { refetch }] = createResource(
    () => props.bucket,
    async (b) => (await api.listImages(b)).items,
  );

  let fileInput!: HTMLInputElement;

  const handleUpload = async (e: Event) => {
    const f = (e.currentTarget as HTMLInputElement).files?.[0];
    if (!f) return;
    try {
      await api.uploadImage(props.bucket, f);
      await refetch();
      toast.ok(`uploaded ${f.name}`);
    } catch (err) {
      toast.err(String(err));
    } finally {
      // Allow re-uploading the same file.
      fileInput.value = "";
    }
  };

  return (
    <div class="flex flex-col gap-3">
      <div class="flex items-center gap-2">
        <input
          ref={fileInput}
          type="file"
          accept="image/*,video/mp4"
          class="hidden"
          onChange={handleUpload}
        />
        <button
          type="button"
          class="px-3 py-1 border border-stone-600 rounded hover:bg-stone-800"
          onClick={() => fileInput.click()}
        >
          Upload
        </button>
        <button
          type="button"
          class="px-3 py-1 border border-stone-600 rounded hover:bg-stone-800"
          onClick={() => props.onPick(null)}
        >
          Clear
        </button>
        <span class="text-xs text-stone-400 ml-auto">
          bucket: {props.bucket}
        </span>
      </div>

      <Show
        when={!items.loading}
        fallback={<div class="text-stone-500 text-sm">loading…</div>}
      >
        <div class="grid grid-cols-[repeat(auto-fill,minmax(96px,1fr))] gap-2">
          <For each={items() ?? []}>
            {(item) => {
              const ref = refOf(props.bucket, item);
              const selected = () => props.current === ref;
              return (
                <button
                  type="button"
                  onClick={() => props.onPick(ref)}
                  class={`p-1 rounded border-2 hover:bg-stone-800 ${
                    selected() ? "border-amber-400" : "border-stone-700"
                  }`}
                  title={`${item.kind}/${item.name}`}
                >
                  <Show
                    when={isVideoName(item.name)}
                    fallback={
                      <img
                        src={api.imageFileUrl(props.bucket, item.kind, item.name)}
                        alt={item.name}
                        class="object-contain h-20 w-full"
                        loading="lazy"
                      />
                    }
                  >
                    <video
                      src={api.imageFileUrl(props.bucket, item.kind, item.name)}
                      class="object-contain h-20 w-full"
                      muted
                      playsinline
                      preload="metadata"
                      onMouseEnter={(e) => void e.currentTarget.play().catch(() => {})}
                      onMouseLeave={(e) => {
                        e.currentTarget.pause();
                        e.currentTarget.currentTime = 0;
                      }}
                    />
                  </Show>
                  <div class="text-[10px] text-stone-400 truncate mt-1">
                    {item.name}
                  </div>
                </button>
              );
            }}
          </For>
        </div>
      </Show>
    </div>
  );
};
