"""MMX LoRA Stack: model + clip in/out, five rows of (lora, strength, on) as PLAIN widgets, so
anything — the Deck's Send, a script, the API format — sets them by writing widget values.
Enabled rows are applied in row order with core LoraLoader (strength on model and clip alike).
Replaces the Power Lora Loader (rgthree) in the daily graph; the turbo LoRA node stays separate.
"""
from __future__ import annotations

import folder_paths
import nodes as core_nodes

ROWS = 5
NONE = "(none)"


def lora_names(refresh: bool = False) -> list:
    try:
        if refresh:
            # folder_paths caches per folder and re-scans when a directory mtime changes; a file
            # dropped into an existing folder bumps the mtime, so a plain call already sees it —
            # the explicit cache drop covers filesystems that do not update the directory mtime.
            try:
                folder_paths.filename_list_cache.pop("loras", None)
            except Exception:
                pass
        return [NONE] + list(folder_paths.get_filename_list("loras"))
    except Exception:
        return [NONE]


def rows_from_kwargs(kw: dict) -> list:
    """[{name, strength, on}] for the ROWS widget triples, in row order (empty rows included)."""
    out = []
    for i in range(1, ROWS + 1):
        out.append({"name": kw.get(f"lora_{i}") or NONE, "strength": float(kw.get(f"strength_{i}", 1.0) or 0.0),
                    "on": bool(kw.get(f"on_{i}", True))})
    return out


def effective(rows: list) -> list:
    return [r for r in rows if r["on"] and r["name"] and r["name"] != NONE and r["strength"] != 0.0]


def describe(rows: list) -> str:
    eff = effective(rows)
    if not eff:
        return "(no LoRA enabled — model/clip pass through)"
    return "\n".join(f"{i}. {r['name']} @ {r['strength']:.2f}" for i, r in enumerate(eff, start=1))


class MMXLoRAStack:
    @classmethod
    def INPUT_TYPES(cls):
        names = lora_names()
        req = {"model": ("MODEL",), "clip": ("CLIP",)}
        for i in range(1, ROWS + 1):
            req[f"on_{i}"] = ("BOOLEAN", {"default": i == 1, "label_on": "on", "label_off": "off"})
            req[f"lora_{i}"] = (names, {"default": NONE, "tooltip": f"row {i}: file in models/loras (Refresh re-scans)"})
            req[f"strength_{i}"] = ("FLOAT", {"default": 1.0, "min": 0.0, "max": 2.0, "step": 0.05,
                                             "tooltip": "applied to model and clip alike"})
        return {"required": req}

    RETURN_TYPES = ("MODEL", "CLIP", "STRING")
    RETURN_NAMES = ("model", "clip", "stack")
    FUNCTION = "run"
    CATEGORY = "mmx"
    DESCRIPTION = "Five LoRA rows (lora, strength, on) as plain widgets; enabled rows applied in order. The Deck's Send writes these rows."

    @classmethod
    def VALIDATE_INPUTS(cls, **kw):
        # our own check instead of the combo-list check, so a LoRA copied in after the page
        # loaded (then picked via Refresh) queues fine
        available = set(folder_paths.get_filename_list("loras"))
        missing = [r["name"] for r in effective(rows_from_kwargs(kw)) if r["name"] not in available]
        if missing:
            return "MMX LoRA Stack: not in models/loras: " + ", ".join(missing)
        return True

    def run(self, model, clip, **kw):
        rows = rows_from_kwargs(kw)
        loader = core_nodes.LoraLoader()
        for r in effective(rows):
            model, clip = loader.load_lora(model, clip, r["name"], r["strength"], r["strength"])
        text = describe(rows)
        print("[mmx-lora-stack] " + text.replace("\n", " | "))
        return {"ui": {"text": [text]}, "result": (model, clip, text)}


NODE_CLASS_MAPPINGS = {"MMXLoRAStack": MMXLoRAStack}
NODE_DISPLAY_NAME_MAPPINGS = {"MMXLoRAStack": "MMX LoRA Stack"}
