"""MMX Deck: prompt constructor + preset manager. The whole UI is a DOM panel (web/mmx_deck.js);
the Python side only declares the four state widgets so the panel's state rides in
widgets_values (prompt, preset name, LoRA rows, chosen target nodes) and offers the prompt
as a STRING for anyone who wants to wire it. The Deck never saves on queue: executing it is a
no-op passthrough; Save / Update / Delete are buttons that call /mmx/presets/*."""
from __future__ import annotations

import json


class MMXDeck:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "prompt": ("STRING", {"multiline": True, "default": "", "tooltip": "editor text (the panel edits this)"}),
            "preset": ("STRING", {"default": "", "tooltip": "name of the preset the editor was loaded from / saved as"}),
            "loras_json": ("STRING", {"default": "[]", "tooltip": "the five LoRA rows [{name, strength, on}]"}),
            "targets_json": ("STRING", {"default": "{}", "tooltip": "{manager: node id, stack: node id} — where Send writes"}),
        }}

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("prompt", "loras_json")
    FUNCTION = "run"
    CATEGORY = "mmx"
    DESCRIPTION = "Prompt constructor + preset manager. Not wired: 'Send to graph' pushes the text into the References Manager and the rows into the MMX LoRA Stack."

    def run(self, prompt, preset, loras_json, targets_json):
        try:
            rows = json.loads(loras_json or "[]")
        except Exception:
            rows = []
        return {"ui": {"text": [f"{len(prompt)} chars; preset '{preset}'; {sum(1 for r in rows if r.get('on', True) and r.get('name'))} LoRA row(s) on"]},
                "result": (prompt, json.dumps(rows))}


NODE_CLASS_MAPPINGS = {"MMXDeck": MMXDeck}
NODE_DISPLAY_NAME_MAPPINGS = {"MMXDeck": "MMX Deck"}
