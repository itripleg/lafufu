import { Component } from "solid-js";

export const Caption: Component<{ text: () => string | undefined }> = (props) => {
  return (
    <div class="absolute bottom-10 left-0 right-0 flex justify-center pointer-events-none">
      <div class="max-w-[80vw] text-center text-2xl text-slate-100/80 leading-snug">
        {props.text() ?? ""}
      </div>
    </div>
  );
};
