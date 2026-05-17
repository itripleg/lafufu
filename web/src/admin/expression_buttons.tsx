import { Component, For } from "solid-js";
import { api } from "../shared/api";

const EXPRESSIONS = ["happy", "sad", "angry", "surprised", "neutral", "agree", "disagree"];

export const ExpressionButtons: Component = () => {
  return (
    <section class="rounded-lg bg-slate-900 p-4">
      <h2 class="text-lg font-semibold mb-3">Expressions</h2>
      <div class="flex flex-wrap gap-2">
        <For each={EXPRESSIONS}>{(name) => (
          <button
            class="px-3 py-2 rounded bg-slate-700 hover:bg-slate-600 capitalize"
            onClick={() => api.animatorExpression(name).catch((e) => alert(e.message))}
          >{name}</button>
        )}</For>
      </div>
    </section>
  );
};
