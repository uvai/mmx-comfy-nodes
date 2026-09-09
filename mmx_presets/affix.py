"""MMX Prompt Affix: the written prompt with the LoRA trigger words (from MMX LoRA Stack's
`triggers` output) prepended or appended, plus a free prefix / suffix. Sits between the
References Manager's prompt and its consumers, so what the model reads is the Manager's text
plus exactly the triggers of the rows that are switched on.

    prepend:  [prefix\\n] triggers, prompt [\\nsuffix]
    append:   [prefix\\n] prompt, triggers [\\nsuffix]
"""
from __future__ import annotations

MODES = ["prepend", "append"]


def affix(prompt: str, prefix: str = "", suffix: str = "", auto_triggers: str = "", mode: str = "prepend") -> str:
    prompt = (prompt or "").strip()
    trig = ", ".join(t.strip() for t in (auto_triggers or "").replace("\n", ",").split(",") if t.strip())
    if trig and prompt:
        body = f"{trig}, {prompt}" if mode != "append" else f"{prompt}, {trig}"
    else:
        body = trig or prompt
    parts = [p.strip() for p in ((prefix or ""), body, (suffix or "")) if p and p.strip()]
    return "\n".join(parts)


class MMXPromptAffix:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "prompt": ("STRING", {"forceInput": True, "tooltip": "the References Manager's prompt output"}),
            "prefix": ("STRING", {"default": "", "multiline": True, "tooltip": "free text placed before everything (own line)"}),
            "suffix": ("STRING", {"default": "", "multiline": True, "tooltip": "free text placed after everything (own line)"}),
            "mode": (MODES, {"default": "prepend", "tooltip": "where the trigger words go relative to the prompt"}),
        }, "optional": {
            "auto_triggers": ("STRING", {"default": "", "tooltip": "wire MMX LoRA Stack's `triggers` here; comma-separated"}),
        }}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("prompt",)
    FUNCTION = "run"
    CATEGORY = "mmx"
    DESCRIPTION = "Prompt + LoRA trigger words (from the Stack) + prefix / suffix. Shows the final text in the node."

    def run(self, prompt, prefix="", suffix="", mode="prepend", auto_triggers=""):
        out = affix(prompt, prefix, suffix, auto_triggers, mode)
        trig = ", ".join(t.strip() for t in (auto_triggers or "").split(",") if t.strip())
        head = f"triggers ({mode}): {trig}" if trig else "no triggers (no enabled Stack row has any)"
        return {"ui": {"text": [head + "\n" + "—" * 24 + "\n" + out]}, "result": (out,)}


NODE_CLASS_MAPPINGS = {"MMXPromptAffix": MMXPromptAffix}
NODE_DISPLAY_NAME_MAPPINGS = {"MMXPromptAffix": "MMX Prompt Affix"}
