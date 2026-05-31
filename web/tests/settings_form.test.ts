import { describe, expect, it } from "vitest";
import { categoryOf, HIDDEN_KEYS } from "../src/admin/settings_form";

describe("categoryOf", () => {
  it("routes agent.* settings to the agent tab", () => {
    expect(categoryOf("agent.llm_model")).toBe("agent");
    expect(categoryOf("agent.system_prompt")).toBe("agent");
    expect(categoryOf("agent.voice_model")).toBe("agent");
    expect(categoryOf("agent.stt_backend")).toBe("agent");
    expect(categoryOf("agent.whisper_model")).toBe("agent");
    expect(categoryOf("agent.silence_threshold")).toBe("agent");
    expect(categoryOf("agent.silence_seconds")).toBe("agent");
    expect(categoryOf("agent.auto_listen")).toBe("agent");
    expect(categoryOf("agent.interaction_mode")).toBe("agent");
    expect(categoryOf("agent.trigger.phrase")).toBe("agent");
    expect(categoryOf("agent.wakeword.enabled")).toBe("agent");
    expect(categoryOf("agent.input_device")).toBe("agent");
  });

  it("routes animator.* settings to the animator tab", () => {
    expect(categoryOf("animator.idle_animation.enabled")).toBe("animator");
    expect(categoryOf("animator.head_lr.default")).toBe("animator");
    expect(categoryOf("animator.jaw.default")).toBe("animator");
  });

  it("routes speaker.* and tts.* to the audio tab", () => {
    expect(categoryOf("speaker.volume")).toBe("audio");
    expect(categoryOf("speaker.alsa_card")).toBe("audio");
    expect(categoryOf("tts.length_scale")).toBe("audio");
  });

  it("routes printer.* to the printer tab", () => {
    expect(categoryOf("printer.auto_print")).toBe("printer");
    expect(categoryOf("printer.media")).toBe("printer");
  });

  it("falls back to other for unknown prefixes", () => {
    expect(categoryOf("settings.bootstrap.no_new_settings")).toBe("other");
    expect(categoryOf("custom.foo")).toBe("other");
  });
});

describe("HIDDEN_KEYS", () => {
  it("hides the raw prompt keys owned by PromptCard", () => {
    for (const key of [
      "agent.system_prompt",
      "agent.prompt_preset",
      "agent.prompt.street_oracle",
      "agent.prompt.fortune_teller",
    ]) {
      expect(HIDDEN_KEYS.has(key)).toBe(true);
    }
  });

  it("does not hide the plain fortune settings (they auto-render)", () => {
    expect(HIDDEN_KEYS.has("agent.fortune.lucky_numbers_count")).toBe(false);
    expect(HIDDEN_KEYS.has("agent.fortune.lucky_number_max")).toBe(false);
    expect(HIDDEN_KEYS.has("agent.fortune.lucky_subway_stop")).toBe(false);
  });
});
